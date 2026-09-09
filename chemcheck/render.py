"""구조를 그린다. 이름이 가리키는 것과 그림에서 읽은 것을 나란히 놓고 차이를 표시한다.

InChIKey 문자열 비교는 판정에는 충분하지만 사람에게 보여주기에는 난수처럼 보인다.
어디가 어떻게 다른지는 그림으로 보여야 한다.

출력은 SVG다. cairo 같은 추가 의존성이 필요 없고 확대해도 깨지지 않는다.
"""
from __future__ import annotations

from rdkit import Chem, RDLogger
from rdkit.Chem import rdFMCS
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")

SIZE = (320, 250)
DIFF_COLOR = (0.94, 0.27, 0.27)   # 다른 부분
MCS_TIMEOUT = 5


def from_inchi(text: str | None):
    if not text:
        return None
    return Chem.MolFromInchi(text)


def from_smiles(text: str | None):
    if not text or not text.strip():
        return None
    return Chem.MolFromSmiles(text.strip())


def draw(mol, highlight: list[int] | None = None, size: tuple[int, int] = SIZE) -> str:
    """분자 하나를 SVG로 그린다. highlight에 든 원자는 다른 색으로 칠한다."""
    if mol is None:
        return ""
    mol = Chem.Mol(mol)
    rdMolDraw2D.PrepareMolForDrawing(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(*size)
    options = drawer.drawOptions()
    options.clearBackground = False
    highlight = highlight or []
    bonds = [
        b.GetIdx()
        for b in mol.GetBonds()
        if b.GetBeginAtomIdx() in highlight and b.GetEndAtomIdx() in highlight
    ]
    drawer.DrawMolecule(
        mol,
        highlightAtoms=highlight,
        highlightAtomColors={i: DIFF_COLOR for i in highlight},
        highlightBonds=bonds,
        highlightBondColors={i: DIFF_COLOR for i in bonds},
    )
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _differing_atoms(mol, other) -> tuple[list[int], list[int]]:
    """두 분자의 최대 공통 부분구조에 들지 않는 원자를 각각 돌려준다.

    공통부를 뺀 나머지가 곧 '다른 곳'이다. 공통부를 못 찾으면 아무것도 칠하지 않는다 —
    전부 빨간 그림은 어디가 문제인지 알려주지 못한다.
    """
    try:
        result = rdFMCS.FindMCS(
            [mol, other],
            timeout=MCS_TIMEOUT,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
        )
    except Exception:
        return [], []
    if result.canceled or not result.smartsString:
        return [], []
    patt = Chem.MolFromSmarts(result.smartsString)
    if patt is None:
        return [], []
    left = set(mol.GetSubstructMatch(patt))
    right = set(other.GetSubstructMatch(patt))
    if not left or not right:
        return [], []
    return (
        [i for i in range(mol.GetNumAtoms()) if i not in left],
        [i for i in range(other.GetNumAtoms()) if i not in right],
    )


def diff(reference, predicted) -> tuple[str, str]:
    """(이름이 가리키는 구조, 그림에서 읽은 구조)를 차이가 칠해진 SVG 한 쌍으로 준다."""
    if reference is None or predicted is None:
        return draw(reference), draw(predicted)
    left, right = _differing_atoms(reference, predicted)
    return draw(reference, left), draw(predicted, right)
