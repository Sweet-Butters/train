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


def test_broken_engine_does_not_crash() -> None:
    """인식기 적재가 깨져도 도구가 죽으면 안 된다. 없는 것으로 보고하고 보류한다.

    DECIMER는 import 시점에 가중치를 내려받는다. 배포처(Zenodo)가 죽어 있거나
    받다 만 zip이 남아 있으면 ImportError가 아닌 예외(DownloadError, BadZipFile)로
    죽는데, 그게 그대로 올라오면 이름 검사까지 같이 멈춘다.
    """
    import builtins

    from chemcheck.ocsr import DecimerEngine

    real_import = builtins.__import__

    def exploding_import(name, *args, **kwargs):
        if name == "DECIMER":
            raise RuntimeError("가중치 압축 파일이 깨졌다 (BadZipFile 흉내)")
        return real_import(name, *args, **kwargs)

    engine = DecimerEngine()
    builtins.__import__ = exploding_import
    try:
        available = engine.available()
    finally:
        builtins.__import__ = real_import

    assert available is False, "깨진 인식기를 쓸 수 있다고 보고했다"
    assert engine.unavailable_reason, "못 쓰는 이유를 남기지 않았다"
    print(f"  깨진 인식기 -> 사용 불가로 보고: {engine.unavailable_reason}")
    print("통과: 인식기가 깨져도 터지지 않고 판정만 보류한다.")


STUB_WORKER = '''
import json, sys

def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

emit({"ready": True})
for line in sys.stdin:
    path = line.strip()
    if not path:
        continue
    if "die" in path:          # 워커가 죽는 상황
        break
    if "noconf" in path:       # 신뢰도를 주지 않는 인식기
        emit({"smiles": "CC(=O)Oc1ccccc1C(=O)O", "confidence": None})
    else:
        emit({"smiles": "c1ccccc1", "confidence": 0.91})
'''


def test_subprocess_bridge_speaks_the_protocol() -> None:
    """옆 환경의 인식기를 프로세스 너머로 쓰는 다리 검증.

    진짜 MolScribe 는 1.13GB 에 장당 30초라 테스트에서 부를 수 없다. 프로토콜만
    스텁 워커로 본다: 적재 악수, 요청-응답, 신뢰도 없는 응답, 그리고 워커가
    죽었을 때 조용히 접히는지.
    """
    import math
    import sys

    from chemcheck.ocsr import SubprocessEngine

    with tempfile.TemporaryDirectory() as tmp:
        worker = Path(tmp) / "stub_worker.py"
        worker.write_text(STUB_WORKER, encoding="utf-8")

        engine = SubprocessEngine(Path(sys.executable), "stub",
                                  load_timeout=30.0, call_timeout=10.0)
        engine.WORKER = worker

        assert engine.available(), f"다리를 못 씀: {engine.unavailable_reason}"

        pred = engine.recognize(Path("slide.png"))
        assert pred is not None and pred.smiles == "c1ccccc1", f"예측 없음: {pred}"
        assert abs(pred.confidence - 0.91) < 1e-9, f"신뢰도 어긋남: {pred.confidence}"

        pred = engine.recognize(Path("noconf.png"))
        assert pred is not None and math.isnan(pred.confidence), \
            "신뢰도를 주지 않는 인식기의 값을 지어냈다"

        assert engine.recognize(Path("die.png")) is None, "죽은 워커가 예측을 냈다"
        assert engine.unavailable_reason, "워커가 죽은 이유를 남기지 않았다"

    print(f"  다리 -> 예측 전달·NaN 보존·죽으면 접힘: {engine.unavailable_reason}")
    print("통과: 인식기를 옆 환경에서 돌려도 프로토콜이 지켜진다.")


def test_self_consistency_only_when_decimer_is_alone() -> None:
    """자체 일관성 검사는 DECIMER 가 혼자일 때만 켠다.

    추론을 3배로 늘리는 장치다. 다른 인식기가 있으면 합의 게이트가 이미
    불일치를 보류로 잡으므로 그 값을 치를 이유가 없다.
    """
    from unittest.mock import patch

    from chemcheck import ocsr

    with patch.object(ocsr.DecimerEngine, "available", lambda self: True):
        with patch.object(ocsr.MolScribeEngine, "available", lambda self: False), \
             patch.object(ocsr.SubprocessEngine, "available", lambda self: False):
            alone = ocsr.load_engines(None)

        with patch.object(ocsr.MolScribeEngine, "available", lambda self: True):
            paired = ocsr.load_engines(None)

    assert len(alone) == 1 and isinstance(alone[0], ocsr.SelfConsistent), \
        f"혼자인데 자체 일관성 검사가 없다: {[e.name for e in alone]}"
    assert len(paired) == 2, f"인식기가 둘이 아니다: {[e.name for e in paired]}"
    assert not any(isinstance(e, ocsr.SelfConsistent) for e in paired), \
        f"둘인데도 3배로 돌린다: {[e.name for e in paired]}"

    print(f"  혼자 -> {[e.name for e in alone]}")
    print(f"  둘   -> {[e.name for e in paired]}")
    print("통과: 3배 비용은 그것 말고 방법이 없을 때만 치른다.")


if __name__ == "__main__":
    test_detects_planted_errors()
    print()
    test_self_consistency_forces_abstain()
    print()
    test_broken_engine_does_not_crash()
    print()
    test_subprocess_bridge_speaks_the_protocol()
    print()
    test_self_consistency_only_when_decimer_is_alone()
