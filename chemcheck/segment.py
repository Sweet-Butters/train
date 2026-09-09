"""페이지 그림 안에서 화학 구조 영역을 찾아 오려낸다.

extract.py 가 쓰는 세 번째 경로다.

    벡터로 그린 PDF   -> 경로를 그대로 뽑는다
    PPT 도형          -> 도형 묶음의 자리를 잡아 오려낸다
    래스터 페이지     -> 여기. 픽셀 안에 구워진 구조를 찾아야 한다

스캔본, 화면 캡처, AI 슬라이드 생성기가 내보낸 PDF 가 여기 해당한다. 페이지가
사진 한 장이라 앞의 두 경로가 아무것도 얻지 못한다.

분할기가 없으면 빈 목록을 돌려준다. 없는 것을 있는 척하지 않는다 - 부르는
쪽은 페이지 통이미지를 쓰던 기존 동작을 그대로 이어가면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bridge import Worker, venv_python

WORKER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "segment_worker.py"

# decimer-segmentation 은 tensorflow<=2.15.1 에 고정돼 있어 py3.13 환경에 들어가지
# 못한다. scripts/setup_segmentation.sh 가 여기에 깐다.
SEG_PYTHON = venv_python(".venv-seg")

# 분할기 적재는 오래 걸린다(가중치 내려받기 포함). 넉넉히 준다.
_LOAD_TIMEOUT = 900.0
_CALL_TIMEOUT = 300.0

_worker: Worker | None = None


def _get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(
            SEG_PYTHON,
            WORKER_SCRIPT,
            load_timeout=_LOAD_TIMEOUT,
            call_timeout=_CALL_TIMEOUT,
        )
    return _worker


def available() -> bool:
    """분할기를 쓸 수 있을 법한지. 무거운 적재는 하지 않는다."""
    return _get_worker().available()


def unavailable_reason() -> str | None:
    """못 쓰는 이유. 조용히 사라지면 원인을 알 수 없다."""
    return _get_worker().unavailable_reason


@dataclass(frozen=True)
class Segmentation:
    """분할 결과. '구조가 없다'와 '분할에 실패했다'를 구분한다.

    둘을 같은 빈 목록으로 뭉뚱그리면 실패가 '구조 없는 페이지'로 둔갑한다.
    실제로 가중치를 못 받아 한 장도 못 본 실행이 '구조 0개'로 보고된 적이 있다.
    검사기가 침묵한 이유를 잃어버리는 것은 이 프로젝트에서 가장 나쁜 결함이다.
    """

    regions: list[Path]
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    def __bool__(self) -> bool:
        return bool(self.regions)

    def __iter__(self):
        return iter(self.regions)

    def __len__(self) -> int:
        return len(self.regions)


def segment_page(page_image: Path, out_dir: Path) -> Segmentation:
    """페이지 그림에서 구조 영역들을 오려낸다.

    구조가 없는 페이지(개념도, 표, 그래프)에서는 regions 가 비고 failure 는 None
    이다. 분할을 못 한 경우에는 failure 에 사유가 남는다. 부르는 쪽은 그 둘을
    반드시 다르게 다뤄야 한다 - 전자는 '볼 것이 없었다', 후자는 '보지 못했다'다.
    """
    worker = _get_worker()
    if not worker.available():
        return Segmentation([], worker.unavailable_reason or "분할기를 쓸 수 없음")

    reply = worker.call(f"{page_image}|{out_dir}")
    if reply is None:
        return Segmentation([], worker.unavailable_reason or "분할기가 응답하지 않음")
    if "error" in reply:
        return Segmentation([], reply["error"])
    if "regions" not in reply:
        return Segmentation([], "분할기가 알 수 없는 응답을 냈음")
    return Segmentation([Path(p) for p in reply["regions"]])


def shutdown() -> None:
    """워커를 접는다. 한 번 실행이 끝나면 불러도 좋다."""
    if _worker is not None:
        _worker.stop()
