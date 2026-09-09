"""페이지 분할 다리 검증.

진짜 분할기는 별도 환경(.venv-seg)에 있고 가중치도 무겁다. 여기서는 그것을
부르지 않고, extract.py 가 기대는 약속만 본다.

    분할기가 없으면 예외를 던지지 않는다 - 파이프라인을 멈추지 않는다.
    '구조가 없다'와 '분할에 실패했다'를 구분한다.
    워커가 내놓은 경로를 그대로 전달한다.

둘을 구분하는 것이 핵심이다. 같은 빈 목록으로 뭉뚱그리면 실패가 '구조 없는
페이지'로 둔갑한다. 실제로 가중치를 못 받아 한 장도 못 본 실행이 '구조 0개'로
보고된 적이 있다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import segment  # noqa: E402


def _fresh(monkey_python: Path, monkey_script: Path) -> None:
    """모듈 전역 워커를 지정한 경로로 새로 세운다."""
    segment.shutdown()
    segment._worker = None
    segment.SEG_PYTHON = monkey_python
    segment.WORKER_SCRIPT = monkey_script


STUB_WORKER = '''
import json, sys
from pathlib import Path

def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

emit({"ready": True})
for line in sys.stdin:
    req = line.strip()
    if not req:
        continue
    page, _, out = req.partition("|")
    if "empty" in page:            # 구조가 없는 페이지
        emit({"regions": []})
    elif "boom" in page:           # 이번 건만 실패
        emit({"error": "분할 실패"})
    else:
        dest = Path(out)
        dest.mkdir(parents=True, exist_ok=True)
        made = []
        for i in (1, 2):
            f = dest / f"{Path(page).stem}_s{i:02d}.png"
            f.write_bytes(b"")
            made.append(str(f))
        emit({"regions": made})
'''


def test_missing_segmenter_is_empty_not_an_error() -> None:
    """분할기가 없으면 조용히 빈 목록. 파이프라인을 멈추지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        _fresh(Path(tmp) / "없는파이썬.exe", Path(tmp) / "없는워커.py")
        assert segment.available() is False, "없는 분할기를 쓸 수 있다고 했다"
        got = segment.segment_page(Path("page.png"), Path(tmp))
        assert list(got) == [], f"영역이 있다고 했다: {list(got)}"
        assert not got.ok, "분할기가 없는데 성공했다고 했다"
        assert got.failure, "실패 사유를 남기지 않았다"
    print(f"  분할기 없음 -> {got.failure}")


def test_regions_come_back_and_empty_page_is_empty() -> None:
    """워커가 준 경로를 그대로 전달하고, 구조 없는 페이지는 빈 목록."""
    with tempfile.TemporaryDirectory() as tmp:
        worker = Path(tmp) / "stub_segment_worker.py"
        worker.write_text(STUB_WORKER, encoding="utf-8")
        _fresh(Path(sys.executable), worker)

        assert segment.available(), f"다리를 못 씀: {segment.unavailable_reason()}"

        found = segment.segment_page(Path("p006.png"), Path(tmp) / "out")
        assert len(found) == 2, f"영역 2개가 아니다: {list(found)}"
        assert found.ok, f"성공인데 실패로 봤다: {found.failure}"
        assert all(p.exists() for p in found), "받은 경로가 실제 파일이 아니다"

        empty = segment.segment_page(Path("empty.png"), Path(tmp) / "out")
        assert len(empty) == 0 and empty.ok, \
            f"구조 없는 페이지를 실패로 봤다: {empty.failure}"

        # 여기가 핵심이다. 실패도 영역 0개지만 '구조 없음'과 같아서는 안 된다.
        boom = segment.segment_page(Path("boom.png"), Path(tmp) / "out")
        assert len(boom) == 0, "실패했는데 영역을 냈다"
        assert not boom.ok and boom.failure, "실패가 '구조 없음'으로 둔갑했다"

        segment.shutdown()
    print(f"  구조 2개 -> {len(found)}개 ok / 빈 페이지 -> 0개 ok / 실패 -> {boom.failure!r}")


if __name__ == "__main__":
    test_missing_segmenter_is_empty_not_an_error()
    print("통과: 분할기가 없어도 멈추지 않고, 못 썼다고 말한다.")
    print()
    test_regions_come_back_and_empty_page_is_empty()
    print("통과: '구조 없음'과 '보지 못함'을 구분한다.")
