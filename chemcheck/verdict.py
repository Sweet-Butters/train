"""판정. 이 파일에 LLM 호출은 없다.

설계 원칙: "틀렸다"고 잘못 말하는 것이 가장 나쁘다.
확신이 없으면 반드시 ABSTAIN으로 빠진다. 침묵은 오답보다 낫다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .keys import Match, compare, smiles_to_inchikey
from .names import Reference
from .ocsr import Prediction

# OCSR 정확도는 76~93%다. 이 밑이면 판정하지 않는다.
MIN_CONFIDENCE = 0.80


class Verdict(Enum):
    OK = "ok"            # 이름과 그림이 같은 분자
    ERROR = "error"      # 골격이 다름 = 확실한 오류
    WARN = "warn"        # 골격은 같고 입체화학이 다름
    ABSTAIN = "abstain"  # 판정 불가


@dataclass
class Finding:
    verdict: Verdict
    reason: str
    reference: Reference | None = None
    predictions: list[Prediction] = field(default_factory=list)
    predicted_key: str | None = None

    @property
    def is_actionable(self) -> bool:
        return self.verdict in (Verdict.ERROR, Verdict.WARN)


def _consensus(predictions: list[Prediction]) -> tuple[str | None, str | None]:
    """여러 인식기의 합의를 본다. 갈리면 (None, 사유)를 준다.

    VERDICT(2608.22183)의 결론: 렌더 후 재인식보다 다중 엔진 합의가 낫다.
    """
    keys = {}
    for p in predictions:
        key = smiles_to_inchikey(p.smiles)
        if key is None:
            return None, f"{p.engine}가 내놓은 SMILES를 해석할 수 없음"
        keys[p.engine] = key
    distinct = set(keys.values())
    if not distinct:
        return None, "인식 결과 없음"
    if len(distinct) > 1:
        detail = ", ".join(f"{e}={k[:14]}" for e, k in keys.items())
        return None, f"인식기 간 결과 불일치 ({detail})"
    return distinct.pop(), None


def judge(references: list[Reference], predictions: list[Prediction]) -> Finding:
    """참조(이름에서 얻은 정답)와 인식 결과(그림에서 얻은 것)를 대조한다."""
    if not references:
        return Finding(Verdict.ABSTAIN, "그림 옆에서 화합물 이름을 찾지 못함")
    if not predictions:
        return Finding(Verdict.ABSTAIN, "구조 인식기를 쓸 수 없음", references[0])

    scored = [p for p in predictions if p.confidence == p.confidence]  # NaN 제외
    if scored and max(p.confidence for p in scored) < MIN_CONFIDENCE:
        best = max(p.confidence for p in scored)
        return Finding(
            Verdict.ABSTAIN,
            f"인식 신뢰도 부족 ({best:.2f} < {MIN_CONFIDENCE})",
            references[0],
            predictions,
        )

    predicted_key, why = _consensus(predictions)
    if predicted_key is None:
        return Finding(Verdict.ABSTAIN, why or "합의 실패", references[0], predictions)

    # 이름 후보가 여럿이면 하나라도 맞으면 통과로 본다 (동의어 때문).
    best_match = Match.DIFFERENT
    best_ref = references[0]
    for ref in references:
        result = compare(ref.inchikey, predicted_key)
        if result.match is Match.EXACT:
            best_match, best_ref = Match.EXACT, ref
            break
        if result.match is Match.SKELETON_ONLY and best_match is Match.DIFFERENT:
            best_match, best_ref = Match.SKELETON_ONLY, ref

    if best_match is Match.EXACT:
        return Finding(Verdict.OK, f"'{best_ref.name}'와 일치", best_ref, predictions, predicted_key)
    if best_match is Match.SKELETON_ONLY:
        return Finding(
            Verdict.WARN,
            f"골격은 '{best_ref.name}'와 같으나 입체화학이 다름 "
            f"(OCSR 입체 인식은 신뢰도가 낮으니 사람이 확인할 것)",
            best_ref, predictions, predicted_key,
        )
    return Finding(
        Verdict.ERROR,
        f"'{best_ref.name}'와 골격이 다름 "
        f"(이름 {best_ref.inchikey[:14]} vs 그림 {predicted_key[:14]})",
        best_ref, predictions, predicted_key,
    )
