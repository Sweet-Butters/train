"""사용자가 던지는 어떤 그림이든 인식기가 먹을 수 있는 모양으로 다듬는다.

`recognize` 의 입력은 슬라이드가 아니라 사용자가 직접 넣는 그림이다. 스크린샷,
카톡으로 받은 JPG, 투명 배경 PNG, 다크모드에서 찍은 반전 이미지, 5000px 캡처,
80px 썸네일, 한글 경로. 인식기는 이 중 절반에서 조용히 엉뚱한 답을 낸다 -
에러가 아니라 틀린 SMILES 가 나오므로 여기서 먼저 잡아야 한다.

하는 일 (순서대로):

  1. PDF 한 장이면 래스터로 그린다 (첫 장만. 여러 장이면 경고)
  2. EXIF 회전을 픽셀에 반영한다 (폰 사진)
  3. 투명 → 배경을 칠한다. 선이 밝으면 검정 위에, 어두우면 흰색 위에 -
     다크모드에서 내보낸 흰 선 PNG 를 흰 배경에 얹으면 그림이 사라진다
  4. RGB 로 바꾼다 (회색조·팔레트·CMYK 전부)
  5. 배경이 어두우면 반전한다 (테두리 밝기로 판단)
  6. 여백을 다듬는다 - 내용 둘레만 남기고 일정한 흰 여백을 준다
  7. 너무 크면 축소한다 (긴 변 ~1024). 너무 작으면 경고만 한다.
     확대는 하지 않는다 - 없는 정보를 만들지 않는다
  8. ASCII 경로의 PNG 로 저장한다. OpenCV 는 윈도우 한글 경로를 열지 못한다

각 단계가 무엇을 바꿨는지 `Prepared.notes` 에 사람이 읽는 문장으로 남긴다.
고칠 수 없어 인식 품질이 의심되는 것은 `Prepared.warnings` 에 남긴다.

무거운 모델은 없다. PIL·numpy 만 쓴다 (PDF 는 pypdfium2).
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

# 인식기에 넘길 긴 변의 상한(픽셀). 이보다 크면 축소한다.
# OCSR 모델은 내부에서 어차피 작은 크기로 다시 맞추므로 더 큰 그림은 낭비다.
MAX_SIDE = 1024

# 이 밑이면 결합선이 한두 픽셀이라 인식이 불안하다. 경고만 하고 확대하지 않는다.
MIN_SIDE = 150

# 어두운 배경 판정 기준. 테두리 밝기의 중앙값(0~255)이 이 밑이면 반전한다.
DARK_BACKGROUND_LUMA = 128

# 여백을 자를 때 "내용" 으로 볼 밝기 기준. JPEG 잡음(250 근처)은 배경으로 본다.
CONTENT_LUMA = 225

# 다듬은 뒤 둘레에 남길 흰 여백. 긴 변에 대한 비율과 최소 픽셀.
MARGIN_RATIO = 0.04
MARGIN_MIN_PX = 8

# 내용으로 보이는 픽셀이 전체의 이 비율도 안 되면 빈 그림으로 본다.
BLANK_CONTENT_RATIO = 0.0002

# PDF 첫 장을 그릴 때 목표 긴 변(픽셀). 그린 뒤 다른 그림과 같은 길을 탄다.
PDF_RENDER_SIDE = 2048

PDF_SUFFIXES = {".pdf"}


@dataclass
class Prepared:
    """정규화된 그림과 그 과정.

    path      정규화된 PNG. 경로 전체가 ASCII 임을 보장한다
    notes     무엇을 고쳤는지, 사람이 읽는 문장 목록. 고친 것이 없으면 빈 목록
    warnings  고칠 수 없어 인식 결과를 의심해야 하는 이유. 없으면 빈 목록.
              경로 문제처럼 여기서 해결한 것은 warnings 가 아니라 notes 다
    source    사용자가 넣은 원본 경로
    size      정규화된 그림의 (가로, 세로)
    """

    path: Path
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: Path | None = None
    size: tuple[int, int] = (0, 0)


class UnreadableImage(ValueError):
    """그림으로 열 수 없는 파일."""


# ------------------------------------------------------------------ 진입점


def prepare_image(path: Path, work_dir: Path) -> Prepared:
    """사용자 그림 하나를 인식기가 먹을 수 있는 PNG 로 다듬어 work_dir 에 둔다.

    work_dir 이 ASCII 가 아니면 (윈도우 사용자 폴더가 한글인 경우가 그렇다)
    ASCII 임시 폴더로 대신 쓰고 노트에 남긴다. 반환 경로는 언제나 ASCII 다.
    """
    from PIL import Image, ImageOps

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    notes: list[str] = []
    warnings: list[str] = []
    raw = path.read_bytes()

    if path.suffix.lower() in PDF_SUFFIXES:
        image = _rasterize_pdf(raw, notes, warnings)
    else:
        image = _open_image(raw, path)

    with image:
        image.load()
        if _exif_orientation(image) not in (None, 1):
            notes.append("EXIF 회전 정보를 픽셀에 반영했다")
        oriented = ImageOps.exif_transpose(image)
        image = oriented if oriented is not None else image.copy()

    rgb = _flatten_to_rgb(image, notes)
    rgb = _invert_if_dark(rgb, notes)
    rgb = _trim_margins(rgb, notes, warnings)
    rgb = _shrink_if_large(rgb, notes)
    _warn_if_small(rgb, warnings)

    out_dir = _ascii_work_dir(Path(work_dir), notes)
    out = out_dir / _ascii_name(path, raw)
    rgb.save(out, format="PNG")
    return Prepared(path=out, notes=notes, warnings=warnings, source=path, size=rgb.size)


# --------------------------------------------------------------------- 열기


def _open_image(raw: bytes, path: Path):
    """PIL 로 연다. 경로가 아니라 바이트를 넘기므로 한글 경로 문제가 없다."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnreadableImage(f"그림으로 열 수 없다: {path} ({exc})") from exc
    return image


