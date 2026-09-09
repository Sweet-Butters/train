"""judge() 의 새 규칙(docs/DIRECTION.md 결정 6·8) 검증.

이름이 있으면 이름 대조가 1순위다. 인식기 하나가 SMILES 를 못 읽어도 다른
인식기가 낸 멀쩡한 답은 이름과 대조돼야 한다 - 실측 6(Gemini 카페인)의 근본
원인이 정확히 이것이었다(MolScribe 의 5가 질소가 DECIMER 의 멀쩡한 답까지
묻었다). 여기서는 실제 인식기 없이 Prediction 을 손으로 지어 규칙만 잰다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.keys import smiles_to_inchikey  # noqa: E402
from chemcheck.names import Reference  # noqa: E402
from chemcheck.ocsr import Prediction  # noqa: E402
from chemcheck.verdict import Verdict, judge  # noqa: E402

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
SALICYLIC = "OC(=O)c1ccccc1O"  # 골격이 다름 - 아세틸기 하나 차이
IBUPROFEN = "CC(C)Cc1ccc(cc1)C(C)C(=O)O"
INVALID = "N(C)=CN2(C)C(C)C"  # 원자가가 깨진 SMILES - RDKit 이 못 읽는다
L_ALANINE = "N[C@H](C)C(=O)O"
D_ALANINE = "N[C@@H](C)C(=O)O"


def _ref(name: str, smiles: str) -> Reference:
    key = smiles_to_inchikey(smiles)
    assert key is not None, f"테스트 전제가 깨졌다: {smiles} 를 못 읽는다"
    return Reference(name, key, "", "test", smiles)


def test_single_readable_engine_still_catches_mismatch() -> None:
    """한 인식기가 파싱 실패해도, 읽을 수 있는 다른 인식기의 답은 이름과 대조된다.

    실측 6 그대로: MolScribe(무효) + DECIMER(유효, 이름과 다름) -> [약] 오류.
    합의 게이트가 이름 대조보다 앞서 있던 옛 버그라면 여기서 판정불가가 나온다.
    """
    ref = _ref("아스피린", ASPIRIN)
    predictions = [
        Prediction(INVALID, 0.81, "molscribe"),
        Prediction(SALICYLIC, 0.90, "decimer"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.ERROR, finding.reason
    assert "[약]" in finding.reason, f"단일 유효 인식기 등급이 아니다: {finding.reason}"
    print(f"  {finding.reason}")
    print("통과: 인식기 하나가 못 읽어도 다른 인식기의 유효한 답은 이름과 대조된다.")


def test_two_families_agree_against_name_is_strong() -> None:
    """종류가 다른 인식기 둘이 서로 합의한 답이 이름과 다르면 [강] 등급이다."""
    ref = _ref("아스피린", ASPIRIN)
    predictions = [
        Prediction(SALICYLIC, 0.85, "molscribe"),
        Prediction(SALICYLIC, 0.90, "decimer"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.ERROR, finding.reason
    assert "[강]" in finding.reason, f"합의 등급이 아니다: {finding.reason}"
    print(f"  {finding.reason}")
    print("통과: 두 종류가 합의한 오답은 [강] 등급이다.")


def test_disagreeing_engines_neither_matching_name_is_weak() -> None:
    """둘 다 이름과 다르고, 서로도 다르면(합의 없음) [약] 등급이다."""
    ref = _ref("아스피린", ASPIRIN)
    predictions = [
        Prediction(SALICYLIC, 0.85, "molscribe"),
        Prediction(IBUPROFEN, 0.90, "decimer"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.ERROR, finding.reason
    assert "[약]" in finding.reason, f"등급이 잘못됐다: {finding.reason}"
    print(f"  {finding.reason}")
    print("통과: 서로도 다른 오답 둘은 '합의'가 아니라 [약] 등급이다.")


def test_any_matching_engine_is_enough_for_ok() -> None:
    """한 종류가 이름과 맞으면, 다른 종류가 틀려도 OK다 - 이름 대조가 최종 근거다."""
    ref = _ref("아스피린", ASPIRIN)
    predictions = [
        Prediction(ASPIRIN, 0.85, "molscribe"),
        Prediction(SALICYLIC, 0.90, "decimer"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.OK, finding.reason
    print(f"  {finding.reason}")
    print("통과: 하나라도 이름과 맞으면 확정한다.")


def test_self_inconsistent_family_is_excluded_even_if_it_would_match() -> None:
    """같은 종류가 이미지 변형마다 답이 갈리면, 그중 하나가 이름과 맞아도 안 믿는다."""
    ref = _ref("아스피린", ASPIRIN)
    predictions = [
        Prediction(ASPIRIN, 0.80, "decimer/x1.0"),
        Prediction(SALICYLIC, 0.80, "decimer/x0.9"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.ABSTAIN, finding.reason
    print(f"  {finding.reason}")
    print("통과: 스스로 흔들리는 인식은 우연히 맞아도 근거로 쓰지 않는다.")


def test_no_readable_prediction_abstains() -> None:
    ref = _ref("아스피린", ASPIRIN)
    predictions = [Prediction(INVALID, 0.81, "molscribe")]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.ABSTAIN, finding.reason
    assert "해석할 수 없음" in finding.reason
    print(f"  {finding.reason}")
    print("통과: 유효한 구조가 하나도 없으면 판정불가 그대로다.")


def test_stereo_only_difference_says_not_judged() -> None:
    """골격이 같고 입체만 다르면 '주의'가 아니라 '입체는 판정하지 않았습니다'."""
    ref = _ref("L-알라닌", L_ALANINE)
    predictions = [
        Prediction(D_ALANINE, 0.85, "molscribe"),
        Prediction(D_ALANINE, 0.90, "decimer"),
    ]
    finding = judge([ref], predictions)
    assert finding.verdict is Verdict.WARN, finding.reason
    assert "입체는 판정하지 않았습니다" in finding.reason, finding.reason
    print(f"  {finding.reason}")
    print("통과: 입체 차이는 '판정하지 않았다'고 정직하게 말한다.")


if __name__ == "__main__":
    for test in [
        test_single_readable_engine_still_catches_mismatch,
        test_two_families_agree_against_name_is_strong,
        test_disagreeing_engines_neither_matching_name_is_weak,
        test_any_matching_engine_is_enough_for_ok,
        test_self_inconsistent_family_is_excluded_even_if_it_would_match,
        test_no_readable_prediction_abstains,
        test_stereo_only_difference_says_not_judged,
    ]:
        test()
        print()
