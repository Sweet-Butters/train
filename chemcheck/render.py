"""구조를 그린다. 사람 눈이 마지막 판정이므로 결과는 늘 그림으로 남긴다.

InChIKey 문자열 비교는 판정에는 충분하지만 사람에게는 난수처럼 보인다.
어디가 어떻게 다른지는 그림으로 보여야 한다.

두 후보를 그냥 나란히 놓는 것은 아무것도 주지 않는다 - 아세틸기 하나 차이는
눈으로 구분되지 않는다. 그래서 최대 공통 부분구조(MCS)를 잡고 **나머지**를
칠한다. 한쪽에만 있는 원자·결합은 빨강, 그 조각이 붙는 자리는 반대쪽에 주황.
질문이 '둘 중 뭐가 맞나'(불가능)에서 '원본 그림의 이 자리에 이 조각이
있었나'(가능)로 바뀐다.

출력은 PNG 다. 슬라이드에 바로 붙일 수 있고 어떤 뷰어에서든 열린다.
(버려진 웹 브랜치 origin/claude/repo-status-check-4vmkco 의 '다른 곳을 칠해
보여준다' 아이디어를 PNG 로 옮기고 붙는 자리와 조각 설명을 더했다. 브랜치는
머지하지 않는다.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rdkit import Chem, RDLogger
from rdkit.Chem import rdFMCS
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")

SIZE = (500, 400)
DIFF_COLOR = (0.94, 0.27, 0.27)     # 한쪽에만 있는 원자·결합
ATTACH_COLOR = (1.0, 0.65, 0.15)    # 그 조각이 붙는 자리 (반대쪽)
MCS_TIMEOUT = 5

# 자주 나오는 조각의 이름. 없는 조각은 SMILES 그대로 보여준다 - 지어내지 않는다.
FRAGMENT_NAMES = {
    "C": "메틸기(-CH3)",
    "CC=O": "아세틸기(-C(=O)CH3)",
    "C=O": "카보닐(C=O)",
    "O": "산소 하나(-OH 또는 =O)",
    "N": "질소 하나(-NH2 등)",
    "OC=O": "카복실기(-COOH)",
    "O=CO": "카복실기(-COOH)",
    "CO": "메톡시/히드록시메틸(-OCH3 / -CH2OH)",
    "CC": "에틸기(-CH2CH3)",
    "Cl": "염소(-Cl)", "Br": "브롬(-Br)", "F": "플루오린(-F)", "I": "아이오딘(-I)",
    "S": "황 하나(-SH 등)",
}


def from_smiles(text: str | None):
    if not text or not text.strip():
        return None
    return Chem.MolFromSmiles(text.strip())


def _highlight_bonds(mol, atoms: set[int]) -> list[int]:
    """칠할 원자에 닿는 결합 전부. 붙는 결합도 칠해야 '어디에' 붙었는지 보인다."""
    return [b.GetIdx() for b in mol.GetBonds()
            if b.GetBeginAtomIdx() in atoms or b.GetEndAtomIdx() in atoms]


def _drawing(mol, highlight: list[int] | None, size: tuple[int, int], legend: str):
    mol = Chem.Mol(mol)
    rdMolDraw2D.PrepareMolForDrawing(mol)
    drawer = rdMolDraw2D.MolDraw2DCairo(*size)
    highlight = highlight or []
    bonds = _highlight_bonds(mol, set(highlight))
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


# ── 차이 ───────────────────────────────────────────────────────────────────

@dataclass
class Side:
    """한쪽 구조에서 칠할 것."""

    extra: list[int] = field(default_factory=list)     # 이쪽에만 있는 원자
    attach: list[int] = field(default_factory=list)    # 반대쪽 조각이 붙는 자리
    fragments: list[str] = field(default_factory=list)  # 이쪽에만 있는 조각(SMILES)

    @property
    def fragment_names(self) -> list[str]:
        return [FRAGMENT_NAMES.get(f, f) for f in self.fragments]


@dataclass
class Diff:
    left: Side
    right: Side
    common_atoms: int      # 공통 부분구조의 원자 수. 0 이면 공통부를 못 찾은 것

    @property
    def found(self) -> bool:
        return self.common_atoms > 0


def _fragments(mol, atoms: list[int]) -> list[str]:
    """이쪽에만 있는 원자들을 연결된 덩어리별로 SMILES 로. 원자가 없으면 빈 목록."""
    if not atoms:
        return []
    keep = set(atoms)
    seen: set[int] = set()
    out: list[str] = []
    for start in atoms:
        if start in seen:
            continue
        stack, comp = [start], []
        while stack:
            a = stack.pop()
            if a in seen or a not in keep:
                continue
            seen.add(a)
            comp.append(a)
            stack.extend(n.GetIdx() for n in mol.GetAtomWithIdx(a).GetNeighbors())
        try:
            out.append(Chem.MolFragmentToSmiles(mol, atomsToUse=sorted(comp), canonical=True))
        except Exception:
            out.append("?")
    return out


def diff(left_smiles: str, right_smiles: str) -> Diff | None:
    """두 구조의 최대 공통 부분구조를 잡고 나머지와 붙는 자리를 찾는다.

    공통부를 못 찾으면 아무것도 칠하지 않는다 - 전부 빨간 그림은 어디가 문제인지
    알려주지 못한다. 한쪽이라도 파싱이 안 되면 None.
    """
    left, right = from_smiles(left_smiles), from_smiles(right_smiles)
    if left is None or right is None:
        return None
    try:
        result = rdFMCS.FindMCS(
            [left, right],
            timeout=MCS_TIMEOUT,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
        )
    except Exception:
        return Diff(Side(), Side(), 0)
    if result.canceled or not result.smartsString:
        return Diff(Side(), Side(), 0)
    patt = Chem.MolFromSmarts(result.smartsString)
    if patt is None:
        return Diff(Side(), Side(), 0)
    lmatch, rmatch = left.GetSubstructMatch(patt), right.GetSubstructMatch(patt)
    if not lmatch or not rmatch:
        return Diff(Side(), Side(), 0)

    # 패턴 원자 p 는 왼쪽 lmatch[p], 오른쪽 rmatch[p] 다. 이 대응으로 붙는 자리를 건넨다.
    l_to_p = {a: p for p, a in enumerate(lmatch)}
    r_to_p = {a: p for p, a in enumerate(rmatch)}
    l_extra = [i for i in range(left.GetNumAtoms()) if i not in l_to_p]
    r_extra = [i for i in range(right.GetNumAtoms()) if i not in r_to_p]

    def attach_sites(mol, extra, to_p, other_match):
        sites: set[int] = set()
        for a in extra:
            for n in mol.GetAtomWithIdx(a).GetNeighbors():
                p = to_p.get(n.GetIdx())
                if p is not None:
                    sites.add(other_match[p])
        return sorted(sites)

    return Diff(
        Side(l_extra, attach_sites(right, r_extra, r_to_p, lmatch), _fragments(left, l_extra)),
        Side(r_extra, attach_sites(left, l_extra, l_to_p, rmatch), _fragments(right, r_extra)),
        len(lmatch),
    )


def describe(d: Diff, left_label: str, right_label: str) -> list[str]:
    """차이를 사람의 말로. '왼쪽에만 아세틸기, 오른쪽은 그 자리가 -OH'."""
    if not d.found:
        return ["공통 부분구조를 찾지 못했다 - 두 후보가 아예 다른 분자다. 그림을 통째로 견줄 것"]
    lines: list[str] = []
    for side, label, other in ((d.left, left_label, right_label), (d.right, right_label, left_label)):
        if side.extra:
            names = ", ".join(side.fragment_names)
            lines.append(f"{label} 에만 있음: {names}  (빨강. {other} 쪽 주황 자리에 붙는다)")
    if not lines:
        return ["원자 집합은 같고 결합 방식만 다르다 (고리 닫힘·결합 차수). 두 그림을 견줄 것"]
    lines.append("원본 그림에서 주황 자리에 빨강 조각이 있었는지 보면 된다")
    return lines


def draw_diff(left_smiles: str, right_smiles: str, dest: Path,
              legends: tuple[str, str] = ("", ""),
              size: tuple[int, int] = SIZE) -> tuple[Path | None, Diff | None]:
    """두 구조를 한 장에 나란히, 다른 곳을 칠해 그린다. (PNG 경로, 차이) 를 준다."""
    d = diff(left_smiles, right_smiles)
    if d is None:
        return None, None
    mols = [Chem.Mol(from_smiles(left_smiles)), Chem.Mol(from_smiles(right_smiles))]
    for m in mols:
        rdMolDraw2D.PrepareMolForDrawing(m)

    atoms, bonds, acolors, bcolors = [], [], [], []
    for m, side in zip(mols, (d.left, d.right)):
        hl = list(side.extra) + [a for a in side.attach if a not in side.extra]
        colors = {a: DIFF_COLOR for a in side.extra}
        colors.update({a: ATTACH_COLOR for a in side.attach if a not in colors})
        hb = _highlight_bonds(m, set(side.extra))
        atoms.append(hl)
        bonds.append(hb)
        acolors.append(colors)
        bcolors.append({b: DIFF_COLOR for b in hb})

    w, h = size
    drawer = rdMolDraw2D.MolDraw2DCairo(2 * w, h, w, h)
    drawer.DrawMolecules(
        mols,
        highlightAtoms=atoms,
        highlightBonds=bonds,
        highlightAtomColors=acolors,
        highlightBondColors=bcolors,
        legends=list(legends),
    )
    drawer.FinishDrawing()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(drawer.GetDrawingText())
    return dest, d


def draw_pair(left_smiles: str, right_smiles: str, left_dest: Path, right_dest: Path,
              legends: tuple[str, str] = ("", "")) -> tuple[Path | None, Path | None]:
    """두 구조를 차이가 칠해진 PNG 한 쌍(따로)으로 그린다. 한쪽이 안 읽히면 그쪽만 None."""
    d = diff(left_smiles, right_smiles)
    hl, hr = (d.left.extra, d.right.extra) if d is not None else ([], [])
    return (draw_png(left_smiles, left_dest, hl, legend=legends[0]),
            draw_png(right_smiles, right_dest, hr, legend=legends[1]))
