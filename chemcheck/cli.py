"""명령줄 진입점:  python -m chemcheck 슬라이드.pdf"""
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
        reasons = Counter(_bucket(f.reason)
                          for r in results for _, f in r.findings
                          if f.verdict is Verdict.ABSTAIN)
        out.append("")
        out.append(f"  {MARK[Verdict.ABSTAIN]} {silent:4d}")
        for reason, n in reasons.most_common():
            out.append(f"        {n:4d}  {reason}")

    if not judged:
        out.append("")
        out.append("  판정한 것이 없습니다. 위 사유를 해결하기 전까지 이 결과는")
        out.append("  '문제 없음'이 아니라 '보지 못함'입니다.")
    return out


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
        for line in LAST_DIAGNOSTICS:
            print(f"      {line}")
        print("      bash scripts/fetch_decimer.sh 로 가중치를 먼저 받으세요.")
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

    for line in summary_lines(results):
        print(line)

    counts = summarize(results)
    return 1 if counts[Verdict.ERROR] else 0


if __name__ == "__main__":
    raise SystemExit(main())
