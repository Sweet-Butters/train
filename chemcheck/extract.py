"""PDF / PPTX에서 장(슬라이드)별 텍스트와 그림을 뽑는다.

같은 장에 있는 이름 텍스트와 구조 그림을 짝지어야 판정이 가능하므로
장 단위로 묶어서 돌려준다.

강의자료의 구조도는 두 경로로 들어온다.

  1. 래스터 이미지 — ChemDraw에서 복사해 붙인 그림
  2. 벡터 — PPT 도형으로 직접 그리거나, 그 파일을 PDF로 내보낸 것

2번은 임베드된 이미지가 하나도 없다. 임베드 이미지만 보면 "그림 0개"로
조용히 넘어가는데, 그건 판정이 아니라 침묵이다. 그래서 PDF는 페이지를
그려서 오려내고, PPTX는 최소한 못 뽑았다는 사실을 남긴다.

PDF 백엔드는 pdfminer.six(MIT) + pypdfium2(BSD-3/Apache-2.0)를 쓴다.
PyMuPDF는 AGPL-3.0 이라 상업 이용 시 전염되므로 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

# 아이콘·로고처럼 너무 작은 그림은 구조도가 아니다.
MIN_IMAGE_BYTES = 4096
MIN_IMAGE_SIDE = 80

# 오려낼 영역의 최소 크기(포인트). 밑줄·표 테두리 조각을 걸러낸다.
MIN_REGION_PT = 40.0

# PDF에서 구조로 인정할 최소 꼭짓점 수.
# 결합선을 따로 그리면 선 하나가 2점, 고리를 한 경로로 그리면 육각형이 7점이다.
# 밑줄(2점)과 표 테두리 사각형(5점)은 이 밑으로 떨어진다.
MIN_PDF_VECTOR_POINTS = 6

# PPTX에서 구조로 인정할 최소 도형 수.
# 벤젠 고리를 선 도형으로 그리면 결합선만 6개가 된다.
MIN_PPTX_VECTOR_SHAPES = 6

# 흩어진 조각을 한 구조로 묶을 때의 최대 간격(포인트).
CLUSTER_GAP_PT = 12.0

# 오려낸 영역을 렌더할 배율. 1.0 = 72dpi 이므로 4.0 은 약 288dpi.
# OCSR 인식기는 저해상도에서 급격히 나빠진다.
RENDER_SCALE = 4.0

# 영역 둘레에 남길 여백(포인트). 결합선이 잘리면 인식이 깨진다.
REGION_PAD_PT = 6.0

Box = tuple[float, float, float, float]


@dataclass
class VectorRegion:
    """도형으로 그려졌지만 아직 이미지로 만들지 못한 구조 후보.

    PPTX 경로에서만 채워진다. PDF는 실제로 오려내므로 images 로 들어간다.
    """

    slide_index: int
    part_count: int
    grouped: bool


@dataclass
class Slide:
    index: int
    text: str
    images: list[Path] = field(default_factory=list)
    # 뽑지 못한 벡터 구조 후보. pipeline 은 읽지 않고 리포트가 쓴다.
    vector_regions: list[VectorRegion] = field(default_factory=list)


def _keep(width: int, height: int, size: int) -> bool:
    return size >= MIN_IMAGE_BYTES and min(width, height) >= MIN_IMAGE_SIDE


def _keep_blob(blob: bytes) -> bool:
    """바이트 크기와 실제 화소 크기를 모두 본다."""
    if len(blob) < MIN_IMAGE_BYTES:
        return False
    try:
        from PIL import Image

        with Image.open(BytesIO(blob)) as im:
            width, height = im.size
    except Exception:
        # 크기를 못 읽으면 버리지 않는다. 놓치는 쪽이 더 나쁘다.
        return True
    return min(width, height) >= MIN_IMAGE_SIDE


# ---------------------------------------------------------------- 영역 묶기


def _merged(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a: Box, b: Box, gap: float) -> bool:
    """두 상자가 겹치거나 gap 이내로 붙어 있는가."""
    return (
        a[0] - gap <= b[2]
        and b[0] - gap <= a[2]
        and a[1] - gap <= b[3]
        and b[1] - gap <= a[3]
    )


def _cluster(
    items: list[tuple[Box, int]], gap: float = CLUSTER_GAP_PT
) -> list[tuple[Box, int]]:
    """가까운 상자들을 하나로 합친다. (합친 상자, 무게 합) 목록을 돌려준다.

    무게는 래스터면 1, 벡터면 꼭짓점 수다. 한 장에 구조가 여러 개면
    서로 떨어져 있으므로 각각 다른 덩어리가 된다.
    """
    clusters: list[tuple[Box, int]] = list(items)
    changed = True
    while changed:
        changed = False
        out: list[tuple[Box, int]] = []
        for box, count in clusters:
            for i, (other, other_count) in enumerate(out):
                if _near(box, other, gap):
                    out[i] = (_merged(box, other), other_count + count)
                    changed = True
                    break
            else:
                out.append((box, count))
        clusters = out
    return clusters


def _big_enough(box: Box) -> bool:
    return (box[2] - box[0]) >= MIN_REGION_PT and (box[3] - box[1]) >= MIN_REGION_PT


# ---------------------------------------------------------------------- PDF


def _pdf_elements(
    layout,
) -> tuple[list[str], list[tuple[Box, int]], list[tuple[Box, int]]]:
    """페이지 레이아웃을 훑어 텍스트·래스터·벡터를 모은다.

    벡터는 (상자, 꼭짓점 수)로 담는다. 사각형은 표 테두리와 글상자 윤곽이
    대부분이라 구조 후보에서 뺀다. 구조는 선과 곡선으로 그린다.

    LTRect 와 LTLine 은 둘 다 LTCurve 의 하위형이라 검사 순서가 중요하다.
    LTFigure 안에 중첩되는 경우가 있어 재귀로 들어간다.
    """
    from pdfminer.layout import (
        LTCurve,
        LTFigure,
        LTImage,
        LTLine,
        LTRect,
        LTTextContainer,
    )

    texts: list[str] = []
    rasters: list[tuple[Box, int]] = []
    vectors: list[tuple[Box, int]] = []

    def walk(element) -> None:
        for child in element:
            if isinstance(child, LTTextContainer):
                stripped = child.get_text().strip()
                if stripped:
                    texts.append(stripped)
            elif isinstance(child, LTImage):
                rasters.append((child.bbox, 1))
            elif isinstance(child, LTRect):
                pass  # 표 테두리·윤곽선. 구조가 아니다.
            elif isinstance(child, LTLine):
                vectors.append((child.bbox, 2))  # 결합선 하나
            elif isinstance(child, LTCurve):
                vectors.append((child.bbox, len(child.pts)))
            if isinstance(child, LTFigure):
                walk(child)

    walk(layout)
    return texts, rasters, vectors


def _pdf_regions(
    rasters: list[tuple[Box, int]], vectors: list[tuple[Box, int]]
) -> list[Box]:
    """오려낼 영역을 고른다.

    래스터는 한 장이어도 그림이지만, 벡터는 꼭짓점이 충분히 모여야
    구조도로 본다. 밑줄 하나를 구조라고 부르면 오탐이 된다.
    """
    regions = [box for box, _ in _cluster(rasters) if _big_enough(box)]
    for box, points in _cluster(vectors):
        if points >= MIN_PDF_VECTOR_POINTS and _big_enough(box):
            regions.append(box)
    return regions


def _render_region(page, width: float, height: float, box: Box, dest: Path) -> bool:
    """페이지의 한 영역만 그려서 png 로 저장한다. 저장했으면 True."""
    left = max(0.0, box[0] - REGION_PAD_PT)
    bottom = max(0.0, box[1] - REGION_PAD_PT)
    right = min(width, box[2] + REGION_PAD_PT)
    top = min(height, box[3] + REGION_PAD_PT)
    if right - left <= 0 or top - bottom <= 0:
        return False

    # pypdfium2 의 crop 은 각 변에서 잘라낼 여백을 받는다.
    crop = (left, bottom, width - right, height - top)
    try:
        image = page.render(scale=RENDER_SCALE, crop=crop).to_pil()
    except Exception:
        return False
    if min(image.size) < MIN_IMAGE_SIDE:
        return False
    image.save(dest)
    return True


def from_pdf(path: Path, out_dir: Path) -> list[Slide]:
    """페이지에서 구조 후보 영역을 찾아 그 자리를 그려서 오려낸다.

    임베드 이미지를 그대로 꺼내지 않고 렌더해서 자르는 이유:
      - 벡터로 그린 구조는 꺼낼 임베드 이미지 자체가 없다
      - CMYK·JPX 같은 코덱을 직접 다룰 필요가 없다
      - 화면에 보이는 그대로를 얻는다
    """
    import pypdfium2 as pdfium
    from pdfminer.high_level import extract_pages

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[Slide] = []
    document = pdfium.PdfDocument(str(path))
    try:
        for page_no, layout in enumerate(extract_pages(str(path)), start=1):
            texts, rasters, vectors = _pdf_elements(layout)
            slide = Slide(page_no, "\n".join(texts))

            regions = _pdf_regions(rasters, vectors)
            if regions:
                page = document[page_no - 1]
                width, height = page.get_size()
                for img_no, box in enumerate(sorted(regions), start=1):
                    dest = out_dir / f"p{page_no:03d}_{img_no:02d}.png"
                    if _render_region(page, width, height, box, dest):
                        slide.images.append(dest)
            slides.append(slide)
    finally:
        document.close()
    return slides


# --------------------------------------------------------------------- PPTX


def _flatten(shapes, group_id: int | None = None):
    """그룹 안까지 평탄화한다. (도형, 최상위 그룹 id) 를 낸다.

    강의자료의 그림은 대개 설명 상자와 함께 그룹으로 묶여 있다.
    그룹을 안 들어가면 그 장은 통째로 그림 0개가 된다.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            top = group_id if group_id is not None else id(shape)
            yield from _flatten(shape.shapes, top)
        else:
            yield shape, group_id


