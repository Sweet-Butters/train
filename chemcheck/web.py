"""브라우저에서 쓰는 검사 화면.

파일을 올리면 장별 판정과 함께, 이름이 가리키는 구조와 그림에서 읽은 구조를
나란히 놓고 차이를 칠해 보여준다.

표준 라이브러리만 쓴다. 행사장 회선이 끊겨도 뜨도록 외부 CDN을 부르지 않고
스타일과 그림을 전부 페이지 안에 담는다.
"""
from __future__ import annotations

import base64
import html
import re
import tempfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import render
from .ocsr import load_engines
from .pipeline import SlideResult, run, summarize
from .verdict import Finding, Verdict

MAX_UPLOAD = 64 << 20
ACCEPTED = (".pdf", ".pptx")

LABEL = {
    Verdict.OK: ("일치", "ok"),
    Verdict.ERROR: ("오류", "error"),
    Verdict.WARN: ("주의", "warn"),
    Verdict.ABSTAIN: ("판정 불가", "abstain"),
}

STYLE = """
:root{--bg:#f6f7f9;--fg:#16181d;--muted:#666e7a;--line:#dfe3e8;--card:#fff;
--ok:#1f8b4c;--error:#c92a2a;--warn:#b8860b;--abstain:#5a6472}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8eaed;--muted:#9aa3ae;
--line:#2c3138;--card:#1c1f25;--ok:#4caf7d;--error:#ff6b6b;--warn:#d9a441;--abstain:#8b95a3}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 4px} .sub{color:var(--muted);margin:0 0 28px}
.drop{background:var(--card);border:2px dashed var(--line);border-radius:12px;
padding:44px 24px;text-align:center}
.drop input{margin:14px 0 18px}
button{background:#2f6fed;color:#fff;border:0;border-radius:8px;
padding:11px 26px;font-size:15px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--warn);
border-radius:8px;padding:12px 16px;margin:0 0 20px;color:var(--muted);font-size:14px}
.tally{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 26px}
.tally span{background:var(--card);border:1px solid var(--line);border-radius:999px;
padding:5px 14px;font-size:13px}
.slide{margin:0 0 30px}
.slide h2{font-size:14px;color:var(--muted);font-weight:600;margin:0 0 10px;
padding-bottom:8px;border-bottom:1px solid var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px;margin:0 0 14px}
.head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.badge{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;color:#fff}
.badge.ok{background:var(--ok)}.badge.error{background:var(--error)}
.badge.warn{background:var(--warn)}.badge.abstain{background:var(--abstain)}
.why{color:var(--muted);font-size:14px;margin:0 0 14px}
.panes{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.pane{text-align:center}
.pane .cap{font-size:12px;color:var(--muted);margin-bottom:6px}
.art{background:#fff;border:1px solid var(--line);border-radius:8px;padding:6px;
min-height:120px;display:flex;align-items:center;justify-content:center;overflow:auto}
.art img{max-width:100%;height:auto}
.miss{color:var(--muted);font-size:13px}
.legend{font-size:12px;color:var(--muted);margin-top:10px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;
background:#EF4444;vertical-align:middle;margin-right:5px}
.cands{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.cands .cap{font-size:13px;margin-bottom:8px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
color:var(--muted);word-break:break-all}
"""

PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>chemcheck</title><style>{style}</style>
<body><div class="wrap">
<h1>chemcheck</h1>
<p class="sub">슬라이드 속 화학 구조가 옆에 적힌 이름과 맞는지 검사합니다. 판정에 LLM을 쓰지 않습니다.</p>
{body}
</div></body></html>"""

FORM = """<form method="post" action="/check" enctype="multipart/form-data">
<div class="drop">
  <div>검사할 <b>.pptx</b> 또는 <b>.pdf</b> 파일을 고르세요</div>
  <input type="file" name="deck" accept=".pptx,.pdf" required>
  <div><button type="submit">검사</button></div>
