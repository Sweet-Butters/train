"""저장된 recognize 인식 결과를 이름 대조 우선 규칙(결정 6·8, verdict.judge)으로
다시 채점한다. 엔진은 다시 돌리지 않는다 - bench/recog.py 가 저장한 reads json
(engine/raw/inchikey/confidence)을 그대로 읽는다. 몇 초면 끝난다.

    python -m bench.rescore_name_first bench/results/recognize_50_20260909.json

무엇을 재현하는가: 50장 세트의 각 항목은 애초에 알려진 화합물을 렌더한 것이다.
그 이름을 아는 사용자가 `check --name <이름> <그림>` 을 썼다고 가정하고, 저장된
Read 를 chemcheck.ocsr.Prediction 으로 되돌려 verdict.judge() 를 그대로 부른다 -
새 채점 로직을 여기서 다시 구현하지 않는다. bench/recog.py 의 옛 두 팔(합의
게이트·신뢰도 게이트)도 같은 원문으로 나란히 다시 채점해 대조한다.

결정 3 의 사망 기준: 이름 대조 팔에서 '자신 있게 틀림'이 0이 아니면 규칙을
기각한다 - 문턱을 완화하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.keys import skeleton  # noqa: E402
from chemcheck.names import Reference  # noqa: E402
from chemcheck.ocsr import Prediction  # noqa: E402
from chemcheck.verdict import Verdict, judge  # noqa: E402

from .recog import Outcome, Read, classify as classify_old, consensus_gate  # noqa: E402


@dataclass(frozen=True)
class NameFirstRow:
    index: int
    name: str
    variant: str
    item_skeleton: str
    finding_verdict: Verdict
    reason: str

    @property
    def outcome(self) -> Outcome:
        if self.finding_verdict is Verdict.ABSTAIN:
            return Outcome.DECLINED
        # OK·WARN 은 골격이 이름과 같다고 판정한 것 - 옛 팔의 CORRECT 와 같은 자리.
        if self.finding_verdict in (Verdict.OK, Verdict.WARN):
            return Outcome.CORRECT
        return Outcome.CONFIDENT_WRONG


def _pct(n: int, total: int) -> str:
    return "  --  " if not total else f"{n / total * 100:5.1f}%"


def rescore(data: dict) -> tuple[list[NameFirstRow], list]:
    """(이름 대조 우선 결과, 옛 합의 게이트 결과) - 같은 원문에서 둘 다 뽑는다."""
    name_first_rows: list[NameFirstRow] = []
    old_rows = []
    for item in data["items"]:
        reads = [Read(r["engine"], r["raw"], r["inchikey"],
                      float("nan") if r["confidence"] is None else float(r["confidence"]))
                 for r in item["reads"]]

        # 새 규칙: verdict.judge() 를 그대로 부른다 - 이름을 아는 사용자가
        # check --name 을 썼다고 가정한다. raw SMILES 를 다시 준다 - judge() 가
        # 스스로 파싱한다(저장된 inchikey 를 지름길로 쓰지 않는다).
        ref = Reference(item["name"], item["inchikey"], "", "bench", "")
        predictions = [Prediction(r.raw, r.confidence, r.engine) for r in reads]
        finding = judge([ref], predictions)
        name_first_rows.append(NameFirstRow(
            item["index"], item["name"], item["variant"], skeleton(item["inchikey"]) or "",
            finding.verdict, finding.reason,
        ))

        # 옛 규칙(합의 게이트) - 같은 원문으로 대조군을 다시 만든다.
        decision = consensus_gate(reads)

        class _Item:  # bench.recog.classify 는 .skeleton 속성만 본다
            skeleton = skeleton(item["inchikey"]) or ""

        old_rows.append((item, reads, decision, classify_old(_Item(), decision)))

    return name_first_rows, old_rows


def report(name_first_rows: list[NameFirstRow], old_rows: list) -> str:
    n = len(name_first_rows)
    nf_cw = sum(1 for r in name_first_rows if r.outcome is Outcome.CONFIDENT_WRONG)
    nf_ok = sum(1 for r in name_first_rows if r.outcome is Outcome.CORRECT)
    nf_dec = sum(1 for r in name_first_rows if r.outcome is Outcome.DECLINED)

    old_cw = sum(1 for _, _, _, o in old_rows if o is Outcome.CONFIDENT_WRONG)
    old_ok = sum(1 for _, _, _, o in old_rows if o is Outcome.CORRECT)
    old_dec = sum(1 for _, _, _, o in old_rows if o is Outcome.DECLINED)

    out = [
        f"재채점 · 그림 {n}장 · 엔진은 다시 돌리지 않음 (저장된 원문에서 규칙만 재적용)",
        "",
        "[이름 대조 우선 (결정 6·8, check --name 의 규칙)]",
        f"  자신 있게 틀림 {_pct(nf_cw, n)}  ({nf_cw}/{n})   <- 사망 기준: 0 이어야 규칙이 산다",
        f"  정확도(골격)    {_pct(nf_ok, n)}  ({nf_ok}/{n})",
        f"  물러남          {_pct(nf_dec, n)}  ({nf_dec}/{n})",
        "",
        "[옛 합의 게이트 (같은 원문, 대조군)]",
        f"  자신 있게 틀림 {_pct(old_cw, n)}  ({old_cw}/{n})",
        f"  정확도(골격)    {_pct(old_ok, n)}  ({old_ok}/{n})",
        f"  물러남          {_pct(old_dec, n)}  ({old_dec}/{n})",
        "",
        f"  대조: 정확도 {nf_ok - old_ok:+d} · 물러남 {nf_dec - old_dec:+d} "
        f"(둘의 합이 0이어야 한다 - 옮겨간 것이지 사라진 게 아니다)",
    ]

    flips = [(r, old_rows[r.index - 1][3]) for r in name_first_rows
             if r.outcome is not old_rows[r.index - 1][3]]
    if flips:
        out.append("")
        out.append("  판정이 바뀐 장:")
        for r, old_outcome in flips:
            out.append(f"    {r.index:2d}. {r.name:16} {r.variant:8} "
                       f"{old_outcome.value:16} -> {r.outcome.value:16}  {r.reason}")

    if nf_cw:
        out.append("")
        out.append(f"!! 사망 기준 위반 - 이름 대조 우선 팔에서 자신 있게 틀림 {nf_cw}건. 규칙을 기각한다.")
        for r in name_first_rows:
            if r.outcome is Outcome.CONFIDENT_WRONG:
                out.append(f"    {r.index:2d}. {r.name:16} {r.variant:8}  {r.reason}")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.rescore_name_first",
        description="저장된 recognize 결과를 이름 대조 우선 규칙으로 다시 채점한다 (엔진 재실행 없음)",
    )
    parser.add_argument("path", type=Path, nargs="?",
                        default=ROOT / "bench" / "results" / "recognize_50_20260909.json",
                        help="bench/recog.py 가 저장한 원문 json (rows_to_json 형식)")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"파일이 없습니다: {args.path}", file=sys.stderr)
        return 2

    data = json.loads(args.path.read_text(encoding="utf-8"))
    print(f"원문: {args.path}  · 인식기 {', '.join(data.get('engines', []))}\n")

    name_first_rows, old_rows = rescore(data)
    print(report(name_first_rows, old_rows))

    nf_cw = sum(1 for r in name_first_rows if r.outcome is Outcome.CONFIDENT_WRONG)
    return 1 if nf_cw else 0


if __name__ == "__main__":
    raise SystemExit(main())