def _shape_text(shape) -> str:
    """도형과 표에서 텍스트를 모은다."""
    parts: list[str] = []
    if getattr(shape, "has_text_frame", False):
        stripped = shape.text_frame.text.strip()
        if stripped:
            parts.append(stripped)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                stripped = cell.text.strip()
                if stripped:
                    parts.append(stripped)
    return "\n".join(parts)


def _picture(shape):
    """그림이면 (blob, 확장자), 아니면 None.

    자리표시자 안에 들어간 그림은 shape_type 이 PICTURE 가 아니라서
    타입 대신 image 속성 유무로 판단한다.
    """
    try:
        image = shape.image
    except (AttributeError, ValueError, KeyError):
        return None
    return image.blob, (image.ext or "png")


def _is_vector_part(shape) -> bool:
    """구조를 이루는 도형 조각인가. 글자만 든 상자와 그림은 제외한다."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    kinds = {
        kind
        for kind in (
            getattr(MSO_SHAPE_TYPE, name, None)
            for name in ("AUTO_SHAPE", "FREEFORM", "LINE")
        )
        if kind is not None
    }
    if shape.shape_type not in kinds:
        return False
    return not _shape_text(shape)


def from_pptx(path: Path, out_dir: Path) -> list[Slide]:
    from pptx import Presentation

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[Slide] = []
    deck = Presentation(str(path))
    for slide_no, raw in enumerate(deck.slides, start=1):
        texts: list[str] = []
        images: list[Path] = []
        img_no = 0
        # 그룹별 도형 조각 수. None 키는 그룹에 안 묶인 조각들.
        parts: dict[int | None, int] = {}

        for shape, group_id in _flatten(raw.shapes):
            stripped = _shape_text(shape)
            if stripped:
                texts.append(stripped)

            found = _picture(shape)
            if found is not None:
                blob, ext = found
                if not _keep_blob(blob):
                    continue
                img_no += 1
                dest = out_dir / f"s{slide_no:03d}_{img_no:02d}.{ext}"
                dest.write_bytes(blob)
                images.append(dest)
                continue

            if _is_vector_part(shape):
                parts[group_id] = parts.get(group_id, 0) + 1

        regions = [
            VectorRegion(slide_no, count, group_id is not None)
            for group_id, count in parts.items()
            if count >= MIN_PPTX_VECTOR_SHAPES
        ]
        slides.append(Slide(slide_no, "\n".join(texts), images, regions))
    return slides


def load(path: Path, out_dir: Path) -> list[Slide]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return from_pdf(path, out_dir)
    if suffix == ".pptx":
        return from_pptx(path, out_dir)
    raise ValueError(f"지원하지 않는 형식: {suffix} (pdf 또는 pptx만 가능)")
