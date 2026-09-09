"""채점기 검증.

측정 장치의 채점기가 검증되지 않은 채로 있으면 그 장치가 내는 숫자도 믿을 수 없다.
classify() 는 라벨 x 판정 짜리 진리표라 전수 검증이 가능하다. 칸을 손으로
전부 적어두면 라벨이 늘 때 이 테스트가 먼저 깨진다 - 새 라벨이 채점기에서 조용히
아무 칸에나 떨어지는 일을 막는다.

여기서 가장 중요한 것은 오탐률의 분모다. 오탐은 '골격이 같은데 다르다고 단정한 것'
이므로 SAME 뿐 아니라 STEREO_DIFF 에서도 나온다. 분모를 SAME 으로만 잡으면
분자가 분모를 넘을 수 있는 지표가 된다 - 실제로 한 번 그렇게 새어 있었다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.cases import JUDGEABLE, NOT_JUDGEABLE, Case, Truth  # noqa: E402
from bench.score import Outcome, Scorecard, classify  # noqa: E402
from chemcheck.verdict import Finding, Verdict  # noqa: E402

# (라벨, 판정) -> 결과. 12 칸 전부를 손으로 적는다.
TRUTH_TABLE = {
    (Truth.SAME, Verdict.OK): Outcome.CLEARED,
    (Truth.SAME, Verdict.ERROR): Outcome.FALSE_ALARM,      # 가장 나쁜 칸
    (Truth.SAME, Verdict.WARN): Outcome.MISGRADED,
    (Truth.SAME, Verdict.ABSTAIN): Outcome.SILENT,

    (Truth.SKELETON_DIFF, Verdict.OK): Outcome.MISSED,
    (Truth.SKELETON_DIFF, Verdict.ERROR): Outcome.CAUGHT,
    (Truth.SKELETON_DIFF, Verdict.WARN): Outcome.MISGRADED,
    (Truth.SKELETON_DIFF, Verdict.ABSTAIN): Outcome.SILENT,

    (Truth.STEREO_DIFF, Verdict.OK): Outcome.MISSED,
    (Truth.STEREO_DIFF, Verdict.ERROR): Outcome.FALSE_ALARM,  # 골격이 같은데 다르다고 함
    (Truth.STEREO_DIFF, Verdict.WARN): Outcome.CAUGHT,
    (Truth.STEREO_DIFF, Verdict.ABSTAIN): Outcome.SILENT,

    # 구조식이 아닌 그림. 물러나는 것이 정답이고, 오류라 단정하면 멀쩡한 자료를
    # 틀렸다고 말하는 것이다.
    (Truth.NOT_A_STRUCTURE, Verdict.ABSTAIN): Outcome.DECLINED,
    (Truth.NOT_A_STRUCTURE, Verdict.ERROR): Outcome.FALSE_ALARM,
    (Truth.NOT_A_STRUCTURE, Verdict.OK): Outcome.MISGRADED,
    (Truth.NOT_A_STRUCTURE, Verdict.WARN): Outcome.MISGRADED,

    # 구조식은 맞지만 슬라이드가 화합물을 지목하지 않았다. 검사할 주장이 없다.
    (Truth.NO_CLAIM, Verdict.ABSTAIN): Outcome.DECLINED,
    (Truth.NO_CLAIM, Verdict.ERROR): Outcome.FALSE_ALARM,
    (Truth.NO_CLAIM, Verdict.OK): Outcome.MISGRADED,
    (Truth.NO_CLAIM, Verdict.WARN): Outcome.MISGRADED,

    # R 기 일반식. InChIKey 가 없으므로 무엇과도 대조되지 않는다.
    (Truth.GENERIC_FORMULA, Verdict.ABSTAIN): Outcome.DECLINED,
    (Truth.GENERIC_FORMULA, Verdict.ERROR): Outcome.FALSE_ALARM,
    (Truth.GENERIC_FORMULA, Verdict.OK): Outcome.MISGRADED,
    (Truth.GENERIC_FORMULA, Verdict.WARN): Outcome.MISGRADED,

    # 반응 도식. 단일 화합물이 아니라 어느 분자를 판정했는지도 말할 수 없다.
    (Truth.REACTION_SCHEME, Verdict.ABSTAIN): Outcome.DECLINED,
    (Truth.REACTION_SCHEME, Verdict.ERROR): Outcome.FALSE_ALARM,
    (Truth.REACTION_SCHEME, Verdict.OK): Outcome.MISGRADED,
    (Truth.REACTION_SCHEME, Verdict.WARN): Outcome.MISGRADED,
}


def _card(pairs: list[tuple[Truth, Verdict]], arm: str = "test") -> Scorecard:
    card = Scorecard(arm)
    for i, (truth, verdict) in enumerate(pairs):
        card.add(Case(f"c{i}", "C", truth), Finding(verdict, "사유"))
    return card


def test_truth_table_is_complete() -> None:
    """12 칸 전부. 빠진 칸이 없어야 하고 값이 어긋나서도 안 된다."""
    cells = [(t, v) for t in Truth for v in Verdict]
    assert len(cells) == 28, f"칸 수가 28 이 아니다: {len(cells)}"
    assert set(cells) == set(TRUTH_TABLE), "진리표에 빠진 칸이 있다"

    for (truth, verdict), expected in TRUTH_TABLE.items():
        got = classify(truth, verdict)
        assert got is expected, f"{truth.value} + {verdict.value} -> {got.value} (기대 {expected.value})"
    print(f"  {len(cells)} 칸 전수 확인")
    print("통과: 채점 진리표가 온전하다.")


def test_abstain_is_silent_except_when_it_is_the_right_answer() -> None:
    """판정할 대상에서 침묵하면 못 본 것이고, 구조식이 아닌 것에서는 잘한 것이다."""
    for truth in JUDGEABLE:
        assert classify(truth, Verdict.ABSTAIN) is Outcome.SILENT, truth
    for truth in NOT_JUDGEABLE:
        assert classify(truth, Verdict.ABSTAIN) is Outcome.DECLINED, truth
    print("통과: 옳게 물러난 것을 못 본 것으로 세지 않는다.")


def test_declining_is_not_counted_against_coverage() -> None:
    """구조식이 아닌 그림에 물러난 것이 판정률을 깎으면, 잘한 일에 벌점을 매기는 것이다."""
    card = _card([(Truth.SAME, Verdict.OK),
                  (Truth.NOT_A_STRUCTURE, Verdict.ABSTAIN),
                  (Truth.NO_CLAIM, Verdict.ABSTAIN)])
    assert card.n_judgeable == 1, card.n_judgeable
    assert card.coverage == 1.0, f"물러남이 판정률을 깎았다: {card.coverage}"
    assert card.decline_rate == 1.0, card.decline_rate
    print("  판정 대상 1건 + 구조식 아님 2건 -> 판정률 100%, 물러남 100%")
    print("통과: 분모가 '판정했어야 할 것'이다.")


def test_error_on_non_structure_is_a_false_alarm() -> None:
    """장식 그림에서 읽은 것을 근거로 멀쩡한 슬라이드를 '오류'라 하면 오탐이다."""
    card = _card([(Truth.NOT_A_STRUCTURE, Verdict.ERROR)] * 2)
    assert card.count(Outcome.FALSE_ALARM) == 2
    assert card.false_alarm_rate == 1.0, card.false_alarm_rate
    print("통과: 구조식이 아닌 그림에서 난 오류도 오탐으로 센다.")


def test_false_alarm_denominator_covers_stereo() -> None:
    """오탐률 분모는 '골격이 같은 케이스'다. SAME 만 세면 분자가 분모를 넘는다.

    이 테스트가 잡는 회귀: 분모를 n_same 으로 되돌리면 아래에서 분모가 0 이 되어
    비율이 None 으로 떨어진다.
    """
    card = _card([(Truth.STEREO_DIFF, Verdict.ERROR)] * 3)

    assert card.n_same == 0, "이 덱에는 SAME 케이스가 없다"
    assert card.n_skeleton_same == 3, f"골격이 같은 케이스는 3건이어야 한다: {card.n_skeleton_same}"
    assert card.count(Outcome.FALSE_ALARM) == 3
    assert card.false_alarm_rate == 1.0, \
        f"오탐 3/3 인데 {card.false_alarm_rate} 다 (분모를 SAME 으로 잡았나)"
    print("  STEREO_DIFF 3건을 전부 ERROR -> 오탐률 100.0%")
    print("통과: 오탐률의 분모가 골격 기준이다.")


def test_rates_stay_within_bounds() -> None:
    """어떤 조합에서도 비율이 1 을 넘지 않는다."""
    card = _card([(t, v) for t in Truth for v in Verdict])
    for name, value in (("판정률", card.coverage),
                        ("오탐률", card.false_alarm_rate),
                        ("검출률", card.detection_rate)):
        assert value is not None and 0.0 <= value <= 1.0, f"{name} 가 범위 밖: {value}"
    print(f"  판정률 {card.coverage:.3f} · 오탐률 {card.false_alarm_rate:.3f} "
          f"· 검출률 {card.detection_rate:.3f}")
    print("통과: 비율이 범위를 벗어나지 않는다.")


def test_silence_is_visible_not_free() -> None:
    """전부 침묵하면 판정률 0, 오탐률 0. 오탐률만 보면 완벽해 보이는 그 상태다."""
    card = _card([(t, Verdict.ABSTAIN) for t in Truth])
    assert card.coverage == 0.0, card.coverage
    assert card.false_alarm_rate == 0.0, card.false_alarm_rate
    assert card.count(Outcome.SILENT) == 3
    print("  전부 침묵 -> 판정률 0.0%, 오탐률 0.0%")
    print("통과: 공짜 만점이 판정률에서 드러난다.")


def test_empty_card_has_no_rates() -> None:
    """잰 것이 없으면 비율을 지어내지 않는다."""
    card = _card([])
    assert card.coverage is None and card.false_alarm_rate is None
    assert card.detection_rate is None
    print("통과: 분모가 없으면 None 이다. 0% 라고 하지 않는다.")


def test_silence_reasons_are_counted() -> None:
    card = Scorecard("test")
    for reason in ("인식기 없음", "인식기 없음", "이름 없음"):
        card.add(Case("c", "C", Truth.SAME), Finding(Verdict.ABSTAIN, reason))
    assert card.silence_reasons["인식기 없음"] == 2, card.silence_reasons
    print(f"  {dict(card.silence_reasons)}")
    print("통과: 침묵 사유가 집계된다.")


if __name__ == "__main__":
    for fn in (test_truth_table_is_complete,
               test_abstain_is_silent_except_when_it_is_the_right_answer,
               test_declining_is_not_counted_against_coverage,
               test_error_on_non_structure_is_a_false_alarm,
               test_false_alarm_denominator_covers_stereo, test_rates_stay_within_bounds,
               test_silence_is_visible_not_free, test_empty_card_has_no_rates,
               test_silence_reasons_are_counted):
        fn()
        print()
