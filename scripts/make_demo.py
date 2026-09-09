"""심사위원이 눈으로 못 잡는 데모 슬라이드를 만든다.

의도적으로 틀린 구조를 넣는다. 현장에서 AI에게 즉석으로 시키면
맞게 나올 수도 있으므로 미리 고정해 둔다.
"""
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt
from rdkit import Chem
from rdkit.Chem import Draw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"

# (슬라이드 제목, 그려질 실제 구조 SMILES, 이게 맞는 구조인가)
CASES = [
    ("Aspirin", "CC(=O)Oc1ccccc1C(=O)O", True),
    # 살리실산 — 아세틸기가 빠졌다. 눈으로는 거의 구분되지 않는다.
    ("Aspirin", "OC(=O)c1ccccc1O", False),
    ("Caffeine", "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", True),
    # 테오브로민 — 메틸기 하나가 없다.
    ("Caffeine", "CN1C=NC2=C1C(=O)NC(=O)N2C", False),
]


def draw(smiles: str, dest: Path) -> None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"SMILES 파싱 실패: {smiles}")
    Draw.MolToFile(mol, str(dest), size=(500, 400))


def build(out_dir: Path = OUT) -> Path:
    """데모 덱을 만든다. 테스트는 임시 폴더를 넘겨 samples/ 를 건드리지 않는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    deck = Presentation()
    blank = deck.slide_layouts[6]

    for i, (name, smiles, correct) in enumerate(CASES, start=1):
        png = out_dir / f"demo_{i:02d}.png"
        draw(smiles, png)

        slide = deck.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(8.8), Inches(1.0))
        para = box.text_frame.paragraphs[0]
        para.text = name
        para.font.size = Pt(40)
        para.font.bold = True

        slide.shapes.add_picture(str(png), Inches(2.6), Inches(1.6), height=Inches(4.2))

    dest = out_dir / "demo_slides.pptx"
    deck.save(str(dest))
    return dest


if __name__ == "__main__":
    path = build()
    print(f"생성: {path}")
    for i, (name, smiles, correct) in enumerate(CASES, start=1):
        mark = "정답" if correct else "의도적 오류"
        print(f"  {i}장  {name:10} {smiles:32} {mark}")
