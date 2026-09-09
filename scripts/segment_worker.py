"""페이지 그림에서 화학 구조 영역을 오려내는 워커 (.venv-seg 에서 돈다).

왜 별도 환경인가:
    decimer-segmentation 은 tensorflow<=2.15.1 에 고정돼 있고, TF 2.15 는
    py3.13 휠이 없다. 반대로 .venv 의 DECIMER 인식기는 py3.13/TF 2.20 을 쓴다.
    핀이 배타적이라 한 프로세스에 못 올린다.

프로토콜 - 한 줄에 요청 하나, 한 줄에 응답 하나:
    입력   <페이지 이미지 경로>|<잘라낸 그림을 둘 폴더>
    출력   {"regions": ["...png", ...]}      구조가 없으면 빈 목록
           {"error": "..."}
    시작   {"ready": true} 또는 {"error": "..."}

응답은 반드시 한 줄 JSON 이다. 모델이 stdout 으로 뱉는 잡소리와 섞이면 안 되므로
적재·추론 중 출력은 전부 stderr 로 돌린다.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_segmenter():
    from decimer_segmentation import segment_chemical_structures
    from PIL import Image
    import numpy as np

    def segment(page_path: str, out_dir: str) -> dict:
        page = Path(page_path)
        dest = Path(out_dir)
        dest.mkdir(parents=True, exist_ok=True)

        with Image.open(page) as im:
            array = np.array(im.convert("RGB"))

        # expand=True 는 잘라낸 영역을 넉넉히 잡는다. 구조 가장자리의 원자
        # 기호가 잘리면 인식기가 그 자리를 통째로 놓친다.
        segments = segment_chemical_structures(array, expand=True)

        written = []
        for i, seg in enumerate(segments, start=1):
            target = dest / f"{page.stem}_s{i:02d}.png"
            Image.fromarray(seg).save(target)
            written.append(str(target))
        return {"regions": written}

    return segment


def main() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            segment = load_segmenter()
    except Exception as exc:
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1

    emit({"ready": True})

    for line in sys.stdin:
        request = line.strip()
        if not request:
            continue
        try:
            page_path, _, out_dir = request.partition("|")
            if not out_dir:
                raise ValueError("요청 형식은 <페이지 경로>|<출력 폴더> 이다")
            with contextlib.redirect_stdout(sys.stderr):
                result = segment(page_path, out_dir)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
