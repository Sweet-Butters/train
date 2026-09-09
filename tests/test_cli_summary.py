"""요약 출력 검증.

지키려는 것은 형식이 아니라 원칙이다: 이 도구가 몇 건을 실제로 판정했는지가
일치/오류 건수보다 먼저 와야 한다. 침묵하는 검사기는 오류 0건을 공짜로 얻으므로,
판정률이 묻히면 요약이 스스로를 속인다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.cases import CORPUS  # noqa: E402
from chemcheck.cli import summary_lines  # noqa: E402
from chemcheck.ocsr import Engine, Prediction  # noqa: E402
from chemcheck.pipeline import run  # noqa: E402

DECK = ROOT / "bench" / "decks" / "synthetic.pptx"


class MixedEngine(Engine):
    """장 번호에 따라 다른 방식으로 실패하는 인식기. 침묵 사유를 골고루 만든다."""

    name = "mixed"

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        n = int(image_path.stem.split("_")[0].lstrip("s"))
        if n <= 4:
            return Prediction(CORPUS[n - 1].smiles, 0.95, self.name)
        if n == 5:
            return Prediction(CORPUS[4].smiles, 0.42, self.name)   # 신뢰도 부족
        if n == 6:
            return Prediction("이건 SMILES 가 아니다", 0.99, self.name)  # 구조로 못 읽음
        return None                                                 # 아무것도 못 냄


def _deck() -> Path:
    if not DECK.exists():
        from bench.cases import DECKS, build_deck
        return build_deck(CORPUS, DECKS / "synthetic.pptx")
    return DECK


def _summary(engines: list[Engine]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        results = run(_deck(), Path(tmp), engines)
    return summary_lines(results)


def test_coverage_comes_before_counts() -> None:
    """판정률이 일치/오류 건수보다 먼저 나와야 한다."""
    lines = _summary([MixedEngine()])
    body = [ln for ln in lines if ln.strip() and set(ln.strip()) != {"─"}]

    assert "판정했습니다" in body[0], f"첫 줄이 판정률이 아니다: {body[0]!r}"
    assert "판정률" in body[0], f"판정률 수치가 없다: {body[0]!r}"

    where_rate = next(i for i, ln in enumerate(body) if "판정률" in ln)
    where_ok = next(i for i, ln in enumerate(body) if "일치" in ln)
    assert where_rate < where_ok, "일치 건수가 판정률보다 먼저 나온다"
    print(f"  {body[0].strip()}")
    print("통과: 몇 건을 봤는지를 먼저 말한다.")


def test_silence_reasons_are_grouped() -> None:
    """보류 사유는 값이 박힌 자유 문장이다. 묶어서 세지 않으면 전부 1건씩 흩어진다."""
    lines = _summary([MixedEngine()])
    text = "\n".join(lines)

    assert "인식기를 쓸 수 없음" in text, "'인식 결과 없음'을 묶지 못했다"
    assert "인식 신뢰도가 낮음" in text, "신뢰도 사유를 묶지 못했다"
    assert "0.42" not in text, "요약에 원본 신뢰도 값이 새어 나왔다 = 묶이지 않았다"
    print("  " + " / ".join(ln.strip() for ln in lines if ln.startswith("        ")))
    print("통과: 사유가 값이 아니라 종류로 묶인다.")


def test_nothing_judged_is_not_reported_as_clean() -> None:
    """전부 침묵했을 때 '문제 없음'처럼 보이면 안 된다."""
    lines = _summary([])
    text = "\n".join(lines)

    assert "판정률 0.0%" in text, f"판정률 0% 를 말하지 않는다:\n{text}"
    assert "보지 못함" in text, "판정 0건인데 경고가 없다"
    print("  " + next(ln.strip() for ln in lines if "판정했습니다" in ln))
    print("통과: 침묵을 무결과로 포장하지 않는다.")


def test_no_images_says_so() -> None:
    lines = summary_lines([])
    assert any("검사할 그림이 없습니다" in ln for ln in lines), lines
    print("  그림 0건 -> 나눗셈 대신 그렇다고 말한다")
    print("통과: 분모가 0일 때 터지지 않는다.")


if __name__ == "__main__":
    test_coverage_comes_before_counts()
    print()
    test_silence_reasons_are_grouped()
    print()
    test_nothing_judged_is_not_reported_as_clean()
    print()
    test_no_images_says_so()
