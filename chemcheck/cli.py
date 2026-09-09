"""명령줄 진입점.

    python -m chemcheck recognize 그림.png [그림2.png ...] [--out 폴더]   그림 -> SMILES
    python -m chemcheck draw "아스피린" [--out 폴더]                     이름 -> 구조
    python -m chemcheck check 슬라이드.pdf                                (기존) 슬라이드 검사

부명령 없이 파일만 주면 check 로 본다 - 기존 사용법을 깨지 않는다.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter
from pathlib import Path

from .ocsr import LAST_DIAGNOSTICS, load_engines
from .pipeline import run, summarize
from .verdict import Verdict

# scripts/fetch_models.py 가 내려받는 기본 위치
DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[1] / "models" / "molscribe.pth"


MARK = {
    Verdict.OK: "[ 일치 ]",
    Verdict.ERROR: "[ 오류 ]",
    Verdict.WARN: "[ 주의 ]",
    Verdict.ABSTAIN: "[판정불가]",
}


# verdict.py 의 보류 사유는 자유 문장이라(신뢰도 값·엔진 이름이 박혀 있다) 그대로
# 세면 전부 1건씩 흩어진다. 여기서 묶는다. verdict.py 는 동결이므로 문구가 바뀌면
# 이 표가 먼저 어긋나야 한다 - 그래서 못 묶은 사유는 감추지 않고 원문 그대로 센다.
SILENCE_BUCKETS = (
    ("구조 인식기를 쓸 수 없음", "인식기를 쓸 수 없음"),
    ("그림 옆에서 화합물 이름을 찾지 못함", "그림 옆에 이름이 없음"),
    ("인식 신뢰도 부족", "인식 신뢰도가 낮음"),
    ("인식기 간 결과 불일치", "인식기끼리 답이 갈림"),
    ("SMILES를 해석할 수 없음", "인식 결과를 구조로 읽지 못함"),
    ("인식 결과 없음", "인식기가 아무것도 내지 못함"),
)


def _bucket(reason: str) -> str:
    for needle, label in SILENCE_BUCKETS:
        if needle in reason:
            return label
    return reason


def document_note(results: list) -> list[str]:
    """문서 수준의 진단. 장별 사유로는 말할 수 없는 것을 말한다.

    보류 사유는 장 단위로 붙는데, 원인이 문서 전체에 있으면 그 사유가 사람을
    속인다. 텍스트 레이어가 없는 PDF 에서 '그림 옆에서 이름을 찾지 못함'은
    거짓이다 - 이름은 그림 옆에 있고, 다만 픽셀이라 읽지 못한 것이다. 그대로
    두면 사용자는 멀쩡히 적혀 있는 이름을 찾으러 간다.
    """
    images = sum(len(r.slide.images) for r in results)
    if not images:
        return []
    text_chars = sum(len(r.slide.text.strip()) for r in results)
    named = sum(1 for r in results if r.references)

    if text_chars == 0:
        return [
            "  이 문서에는 글자가 하나도 없습니다 - 텍스트 레이어가 없는 PDF입니다.",
            "  이름이 그림 안에 픽셀로만 있어 읽을 수 없습니다. 슬라이드에 이름이",
            "  적혀 있어도 마찬가지입니다.",
            "  원본 PPTX 가 있으면 그것을 넣으십시오. 없으면 이 문서는 지금 형태로는",
            "  검사할 수 없습니다 (이름을 읽으려면 OCR 이 필요한데 이 도구에는 없습니다).",
        ]
    if named == 0:
        return [
            "  글자는 읽었으나 PubChem 이 화합물로 인정한 이름이 하나도 없습니다.",
            "  자료에 이름이 있는데도 이렇다면 이름 해석 쪽 문제일 수 있습니다.",
        ]
    return []


def summary_lines(results: list) -> list[str]:
    """요약. 판정률이 먼저 온다.

    '일치 3 · 오류 1 · 판정불가 12' 처럼 나란히 적으면 이 도구가 16건 중 4건만
    실제로 봤다는 사실이 묻힌다. 침묵하는 검사기는 오류 0건을 공짜로 얻는다.
    그러므로 몇 건을 판정했는지를 먼저 말하고, 왜 침묵했는지를 모아서 보여준다.
    """
    counts = summarize(results)
    total = sum(counts.values())
    silent = counts[Verdict.ABSTAIN]
    judged = total - silent

    out = ["", "─" * 46]
    if not total:
        out.append("검사할 그림이 없습니다.")
        return out

    out.append(f"그림 {total}건 중 {judged}건을 판정했습니다"
               f"  (판정률 {judged / total * 100:.1f}%)")
    out.append("")
    for verdict in (Verdict.OK, Verdict.ERROR, Verdict.WARN):
        out.append(f"  {MARK[verdict]} {counts[verdict]:4d}")

    if silent:
        images = sum(len(r.slide.images) for r in results)
        no_text = images > 0 and sum(len(r.slide.text.strip()) for r in results) == 0
        reasons = Counter(
            "이름을 읽을 수 없음 (문서에 텍스트 레이어가 없음)" if no_text else _bucket(f.reason)
            for r in results for _, f in r.findings
            if f.verdict is Verdict.ABSTAIN)
        out.append("")
        out.append(f"  {MARK[Verdict.ABSTAIN]} {silent:4d}")
        for reason, n in reasons.most_common():
            out.append(f"        {n:4d}  {reason}")
        note = document_note(results)
        if note:
            out.append("")
            out.extend(note)

    if not judged:
        out.append("")
        out.append("  판정한 것이 없습니다. 위 사유를 해결하기 전까지 이 결과는")
        out.append("  '문제 없음'이 아니라 '보지 못함'입니다.")
    return out


def check_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="chemcheck check",
        description="슬라이드·PDF 속 화학 구조 그림이 옆에 적힌 이름과 맞는지 검사합니다.",
    )
    parser.add_argument("file", type=Path, help="검사할 .pdf 또는 .pptx")
    parser.add_argument("--keep", type=Path, default=None, help="추출한 그림을 남길 폴더")
    parser.add_argument(
        "--molscribe",
        type=Path,
        default=None,
        help=f"MolScribe 체크포인트 경로 (기본: {DEFAULT_CHECKPOINT})",
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"파일이 없습니다: {args.file}", file=sys.stderr)
        return 2

    checkpoint = args.molscribe or DEFAULT_CHECKPOINT
    engines = load_engines(checkpoint if checkpoint.exists() else None)
    if not engines:
        print("경고: 구조 인식기(OCSR)를 쓸 수 없습니다.")
        for line in LAST_DIAGNOSTICS:
            print(f"      {line}")
        print("      bash scripts/fetch_decimer.sh 로 가중치를 먼저 받으세요.")
        print("      그림 판정은 전부 '판정불가'로 나옵니다. 이름 추출은 정상 동작합니다.\n")
    else:
        print(f"인식기: {', '.join(e.name for e in engines)}\n")

    with tempfile.TemporaryDirectory() as tmp:
        work = args.keep or Path(tmp)
        results = run(args.file, work, engines, checkpoint)

    # 문서에 글자가 하나도 없으면 '이름을 찾지 못함'은 거짓이다. 이름은 그림 옆에
    # 있고 우리가 못 읽은 것이다. verdict.py 는 동결이므로 보여줄 때 바로잡는다.
    no_text = (sum(len(r.slide.images) for r in results) > 0
               and sum(len(r.slide.text.strip()) for r in results) == 0)

    for r in results:
        names = ", ".join(f"{ref.name}" for ref in r.references) or "(이름 없음)"
        print(f"── {r.slide.index}장  이름: {names}")
        if not r.slide.images:
            print("     그림 없음")
        for image, finding in r.findings:
            reason = finding.reason
            if no_text and finding.verdict is Verdict.ABSTAIN:
                reason = "이름을 읽을 수 없음 (문서에 텍스트 레이어가 없음)"
            print(f"     {MARK[finding.verdict]} {image.name}  {reason}")
        print()

    for line in summary_lines(results):
        print(line)

    counts = summarize(results)
    return 1 if counts[Verdict.ERROR] else 0

# ── 양방향 구조 도구 ────────────────────────────────────────────────────────

STATUS_MARK = {
    "agreed": "[ 합의 ]",
    "uncertain": "[확신없음]",
    "single": "[확신없음]",
    "no_result": "[결과없음]",
    "no_engine": "[인식기없음]",
    "unreadable": "[읽기실패]",
}


def _fmt_conf(value: float) -> str:
    return "nan" if value != value else f"{value:.3f}"


def _load_engines_or_explain(checkpoint: Path) -> list:
    engines = load_engines(checkpoint if checkpoint.exists() else None)
    if not engines:
        print("인식기 없음: 설치된 구조 인식기(OCSR)가 없습니다.")
        for line in LAST_DIAGNOSTICS:
            print(f"      {line}")
        print("      bash scripts/setup_ocsr.sh 와 scripts/fetch_models.py 로 인식기를 먼저 세우세요.")
    elif len(engines) == 1:
        print(f"인식기: {engines[0].name}  (하나뿐 - 합의를 볼 수 없어 답은 전부 '확신 없음'으로 나옵니다)")
    else:
        print(f"인식기: {', '.join(e.name for e in engines)}")
    return engines


def recognition_lines(rec) -> list[str]:
    """recognize 결과 한 건을 사람에게 보여준다. 엔진별 원문·정규화·키·신뢰도, 그 다음 결론."""
    out = [f"── {rec.image.name}"]
    for note in rec.notes:
        out.append(f"   입력  {note}")
    for warning in rec.warnings:
        out.append(f"   경고  {warning}")
    for r in rec.results:
        out.append(f"   {r.engine}")
        out.append(f"      원 SMILES  {r.raw_smiles}")
        out.append(f"      canonical  {r.canonical or '(구조로 읽지 못함)'}")
        out.append(f"      InChIKey   {r.inchikey or '-'}")
        out.append(f"      신뢰도     {_fmt_conf(r.confidence)}")
    out.append(f"   {STATUS_MARK[rec.status]} {rec.reason}")
    answer = rec.answer
    if answer is not None:
        out.append(f"      SMILES    {answer.smiles}")
        out.append(f"      InChIKey  {answer.inchikey}")
        out.append(f"      신뢰도    {_fmt_conf(answer.confidence)}")
        if answer.image:
            out.append(f"      다시 그림  {answer.image}")
    elif rec.candidates:
        for i, c in enumerate(rec.candidates, start=1):
            out.append(f"      후보 {i} ({'+'.join(c.engines)})  신뢰도 {_fmt_conf(c.confidence)}")
            out.append(f"         SMILES    {c.smiles}")
            out.append(f"         InChIKey  {c.inchikey}")
            if c.image:
                out.append(f"         다시 그림  {c.image}")
        out.append("      후보 중 하나를 답으로 고르지 않습니다. 그림을 보고 사람이 정하세요.")
    return out


def recognize_main(argv: list[str]) -> int:
    from .structure import recognize

    parser = argparse.ArgumentParser(
        prog="chemcheck recognize",
        description="구조 그림을 SMILES 로 읽습니다. 인식기 둘이 합의해야 답입니다.",
    )
    parser.add_argument("images", nargs="+", type=Path, help="구조 그림 (png/jpg)")
    parser.add_argument("--out", type=Path, default=None,
                        help="다시 그린 그림을 남길 폴더 (기본: 그림과 같은 폴더)")
    parser.add_argument("--molscribe", type=Path, default=None,
                        help=f"MolScribe 체크포인트 (기본: {DEFAULT_CHECKPOINT})")
    args = parser.parse_args(argv)

    missing = [p for p in args.images if not p.exists()]
    if missing:
        for p in missing:
            print(f"파일이 없습니다: {p}", file=sys.stderr)
        return 2

    engines = _load_engines_or_explain(args.molscribe or DEFAULT_CHECKPOINT)
    print()
    worst = 0
    for image in args.images:
        out_dir = args.out or image.parent
        rec = recognize(image, engines, out_dir)
        for line in recognition_lines(rec):
            print(line)
        print()
        worst = max(worst, {"agreed": 0, "uncertain": 1, "single": 1,
                            "no_result": 1, "unreadable": 2, "no_engine": 3}[rec.status])
    return worst


def draw_main(argv: list[str]) -> int:
    from .structure import draw, lookup_name

    parser = argparse.ArgumentParser(
        prog="chemcheck draw",
        description="화합물 이름으로 PubChem 정본 구조를 그립니다. 추측하지 않습니다.",
    )
    parser.add_argument("name", help='화합물 이름 (예: "아스피린", "ibuprofen", "vitamin C")')
    parser.add_argument("--out", type=Path, default=Path("."), help="PNG 를 남길 폴더 (기본: 현재 폴더)")
    args = parser.parse_args(argv)

    drawn = draw(args.name, args.out)
    if drawn is None:
        looked = lookup_name(args.name)
        print(f"찾지 못함: '{args.name}'", end="")
        if looked != args.name:
            print(f" ('{looked}' 로 조회)", end="")
        print()
        print("      PubChem 이 이 이름을 화합물로 해석하지 못했습니다 (또는 망이 없고 동봉 표에도 없음).")
        print("      비슷한 이름을 추측해 그리지 않습니다. 영문 이름이나 IUPAC 이름으로 다시 시도하세요.")
        return 1

    print(f"이름      {drawn.query}" + (f"  ({drawn.name})" if drawn.name != drawn.query else ""))
    print(f"출처      {drawn.source}")
    print(f"분자식    {drawn.formula}")
    print(f"SMILES    {drawn.smiles}")
    if drawn.canonical != drawn.smiles:
        print(f"canonical {drawn.canonical}")
    print(f"InChIKey  {drawn.inchikey}")
    print(f"그림      {drawn.image}")
    return 0


SUBCOMMANDS = {"recognize": recognize_main, "draw": draw_main, "check": check_main}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return SUBCOMMANDS[argv[0]](argv[1:])
    if argv and argv[0] not in ("-h", "--help"):
        return check_main(argv)  # 기존 사용법: python -m chemcheck 슬라이드.pdf
    print(__doc__.strip())
    return 0 if argv else 2


if __name__ == "__main__":
    raise SystemExit(main())
