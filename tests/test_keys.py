"""InChIKey 비교(트랙 C) 검증.

전부 망 없이 돈다. 여기서 검증하는 것은 하나다 - 그림이 맞는데도 '오류'라고
말하게 만드는 경로가 없는가. 이 도구에서 가장 나쁜 실패이기 때문이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.keys import (  # noqa: E402
    Match,
    compare,
    heavy_atom_formula,
    principal_smiles,
    smiles_to_inchikey,
)

MORPHINE = "CN1CC[C@]23[C@@H]4[C@H]1CC5=C2C(=C(C=C5)O)O[C@H]3[C@H](C=C4)O"
SULFURIC = "OS(=O)(=O)O"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
SALICYLIC = "OC(=O)c1ccccc1O"
L_ALANINE = "C[C@@H](C(=O)O)N"
D_ALANINE = "C[C@H](C(=O)O)N"

# PubChem 이 실제로 돌려준 값. '(S)-ibuprofen' 은 음이온으로, 'ibuprofen' 은 중성으로 나온다.
IBUPROFEN_ANION = "HEFNNWSXXWATRW-JTQLQIEISA-M"
IBUPROFEN_NEUTRAL = "HEFNNWSXXWATRW-JTQLQIEISA-N"


def test_salt_compares_as_its_parent() -> None:
    """'morphine sulfate' 는 morphine 과 골격 키부터 다르다.

    정규화하지 않으면 morphine 을 정확히 그린 그림이 '오류'로 보고된다.
    """
    salt = f"{MORPHINE}.{MORPHINE}.{SULFURIC}"
    assert principal_smiles(salt) != salt, "염을 떼어내지 못했다"
    assert smiles_to_inchikey(salt) == smiles_to_inchikey(MORPHINE)

    pair = compare(smiles_to_inchikey(salt), smiles_to_inchikey(MORPHINE))
    assert pair.match is Match.EXACT, f"염 때문에 {pair.match} 로 갈렸다"
    print(f"  morphine sulfate -> {smiles_to_inchikey(salt)} (morphine 과 동일)")
    print("통과: 염 형태 이름이 유리 염기 그림을 오류로 만들지 않는다.")


def test_small_inorganic_salt_stays_whole() -> None:
    """NaCl 은 그 전체가 곧 화합물이다. 한쪽만 남기면 다른 물질이 된다."""
    nacl = "[Na+].[Cl-]"
    assert principal_smiles(nacl) == nacl, "무기염을 쪼갰다"
    assert smiles_to_inchikey(nacl) != smiles_to_inchikey("[Cl-]")
    print("  [Na+].[Cl-] -> 그대로 둠")
    print("통과: 주성분이라 부를 만큼 뚜렷하지 않으면 손대지 않는다.")


def test_repeated_component_collapses() -> None:
    """같은 분자가 반복돼도 그 분자 하나로 본다."""
    assert smiles_to_inchikey("CCO.CCO") == smiles_to_inchikey("CCO")
    print("  CCO.CCO -> CCO")
    print("통과: 같은 성분의 반복은 그 성분이다.")


def test_protonation_only_is_not_a_structural_error() -> None:
    """PubChem 은 이름에 따라 이온 형태를 돌려준다. 중성 분자를 그린 게 틀린 게 아니다."""
    pair = compare(IBUPROFEN_ANION, IBUPROFEN_NEUTRAL)
    assert pair.match is Match.EXACT, (
        f"프로토네이션 차이를 {pair.match} 로 봤다 - "
        "'입체화학이 다름'이라는 잘못된 사유로 보고된다"
    )
    print(f"  {IBUPROFEN_ANION} vs {IBUPROFEN_NEUTRAL} -> EXACT")
    print("통과: 프로토네이션만 다른 것은 구조 오류가 아니다.")


def test_stereochemistry_difference_is_only_a_warning() -> None:
    """입체화학은 OCSR 이 가장 약한 부분이다. 단정하지 않고 사람에게 넘긴다."""
    left, right = smiles_to_inchikey(L_ALANINE), smiles_to_inchikey(D_ALANINE)
    assert left != right, "테스트 전제가 깨졌다 - 두 이성질체의 키가 같다"
    pair = compare(left, right)
    assert pair.match is Match.SKELETON_ONLY, f"{pair.match} 로 갈렸다"
    print(f"  L-alanine {left[:14]} vs D-alanine (블록2만 다름) -> SKELETON_ONLY")
    print("통과: 입체화학 차이는 오류가 아니라 주의다.")


def test_real_error_is_still_caught() -> None:
    """관대해진 만큼 진짜 오류를 놓치면 도구가 무의미해진다."""
    pair = compare(smiles_to_inchikey(ASPIRIN), smiles_to_inchikey(SALICYLIC))
    assert pair.match is Match.DIFFERENT, f"아세틸기 하나 차이를 {pair.match} 로 봤다"
    print("  아스피린 vs 살리실산 -> DIFFERENT")
    print("통과: 골격이 다르면 여전히 오류다.")


def test_heavy_atom_formula_counts_even_broken_valence() -> None:
    """결정 6·7: 원자가가 깨진 답(실측 6 의 5가 질소 같은 것)도 중원자를 셀 수 있어야 한다.

    N,N,N,N-테트라메틸아민(5가 질소, sanitize=True 로는 파싱조차 안 됨) - 메틸이
    정상(3개, trimethylamine)보다 하나 더 붙은 상황을 흉내낸다.
    """
    normal = "CN(C)C"           # 정상 - 트리메틸아민, C3N
    broken = "CN(C)(C)(C)C"     # 원자가 깨짐 - 메틸 하나 더, C5N

    assert heavy_atom_formula(normal) == "C3N"
    assert heavy_atom_formula(broken) == "C5N"
    print(f"  정상 {heavy_atom_formula(normal)} vs 원자가 깨짐 {heavy_atom_formula(broken)}")
    print("통과: 원자가가 깨져도 중원자는 셀 수 있다.")


def test_heavy_atom_formula_drops_counterions() -> None:
    """짝이온은 뗀다(principal_smiles) - DECIMER 가 지어낸 요오드화물이 조성을 흐리면 안 된다."""
    salt = "CC(=O)Oc1ccccc1C(=O)O.[I-].[I-]"
    assert heavy_atom_formula(salt) == heavy_atom_formula("CC(=O)Oc1ccccc1C(=O)O")
    print(f"  {salt} -> {heavy_atom_formula(salt)} (요오드 제외)")
    print("통과: 짝이온이 조성에 섞이지 않는다.")


def test_heavy_atom_formula_unparseable_is_none() -> None:
    assert heavy_atom_formula("") is None
    assert heavy_atom_formula("(((") is None
    print("통과: 파싱조차 안 되면 조성을 지어내지 않는다.")


def test_unreadable_structure_is_none_not_a_guess() -> None:
    assert smiles_to_inchikey("이건 SMILES 가 아니다") is None
    assert smiles_to_inchikey("") is None
    assert compare(None, "BSYNRYMUTXBXSQ-UHFFFAOYSA-N").match is Match.UNCOMPARABLE
    print("  못 읽는 구조 -> None / UNCOMPARABLE")
    print("통과: 읽지 못한 것을 지어내지 않는다.")


if __name__ == "__main__":
    for test in [
        test_salt_compares_as_its_parent,
        test_small_inorganic_salt_stays_whole,
        test_repeated_component_collapses,
        test_protonation_only_is_not_a_structural_error,
        test_stereochemistry_difference_is_only_a_warning,
        test_real_error_is_still_caught,
        test_heavy_atom_formula_counts_even_broken_valence,
        test_heavy_atom_formula_drops_counterions,
        test_heavy_atom_formula_unparseable_is_none,
        test_unreadable_structure_is_none_not_a_guess,
    ]:
        test()
        print()
