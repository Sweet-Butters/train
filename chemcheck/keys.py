"""InChIKey 기반 구조 동일성 판정.

이 모듈에는 추론이 없다. 문자열 비교뿐이다.
InChIKey 구조:  AAAAAAAAAAAAAA-BBBBBBBBCC-D
  블록1(14자) 골격/연결성   블록2(10자) 입체화학·동위원소   블록3(1자) 프로토네이션
골격이 다르면 확실한 오류, 입체화학만 다르면 OCSR 신뢰도가 낮으므로 경고로만 다룬다.

프로토네이션(블록3)만 다른 것은 구조 오류가 아니다. PubChem 은 이름에 따라
이온 형태를 돌려주기도 한다('(S)-ibuprofen' -> C13H17O2-). 그림이 중성 분자를
그렸다고 해서 틀린 것이 아니므로 일치로 본다.

염·수화물은 주성분으로 정규화한다. 'morphine sulfate' 는 morphine 과 골격
키가 통째로 다르기 때문에, 정규화하지 않으면 morphine 을 정확히 그린 그림이
'오류'로 보고된다. 틀렸다고 잘못 말하는 것이 이 도구의 가장 나쁜 실패다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from rdkit import Chem, RDLogger
from rdkit.Chem import inchi

RDLogger.DisableLog("rdApp.*")

# 주성분으로 볼 최소 크기(무거운 원자 수)와, 나머지 성분보다 몇 배 커야 하는지.
# NaCl 처럼 작은 조각들만 있으면 그 전체가 곧 화합물이므로 쪼개지 않는다.
MIN_PRINCIPAL_ATOMS = 6
PRINCIPAL_RATIO = 2


class Match(Enum):
    EXACT = "exact"            # 완전 일치
    SKELETON_ONLY = "skeleton"  # 골격은 같고 입체화학이 다름
    DIFFERENT = "different"     # 골격이 다름 = 확실한 오류
    UNCOMPARABLE = "uncomparable"  # 한쪽이라도 키를 못 만들면 비교 불가


@dataclass(frozen=True)
class KeyPair:
    left: str | None
    right: str | None
    match: Match


def principal_smiles(smiles: str) -> str:
    """염·용매를 떼고 주성분만 남긴다. 판단이 서지 않으면 원본을 그대로 돌려준다.

    'morphine sulfate' 는 morphine 두 분자와 황산 한 분자다. 같은 성분이
    반복될 수 있으므로 먼저 중복을 없앤 뒤 크기를 견준다.
    """
    if "." not in smiles:
        return smiles

    unique: dict[str, int] = {}
    for part in smiles.split("."):
        mol = Chem.MolFromSmiles(part)
        if mol is None:
            return smiles  # 한 조각이라도 못 읽으면 손대지 않는다
        unique.setdefault(Chem.MolToSmiles(mol), mol.GetNumHeavyAtoms())

    if len(unique) < 2:
        return next(iter(unique))  # 같은 분자의 반복이었다

    ranked = sorted(unique.items(), key=lambda kv: -kv[1])
    (best, best_size), (_, next_size) = ranked[0], ranked[1]
    if best_size < MIN_PRINCIPAL_ATOMS or best_size < PRINCIPAL_RATIO * next_size:
        return smiles  # 주성분이라 부를 만큼 뚜렷하지 않다
    return best


def smiles_to_inchikey(smiles: str) -> str | None:
    """SMILES -> InChIKey. 파싱 실패하면 None (예외를 삼키지 않고 None으로 명시).

    염·용매는 주성분으로 정규화한다. 단일 성분이면 아무 일도 하지 않는다.
    """
    if not smiles or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(principal_smiles(smiles.strip()))
    if mol is None:
        return None
    try:
        key = inchi.MolToInchiKey(mol)
    except Exception:
        return None
    return key or None


def heavy_atom_formula(smiles: str) -> str | None:
    """중원자 조성(C 먼저, 나머지는 알파벳 순). 원자가 검사 없이 파싱한다.

    `sanitize=False` 로 읽는다 - 원자가가 깨진 그림(결정 6·7 의 5가 질소 같은
    것)도 세야 하기 때문이다. 암시적 수소는 원자가 규칙에서 나오는데 그게 깨진
    상황이니 중원자만 센다. 염·짝이온은 `principal_smiles` 로 먼저 뗀다.

    파싱조차 안 되면 None - 조성을 지어내지 않는다.

    일방향 신호다: 다르면 구조가 다른 것이 확실하다. 같다고 구조가 같은 것은
    아니다. 판정(`verdict.judge`)에는 쓰지 않는다 - 표시에만 곁들인다.
    """
    if not smiles or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(principal_smiles(smiles.strip()), sanitize=False)
    if mol is None:
        return None
    counts = Counter(atom.GetSymbol() for atom in mol.GetAtoms())
    if not counts:
        return None

    def part(symbol: str) -> str:
        n = counts[symbol]
        return symbol if n == 1 else f"{symbol}{n}"

    parts = [part("C")] if "C" in counts else []
    parts += [part(sym) for sym in sorted(counts) if sym != "C"]
    return "".join(parts)


def molblock_to_inchikey(molblock: str) -> str | None:
    """MOL/SDF 블록 -> InChIKey."""
    if not molblock or not molblock.strip():
        return None
    mol = Chem.MolFromMolBlock(molblock)
    if mol is None:
        return None
    try:
        return inchi.MolToInchiKey(mol) or None
    except Exception:
        return None


def skeleton(key: str | None) -> str | None:
    """InChIKey의 골격 블록(첫 14자)."""
    if not key or len(key) < 14:
        return None
    return key[:14]


def _blocks(key: str) -> list[str]:
    return key.split("-")


def compare(left: str | None, right: str | None) -> KeyPair:
    """두 InChIKey를 비교한다. 판단 근거는 문자열 동일성뿐이다."""
    if not left or not right:
        return KeyPair(left, right, Match.UNCOMPARABLE)
    if left == right:
        return KeyPair(left, right, Match.EXACT)

    lhs, rhs = _blocks(left), _blocks(right)
    # 골격과 입체화학이 같고 프로토네이션만 다르면 구조 오류가 아니다.
    # 이름 쪽이 이온 형태로 해석되는 경우가 있어서 여기서 걸러야 한다.
    if len(lhs) >= 2 and len(rhs) >= 2 and lhs[0] == rhs[0] and lhs[1] == rhs[1]:
        return KeyPair(left, right, Match.EXACT)

    if skeleton(left) == skeleton(right):
        return KeyPair(left, right, Match.SKELETON_ONLY)
    return KeyPair(left, right, Match.DIFFERENT)
