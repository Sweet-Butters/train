"""PDF / PPTX에서 장(슬라이드)별 텍스트와 그림을 뽑는다.

같은 장에 있는 이름 텍스트와 구조 그림을 짝지어야 판정이 가능하므로
장 단위로 묶어서 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 아이콘·로고처럼 너무 작은 그림은 구조도가 아니다.
MIN_IMAGE_BYTES = 4096
MIN_IMAGE_SIDE = 80


@dataclass
class Slide:
    index: int
    text: str
    images: list[Path] = field(default_factory=list)


def _keep(width: int, height: int, size: int) -> bool:
    return size >= MIN_IMAGE_BYTES and min(width, height) >= MIN_IMAGE_SIDE


def from_pdf(path: Path, out_dir: Path) -> list[Slide]:
    import pymupdf as fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[Slide] = []
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            slide = Slide(page_no, page.get_text())
            for img_no, info in enumerate(page.get_images(full=True), start=1):
                xref = info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    continue
                if pix.colorspace is None:
                    continue
                if pix.n - pix.alpha > 3:  # CMYK 등은 RGB로 변환
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                data = pix.tobytes("png")
                if not _keep(pix.width, pix.height, len(data)):
                    continue
                dest = out_dir / f"p{page_no:03d}_{img_no:02d}.png"
                dest.write_bytes(data)
                slide.images.append(dest)
            slides.append(slide)
    return slides


def from_pptx(path: Path, out_dir: Path) -> list[Slide]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[Slide] = []
    deck = Presentation(str(path))
    for slide_no, raw in enumerate(deck.slides, start=1):
        texts: list[str] = []
        images: list[Path] = []
        img_no = 0
        for shape in raw.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                img_no += 1
                blob = shape.image.blob
                if len(blob) < MIN_IMAGE_BYTES:
                    continue
                ext = shape.image.ext or "png"
                dest = out_dir / f"s{slide_no:03d}_{img_no:02d}.{ext}"
                dest.write_bytes(blob)
                images.append(dest)
        slides.append(Slide(slide_no, "\n".join(texts), images))
    return slides


def load(path: Path, out_dir: Path) -> list[Slide]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return from_pdf(path, out_dir)
    if suffix == ".pptx":
        return from_pptx(path, out_dir)
    raise ValueError(f"지원하지 않는 형식: {suffix} (pdf 또는 pptx만 가능)")
