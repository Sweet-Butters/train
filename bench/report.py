"""리포트 골격 - 사람이 위에서 두 줄만 읽고 판단할 수 있게 쓴다.

첫 줄이 판정률인 이유: 침묵하는 검사기는 오탐률 0% 를 공짜로 얻는다.
판정률 없이 오탐률만 적힌 리포트는 스스로를 속인다.
"""
from __future__ import annotations

from .score import Outcome, Scorecard
from .validate import LabelCheck

LABEL = {
    Outcome.CLEARED: "맞는 것을 맞다고 함",
    Outcome.CAUGHT: "틀린 것을 잡음",
    Outcome.FALSE_ALARM: "오탐 (맞는 것을 틀렸다고 함)",
    Outcome.MISSED: "놓침 (틀린 것을 맞다고 함)",
    Outcome.MISGRADED: "등급 어긋남",
    Outcome.SILENT: "판정 안 함",
    Outcome.DECLINED: "판정 대상이 아닌 것에 옳게 물러남",
}


def caveat(engine: str, synthetic_deck: bool) -> str:
    """숫자에서 떼어낼 수 없는 단서. 리포트 위아래 양쪽에 찍는다.

    README 에 적어두면 인용될 때 떨어져 나간다. 숫자와 같은 출력에 붙어 있어야
    한다 - 누가 이 리포트를 잘라 붙이더라도 단서가 같이 따라가도록.
    """
    why: list[str] = []
    if synthetic_deck:
        why.append("덱이 RDKit 이 그린 합성 그림이다 - OCSR 입장에서 가장 쉬운 입력이다")
    if engine == "oracle":
        why.append("인식기가 oracle 이다 - 그림을 100% 읽는다고 가정한 가상 인식기다")
    if engine == "none":
        why.append("인식기가 없다 - 전부 침묵하는 것이 정상이다")
    if engine == "hallucinating":
        why.append("인식기가 구조식이 아닌 그림에도 자신 있게 답하는 가상 인식기다")
    if not why:
        return ""
    lines = ["!! 아래 숫자는 성능이 아니다 !!"]
    lines += [f"   - {w}" for w in why]
    lines += [
        "   상한치이자 장치 점검 결과다. 성능으로 인용하지 말 것.",
        "   실제 성능은 bench/decks/ 에 실제 강의자료가 들어온 뒤에만 나온다.",
    ]
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return "  --  " if v is None else f"{v * 100:5.1f}%"


def labels(checks: list[LabelCheck]) -> str:
    bad = [c for c in checks if not c.ok]
    out = ["라벨 검증",
           f"  케이스 {len(checks)}건 중 {len(checks) - len(bad)}건 확인, {len(bad)}건 문제"]
    for c in bad:
        out.append(f"    ! {c.case.name:14} {c.problem}")
    if bad:
        out.append("  라벨이 틀린 채로 잰 숫자는 숫자가 아니다. 위를 먼저 고칠 것.")
    return "\n".join(out)


def scorecard(card: Scorecard) -> str:
    judged = card.n_judgeable - card.count(Outcome.SILENT)
    out = [
        f"[{card.arm}]",
        f"  판정률   {_pct(card.coverage)}   "
        f"({judged}/{card.n_judgeable} 건, 분모는 판정했어야 할 케이스)",
        f"  오탐률   {_pct(card.false_alarm_rate)}   "
        f"({card.count(Outcome.FALSE_ALARM)}/{card.n_skeleton_same} 건, "
        f"분모는 골격이 같은 케이스)",
        f"  검출률   {_pct(card.detection_rate)}   "
        f"({card.count(Outcome.CAUGHT)}/{card.n_wrong} 건, 분모는 '틀린' 케이스)",
    ]
    if card.n_not_judgeable:
        out.append(f"  물러남   {_pct(card.decline_rate)}   "
                   f"({card.count(Outcome.DECLINED)}/{card.n_not_judgeable} 건, "
                   f"판정 대상이 아닌 그림)")
    if card.n_out_of_scope:
        # 도구의 성적이 아니라 자료의 사실이다. 물러남 안에 섞여 보이면
        # '자료가 우리 범위 밖이었다'는 사실이 성적표에 묻힌다.
        out.append(f"  범위 밖  {card.n_out_of_scope:5d} 건   "
                   f"(일반식·반응 도식 - 짝 없는 그림 {card.n_no_target} 건과 별개)")
    out.append("")
    for outcome in Outcome:
        n = card.count(outcome)
        if n:
            out.append(f"    {LABEL[outcome]:28} {n:3d}")
    if card.silence_reasons:
        out += ["", "    침묵 사유"]
        for reason, n in card.silence_reasons.most_common():
            out.append(f"      {n:3d}  {reason}")
    return "\n".join(out)


def contrast(chemcheck: Scorecard, baseline: Scorecard) -> str:
    """보류 규칙이 무엇을 사고 무엇을 팔았는가."""
    saved = baseline.count(Outcome.FALSE_ALARM) - chemcheck.count(Outcome.FALSE_ALARM)
    given_up = ((chemcheck.n_judgeable - chemcheck.count(Outcome.SILENT))
                - (baseline.n_judgeable - baseline.count(Outcome.SILENT)))
    return "\n".join([
        "대조 - 보류 규칙의 값어치",
        f"  막은 오탐   {saved:+d} 건",
        f"  포기한 판정 {given_up:+d} 건",
        "  이 둘의 교환비가 chemcheck 이 부품 위에 얹은 전부다.",
    ])


def detail(card: Scorecard) -> str:
    """케이스별 한 줄. 숫자가 이상할 때 어디를 볼지 찾는 용도."""
    out = [f"[{card.arm}] 케이스별"]
    for i, row in enumerate(card.rows, start=1):
        out.append(f"  {i:2d}. {row.case.name:14} {row.case.truth.value:14} "
                   f"-> {row.finding.verdict.value.upper():8} {LABEL[row.outcome]}")
        out.append(f"      {row.finding.reason}")
    return "\n".join(out)
