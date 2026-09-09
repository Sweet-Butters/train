"""InChIKey 기반 구조 동일성 판정.

이 모듈에는 추론이 없다. 문자열 비교뿐이다.
InChIKey 구조:  AAAAAAAAAAAAAA-BBBBBBBBCC-D
  블록1(14자) 골격/연결성   블록2(8자) 입체화학   나머지 프로토네이션
골격이 다르면 확실한 오류, 입체화학만 다르면 OCSR 신뢰도가 낮으므로 경고로만 다룬다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rdkit import Chem, RDLogger
from rdkit.Chem import inchi

RDLogger.DisableLog("rdApp.*")


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


def smiles_to_inchikey(smiles: str) -> str | None:
    """SMILES -> InChIKey. 파싱 실패하면 None (예외를 삼키지 않고 None으로 명시)."""
    if not smiles or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None:
        return None
    try:
        key = inchi.MolToInchiKey(mol)
    except Exception:
        return None
    return key or None


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


def compare(left: str | None, right: str | None) -> KeyPair:
    """두 InChIKey를 비교한다. 판단 근거는 문자열 동일성뿐이다."""
    if not left or not right:
        return KeyPair(left, right, Match.UNCOMPARABLE)
    if left == right:
        return KeyPair(left, right, Match.EXACT)
    if skeleton(left) == skeleton(right):
        return KeyPair(left, right, Match.SKELETON_ONLY)
    return KeyPair(left, right, Match.DIFFERENT)
