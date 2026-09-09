"""추출 검증.

인식기 없이 돌아간다. 실제 pptx 파일을 만들어서 뽑히는 것과
뽑히지 말아야 할 것을 나눈다.

가장 중요한 것: 그룹 안에 든 그림이 누락되지 않는다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import extract  # noqa: E402
from chemcheck.extract import from_pptx, load  # noqa: E402


# ------------------------------------------------------------------ 픽스처


def _png(dest: Path, side: int) -> Path:
    """실제 png 파일. 노이즈를 넣어 압축돼도 최소 크기를 넘기게 한다."""
    import random

    from PIL import Image

    rng = random.Random(0)
    image = Image.new("RGB", (side, side))
    image.putdata([
        (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        for _ in range(side * side)
    ])
    image.save(dest)
    return dest


def _deck(dest: Path, build) -> Path:
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    build(slide)
    deck.save(str(dest))
    return dest


# --------------------------------------------------------------- PPTX: 그룹


def test_pptx_picture_inside_group_is_found() -> None:
    """그룹 안에 든 그림을 놓치지 않는다.

    강의자료의 구조도는 설명 상자와 함께 묶여 있는 경우가 많다.
    그룹을 안 들어가면 그 장은 통째로 그림 0개가 된다.
    """
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        png = _png(work / "structure.png", 240)

        def build(slide):
            group = slide.shapes.add_group_shape()
            group.shapes.add_picture(str(png), Inches(1), Inches(1), Inches(3), Inches(3))

        deck = _deck(work / "grouped.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert len(slides[0].images) == 1, "그룹 안의 그림을 놓쳤다"


def test_pptx_text_inside_group_is_found() -> None:
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        def build(slide):
            group = slide.shapes.add_group_shape()
            box = group.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
            box.text_frame.text = "Aspirin"

        deck = _deck(work / "grouptext.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert "Aspirin" in slides[0].text


def test_pptx_table_text_is_found() -> None:
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        def build(slide):
            table = slide.shapes.add_table(
                2, 2, Inches(1), Inches(1), Inches(6), Inches(2)
            ).table
            table.cell(0, 0).text = "Caffeine"

        deck = _deck(work / "table.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert "Caffeine" in slides[0].text


def test_pptx_small_image_is_filtered() -> None:
    """아이콘 크기 그림은 구조도가 아니다. 예전에는 바이트 수만 봤다."""
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        icon = _png(work / "icon.png", 32)

        def build(slide):
            slide.shapes.add_picture(str(icon), Inches(1), Inches(1), Inches(1), Inches(1))

        deck = _deck(work / "icon.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert slides[0].images == []


def test_pptx_drawn_structure_is_recorded() -> None:
    """PPT 도형으로 그린 구조는 아직 이미지로 못 만든다.

    그래도 못 뽑았다는 사실은 남긴다. 조용히 0개로 넘어가면
    사용자는 검사가 된 줄 안다.
    """
    from pptx.enum.shapes import MSO_CONNECTOR
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        def build(slide):
            group = slide.shapes.add_group_shape()
            for i in range(6):
                x = Inches(2 + i * 0.3)
                group.shapes.add_connector(
                    MSO_CONNECTOR.STRAIGHT, x, Inches(2), x + Inches(0.3), Inches(2.5)
                )

        deck = _deck(work / "drawn.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert slides[0].images == []
        assert len(slides[0].vector_regions) == 1
        region = slides[0].vector_regions[0]
        assert region.part_count >= extract.MIN_PPTX_VECTOR_SHAPES
        assert region.grouped is True


def test_pptx_few_shapes_are_not_a_structure() -> None:
    """화살표 한두 개를 구조라고 부르면 안 된다."""
    from pptx.enum.shapes import MSO_CONNECTOR
    from pptx.util import Inches

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        def build(slide):
            slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT, Inches(1), Inches(1), Inches(3), Inches(1)
            )

        deck = _deck(work / "arrow.pptx", build)
        slides = from_pptx(deck, work / "out")

        assert slides[0].vector_regions == []


# ------------------------------------------------------------------- 공통


def test_load_rejects_unknown_suffix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        stray = work / "notes.txt"
        stray.write_text("x", encoding="utf-8")

        try:
            load(stray, work / "out")
        except ValueError as err:
            assert "지원하지 않는 형식" in str(err)
        else:
            raise AssertionError("ValueError 를 기대했다")
