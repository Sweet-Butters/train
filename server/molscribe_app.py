"""MolScribe 를 Modal 위에 **따로** 올린다 - DECIMER 와 같은 컨테이너에 두지 않는다.

노트북에서는 둘을 한 기계에 올려야 해서 `.venv310` + `bridge.py` 로 프로세스 너머
호출을 짰다. 서버리스에서는 그 문제가 없다 - 이미지를 둘 선언하면 컨테이너가
따로 뜨고 메모리도 각자다. Python 3.10/torch1.x 와 3.11/TF 가 서로를 모른다.

    MolScribe 1.1.1 은 torch<2.0 에 고정돼 있고 torch 1.x 는 py3.11+ 휠이 없다.
    그래서 이 이미지만 python_version="3.10" 이다.

DECIMER 쪽(server/modal_app.py)이 이 함수를 spawn 으로 부르고, 자기 추론을 끝낸 뒤
결과를 수거한다. 즉 둘이 **병렬**로 돈다. 직렬로 할 이유가 없다 - 메모리를
공유하지 않으므로 직렬은 시간만 두 배가 된다.

이 파일이 죽어도 DECIMER 경로는 살아 있어야 한다. 부르는 쪽이 예외를 삼킨다.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

CKPT_DIR = "/opt/molscribe"
CKPT = f"{CKPT_DIR}/molscribe.pth"
CKPT_URL = "https://huggingface.co/yujieq/MolScribe/resolve/main/swin_base_char_aux_1m.pth"
CKPT_SIZE = 1_134_940_406

MIN_CONTAINERS = int(os.environ.get("CHEMCHECK_MIN_CONTAINERS", "0"))
SCALEDOWN_WINDOW = int(os.environ.get("CHEMCHECK_SCALEDOWN", "900"))


def _bake_checkpoint() -> None:
    """체크포인트 1.13GB 를 빌드 때 받아 레이어에 굳힌다.

    HuggingFace 가 Range 를 지원하므로 이어받는다. 콜드스타트마다 1.13GB 를 받으면
    심사에서 못 쓴다. 실패해도 빌드를 세우지 않는다 - 런타임이 available()=False 로
    접고, 부르는 쪽은 DECIMER 만으로 계속한다.
    """
    import shutil, time, urllib.request

    dest = Path(CKPT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, 13):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= CKPT_SIZE:
            print(f"BAKE(molscribe): 완료 {have/1e9:.2f}GB")
            return
        try:
            req = urllib.request.Request(
                CKPT_URL,
                headers={"Range": f"bytes={have}-", "User-Agent": "chemcheck/1.0"},
            )
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "ab") as f:
                shutil.copyfileobj(r, f, 4 * 1024 * 1024)
        except Exception as exc:
            print(f"BAKE(molscribe): {i}회차 실패 {exc}")
            time.sleep(4 * i)
    got = dest.stat().st_size if dest.exists() else 0
    print(f"BAKE(molscribe): 미완 {got/1e9:.2f}GB - 런타임이 접는다")


image = (
    # torch<2.0 때문에 3.10 이다. DECIMER 이미지(3.11)와 섞이지 않는다.
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("libgl1", "libglib2.0-0", "libsm6", "libxext6")
    .env({"OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    .pip_install("molscribe", "rdkit", "pillow", "numpy<2")
    .run_function(_bake_checkpoint)
)

app = modal.App("chemcheck-molscribe")


def _serial_convert_graph_to_smiles(coords, symbols, edges, images=None, num_workers=1):
    """molscribe 원본은 이미지 한 장에도 multiprocessing.Pool(16) 을 연다.

    컨테이너에서 그러면 파이썬과 rdkit 이 16벌 올라가 메모리가 터진다.
    한 번에 한 장만 다루므로 같은 프로세스에서 순서대로 한다
    (scripts/ocsr_worker.py 가 같은 이유로 같은 패치를 한다).
    """
    import numpy as np
    from molscribe.chemistry import _convert_graph_to_smiles

    if images is None:
        results = [_convert_graph_to_smiles(c, s, e) for c, s, e in zip(coords, symbols, edges)]
    else:
        results = [_convert_graph_to_smiles(c, s, e, im)
                   for c, s, e, im in zip(coords, symbols, edges, images)]
    smiles_list, molblock_list, success = zip(*results)
    return smiles_list, molblock_list, float(np.mean(success))


@app.cls(
    image=image,
    cpu=2.0,
    memory=6144,          # torch + 1.13GB 체크포인트. 컨테이너가 따로라 DECIMER 와 안 겹친다.
    timeout=600,
    min_containers=MIN_CONTAINERS,
    scaledown_window=SCALEDOWN_WINDOW,
)
@modal.concurrent(max_inputs=2)
class MolScribeReader:
    @modal.enter()
    def load(self):
        self.model = None
        self.error = None
        try:
            if not Path(CKPT).exists() or Path(CKPT).stat().st_size < CKPT_SIZE:
                self.error = "체크포인트가 없습니다"
                print("MolScribe:", self.error)
                return
            import torch
            from molscribe import MolScribe, interface

            interface.convert_graph_to_smiles = _serial_convert_graph_to_smiles
            self.model = MolScribe(CKPT, device="cpu")
            print("MolScribe 준비됨, torch", torch.__version__)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            print("MolScribe 적재 실패:", self.error)

    @modal.method()
    def read(self, image_bytes: bytes) -> dict:
        """그림 바이트 -> {"smiles","confidence"} 또는 {"error"}. 예외를 올리지 않는다."""
        if self.model is None:
            return {"error": self.error or "모델 없음"}
        try:
            import io

            import numpy as np
            from PIL import Image

            # cv2.imread 를 쓰지 않는다 - 경로 인코딩에 걸린다. PIL 로 직접 읽는다.
            with Image.open(io.BytesIO(image_bytes)) as img:
                arr = np.asarray(img.convert("RGB"))
            out = self.model.predict_image(arr, return_atoms_bonds=False, return_confidence=True)
            smiles = out.get("smiles")
            if not smiles:
                return {"error": "빈 SMILES"}
            return {"smiles": smiles, "confidence": float(out.get("confidence", 0.0))}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
