"""recognize 평가 장치 검증 - 인식기 없이 스텁으로만.

장치가 재는 것은 '자신 있게 틀림' 이다. 그 숫자가 장치 안에서 옳게 나오는지를
스텁으로 확인해 두지 않으면, 하루 끝에 실제 인식기로 뽑은 숫자를 믿을 수 없다.
네 스텁이 네 상황이다: 완벽한 둘 / 흔들리는 하나 / 같이 틀리는 둘 / 없음.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench import recog  # noqa: E402
from bench.molecules import MOLECULES, SET_SIZE  # noqa: E402
from bench.recog import Outcome, Read, TruthEngine  # noqa: E402
from bench.variants import VARIANT_NAMES, Item, build_set  # noqa: E402
from chemcheck.keys import smiles_to_inchikey  # noqa: E402


@pytest.fixture(scope="module")
def items(tmp_path_factory) -> list[Item]:
    return build_set(tmp_path_factory.mktemp("recognize_set"))


def test_set_is_fifty_and_truth_is_computed_not_labeled(items: list[Item]) -> None:
    """정답 InChIKey 는 SMILES 에서 계산한 것이어야 하고, 50개가 전부 다른 분자여야 한다."""
    from PIL import Image

    assert len(MOLECULES) == SET_SIZE == 50, f"세트가 50이 아니다: {len(MOLECULES)}"
    assert len(items) == 50
    keys = {it.inchikey for it in items}
    assert len(keys) == 50, "같은 분자가 두 번 들었다"
    for it in items:
        assert it.inchikey == smiles_to_inchikey(it.molecule.smiles), it.molecule.name
        assert it.path.exists() and it.path.stat().st_size > 1000, it.path
        assert it.path.suffix == it.variant.suffix, it.path
        with Image.open(it.path) as img:
            assert img.size == it.variant.size, (it.path, img.size)
    used = {it.variant.name for it in items}
    assert used == set(VARIANT_NAMES), f"쓰이지 않은 변주가 있다: {set(VARIANT_NAMES) - used}"
    print(f"  분자 50 · 변주 {len(used)}종 · 정답은 전부 SMILES 에서 계산")
    print("통과: 평가 세트가 스스로 옳다.")


def test_perfect_pair_gives_no_confident_wrong(items: list[Item]) -> None:
    cards = recog.evaluate(items, recog.stub_engines("oracle", items))
    product, baseline = cards
    assert product.count(Outcome.CORRECT) == 50, product.count(Outcome.CORRECT)
    assert product.count(Outcome.CONFIDENT_WRONG) == 0
    assert product.n_exact == 50, "입체까지 맞아야 한다 - 정답을 그대로 돌려줬으니"
    assert baseline.count(Outcome.CORRECT) == 50
    assert recog.exit_code(cards) == 0
    print("통과: 완벽한 인식기 둘 -> 정확도 100%, 자신 있게 틀림 0.")


def test_single_shaky_engine_is_declined_by_consensus_but_trusted_by_confidence(
        items: list[Item]) -> None:
    """이 테스트가 이 장치의 존재 이유다.

    흔들리는 인식기 하나는 신뢰도 0.93 으로 엉뚱한 골격을 낸다. 신뢰도 게이트는
    그것을 그대로 내보내 '자신 있게 틀림' 이 되고, 합의 게이트는 인식기가 하나라
    아예 답하지 않는다. 두 팔의 차이가 곧 합의 게이트의 값어치다.
    """
    cards = recog.evaluate(items, recog.stub_engines("shaky", items))
    product, baseline = cards

    assert product.count(Outcome.DECLINED) == 50, "인식기 하나로는 답을 내면 안 된다"
    assert product.count(Outcome.CONFIDENT_WRONG) == 0
    # 3의 배수 장 16개 중 12번(피리딘)은 엉뚱한 답이 우연히 정답이다.
    assert baseline.count(Outcome.CONFIDENT_WRONG) == 15, baseline.count(Outcome.CONFIDENT_WRONG)
    assert baseline.count(Outcome.CORRECT) == 35
    # 물러난 50건 중 35건은 정답이 있었는데 뺀 것이다 - 조심의 값이 그대로 보여야 한다.
    assert product.n_declined_with_truth == 35, product.n_declined_with_truth
    assert recog.exit_code(cards) == 0, "합의 게이트는 자신 있게 틀리지 않았다"

    stats = recog.engine_stats(product.rows)
    assert len(stats) == 1 and stats[0].confident_wrong == 15, stats
    print("  합의 게이트: 물러남 50 · 자신 있게 틀림 0")
    print("  신뢰도 게이트: 자신 있게 틀림 15/50")
    print("통과: 인식기 하나의 자신감은 답이 아니다.")


def test_colluding_engines_trip_the_gate(items: list[Item]) -> None:
    """둘이 같은 방향으로 틀리면 합의로는 못 잡는다. 장치는 그것을 실패로 끝내야 한다."""
    cards = recog.evaluate(items, recog.stub_engines("colluding", items))
    product, _ = cards
    assert product.count(Outcome.CONFIDENT_WRONG) == 15, product.count(Outcome.CONFIDENT_WRONG)
    assert recog.exit_code(cards) == 1, "자신 있게 틀렸는데 통과로 끝냈다"
    print("통과: 합의가 틀리면 리포트가 실패로 끝난다.")


def test_no_engine_declines_everything(items: list[Item]) -> None:
    cards = recog.evaluate(items, recog.stub_engines("none", items))
    for card in cards:
        assert card.count(Outcome.DECLINED) == 50, card.arm
        assert card.rate(Outcome.CONFIDENT_WRONG) == 0.0
    assert recog.exit_code(cards) == 0
    print("통과: 인식기가 없으면 전부 물러난다 - 자신 있게 틀림 0 이 공짜로 보인다.")


def test_report_leads_with_confident_wrong(items: list[Item]) -> None:
    """첫 지표 줄은 '자신 있게 틀림' 이다. 정확도가 먼저 오면 리포트가 스스로를 속인다."""
    cards = recog.evaluate(items, recog.stub_engines("shaky", items))
    lines = recog.scorecard(cards[0]).splitlines()
    assert lines[0].startswith("["), lines[0]
    assert lines[1].lstrip().startswith("자신 있게 틀림"), lines[1]
    assert "정확도" in lines[2] and "물러남" in lines[3]

    text = recog.report(cards, "shaky", ["shaky"])
    assert text.startswith("!!"), "스텁 결과에는 단서가 맨 위에 붙어야 한다"
    assert text.rstrip().endswith("real 로만 나온다."), "단서가 아래에도 붙어야 한다"
    assert "엔진별" in text and "변주별" in text
    print("통과: 리포트 첫 줄이 '자신 있게 틀림' 이고 단서가 위아래에 붙는다.")


def _read(engine: str, smiles: str, conf: float = float("nan")) -> Read:
    return Read(engine, smiles, smiles_to_inchikey(smiles), conf)


def test_consensus_needs_two_engine_families() -> None:
    """SelfConsistent 가 낸 'decimer/x0.9' 셋은 인식기 하나다. 자기와의 합의는 합의가 아니다."""
    assert recog.family("decimer/x0.9") == "decimer"
    assert recog.family("molscribe@.venv310") == "molscribe"

    same = [_read("decimer/x1", "c1ccccc1"), _read("decimer/x0.9", "c1ccccc1")]
    assert not recog.consensus_gate(same).committed, "인식기 하나가 자기와 합의했다"

    two = [_read("decimer/x1", "c1ccccc1"), _read("molscribe@.venv310", "c1ccccc1", 0.9)]
    assert recog.consensus_gate(two).committed

    split = [_read("decimer", "c1ccccc1"), _read("molscribe", "Cc1ccccc1", 0.95)]
    assert not recog.consensus_gate(split).committed, "골격이 갈렸는데 답을 냈다"
    print("통과: 합의는 인식기 종류 둘 이상 · 골격 하나일 때만이다.")


def test_confidence_gate_mirrors_verdict() -> None:
    """기준선은 verdict.py 의 단독 경로다: 0.8 미만은 보류, 신뢰도 없음은 그대로 믿음."""
    assert not recog.confidence_gate([_read("m", "c1ccccc1", 0.5)]).committed
    assert recog.confidence_gate([_read("m", "c1ccccc1", 0.85)]).committed
    assert recog.confidence_gate([_read("d", "c1ccccc1")]).committed, "NaN 은 게이트를 지난다"
    assert not recog.confidence_gate([_read("m", "이건 SMILES 가 아니다", 0.99)]).committed
    assert not recog.confidence_gate([]).committed
    print("통과: 기준선이 verdict.py 의 신뢰도 규칙과 같다.")


def test_real_engine_path_uses_load_engines(items: list[Item], tmp_path: Path) -> None:
    """--engine real 은 chemcheck.ocsr.load_engines() 를 그대로 쓴다. 다른 경로를 만들지 않는다."""
    from bench import run as bench_run

    truth = {it.index: it.molecule.smiles for it in items}
    calls: list[Path | None] = []

    def fake_load(checkpoint):
        calls.append(checkpoint)
        return [TruthEngine(truth, "molscribe"), TruthEngine(truth, "decimer")]

    with patch.object(bench_run, "load_engines", fake_load):
        code = bench_run.main(["--task", "recognize", "--engine", "real",
                               "--set-dir", str(tmp_path / "set")])
    assert code == 0
    assert len(calls) == 1, "load_engines 를 정확히 한 번 불러야 한다"
    print("통과: real 경로는 load_engines() 하나다.")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        built = build_set(Path(tmp) / "set")
        for fn in (test_set_is_fifty_and_truth_is_computed_not_labeled,
                   test_perfect_pair_gives_no_confident_wrong,
                   test_single_shaky_engine_is_declined_by_consensus_but_trusted_by_confidence,
                   test_colluding_engines_trip_the_gate,
                   test_no_engine_declines_everything,
                   test_report_leads_with_confident_wrong):
            fn(built)
            print()
        test_consensus_needs_two_engine_families()
        print()
        test_confidence_gate_mirrors_verdict()