</div></form>"""


@dataclass
class Engines:
    """인식기는 무겁다. 요청마다 다시 만들지 않는다."""
    checkpoint: Path | None
    _loaded: list | None = None

    def get(self) -> list:
        if self._loaded is None:
            self._loaded = load_engines(self.checkpoint)
        return self._loaded


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _art(svg: str, fallback: str) -> str:
    return f'<div class="art">{svg}</div>' if svg else f'<div class="art"><span class="miss">{fallback}</span></div>'


def _finding_html(image: Path, finding: Finding) -> str:
    text, css = LABEL[finding.verdict]
    ref_mol = render.from_inchi(finding.reference.inchi if finding.reference else None)
    pred_mol = render.from_smiles(_first_smiles(finding))
    ref_svg, pred_svg = render.diff(ref_mol, pred_mol)

    named = _escape(finding.reference.name) if finding.reference else "이름 없음"
    parts = [
        '<div class="card">',
        f'<div class="head"><span class="badge {css}">{text}</span>'
        f'<code>{_escape(image.name)}</code></div>',
        f'<p class="why">{_escape(finding.reason)}</p>',
        '<div class="panes">',
        f'<div class="pane"><div class="cap">슬라이드의 그림</div>'
        f'<div class="art"><img src="{_data_uri(image)}" alt=""></div></div>',
        f'<div class="pane"><div class="cap">이름이 가리키는 구조 — {named}</div>'
        + _art(ref_svg, "이름을 해석하지 못함") + "</div>",
        '<div class="pane"><div class="cap">그림에서 읽은 구조</div>'
        + _art(pred_svg, "구조를 읽지 못함") + "</div>",
        "</div>",
    ]
    if ref_svg and pred_svg:
        parts.append('<p class="legend"><span class="dot"></span>칠해진 곳이 두 구조가 다른 부분입니다.</p>')
    parts.append(_candidates_html(finding))
    parts.append("</div>")
    return "".join(parts)


def _first_smiles(finding: Finding) -> str | None:
    return finding.predictions[0].smiles if finding.predictions else None


def _candidates_html(finding: Finding) -> str:
    """인식 결과가 갈렸을 때 후보를 전부 보여준다. 하나를 골라 단정하지 않는다."""
    distinct: dict[str, list[str]] = {}
    for pred in finding.predictions:
        distinct.setdefault(pred.smiles, []).append(pred.engine)
    if len(distinct) < 2:
        return ""
    cards = []
    for smiles, engines in distinct.items():
        svg = render.draw(render.from_smiles(smiles))
        cards.append(
            f'<div class="pane"><div class="cap">{_escape(", ".join(engines))}</div>'
            + _art(svg, "해석 불가") + "</div>"
        )
    return (
        '<div class="cands"><div class="cap">인식 결과가 갈렸습니다 — 원본 그림과 대조해 고르세요</div>'
        f'<div class="panes">{"".join(cards)}</div></div>'
    )


def _results_html(results: list[SlideResult], filename: str, engines: list) -> str:
    counts = summarize(results)
    out = []
    if not engines:
        out.append(
            '<p class="note">구조 인식기(OCSR)가 없어 그림 판정은 모두 보류됩니다. '
            "이름 추출과 참조 구조는 정상 동작합니다.</p>"
        )
    out.append(f'<p class="sub"><b>{_escape(filename)}</b> 검사 결과</p>')
    out.append('<div class="tally">' + "".join(
        f"<span>{LABEL[v][0]} <b>{counts[v]}</b></span>" for v in Verdict
    ) + "</div>")
    for result in results:
        out.append('<div class="slide">')
        names = ", ".join(r.name for r in result.references) or "이름 없음"
        out.append(f"<h2>{result.slide.index}장 · {_escape(names)}</h2>")
        if not result.findings:
            out.append('<div class="card"><p class="why">구조 그림이 없습니다.</p></div>')
        for image, finding in result.findings:
            out.append(_finding_html(image, finding))
        out.append("</div>")
    out.append(FORM)
    return "".join(out)


def _parse_upload(body: bytes, content_type: str) -> tuple[str, bytes] | None:
    """multipart/form-data에서 파일 한 개를 꺼낸다 (cgi 모듈은 3.13에서 사라졌다)."""
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not match:
        return None
    boundary = b"--" + (match.group(1) or match.group(2)).strip().encode()
    for part in body.split(boundary):
        header, sep, data = part.partition(b"\r\n\r\n")
        if not sep or b"filename=" not in header:
            continue
        name = re.search(rb'filename="([^"]*)"', header)
        if not name:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        return Path(name.group(1).decode("utf-8", "replace")).name, data
    return None


def make_handler(engines: Engines):
    class Handler(BaseHTTPRequestHandler):
        server_version = "chemcheck"

        def log_message(self, fmt, *args):  # 요청 로그는 화면을 어지럽힌다
            pass

        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _page(self, inner: str, status: int = 200) -> None:
            self._send(PAGE.format(style=STYLE, body=inner), status)

        def do_GET(self) -> None:
            if self.path not in ("/", "/index.html"):
                self._page('<p class="note">없는 주소입니다.</p>' + FORM, 404)
                return
            self._page(FORM)

        def do_POST(self) -> None:
            if self.path != "/check":
                self._page('<p class="note">없는 주소입니다.</p>' + FORM, 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD:
                self._page(f'<p class="note">파일이 없거나 너무 큽니다 (최대 {MAX_UPLOAD >> 20}MB).</p>' + FORM, 413)
                return
            found = _parse_upload(self.rfile.read(length), self.headers.get("Content-Type", ""))
            if found is None:
                self._page('<p class="note">업로드를 읽지 못했습니다.</p>' + FORM, 400)
                return
            filename, blob = found
            if not filename.lower().endswith(ACCEPTED):
                self._page('<p class="note">.pptx 또는 .pdf만 검사할 수 있습니다.</p>' + FORM, 415)
                return
            with tempfile.TemporaryDirectory() as tmp:
                deck = Path(tmp) / filename
                deck.write_bytes(blob)
                try:
                    loaded = engines.get()
                    results = run(deck, Path(tmp) / "work", loaded)
                except Exception as exc:  # 한 파일이 죽어도 서버는 살아 있어야 한다
                    self._page(f'<p class="note">검사 중 오류: {_escape(type(exc).__name__)}: {_escape(str(exc)[:200])}</p>' + FORM, 500)
                    return
                self._page(_results_html(results, filename, loaded))

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000, checkpoint: Path | None = None) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(Engines(checkpoint)))
    print(f"chemcheck 웹 화면: http://{host}:{port}  (Ctrl+C 로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    from .cli import DEFAULT_CHECKPOINT

    parser = argparse.ArgumentParser(prog="chemcheck.web", description="chemcheck 웹 화면")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--molscribe", type=Path, default=None)
    args = parser.parse_args()
    checkpoint = args.molscribe or DEFAULT_CHECKPOINT
    serve(args.host, args.port, checkpoint if checkpoint.exists() else None)
