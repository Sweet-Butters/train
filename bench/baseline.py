"""대조군 - 보류 규칙이 없는 판정기.

chemcheck 의 자산은 인식기가 아니라 '언제 입을 다무는가'라는 규칙이다.
그 규칙이 실제로 무엇을 사는지 알려면, 규칙 없이 같은 인식 결과를 그대로
비교한 성적이 옆에 있어야 한다. 이것이 그 옆자리다.

여기에는 신뢰도 게이트도, 엔진 간 합의도, 골격/입체 구분도 없다.
예측 하나를 참조 하나와 대조하고, 다르면 오류라고 말한다.
"""
from __future__ import annotations

from chemcheck.keys import Match, compare, smiles_to_inchikey
from chemcheck.names import Reference
from chemcheck.ocsr import Prediction
from chemcheck.verdict import Finding, Verdict


def naive_judge(references: list[Reference], predictions: list[Prediction]) -> Finding:
    """비교할 것이 없을 때만 침묵한다. 그 밖에는 언제나 판정한다."""
    if not references:
        return Finding(Verdict.ABSTAIN, "이름 없음 (구조적으로 비교 불가)")
    if not predictions:
        return Finding(Verdict.ABSTAIN, "인식 결과 없음 (구조적으로 비교 불가)")

    ref, pred = references[0], predictions[0]
    key = smiles_to_inchikey(pred.smiles)
    if key is None:
        return Finding(Verdict.ABSTAIN, "SMILES 해석 불가 (구조적으로 비교 불가)", ref, predictions)

    result = compare(ref.inchikey, key)
    if result.match is Match.EXACT:
        return Finding(Verdict.OK, f"'{ref.name}'와 일치", ref, predictions, key)
    # 입체만 달라도 오류라고 한다. 대조군의 요점이 바로 이것이다.
    return Finding(
        Verdict.ERROR,
        f"'{ref.name}'와 불일치 (이름 {ref.inchikey[:14]} vs 그림 {key[:14]})",
        ref, predictions, key,
    )
