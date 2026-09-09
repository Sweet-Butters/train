"""PDF / PPTX에서 장(슬라이드)별 텍스트와 그림을 뽑는다.

같은 장에 있는 이름 텍스트와 구조 그림을 짝지어야 판정이 가능하므로
장 단위로 묶어서 돌려준다.

강의자료의 구조도는 두 경로로 들어온다.

  1. 래스터 이미지 — ChemDraw에서 복사해 붙인 그림
  2. 벡터 — PPT 도형으로 직접 그리거나, 그 파일을 PDF로 내보낸 것

2번은 임베드된 이미지가 하나도 없다. 임베드 이미지만 보면 "그림 0개"로
조용히 넘어가는데, 그건 판정이 아니라 침묵이다. 그래서 PDF는 페이지를
그려서 오려내고, PPTX는 최소한 못 뽑았다는 사실을 남긴다.

강의자료의 그림은 심하게 중복된다. 실측한 398장 덱은 그림 371건 중 고유한 것이
109개였고 배경 클립아트 하나가 46번 나왔다. 장마다 뽑아 그대로 넘기면 같은 그림이
46번 인식기를 타므로 비용도 오탐도 46배가 된다. 그래서 내용 해시가 같은 그림은
파일 하나로 모은다. 어느 장에 나왔는지는 `Slide.images` 가 그대로 들고 있으므로
잃지 않는다 - `figures()` 가 그것을 그림 단위로 뒤집어 준다.

PDF 백엔드는 pdfminer.six(MIT) + pypdfium2(BSD-3/Apache-2.0)를 쓴다.
PyMuPDF는 AGPL-3.0 이라 상업 이용 시 전염되므로 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
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

# pptx를 pdf로 바꾸는 데 줄 시간(초). 장수가 많으면 오래 걸린다.
PPTX_CONVERT_TIMEOUT_S = 180

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
    """PPT 도형으로 그린 구조 후보와 그 자리.

    box 는 슬라이드 크기에 대한 비율 (왼쪽, 위, 오른쪽, 아래) 이다.
    슬라이드를 그려낼 수단이 있으면 이 자리를 오려내고, 없으면 못 뽑았다는
    기록으로 남는다. 비율로 두는 이유는 렌더 배율과 무관하게 쓰기 위해서다.
    """

    slide_index: int
    part_count: int
    grouped: bool
    box: Box = (0.0, 0.0, 1.0, 1.0)
    image: Path | None = None


@dataclass
class Slide:
    index: int
    text: str
    # 이 장에 나온 그림들. 내용이 같은 그림은 덱 전체에서 한 파일이므로,
    # 여러 장의 images 가 같은 Path 를 가리킬 수 있다. 한 장 안에서는 중복이 없다.
    images: list[Path] = field(default_factory=list)
    # 뽑지 못한 벡터 구조 후보. pipeline 은 읽지 않고 리포트가 쓴다.
    vector_regions: list[VectorRegion] = field(default_factory=list)


@dataclass(frozen=True)
class Figure:
    """내용이 같은 그림 하나와, 그것이 나온 장들.

    같은 클립아트가 46 장에 나오면 Figure 는 하나이고 slides 가 46 개다.
    인식기는 path 를 **한 번만** 보면 되고, 나온 판정은 slides 의 장마다 붙는다.
    장 단위로 돌면 46 번 도는 자리다.
    """

    path: Path
    slides: tuple[int, ...]


def figures(slides: list[Slide]) -> list[Figure]:
    """장 목록을 그림 단위로 뒤집는다. 처음 나온 순서를 지킨다.

    같은 내용은 이미 `load()` 가 한 파일로 모아 두었으므로 여기서는 경로로
    묶으면 충분하다 - 다시 해시하려고 64MB 를 읽지 않는다.
    """
    where: dict[Path, list[int]] = {}
    for slide in slides:
        for image in slide.images:
            where.setdefault(image, []).append(slide.index)
    return [Figure(path, tuple(indexes)) for path, indexes in where.items()]


class _FigureStore:
    """내용이 같은 그림을 한 파일로 모은다. 덱 하나마다 새로 만든다.

    파일 이름은 **처음 나온 자리**로 짓는다. 내용 해시로 이름을 지으면 사람이
    읽을 수 없게 되고, 손으로 라벨을 붙여 둔 쪽(bench 의 매니페스트)이 통째로
    끊긴다. 두 번째 장부터는 파일을 쓰지 않고 먼저 쓴 경로를 그대로 돌려준다.
    """

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._by_digest: dict[str, Path] = {}

    def put(self, blob: bytes, name: str) -> Path:
        digest = hashlib.sha256(blob).hexdigest()
        known = self._by_digest.get(digest)
        if known is not None:
            return known
        dest = self._out_dir / name
        dest.write_bytes(blob)
        self._by_digest[digest] = dest
        return dest


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


def _render_region(page, width: float, height: float, box: Box) -> bytes | None:
    """페이지의 한 영역만 그려서 png 바이트로 준다. 못 그리면 None.

    파일로 바로 쓰지 않는다. 같은 그림인지는 바이트를 봐야 알고, 같으면 쓸
    필요가 없다. 어디에 쓸지는 `_FigureStore` 가 정한다.
    """
    left = max(0.0, box[0] - REGION_PAD_PT)
    bottom = max(0.0, box[1] - REGION_PAD_PT)
    right = min(width, box[2] + REGION_PAD_PT)
    top = min(height, box[3] + REGION_PAD_PT)
    if right - left <= 0 or top - bottom <= 0:
        return None

    # pypdfium2 의 crop 은 각 변에서 잘라낼 여백을 받는다.
    crop = (left, bottom, width - right, height - top)
    try:
        image = page.render(scale=RENDER_SCALE, crop=crop).to_pil()
    except Exception:
        return None
    if min(image.size) < MIN_IMAGE_SIDE:
        return None
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
    store = _FigureStore(out_dir)
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
                # 순서 있는 집합. 한 쪽 안에서 같은 그림을 두 번 내지 않는다.
                found: dict[Path, None] = {}
                for img_no, box in enumerate(sorted(regions), start=1):
                    blob = _render_region(page, width, height, box)
                    if blob is None:
                        continue
                    found[store.put(blob, f"p{page_no:03d}_{img_no:02d}.png")] = None
                slide.images.extend(found)
            slides.append(slide)
    finally:
        document.close()
    return slides


# --------------------------------------------------------------------- PPTX


def _flatten(shapes, group=None):
    """그룹 안까지 평탄화한다. (도형, 최상위 그룹 도형) 을 낸다.

    강의자료의 그림은 대개 설명 상자와 함께 그룹으로 묶여 있다.
    그룹을 안 들어가면 그 장은 통째로 그림 0개가 된다.

    최상위 그룹을 함께 내는 이유: 그룹 안 도형의 좌표는 그룹의 자식
    좌표계라 슬라이드 좌표로 바로 못 쓴다. 그룹 자신의 좌표는 슬라이드
    좌표이므로 자리를 잡을 때는 그쪽을 쓴다.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _flatten(shape.shapes, group if group is not None else shape)
        else:
            yield shape, group


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


