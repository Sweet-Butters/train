"""판정 파이프라인 검증.

실제 OCSR 없이도 판정 로직을 검증하기 위해 '무엇이 그려졌는지 아는' 스텁
인식기를 쓴다. 이 스텁은 완벽한 인식기를 흉내낸 것이며 제품 경로가 아니다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.ocsr import Engine, Prediction  # noqa: E402
from chemcheck.pipeline import run  # noqa: E402
from chemcheck.verdict import Verdict  # noqa: E402
from scripts.make_demo import CASES, build  # noqa: E402


class OracleEngine(Engine):
    """그려진 SMILES를 그대로 돌려주는 스텁 = 정확도 100%인 가상 인식기."""

    name = "oracle"

    def __init__(self, by_slide: dict[int, str]):
        self.by_slide = by_slide

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        slide_no = int(image_path.stem.split("_")[0].lstrip("s"))
        smiles = self.by_slide.get(slide_no)
        return Prediction(smiles, 0.99, self.name) if smiles else None


def test_detects_planted_errors() -> None:
    deck = build()
    oracle = OracleEngine({i: smiles for i, (_, smiles, _) in enumerate(CASES, start=1)})
    expected = [Verdict.OK if correct else Verdict.ERROR for _, _, correct in CASES]

    with tempfile.TemporaryDirectory() as tmp:
        results = run(deck, Path(tmp), [oracle])

    got = [finding.verdict for r in results for _, finding in r.findings]
    assert got == expected, f"기대 {expected}, 실제 {got}"

    for r, want in zip(results, expected):
        for _, finding in r.findings:
            print(f"  {r.slide.index}장 {r.references[0].name:9} -> "
                  f"{finding.verdict.value.upper():6} {finding.reason}")
    print("\n통과: 심어둔 오류 2건을 모두 잡고 정답 2건은 통과시켰다.")


if __name__ == "__main__":
    test_detects_planted_errors()
