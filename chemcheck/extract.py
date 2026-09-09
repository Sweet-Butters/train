"""PDF / PPTX에서 장(슬라이드)별 텍스트와 그림을 뽑는다.

같은 장에 있는 이름 텍스트와 구조 그림을 짝지어야 판정이 가능하므로
장 단위로 묶어서 돌려준다. 한 장에 구조가 여럿이면 어느 이름이 어느 그림의
것인지 좌표로 가려야 하므로, 텍스트와 그림의 위치도 함께 싣는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 아이콘·로고처럼 너무 작은 그림은 구조도가 아니다.
MIN_IMAGE_BYTES = 4096
MIN_IMAGE_SIDE = 80


@dataclass(frozen=True)
class Box:
    """장 안에서의 사각 영역. 단위는 장마다 일관되기만 하면 된다."""
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True)
class TextBox:
    text: str
    box: Box | None


@dataclass(frozen=True)
class Picture:
    path: Path
    box: Box | None


@dataclass
class Slide:
    index: int
    text: str
    images: list[Path] = field(default_factory=list)
    text_boxes: list[TextBox] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)

    @property
    def has_layout(self) -> bool:
        """모든 그림과 최소 하나의 텍스트에 좌표가 있는가.

        좌표가 없으면 짝지을 근거가 없다. 그때는 추측하지 않고 보류한다.
        """
        if not self.pictures or not self.text_boxes:
            return False
        if any(p.box is None for p in self.pictures):
            return False
        return any(t.box is not None for t in self.text_boxes)


def _keep(width: int, height: int, size: int) -> bool:
    return size >= MIN_IMAGE_BYTES and min(width, height) >= MIN_IMAGE_SIDE


def _assemble(index: int, texts: list[TextBox], pics: list[Picture]) -> Slide:
    return Slide(
        index=index,
        text="\n".join(t.text for t in texts),
        images=[p.path for p in pics],
        text_boxes=texts,
        pictures=pics,
    )


def from_pdf(path: Path, out_dir: Path) -> list[Slide]:
    import pymupdf as fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[Slide] = []
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            texts: list[TextBox] = []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:  # 0 = 텍스트
                    continue
                content = "".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                )
                if not content.strip():
                    continue
                x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
                texts.append(TextBox(content, Box(x0, y0, x1 - x0, y1 - y0)))

            pics: list[Picture] = []
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
                try:
                    r = page.get_image_bbox(info)
                    box = Box(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0)
                except Exception:
                    box = None  # 좌표를 못 얻으면 없는 대로 둔다. 지어내지 않는다.
                pics.append(Picture(dest, box))
            slides.append(_assemble(page_no, texts, pics))
    return slides


def from_pptx(path: Path, out_dir: Path) -> list[Slide]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    out_dir.mkdir(parents=True, exist_ok=True)

    def geometry(shape) -> Box | None:
        vals = (shape.left, shape.top, shape.width, shape.height)
        if any(v is None for v in vals):
            return None
        return Box(*(float(v) for v in vals))

    slides: list[Slide] = []
    deck = Presentation(str(path))
    for slide_no, raw in enumerate(deck.slides, start=1):
        texts: list[TextBox] = []
        pics: list[Picture] = []
        img_no = 0
        for shape in raw.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(TextBox(shape.text_frame.text, geometry(shape)))
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                img_no += 1
                blob = shape.image.blob
                if len(blob) < MIN_IMAGE_BYTES:
                    continue
                ext = shape.image.ext or "png"
                dest = out_dir / f"s{slide_no:03d}_{img_no:02d}.{ext}"
                dest.write_bytes(blob)
                pics.append(Picture(dest, geometry(shape)))
        slides.append(_assemble(slide_no, texts, pics))
    return slides


def load(path: Path, out_dir: Path) -> list[Slide]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return from_pdf(path, out_dir)
    if suffix == ".pptx":
        return from_pptx(path, out_dir)
    raise ValueError(f"지원하지 않는 형식: {suffix} (pdf 또는 pptx만 가능)")
