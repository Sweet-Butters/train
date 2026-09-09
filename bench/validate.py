"""라벨 검증 - 코퍼스가 스스로 옳은지 먼저 확인한다.

(이름, 그려진 SMILES) 한 쌍의 관계는 OCSR 없이도 계산된다:
이름은 PubChem 이, 그림은 우리가 그렸으니 SMILES 를 이미 알고 있다. 둘을
InChIKey 로 바꿔 비교하면 참 라벨이 나온다. 선언한 라벨과 다르면 케이스가 틀렸다.

이것을 먼저 돌리는 이유: 틀린 라벨로 측정한 오탐률은 오탐률이 아니다.
덤으로 C 트랙(이름 해석)의 상태도 여기서 드러난다 - PubChem 이 이름을 못 찾으면
그 케이스는 어떤 인식기를 붙여도 영원히 판정불가다.
"""
from __future__ import annotations

from dataclasses import dataclass

from chemcheck.keys import Match, compare, smiles_to_inchikey
from chemcheck.names import PubChemResolver

from .cases import Case, Truth

# InChIKey 비교 결과 -> 참 라벨
FROM_MATCH = {
    Match.EXACT: Truth.SAME,
    Match.SKELETON_ONLY: Truth.STEREO_DIFF,
    Match.DIFFERENT: Truth.SKELETON_DIFF,
}


@dataclass
class LabelCheck:
    case: Case
    computed: Truth | None   # 계산된 참 라벨. 계산 불가면 None
    problem: str | None      # 사람이 손봐야 할 것

    @property
    def ok(self) -> bool:
        return self.problem is None


def check(case: Case, resolver: PubChemResolver) -> LabelCheck:
    if case.truth is Truth.NOT_A_STRUCTURE:
        # 그림이 구조식인지 아닌지는 계산으로 판별할 수 없다. 사람이 눈으로 붙인
        # 라벨이고, 그래서 근거(note)를 반드시 남기게 한다 - 분모를 우리가 정하는
        # 라벨이므로 조작 여지가 있다.
        if not case.note:
            return LabelCheck(case, None, "구조식이 아니라고 선언했으면 근거를 남길 것")
        return LabelCheck(case, case.truth, None)

    ref = resolver.resolve(case.name)
    if ref is None:
        return LabelCheck(case, None, f"PubChem 이 '{case.name}' 을 해석하지 못함")

    key = smiles_to_inchikey(case.smiles)
    if key is None:
        return LabelCheck(case, None, f"그릴 SMILES 를 RDKit 이 읽지 못함: {case.smiles}")

    computed = FROM_MATCH.get(compare(ref.inchikey, key).match)
    if computed is None:
        return LabelCheck(case, None, "InChIKey 를 비교할 수 없음")
    if computed is not case.truth:
        return LabelCheck(
            case, computed,
            f"라벨 어긋남: 선언 {case.truth.value} != 계산 {computed.value}",
        )
    return LabelCheck(case, computed, None)


def check_all(cases: list[Case], resolver: PubChemResolver | None = None) -> list[LabelCheck]:
    resolver = resolver or PubChemResolver()
    return [check(c, resolver) for c in cases]
