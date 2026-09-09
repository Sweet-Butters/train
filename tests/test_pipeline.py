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
    oracle = OracleEngine({i: smiles for i, (_, smiles, _) in enumerate(CASES, start=1)})
    expected = [Verdict.OK if correct else Verdict.ERROR for _, _, correct in CASES]

    with tempfile.TemporaryDirectory() as tmp:
        deck = build(Path(tmp) / "deck")
        results = run(deck, Path(tmp) / "work", [oracle])

    got = [finding.verdict for r in results for _, finding in r.findings]
    assert got == expected, f"기대 {expected}, 실제 {got}"

    for r, want in zip(results, expected):
        for _, finding in r.findings:
            print(f"  {r.slide.index}장 {r.references[0].name:9} -> "
                  f"{finding.verdict.value.upper():6} {finding.reason}")
    print("\n통과: 심어둔 오류 2건을 모두 잡고 정답 2건은 통과시켰다.")




class FlakyEngine(Engine):
    """이미지를 조금만 바꿔도 답이 흔들리는 인식기. 실제 오인식 상황을 흉내낸다."""

    name = "flaky"

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        self.calls += 1
        # 첫 호출은 아스피린, 이후 변형본에서는 카페인 — 즉 스스로 합의하지 못한다.
        smiles = "CC(=O)Oc1ccccc1C(=O)O" if self.calls % 3 == 1 else "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
        return Prediction(smiles, float("nan"), self.name)


def test_self_consistency_forces_abstain() -> None:
    """인식이 흔들리면 '오류'라고 말하지 않고 보류해야 한다."""
    from chemcheck.ocsr import SelfConsistent

    engine = SelfConsistent(FlakyEngine())

    with tempfile.TemporaryDirectory() as tmp:
        deck = build(Path(tmp) / "deck")
        results = run(deck, Path(tmp) / "work", [engine])

    verdicts = [f.verdict for r in results for _, f in r.findings]
    assert all(v is Verdict.ABSTAIN for v in verdicts), f"보류가 아님: {verdicts}"

    reason = results[0].findings[0][1].reason
    print(f"  흔들리는 인식 -> {verdicts[0].value.upper()}: {reason}")
    print("통과: 인식이 스스로 합의하지 못하면 오류로 단정하지 않는다.")



if __name__ == "__main__":
    test_detects_planted_errors()
    print()
    test_self_consistency_forces_abstain()
