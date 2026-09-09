"""입력 정규화 검증.

인식기 없이 돌아간다. RDKit 으로 그린 깨끗한 구조 그림을 사용자가 실제로 던지는
모양으로 하나씩 망가뜨려서(투명·다크모드·거대·썸네일·JPG·한글 경로·PDF),
`prepare_image` 가 인식기가 먹을 수 있는 모양으로 되돌리는지 본다.

가장 중요한 세 가지:
  - 반환 경로는 언제나 ASCII 다 (OpenCV 가 윈도우 한글 경로를 못 연다)
  - 다크모드 그림이 흰 배경·검은 선으로 돌아온다 (투명 흰 선 포함)
  - 작은 그림을 확대하지 않는다 - 없는 정보를 만들지 않는다
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import inputs  # noqa: E402
from chemcheck.inputs import Prepared, UnreadableImage, prepare_image  # noqa: E402


# ------------------------------------------------------------------ 픽스처


def _molecule_rgba(size: int = 400) -> Image.Image:
    """RDKit 으로 아스피린을 그린다. 흰 배경, 검은 선, 알파 채널 있음.

    RDKit 이 없으면 PIL 로 육각형을 그린다 - 테스트가 건너뛰어지지 않게.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw

        mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
        return Draw.MolToImage(mol, size=(size, size)).convert("RGBA")
    except Exception:  # noqa: BLE001
        im = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        d = ImageDraw.Draw(im)
        c, r = size / 2, size * 0.3
        pts = [
            (c + r * np.cos(np.pi / 3 * k), c + r * np.sin(np.pi / 3 * k))
            for k in range(6)
        ]
        d.polygon(pts, outline=(0, 0, 0, 255), width=max(1, size // 100))
        return im


def _transparent(im: Image.Image) -> Image.Image:
    """흰 배경을 투명으로 바꾼다 (ChemDraw '투명 배경으로 내보내기')."""
    arr = np.array(im.convert("RGBA"))
    white = (arr[..., :3] > 240).all(axis=-1)
    arr[white, 3] = 0
    return Image.fromarray(arr, "RGBA")


def _dark_mode(im: Image.Image) -> Image.Image:
    """다크모드 화면 캡처: 배경 검정, 선 흰색."""
    return ImageOps.invert(im.convert("RGB"))


def _gray(prepared: Prepared) -> np.ndarray:
    with Image.open(prepared.path) as out:
        return np.asarray(out.convert("L"))


def _corners(gray: np.ndarray) -> list[int]:
    return [int(gray[0, 0]), int(gray[0, -1]), int(gray[-1, 0]), int(gray[-1, -1])]


@pytest.fixture
def mol() -> Image.Image:
    return _molecule_rgba()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


# ------------------------------------------------------------ 계약: 경로·형식


def test_output_is_ascii_png_rgb(mol, tmp_path, work):
    src = tmp_path / "aspirin.png"
    mol.convert("RGB").save(src)

    prep = prepare_image(src, work)

    assert str(prep.path).isascii()
    assert prep.path.suffix == ".png"
    assert prep.path.is_file()
    assert prep.source == src
    with Image.open(prep.path) as out:
        assert out.format == "PNG"
        assert out.mode == "RGB"
        assert out.size == prep.size
    assert prep.warnings == []


def test_korean_source_path_yields_ascii_output(mol, tmp_path, work):
    src = tmp_path / "아스피린 구조도.png"
    mol.convert("RGB").save(src)

    prep = prepare_image(src, work)

    assert str(prep.path).isascii()
    assert prep.path.is_file()
    # 파일명에서 한글은 빠지고 내용 해시가 붙는다.
    assert prep.path.stem.startswith("image-") or prep.path.stem[0].isascii()


def test_korean_work_dir_falls_back_to_ascii_dir(mol, tmp_path):
    src = tmp_path / "a.png"
    mol.convert("RGB").save(src)
    work = tmp_path / "작업폴더"
    work.mkdir()

    prep = prepare_image(src, work)

    assert str(prep.path).isascii()
    assert prep.path.is_file()
    # 해결한 일이므로 경고가 아니라 노트다. 인식 품질 경고는 없어야 한다.
    assert prep.warnings == []
    if not str(work.resolve()).isascii():
        assert any("ASCII" in n for n in prep.notes)
    else:
        assert prep.path.parent == work.resolve()


def test_opencv_can_read_output(mol, tmp_path, work):
    cv2 = pytest.importorskip("cv2")
    src = tmp_path / "카톡에서 받은 그림.jpg"
    mol.convert("RGB").save(src, quality=85)

    prep = prepare_image(src, work)

    loaded = cv2.imread(str(prep.path))
    assert loaded is not None
    assert loaded.shape[:2] == (prep.size[1], prep.size[0])


def test_same_content_same_name(mol, tmp_path, work):
    a = tmp_path / "x.png"
    b = tmp_path / "y" / "x.png"
    b.parent.mkdir()
    mol.convert("RGB").save(a)
    b.write_bytes(a.read_bytes())

    assert prepare_image(a, work).path == prepare_image(b, work).path


def test_missing_file_raises(work, tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_image(tmp_path / "없는파일.png", work)


def test_garbage_raises_unreadable(work, tmp_path):
    src = tmp_path / "notes.png"
    src.write_bytes(b"this is not an image at all")
    with pytest.raises(UnreadableImage):
        prepare_image(src, work)


# --------------------------------------------------------------- 형식 변환


def test_jpeg_becomes_png(mol, tmp_path, work):
    src = tmp_path / "shot.jpg"
    mol.convert("RGB").save(src, quality=80)

    prep = prepare_image(src, work)

    with Image.open(prep.path) as out:
        assert out.format == "PNG"
    assert prep.warnings == []


def test_grayscale_and_palette_become_rgb(mol, tmp_path, work):
    for name, mode in (("g.png", "L"), ("p.png", "P")):
        src = tmp_path / name
        mol.convert("RGB").convert(mode).save(src)
        prep = prepare_image(src, work)
        with Image.open(prep.path) as out:
            assert out.mode == "RGB"
        assert any("RGB" in n for n in prep.notes)


def test_exif_rotation_is_applied(mol, tmp_path, work):
    # 세로로 긴 그림을 90도 돌려 저장하고 EXIF 로 "돌려서 보라" 고 적는다.
    tall = mol.convert("RGB").resize((300, 500))
    stored = tall.transpose(Image.ROTATE_90)  # 500x300 로 눕힘
    exif = Image.Exif()
    exif[0x0112] = 6  # 시계 방향 90도 돌려서 보라
    src = tmp_path / "phone.jpg"
    stored.save(src, exif=exif.tobytes(), quality=90)

    prep = prepare_image(src, work)

    w, h = prep.size
    assert h > w, "EXIF 회전이 픽셀에 반영돼 다시 세로가 돼야 한다"
    assert any("EXIF" in n for n in prep.notes)


# --------------------------------------------------------------- 투명·다크


def test_transparent_background_becomes_white(mol, tmp_path, work):
    src = tmp_path / "transparent.png"
    _transparent(mol).save(src)

    prep = prepare_image(src, work)

    gray = _gray(prep)
    assert all(c >= 250 for c in _corners(gray)), _corners(gray)
    assert (gray < 100).any(), "선은 남아 있어야 한다"
    assert any("투명" in n for n in prep.notes)


def test_dark_mode_capture_is_inverted(mol, tmp_path, work):
    src = tmp_path / "dark.png"
    _dark_mode(mol).save(src)
    ref = tmp_path / "ref.png"
    mol.convert("RGB").save(ref)

    prep = prepare_image(src, work)
    ref_prep = prepare_image(ref, work)

    gray = _gray(prep)
    assert all(c >= 250 for c in _corners(gray)), _corners(gray)
    assert any("반전" in n for n in prep.notes)
    # 반전을 되돌리면 원래 그림과 같아야 한다.
    ref_gray = _gray(ref_prep)
    assert gray.shape == ref_gray.shape
    assert np.abs(gray.astype(int) - ref_gray.astype(int)).mean() < 2.0


def test_transparent_white_strokes_survive(mol, tmp_path, work):
    """다크모드 앱에서 내보낸 투명 PNG: 선이 흰색이다. 흰 배경에 얹으면 사라진다."""
    inverted = ImageOps.invert(mol.convert("RGB")).convert("RGBA")
    arr = np.array(inverted)
    alpha = np.array(mol)[..., :3].mean(axis=-1) < 240  # 원래 선이 있던 자리만 불투명
    arr[..., 3] = np.where(alpha, 255, 0)
    src = tmp_path / "dark_transparent.png"
    Image.fromarray(arr, "RGBA").save(src)

    prep = prepare_image(src, work)

    gray = _gray(prep)
    assert all(c >= 250 for c in _corners(gray)), "배경은 흰색"
    assert (gray < 100).mean() > 0.005, "선이 검게 남아 있어야 한다"
    assert any("검정" in n for n in prep.notes)
    assert any("반전" in n for n in prep.notes)


# ------------------------------------------------------------------- 크기


def test_huge_capture_is_shrunk(mol, tmp_path, work):
    src = tmp_path / "huge.png"
    mol.convert("RGB").resize((5000, 4000)).save(src)

    prep = prepare_image(src, work)

    assert max(prep.size) <= inputs.MAX_SIDE
    assert min(prep.size) > inputs.MAX_SIDE * 0.5, "비율은 유지한다"
    assert any("축소" in n for n in prep.notes)
    assert prep.warnings == []


def test_thumbnail_is_warned_not_upscaled(mol, tmp_path, work):
    src = tmp_path / "thumb.png"
    mol.convert("RGB").resize((80, 80), Image.LANCZOS).save(src)

    prep = prepare_image(src, work)

    assert max(prep.size) < inputs.MIN_SIDE
    assert max(prep.size) <= 80 + 2 * inputs.MARGIN_MIN_PX + 2
    assert any("작다" in w for w in prep.warnings)
    assert not any("확대" in n for n in prep.notes)


def test_medium_image_is_left_alone(mol, tmp_path, work):
    src = tmp_path / "ok.png"
    mol.convert("RGB").save(src)

    prep = prepare_image(src, work)

    assert max(prep.size) <= inputs.MAX_SIDE
    assert not any("축소" in n for n in prep.notes)
    assert prep.warnings == []


# ------------------------------------------------------------------- 여백


def test_wide_margins_are_trimmed(mol, tmp_path, work):
    canvas = Image.new("RGB", (1600, 1200), (255, 255, 255))
    small = mol.convert("RGB").resize((300, 300))
    canvas.paste(small, (1250, 50))  # 오른쪽 위 구석
    src = tmp_path / "slide_capture.png"
    canvas.save(src)

    prep = prepare_image(src, work)

    w, h = prep.size
    assert w < 400 and h < 400, prep.size
    # 내용 상자 + 양쪽 여백이 결과 크기다.
    content = np.asarray(small.convert("L")) < inputs.CONTENT_LUMA
    rows = np.flatnonzero(content.any(axis=1))
    cols = np.flatnonzero(content.any(axis=0))
    box_w, box_h = cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1
    margin = max(inputs.MARGIN_MIN_PX, round(max(box_w, box_h) * inputs.MARGIN_RATIO))
    assert (w, h) == (box_w + 2 * margin, box_h + 2 * margin)
    assert any("여백" in n for n in prep.notes)
    # 축소가 필요할 만큼 크지 않았으므로 축소 노트는 없다.
    assert not any("축소" in n for n in prep.notes)


def test_trim_leaves_uniform_margin(mol, tmp_path, work):
    src = tmp_path / "m.png"
    mol.convert("RGB").save(src)

    prep = prepare_image(src, work)

    gray = _gray(prep)
    mask = gray < inputs.CONTENT_LUMA
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    top, bottom = rows[0], gray.shape[0] - 1 - rows[-1]
    left, right = cols[0], gray.shape[1] - 1 - cols[-1]
    margins = {int(top), int(bottom), int(left), int(right)}
    assert min(margins) >= inputs.MARGIN_MIN_PX
    assert max(margins) - min(margins) <= 1


def test_content_touching_edge_gets_margin(mol, tmp_path, work):
    # 내용 상자 그대로 잘라 저장하면 결합선이 가장자리에 닿는다.
    rgb = mol.convert("RGB")
    bbox = ImageOps.invert(rgb).getbbox()
    tight = rgb.crop(bbox)
    src = tmp_path / "tight.png"
    tight.save(src)

    prep = prepare_image(src, work)

    assert prep.size[0] > tight.size[0] and prep.size[1] > tight.size[1]
    gray = _gray(prep)
    assert all(c >= 250 for c in _corners(gray))
    assert any("여백" in n for n in prep.notes)


def test_blank_image_is_warned(tmp_path, work):
    src = tmp_path / "blank.png"
    Image.new("RGB", (500, 500), (255, 255, 255)).save(src)

    prep = prepare_image(src, work)

    assert any("내용" in w for w in prep.warnings)
    assert prep.path.is_file()


# -------------------------------------------------------------------- PDF


def test_single_page_pdf_is_rasterized(mol, tmp_path, work):
    pytest.importorskip("pypdfium2")
    src = tmp_path / "한 장.pdf"
    mol.convert("RGB").save(src, "PDF", resolution=72)

    prep = prepare_image(src, work)

    assert str(prep.path).isascii()
    assert prep.path.suffix == ".png"
    assert any("PDF" in n for n in prep.notes)
    assert not any("PDF" in w for w in prep.warnings)
    gray = _gray(prep)
    assert (gray < 100).any(), "구조 선이 그려져 있어야 한다"
    assert max(prep.size) <= inputs.MAX_SIDE


def test_multi_page_pdf_uses_first_page_with_warning(mol, tmp_path, work):
    pytest.importorskip("pypdfium2")
    first = mol.convert("RGB")
    second = Image.new("RGB", first.size, (255, 255, 255))
    src = tmp_path / "two.pdf"
    first.save(src, "PDF", save_all=True, append_images=[second], resolution=72)

    prep = prepare_image(src, work)

    assert any("첫 장" in w for w in prep.warnings)
    gray = _gray(prep)
    assert (gray < 100).any(), "첫 장(구조)이 쓰여야 한다"


# ------------------------------------------------------------ 노트는 사람용


def test_notes_are_readable_sentences(mol, tmp_path, work):
    src = tmp_path / "dark_huge.png"
    _dark_mode(mol).resize((3000, 3000)).save(src)

    prep = prepare_image(src, work)

    assert prep.notes, "고친 것이 있으면 노트가 있어야 한다"
    for note in prep.notes:
        assert isinstance(note, str) and len(note) > 4
