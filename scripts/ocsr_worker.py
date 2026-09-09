"""다른 파이썬 환경에서 인식기를 대신 돌려주는 워커.

MolScribe(py3.10/torch1.x)와 DECIMER(py3.13/TF)는 한 프로세스에 같이 올릴 수
없다. 그렇다고 이미지 한 장마다 프로세스를 새로 띄우면 1.13GB 체크포인트를
매번 읽는다. 그래서 이 워커를 한 번 띄워 두고 줄 단위로 주고받는다.

프로토콜 - 한 줄에 요청 하나, 한 줄에 응답 하나:
    입력   <이미지 경로>
    출력   {"smiles": "...", "confidence": 0.93}
           {"error": "..."}            인식 실패나 예외
    시작   {"ready": true} 또는 {"error": "..."}  모델 적재 직후 한 번

응답은 반드시 한 줄 JSON 이다. 모델이 stdout 으로 뱉는 잡소리와 섞이면
안 되므로, 적재 중 출력은 전부 stderr 로 돌린다.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_rgb(path: str):
    """그림 파일을 RGB numpy 배열로. 경로 인코딩과 무관하게 연다."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"))


def _serial_convert_graph_to_smiles(coords, symbols, edges, images=None, num_workers=1):
    """molscribe.chemistry.convert_graph_to_smiles 와 같은 일을 프로세스 없이 한다.

    원본은 이미지 한 장을 넣어도 multiprocessing.Pool(16) 을 열어 그래프 하나를
    후처리한다. 윈도우는 spawn 이라 자식 16개가 각각 파이썬과 rdkit 을 새로
    올린다 - 이 기계(16GB)에서 장당 프로세스 18~27개, 십수 초가 그 값이다.
    이 워커는 한 번에 한 장만 다루므로 같은 프로세스에서 순서대로 하면 된다.
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


def load_molscribe(checkpoint: str):
    import torch
    from molscribe import MolScribe, interface

    # predict_images 는 num_workers 를 넘길 자리가 없다. interface 모듈이 이름을
    # 직접 들여왔으므로 그 이름을 바꿔치기한다.
    interface.convert_graph_to_smiles = _serial_convert_graph_to_smiles

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = MolScribe(checkpoint, device=device)

    def recognize(path: str) -> dict:
        # predict_image_file 은 cv2.imread 를 쓰는데, 윈도우의 OpenCV 는 한글이
        # 든 경로를 못 연다 (이 기계는 임시 폴더부터 한글이다). 빈 배열이 넘어가
        # cvtColor 단언에서 죽는다. PIL 로 직접 읽어 RGB 배열로 넘긴다.
        out = model.predict_image(
            load_rgb(path), return_atoms_bonds=False, return_confidence=True
        )
        smiles = out.get("smiles")
        if not smiles:
            return {"error": "빈 SMILES"}
        return {"smiles": smiles, "confidence": float(out.get("confidence", 0.0))}

    return recognize


def load_decimer(_checkpoint: str | None):
    from DECIMER import predict_SMILES

    def recognize(path: str) -> dict:
        smiles, tokens = predict_SMILES(path, confidence=True)
        if not smiles:
            return {"error": "빈 SMILES"}
        # 토큰별 softmax 확률의 평균 (chemcheck.ocsr.decimer_confidence 와 같은 식).
        # MolScribe 의 점수와 같은 자가 아니다. 토큰이 없으면 None -> 부르는 쪽이 NaN.
        confs = [float(c) for _tok, c in tokens]
        return {"smiles": smiles, "confidence": sum(confs) / len(confs) if confs else None}

    return recognize


LOADERS = {"molscribe": load_molscribe, "decimer": load_decimer}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=sorted(LOADERS))
    parser.add_argument("--checkpoint", default=None)
    args = parser.parse_args()

    # 모델 적재는 stdout 으로 진행 상황을 뱉는 일이 많다. 프로토콜을 더럽히지
    # 않도록 그동안의 stdout 을 통째로 stderr 로 돌린다.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            recognize = LOADERS[args.engine](args.checkpoint)
    except Exception as exc:
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1

    emit({"ready": True})

    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = recognize(path)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
