"""구조를 그린다. 사람 눈이 마지막 판정이므로 결과는 늘 그림으로 남긴다.

InChIKey 문자열 비교는 판정에는 충분하지만 사람에게는 난수처럼 보인다.
어디가 어떻게 다른지는 그림으로 보여야 한다. 두 구조를 나란히 그릴 때는
최대 공통 부분구조(MCS)에 들지 않는 원자를 칠해서 차이를 가리킨다.

출력은 PNG 다. 슬라이드에 바로 붙일 수 있고 어떤 뷰어에서든 열린다.
(버려진 웹 브랜치 origin/claude/repo-status-check-4vmkco 의 render.py 를
PNG 로 옮긴 것이다. 브랜치는 머지하지 않는다.)
"""
from __future__ import annotations

from pathlib import Path

from rdkit import Chem, RDLogger
from rdkit.Chem import rdFMCS
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")

SIZE = (500, 400)
DIFF_COLOR = (0.94, 0.27, 0.27)   # 다른 부분
MCS_TIMEOUT = 5


def from_smiles(text: str | None):
    if not text or not text.strip():
        return None
    return Chem.MolFromSmiles(text.strip())


def _drawing(mol, highlight: list[int] | None, size: tuple[int, int], legend: str):
    mol = Chem.Mol(mol)
    rdMolDraw2D.PrepareMolForDrawing(mol)
    drawer = rdMolDraw2D.MolDraw2DCairo(*size)
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
        legend=legend,
    )
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def draw_png(smiles: str, dest: Path, highlight: list[int] | None = None,
             size: tuple[int, int] = SIZE, legend: str = "") -> Path | None:
    """SMILES 하나를 PNG 로 그린다. 파싱이 안 되면 그리지 않고 None 을 준다."""
    mol = from_smiles(smiles)
    if mol is None:
        return None
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_drawing(mol, highlight, size, legend))
    return dest


def differing_atoms(mol, other) -> tuple[list[int], list[int]]:
    """두 분자의 최대 공통 부분구조에 들지 않는 원자를 각각 돌려준다.

    공통부를 뺀 나머지가 곧 '다른 곳'이다. 공통부를 못 찾으면 아무것도 칠하지
    않는다 - 전부 빨간 그림은 어디가 문제인지 알려주지 못한다.
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


def draw_pair(left_smiles: str, right_smiles: str, left_dest: Path, right_dest: Path,
              legends: tuple[str, str] = ("", "")) -> tuple[Path | None, Path | None]:
    """두 구조를 차이가 칠해진 PNG 한 쌍으로 그린다. 한쪽이 안 읽히면 그쪽만 None."""
    left, right = from_smiles(left_smiles), from_smiles(right_smiles)
    if left is None or right is None:
        return (draw_png(left_smiles, left_dest, legend=legends[0]),
                draw_png(right_smiles, right_dest, legend=legends[1]))
    hl, hr = differing_atoms(left, right)
    return (draw_png(left_smiles, left_dest, hl, legend=legends[0]),
            draw_png(right_smiles, right_dest, hr, legend=legends[1]))
