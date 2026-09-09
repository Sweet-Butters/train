"""렌더 변주 - 같은 분자를 다르게 그린다.

인식기는 RDKit 기본 그림에서 가장 잘 읽는다. 사용자가 넣는 그림은 그렇지 않다 -
스크린샷이라 작고, JPG 라 뭉개지고, 교재 스캔이라 잡음이 있고, 글꼴이 다르고,
기울어져 있다. 그 차이를 그림 50장에 골고루 심는다. 변주가 곧 '어디서 틀리는가'의
축이 되므로 각 그림에 어떤 변주를 썼는지 함께 남긴다.

정답은 여기서 만들지 않는다. 그림에 무엇이 그려졌는지는 SMILES 가 말하고,
InChIKey 는 그것으로 계산한다.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from chemcheck.keys import smiles_to_inchikey

from .molecules import MOLECULES, Molecule

FONTS_DIR = Path("C:/Windows/Fonts")


@dataclass(frozen=True)
class Variant:
    """그림 하나를 만드는 조건. 값이 기본과 같으면 그 축은 건드리지 않은 것이다."""

    name: str
    size: tuple[int, int] = (500, 400)
    line_width: int = 2        # 결합선 굵기 (px)
    font_scale: float = 1.0    # 원자 기호 글자 크기 배율
    font_file: str = ""        # 비어 있으면 RDKit 기본 글꼴
    rotate: float = 0.0        # 분자 회전각 (도)
    noise: float = 0.0         # 가우스 잡음 표준편차 (0~255 스케일)
    jpeg_quality: int = 0      # 0 이면 PNG, 아니면 이 품질로 JPG 저장

    @property
    def suffix(self) -> str:
        return ".jpg" if self.jpeg_quality else ".png"


# 여덞 가지. 50장이면 변주마다 6~7장이 돌아간다 - 변주별 비율을 말할 크기는 아니고
# '어느 변주가 유독 깨지는가'를 가리킬 정도다.
VARIANTS: tuple[Variant, ...] = (
    Variant("plain"),
    Variant("large", size=(1000, 800), line_width=3, font_scale=1.2),
    Variant("small", size=(240, 190), line_width=1, font_scale=0.8),
    Variant("thick", line_width=5, font_scale=1.1),
    Variant("rotated", rotate=37.0),
    Variant("serif", font_file="times.ttf", font_scale=1.15),
    Variant("noisy", noise=14.0),
    Variant("jpeg", size=(360, 280), noise=6.0, jpeg_quality=45),
)


@dataclass(frozen=True)
class Item:
    """평가 세트의 한 장. 정답 키는 SMILES 에서 계산한 것이다 - 라벨이 아니다."""

    index: int
    molecule: Molecule
    variant: Variant
    path: Path
    inchikey: str

    @property
    def skeleton(self) -> str:
        return self.inchikey[:14]

    @property
    def stem(self) -> str:
        return self.path.stem


def variant_for(index: int) -> Variant:
    """장 번호로 변주를 정한다. 같은 번호는 늘 같은 변주 - 실행 간에 비교가 된다."""
    return VARIANTS[(index - 1) % len(VARIANTS)]


def _font_path(name: str) -> str:
    if not name:
        return ""
    path = FONTS_DIR / name
    return str(path) if path.exists() else ""


def render(smiles: str, variant: Variant, dest: Path, seed: int = 0) -> Path:
    """SMILES 를 변주 조건대로 그려 dest 에 저장한다. 실제 저장 경로를 돌려준다."""
    from PIL import Image
    from rdkit import Chem
    from rdkit.Chem.Draw import rdMolDraw2D

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"SMILES 파싱 실패: {smiles}")

    w, h = variant.size
    drawer = rdMolDraw2D.MolDraw2DCairo(w, h)
    opts = drawer.drawOptions()
    opts.bondLineWidth = variant.line_width
    opts.rotate = variant.rotate
    if variant.font_scale != 1.0:
        # 기본 글자 크기를 배율로 키우거나 줄인다. 최소·최대도 같이 밀어야 적용된다.
        base = 0.6 * variant.font_scale
        opts.baseFontSize = base
        opts.minFontSize = max(6, int(12 * variant.font_scale))
        opts.maxFontSize = max(opts.minFontSize, int(40 * variant.font_scale))
    font = _font_path(variant.font_file)
    if font:
        opts.fontFile = font
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()

    img = Image.open(io.BytesIO(drawer.GetDrawingText())).convert("RGB")

    if variant.noise > 0:
        img = _add_noise(img, variant.noise, seed)

    dest = dest.with_suffix(variant.suffix)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if variant.jpeg_quality:
        img.save(dest, "JPEG", quality=variant.jpeg_quality)
    else:
        img.save(dest, "PNG")
    return dest


def _add_noise(img, sigma: float, seed: int):
    """가우스 잡음. numpy 로 한 번에 - 픽셀 루프는 50장에 너무 느리다."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    arr = np.asarray(img, dtype=np.float32)
    arr = arr + rng.normal(0.0, sigma, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def subset_indices(limit: int, total: int | None = None) -> list[int]:
    """축소판. 앞에서 자르지 않고 고르게 뽑는다 - 작은 분자·큰 분자·변주 8종이 다 든다."""
    total = len(MOLECULES) if total is None else total
    if limit <= 0 or limit >= total:
        return list(range(1, total + 1))
    stride = total / limit
    return [int(k * stride) + 1 for k in range(limit)]


def build_set(out_dir: Path, indices: list[int] | None = None) -> list[Item]:
    """평가 세트를 그린다. 파일 이름은 r{번호}_{변주} - 번호가 곧 정답의 열쇠다.

    indices 를 주면 그 번호만 그린다. 번호는 전체 세트에서의 번호라 축소판의 변주와
    정답이 전체 실행과 같다 - 축소판에서 본 장을 전체에서 다시 찾을 수 있다.
    """
    indices = list(range(1, len(MOLECULES) + 1)) if indices is None else indices
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[Item] = []
    for i in indices:
        mol = MOLECULES[i - 1]
        key = smiles_to_inchikey(mol.smiles)
        if key is None:
            raise ValueError(f"정답 SMILES 를 RDKit 이 읽지 못함: {mol.name} {mol.smiles}")
        variant = variant_for(i)
        # 잡음 씨앗은 장 번호로. 다시 그려도 같은 그림이다.
        path = render(mol.smiles, variant, out_dir / f"r{i:02d}_{variant.name}", seed=i)
        items.append(Item(i, mol, variant, path, key))
    return items


def index_of(image_path: Path) -> int:
    """그림 파일 이름에서 장 번호를 읽는다. 스텁 인식기가 정답을 찾을 때 쓴다."""
    return int(Path(image_path).stem.split("_")[0].lstrip("r"))


# 리포트에서 변주별 줄을 붙일 때 이 순서를 지킨다.
VARIANT_NAMES: tuple[str, ...] = tuple(v.name for v in VARIANTS)
