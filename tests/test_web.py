"""웹 화면 검증.

인식기 없이도 화면이 뜨는지, 판정이 화면에 제대로 옮겨지는지,
그리고 외부 리소스를 부르지 않는지 본다. 마지막 항목이 중요하다 —
행사장 회선이 끊기면 CDN을 부르는 페이지는 뜨지 않는다.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import web  # noqa: E402
from chemcheck.pipeline import run  # noqa: E402
from scripts.make_demo import CASES, build  # noqa: E402
from tests.test_pipeline import OracleEngine  # noqa: E402


def _rendered() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        deck = build(Path(tmp))
        oracle = OracleEngine({i: s for i, (_, s, _) in enumerate(CASES, start=1)})
        results = run(deck, Path(tmp) / "work", [oracle])
        return web._results_html(results, "demo_slides.pptx", [oracle])


def test_verdicts_reach_the_screen() -> None:
    """심어둔 오류 2건이 화면에서도 '오류'로 보여야 한다."""
    html = _rendered()
    assert html.count('badge ok') == 2, "일치 2건이 아님"
    assert html.count('badge error') == 2, "오류 2건이 아님"
    assert html.count("data:image/") == 4, "슬라이드 원본 그림이 빠짐"
    print("  배지 일치 2 · 오류 2, 원본 그림 4장")
    print("통과: 판정이 화면에 그대로 옮겨진다.")


def test_shows_both_structures_and_marks_the_difference() -> None:
    """이름이 가리키는 구조와 그림에서 읽은 구조를 나란히 그리고 차이를 칠해야 한다."""
    html = _rendered()
    assert html.count("<svg") == 8, f"구조 그림이 8개가 아님: {html.count('<svg')}"
    assert web.render.DIFF_COLOR == (0.94, 0.27, 0.27)
    assert "#EF4444" in html.upper(), "차이 표시가 없음"
    print(f"  구조 SVG 8개, 차이 표시 {html.upper().count('#EF4444')}곳")
    print("통과: 무엇이 어떻게 다른지 그림으로 보인다.")


def test_page_calls_nothing_from_outside() -> None:
    """회선이 끊겨도 떠야 하므로 외부 리소스를 부르면 안 된다."""
    page = web.PAGE.format(style=web.STYLE, body=_rendered() + web.FORM)
    external = re.findall(r'(?:src|href)\s*=\s*"(https?://[^"]+)"', page)
    assert not external, f"외부 리소스를 부른다: {external}"
    print("통과: 페이지가 외부를 부르지 않는다.")


def test_upload_parser_reads_the_file() -> None:
    """cgi 모듈 없이 multipart를 직접 읽는다 (3.13에서 사라졌다)."""
    body = (
        b"------X\r\n"
        b'Content-Disposition: form-data; name="deck"; filename="a b.pptx"\r\n'
        b"Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n"
        b"\r\n"
        b"PK\x03\x04payload\r\n"
        b"------X--\r\n"
    )
    got = web._parse_upload(body, "multipart/form-data; boundary=----X")
    assert got == ("a b.pptx", b"PK\x03\x04payload"), got

    traversal = body.replace(b'filename="a b.pptx"', b'filename="../../etc/passwd"')
    name, _ = web._parse_upload(traversal, "multipart/form-data; boundary=----X")
    assert name == "passwd", f"경로가 그대로 통과됨: {name}"

    assert web._parse_upload(body, "multipart/form-data") is None
    print("  파일명·본문 복원, 경로 요소 제거, 경계 없음 거부")
    print("통과: 업로드를 표준 라이브러리만으로 안전하게 읽는다.")


if __name__ == "__main__":
    for test in (
        test_verdicts_reach_the_screen,
        test_shows_both_structures_and_marks_the_difference,
        test_page_calls_nothing_from_outside,
        test_upload_parser_reads_the_file,
    ):
        test()
        print()