def _exif_orientation(image) -> int | None:
    try:
        return image.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 - EXIF 가 깨진 파일은 회전 정보 없음으로 본다
        return None


def _rasterize_pdf(raw: bytes, notes: list[str], warnings: list[str]):
    """PDF 첫 장을 그림으로 그린다. 파일이 아니라 바이트를 넘긴다 (한글 경로)."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(raw)
    except Exception as exc:  # noqa: BLE001 - pdfium 예외 종류가 버전마다 다르다
        raise UnreadableImage(f"PDF 로 열 수 없다 ({exc})") from exc
    try:
        page_count = len(document)
        if page_count == 0:
            raise UnreadableImage("PDF 에 페이지가 없다")
        if page_count > 1:
            warnings.append(f"PDF 가 {page_count}장이다. 첫 장만 쓴다")
        page = document[0]
        width, height = page.get_size()
        scale = PDF_RENDER_SIDE / max(width, height, 1.0)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
    finally:
        document.close()
    notes.append("PDF 첫 장을 그림으로 그렸다")
    return image


# ------------------------------------------------------------------ 정규화


def _flatten_to_rgb(image, notes: list[str]):
    """투명을 없애고 RGB 로 만든다.

    투명 PNG 는 내용이 밝은지 어두운지 보고 배경색을 정한다. 다크모드 앱에서
    내보낸 흰 선 그림을 흰 배경에 얹으면 아무것도 남지 않기 때문이다. 검정 위에
    얹으면 다음 단계(어두운 배경 반전)가 검은 선·흰 배경으로 뒤집어 준다.
    """
    import numpy as np
    from PIL import Image

    mode = image.mode
    has_alpha = "A" in image.getbands() or (
        mode == "P" and "transparency" in image.info
    ) or (mode in ("L", "RGB") and "transparency" in image.info)

    if has_alpha:
        rgba = image.convert("RGBA")
        arr = np.asarray(rgba)
        alpha = arr[..., 3]
        opaque = alpha > 128
        if opaque.any():
            luma = arr[..., :3][opaque].mean()
        else:
            luma = 0.0
        if luma > DARK_BACKGROUND_LUMA:
            backdrop = (0, 0, 0)
            notes.append("투명 배경을 검정으로 채웠다 (선이 밝다)")
        else:
            backdrop = (255, 255, 255)
            notes.append("투명 배경을 흰색으로 채웠다")
        base = Image.new("RGBA", rgba.size, backdrop + (255,))
        base.alpha_composite(rgba)
        return base.convert("RGB")

    if mode != "RGB":
        notes.append(f"{mode} 모드를 RGB 로 바꿨다")
        # I;16 처럼 8비트가 아닌 회색조는 바로 RGB 로 가면 값이 잘린다.
        if mode.startswith("I"):
            arr = np.asarray(image, dtype=np.float64)
            hi = arr.max() or 1.0
            image = Image.fromarray((arr / hi * 255).astype("uint8"), "L")
        return image.convert("RGB")
    return image


def _border_luma(gray) -> float:
    import numpy as np

    arr = np.asarray(gray)
    h, w = arr.shape
    ring = max(1, int(round(min(h, w) * 0.02)))
    ring = min(ring, h // 2 or 1, w // 2 or 1)
    edges = np.concatenate(
        [
            arr[:ring, :].ravel(),
            arr[-ring:, :].ravel(),
            arr[:, :ring].ravel(),
            arr[:, -ring:].ravel(),
        ]
    )
    return float(np.median(edges))


def _invert_if_dark(rgb, notes: list[str]):
    """배경(테두리)이 어두우면 색을 뒤집는다. 인식기는 흰 종이 위 검은 선을 배웠다."""
    from PIL import ImageOps

    if _border_luma(rgb.convert("L")) < DARK_BACKGROUND_LUMA:
        notes.append("어두운 배경을 반전했다")
        return ImageOps.invert(rgb)
    return rgb


def _trim_margins(rgb, notes: list[str], warnings: list[str]):
    """내용 둘레의 여백을 일정하게 다듬는다.

    큰 흰 캔버스 구석에 작은 구조가 있으면 축소 단계에서 구조가 함께 줄어든다.
    반대로 여백이 전혀 없으면 가장자리 결합선이 잘린 것처럼 보인다.
    그래서 내용 상자를 찾아 자르고, 긴 변에 비례한 흰 여백을 새로 준다.
    """
    import numpy as np
    from PIL import ImageOps

    gray = np.asarray(rgb.convert("L"))
    mask = gray < CONTENT_LUMA
    content = int(mask.sum())
    if content < max(1, int(mask.size * BLANK_CONTENT_RATIO)):
        warnings.append("내용이 거의 없는 그림이다 (전부 배경)")
        return rgb

    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1

    cropped = rgb.crop((left, top, right, bottom))
    margin = max(MARGIN_MIN_PX, int(round(max(cropped.size) * MARGIN_RATIO)))
    padded = ImageOps.expand(cropped, border=margin, fill=(255, 255, 255))

    before_w, before_h = rgb.size
    after_w, after_h = padded.size
    if (after_w, after_h) != (before_w, before_h):
        removed = (before_w * before_h) - (right - left) * (bottom - top)
        if removed > 0:
            notes.append(
                f"여백을 다듬었다 ({before_w}x{before_h} → {after_w}x{after_h})"
            )
        else:
            notes.append(f"둘레에 여백 {margin}px 를 더했다 (내용이 가장자리에 닿아 있었다)")
    return padded


def _shrink_if_large(rgb, notes: list[str]):
    from PIL import Image

    w, h = rgb.size
    longest = max(w, h)
    if longest <= MAX_SIDE:
        return rgb
    scale = MAX_SIDE / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    notes.append(f"{w}x{h} 를 {new_size[0]}x{new_size[1]} 로 축소했다")
    return rgb.resize(new_size, Image.LANCZOS)


def _warn_if_small(rgb, warnings: list[str]) -> None:
    w, h = rgb.size
    if max(w, h) < MIN_SIDE:
        warnings.append(
            f"그림이 작다 ({w}x{h}). 결합선이 한두 픽셀이면 인식이 불안하다. "
            "확대는 하지 않는다 - 원본을 더 크게 저장해서 넣어라"
        )


# ---------------------------------------------------------------- ASCII 경로


def _is_ascii_path(p: Path) -> bool:
    return str(p).isascii()


def _ascii_work_dir(work_dir: Path, notes: list[str]) -> Path:
    """쓸 수 있는 ASCII 폴더를 고른다. 요청한 work_dir 이 ASCII 면 그대로 쓴다.

    윈도우 사용자 폴더가 한글이면 `tempfile.gettempdir()` 도 한글이다. 그래서
    공용 폴더(`%PUBLIC%`) 와 드라이브 루트의 Temp 까지 차례로 본다.
    """
    candidates: list[Path] = [work_dir]
    if os.name == "nt":
        public = os.environ.get("PUBLIC")
        if public:
            candidates.append(Path(public) / "chemcheck_tmp")
        drive = os.environ.get("SystemDrive", "C:")
        candidates.append(Path(drive + "/") / "Temp" / "chemcheck_tmp")
    candidates.append(Path(tempfile.gettempdir()) / "chemcheck_tmp")
    candidates.append(Path("/tmp") / "chemcheck_tmp")

    for i, candidate in enumerate(candidates):
        candidate = candidate.resolve() if candidate.exists() else candidate.absolute()
        if not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".chemcheck_write_probe"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            continue
        if i > 0:
            notes.append(
                f"작업 폴더가 ASCII 가 아니라 {candidate} 를 대신 썼다 "
                "(OpenCV 는 윈도우 한글 경로를 열지 못한다)"
            )
        return candidate
    raise OSError(f"ASCII 경로의 쓸 수 있는 작업 폴더를 찾지 못했다: {work_dir}")


def _ascii_name(source: Path, raw: bytes) -> str:
    """원본 이름의 ASCII 조각 + 내용 해시. 같은 그림은 같은 이름이 된다."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", source.stem)[:32] or "image"
    digest = hashlib.sha1(raw).hexdigest()[:10]
    return f"{stem}-{digest}.png"
