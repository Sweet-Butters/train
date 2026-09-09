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


def load_molscribe(checkpoint: str):
    import torch
    from molscribe import MolScribe

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = MolScribe(checkpoint, device=device)

    def recognize(path: str) -> dict:
        out = model.predict_image_file(
            path, return_atoms_bonds=False, return_confidence=True
        )
        smiles = out.get("smiles")
        if not smiles:
            return {"error": "빈 SMILES"}
        return {"smiles": smiles, "confidence": float(out.get("confidence", 0.0))}

    return recognize


def load_decimer(_checkpoint: str | None):
    from DECIMER import predict_SMILES

    def recognize(path: str) -> dict:
        smiles = predict_SMILES(path)
        if not smiles:
            return {"error": "빈 SMILES"}
        # DECIMER 는 신뢰도를 주지 않는다. 없는 값을 지어내지 않는다.
        return {"smiles": smiles, "confidence": None}

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