def _shape_box(shape, width: int, height: int) -> Box | None:
    """도형의 자리를 슬라이드 크기 대비 비율로 준다. 좌표가 없으면 None."""
    if not width or not height:
        return None
    try:
        left, top, size_x, size_y = shape.left, shape.top, shape.width, shape.height
    except (AttributeError, ValueError):
        return None
    if None in (left, top, size_x, size_y):
        return None
    return (
        left / width,
        top / height,
        (left + size_x) / width,
        (top + size_y) / height,
    )


def _union(boxes: list[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# LibreOffice 는 Windows 와 macOS 에서 설치해도 PATH 에 등록되지 않는다.
# which 만 믿으면 설치돼 있는데도 못 찾아 조용히 렌더를 건너뛴다.
_CONVERTER_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
)


def _find_converter() -> str | None:
    """LibreOffice 실행 파일을 찾는다. 없으면 None.

    환경변수, PATH, 표준 설치 위치 순으로 본다. 설치 관리자가 PATH 를
    건드리지 않는 플랫폼이 있어서 PATH 만 보면 설치된 것도 놓친다.
    """
    import os
    import shutil

    override = os.environ.get("CHEMCHECK_SOFFICE")
    if override and Path(override).exists():
        return override
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _CONVERTER_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _pptx_to_pdf(path: Path, out_dir: Path) -> Path | None:
    """LibreOffice 로 pptx 를 pdf 로 바꾼다. 없거나 실패하면 None.

    도형으로 그린 구조를 이미지로 만들려면 실제로 그려 주는 프로그램이
    있어야 한다. python-pptx 에는 렌더 기능이 없다. 변환된 pdf 에서는
    그 구조가 벡터 경로가 되므로 PDF 쪽 경로를 그대로 재사용한다.

    없으면 조용히 포기한다. 구조를 못 뽑았다는 사실은 vector_regions 에
    남으므로 침묵이 되지는 않는다.
    """
    import subprocess

    soffice = _find_converter()
    if soffice is None:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=PPTX_CONVERT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    converted = out_dir / f"{path.stem}.pdf"
    return converted if converted.exists() else None


def _fill_vector_images(
    deck_pdf: Path, slides: list[Slide], store: _FigureStore
) -> None:
    """변환된 pdf 에서 구조 자리를 오려내 각 region.image 를 채운다.

    슬라이드 n 장은 pdf n 쪽으로 1:1 대응한다. 자리는 이미 비율로 알고
    있으므로 다시 찾지 않고 그대로 오려낸다.

    임베드 그림과 같은 store 를 쓴다. 도형으로 그린 같은 구조가 여러 장에
    되풀이되면 - 강의자료에서 흔하다 - 그것도 한 파일이어야 한다.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(deck_pdf))
    try:
        for slide in slides:
            index = slide.index - 1
            if index >= len(document):
                continue
            page = document[index]
            width, height = page.get_size()
            seen = set(slide.images)
            for region_no, region in enumerate(slide.vector_regions, start=1):
                # 비율(왼쪽 위 기준) -> 포인트(왼쪽 아래 기준)
                box = (
                    region.box[0] * width,
                    (1.0 - region.box[3]) * height,
                    region.box[2] * width,
                    (1.0 - region.box[1]) * height,
                )
                blob = _render_region(page, width, height, box)
                if blob is None:
                    continue
                dest = store.put(blob, f"s{slide.index:03d}_v{region_no:02d}.png")
                region.image = dest
                if dest not in seen:
                    seen.add(dest)
                    slide.images.append(dest)
    finally:
        document.close()


def from_pptx(path: Path, out_dir: Path, render_vectors: bool = True) -> list[Slide]:
    """render_vectors 를 끄면 도형 구조를 찾기만 하고 그리지는 않는다.

    그리는 데 외부 프로그램을 띄우므로 장당 수 초가 걸린다. 자리만
    알면 되는 곳에서는 끄는 편이 낫다.
    """
    from pptx import Presentation

    out_dir.mkdir(parents=True, exist_ok=True)
    store = _FigureStore(out_dir)
    slides: list[Slide] = []
    deck = Presentation(str(path))
    deck_w, deck_h = deck.slide_width, deck.slide_height

    for slide_no, raw in enumerate(deck.slides, start=1):
        texts: list[str] = []
        # 순서 있는 집합. 한 장 안에서 같은 그림을 두 번 내지 않는다 -
        # 판정이 같은데 두 번 세어질 뿐이다.
        images: dict[Path, None] = {}
        img_no = 0
        # 그룹별 도형 조각 수. None 키는 그룹에 안 묶인 조각들.
        counts: dict[int | None, int] = {}
        loose: list[Box] = []
        groups: dict[int | None, object] = {}

        for shape, group in _flatten(raw.shapes):
            stripped = _shape_text(shape)
            if stripped:
                texts.append(stripped)

            found = _picture(shape)
            if found is not None:
                blob, ext = found
                if not _keep_blob(blob):
                    continue
                # 번호는 중복이어도 올린다. 그래야 새로 쓰이는 파일의 이름이
                # 중복을 모으기 전과 같아진다 - 손으로 붙인 라벨이 안 끊긴다.
                img_no += 1
                images[store.put(blob, f"s{slide_no:03d}_{img_no:02d}.{ext}")] = None
                continue

            if not _is_vector_part(shape):
                continue

            key = id(group) if group is not None else None
            groups.setdefault(key, group)
            counts[key] = counts.get(key, 0) + 1
            if group is None:
                # 그룹 안 도형의 좌표는 자식 좌표계라 못 쓴다. 낱개 조각만
                # 스스로의 자리를 쓰고, 묶인 것은 그룹의 자리를 쓴다.
                box = _shape_box(shape, deck_w, deck_h)
                if box is not None:
                    loose.append(box)

        regions: list[VectorRegion] = []
        for key, count in counts.items():
            if count < MIN_PPTX_VECTOR_SHAPES:
                continue
            group = groups[key]
            if group is not None:
                box = _shape_box(group, deck_w, deck_h)
            else:
                box = _union(loose) if loose else None
            if box is None:
                continue
            regions.append(VectorRegion(slide_no, count, group is not None, box))

        slides.append(Slide(slide_no, "\n".join(texts), list(images), regions))

    if render_vectors and any(slide.vector_regions for slide in slides):
        converted = _pptx_to_pdf(path, out_dir / "_converted")
        if converted is not None:
            _fill_vector_images(converted, slides, store)

    return slides


def load(path: Path, out_dir: Path) -> list[Slide]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return from_pdf(path, out_dir)
    if suffix == ".pptx":
        return from_pptx(path, out_dir)
    raise ValueError(f"지원하지 않는 형식: {suffix} (pdf 또는 pptx만 가능)")
