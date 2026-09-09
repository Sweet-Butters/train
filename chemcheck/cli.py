"""명령줄 진입점:  python -m chemcheck 슬라이드.pdf"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .ocsr import load_engines
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chemcheck",
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
        if not checkpoint.exists():
            print(f"      가중치가 없습니다: {checkpoint}")
            print("      python scripts/fetch_models.py 로 먼저 받으세요.")
        print("      그림 판정은 전부 '판정불가'로 나옵니다. 이름 추출은 정상 동작합니다.\n")
    else:
        print(f"인식기: {', '.join(e.name for e in engines)}\n")

    with tempfile.TemporaryDirectory() as tmp:
        work = args.keep or Path(tmp)
        results = run(args.file, work, engines, checkpoint)

    for r in results:
        names = ", ".join(f"{ref.name}" for ref in r.references) or "(이름 없음)"
        print(f"── {r.slide.index}장  이름: {names}")
        if not r.slide.images:
            print("     그림 없음")
        for image, finding in r.findings:
            print(f"     {MARK[finding.verdict]} {image.name}  {finding.reason}")
        print()

    counts = summarize(results)
    print(f"요약  일치 {counts[Verdict.OK]} · 오류 {counts[Verdict.ERROR]} · "
          f"주의 {counts[Verdict.WARN]} · 판정불가 {counts[Verdict.ABSTAIN]}")
    return 1 if counts[Verdict.ERROR] else 0


if __name__ == "__main__":
    raise SystemExit(main())
