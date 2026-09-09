"""채점 - 판정 결과를 라벨과 대조해 결과(Outcome)로 분류하고 집계한다.

지표의 순서가 곧 설계 의도다:
  1. 판정률   - 침묵하는 검사기는 만들기 쉽다. 이게 낮으면 나머지는 볼 필요가 없다.
  2. 오탐률   - 멀쩡한 것을 틀렸다고 한 비율. 0 이 아니면 제품이 아니다.
  3. 검출률   - 그 다음 문제다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from chemcheck.verdict import Finding, Verdict

from .cases import JUDGEABLE, Case, Truth

# 각 라벨에서 나와야 할 판정.
IDEAL = {
    Truth.SAME: Verdict.OK,
    Truth.SKELETON_DIFF: Verdict.ERROR,
    Truth.STEREO_DIFF: Verdict.WARN,
    Truth.NOT_A_STRUCTURE: Verdict.ABSTAIN,  # 구조식이 아니면 물러나는 것이 정답
}

WRONG_TRUTHS = (Truth.SKELETON_DIFF, Truth.STEREO_DIFF)

# ERROR 가 나오면 안 되는 라벨. 골격이 같거나, 애초에 구조식이 아니거나.
NO_ERROR_TRUTHS = (Truth.SAME, Truth.STEREO_DIFF, Truth.NOT_A_STRUCTURE)


class Outcome(Enum):
    CLEARED = "cleared"          # 맞는 것을 맞다고 함
    CAUGHT = "caught"            # 틀린 것을 제 등급으로 잡음
    FALSE_ALARM = "false_alarm"  # 맞는 것을 틀렸다고 함  <- 가장 나쁘다
    MISSED = "missed"            # 틀린 것을 맞다고 함
    MISGRADED = "misgraded"      # 판정은 했으나 등급이 어긋남
    SILENT = "silent"            # 판정했어야 하는데 하지 않음
    DECLINED = "declined"        # 구조식이 아닌 것에 옳게 물러남 = 정답


def classify(truth: Truth, verdict: Verdict) -> Outcome:
    if truth is Truth.NOT_A_STRUCTURE:
        # 구조식이 아닌 그림이다. 물러나는 것이 정답이고, 오류라 단정하면 멀쩡한
        # 자료를 틀렸다고 말하는 것이다 - 실제 자료에서 가장 흔할 오탐 경로다.
        if verdict is Verdict.ABSTAIN:
            return Outcome.DECLINED
        if verdict is Verdict.ERROR:
            return Outcome.FALSE_ALARM
        return Outcome.MISGRADED  # 판정하지 말았어야 할 것을 판정했다
    if verdict is Verdict.ABSTAIN:
        return Outcome.SILENT
    if verdict is IDEAL[truth]:
        return Outcome.CLEARED if truth is Truth.SAME else Outcome.CAUGHT
    if truth is Truth.SAME:
        # 골격이 다르다고 단정한 것만 오탐으로 센다. 주의는 사람에게 넘긴 것이므로
        # 잡음이지 오경보가 아니다 - 등급 어긋남으로 따로 센다.
        return Outcome.FALSE_ALARM if verdict is Verdict.ERROR else Outcome.MISGRADED
    if verdict is Verdict.OK:
        return Outcome.MISSED
    if truth is Truth.STEREO_DIFF and verdict is Verdict.ERROR:
        # 골격이 같은데 다르다고 했다 = 그림을 잘못 읽었다는 뜻이다.
        return Outcome.FALSE_ALARM
    return Outcome.MISGRADED


@dataclass
class Row:
    case: Case
    finding: Finding
    outcome: Outcome


@dataclass
class Scorecard:
    arm: str                       # 어느 판정기의 성적인가 (chemcheck / baseline)
    rows: list[Row] = field(default_factory=list)

    def add(self, case: Case, finding: Finding) -> None:
        self.rows.append(Row(case, finding, classify(case.truth, finding.verdict)))

    # --- 원자료 ---------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.rows)

    def count(self, outcome: Outcome, *truths: Truth) -> int:
        return sum(1 for r in self.rows if r.outcome is outcome
                   and (not truths or r.case.truth in truths))

    def n_truth(self, *truths: Truth) -> int:
        return sum(1 for r in self.rows if r.case.truth in truths)

    @property
    def n_same(self) -> int:
        return self.n_truth(Truth.SAME)

    @property
    def n_judgeable(self) -> int:
        """판정했어야 할 케이스. 판정률의 분모다.

        구조식이 아닌 그림에 침묵한 것은 잘한 일이지 못 본 것이 아니다.
        그것을 분모에 넣으면 옳게 행동한 데 벌점을 매기게 된다.
        """
        return self.n_truth(*JUDGEABLE)

    @property
    def n_not_structure(self) -> int:
        return self.n_truth(Truth.NOT_A_STRUCTURE)

    @property
    def n_skeleton_same(self) -> int:
        """골격이 실제로 같은 케이스. ERROR 가 나오면 안 되는 모집단이다.

        입체만 다른 케이스도 여기 든다 - 골격이 같으므로 '골격이 다르다'는
        단정은 그 자체로 오탐이다. 오탐률의 분모는 SAME 이 아니라 이쪽이다.
        """
        return self.n_truth(*NO_ERROR_TRUTHS)

    @property
    def n_wrong(self) -> int:
        return self.n_truth(*WRONG_TRUTHS)

    # --- 머리에 오는 두 숫자 ---------------------------------------------
    @property
    def coverage(self) -> float | None:
        """판정했어야 할 것 중 실제로 판정한 비율."""
        n = self.n_judgeable
        if not n:
            return None
        return 1.0 - self.count(Outcome.SILENT, *JUDGEABLE) / n

    @property
    def decline_rate(self) -> float | None:
        """구조식이 아닌 그림에 옳게 물러난 비율."""
        n = self.n_not_structure
        return None if not n else self.count(Outcome.DECLINED) / n

    @property
    def false_alarm_rate(self) -> float | None:
        """오류라고 말하면 안 되는 것을 오류라 한 비율. 0 이 아니면 제품이 아니다."""
        n = self.n_skeleton_same
        return None if not n else self.count(Outcome.FALSE_ALARM) / n

    # --- 그 다음 ---------------------------------------------------------
    @property
    def detection_rate(self) -> float | None:
        n = self.n_wrong
        return None if not n else self.count(Outcome.CAUGHT) / n

    @property
    def silence_reasons(self) -> Counter:
        """왜 침묵했는가. 침묵률이 높을 때 어디를 고쳐야 하는지 알려준다."""
        return Counter(r.finding.reason for r in self.rows if r.outcome is Outcome.SILENT)
