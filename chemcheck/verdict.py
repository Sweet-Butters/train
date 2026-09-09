"""판정. 이 파일에 LLM 호출은 없다.

설계 원칙: "틀렸다"고 잘못 말하는 것이 가장 나쁘다.
확신이 없으면 반드시 ABSTAIN으로 빠진다. 침묵은 오답보다 낫다.

결정 6·8 (docs/DIRECTION.md): 이름이 있으면 이름 대조가 1순위다. 예전에는
인식기들이 전부 합의해야(`_consensus`) 비로소 이름과 비교했는데, 그래서 인식기
하나가 SMILES 를 못 읽으면(원자가가 깨진 답 등) 다른 인식기가 낸 멀쩡한 답까지
묻혔다. 지금은 **읽을 수 있는 답부터** 이름과 대조한다. 합의(종류가 다른 인식기
둘 이상이 같은 골격에 모임)는 등급을 가르는 데만 쓴다 - 이름이 없을 때 쓸 최후
수단이 아니라, 애초에 이름이 없으면 judge() 는 비교할 것이 없어 판정 불가다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .keys import Match, compare, skeleton, smiles_to_inchikey
from .names import Reference
from .ocsr import Prediction

# OCSR 정확도는 76~93%다. 이 밑이면 판정하지 않는다. 모든 예측의 신뢰도가
# 문턱 밑이면(=아무도 자신 없으면) 이름 대조까지 가지 않고 여기서 접는다.
# bench/recog.py 의 confidence_gate(단독 엔진 기준선)도 같은 값을 쓴다.
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


def _family(engine_name: str) -> str:
    """'decimer/x0.9', 'molscribe@.venv310' -> 'decimer', 'molscribe'.

    변형 표기(SelfConsistent 의 스케일, 서브프로세스의 venv 이름)를 걷어내
    인식기 '종류'만 남긴다. 합의는 종류 사이의 일이다 - 같은 종류가 자기 변형과
    합의한 것을 두 종류의 합의로 세지 않는다. (structure.py::engine_family 와
    같은 규칙 - 여기서 다시 import 하면 verdict.py 가 structure.py 에 얽힌다.)
    """
    for sep in ("/", "@", "-"):
        engine_name = engine_name.split(sep)[0]
    return engine_name


@dataclass(frozen=True)
class _Read:
    prediction: Prediction
    family: str
    key: str


def _confidence(pred: Prediction) -> float:
    c = pred.confidence
    return c if c == c else -1.0  # NaN 은 맨 뒤로


def _readable(predictions: list[Prediction]) -> list[_Read]:
    """파싱되는 예측만. 못 읽는 답은 증거가 아니라 그냥 없는 것으로 친다(결정 8)."""
    out = []
    for p in predictions:
        key = smiles_to_inchikey(p.smiles)
        if key is not None:
            out.append(_Read(p, _family(p.engine), key))
    return out


def _stable_reads(reads: list[_Read]) -> list[_Read]:
    """종류(family)마다 스스로 합의했을 때만 대표 답 하나를 낸다.

    한 종류가 이미지 변형(SelfConsistent)이나 여러 예측 사이에서 서로 다른
    골격을 냈다면 그 인식은 못 믿는다 - 흔들리는 답 중 하나가 우연히 이름과
    맞아도 그것을 근거로 쓰지 않는다
    (tests/test_pipeline.py::test_self_consistency_forces_abstain).
    """
    by_family: dict[str, list[_Read]] = {}
    for r in reads:
        by_family.setdefault(r.family, []).append(r)
    out: list[_Read] = []
    for members in by_family.values():
        skeletons = {skeleton(r.key) or r.key for r in members}
        if len(skeletons) > 1:
            continue  # 스스로 갈렸다 - 이 종류는 빼고 간다
        out.append(max(members, key=lambda r: _confidence(r.prediction)))
    return out


def _agreed_skeleton(reads: list[_Read]) -> str | None:
    """종류가 다른 인식기 둘 이상이 모인 골격. 없으면 None."""
    by_skeleton: dict[str, set[str]] = {}
    for r in reads:
        sk = skeleton(r.key) or r.key
        by_skeleton.setdefault(sk, set()).add(r.family)
    for sk, families in by_skeleton.items():
        if len(families) >= 2:
            return sk
    return None


def judge(references: list[Reference], predictions: list[Prediction]) -> Finding:
    """참조(이름에서 얻은 정본)와 인식 결과(그림)를 대조한다.

    결정 6·8: 이름이 있으면 이름 대조가 1순위다. 읽을 수 있는 답 중 하나라도
    이름의 골격과 일치하면 그것으로 확정한다 - 몇 종류가 답했는지는 상관없다.
    전부 다를 때만 '오류'고, 그때 등급이 갈린다:
      - 종류가 다른 인식기 둘 이상이 서로 합의한 답이 이름과 다르면 [강]
      - 그 밖(유효한 답을 낸 종류가 하나뿐이거나, 종류끼리도 갈렸다)이면 [약]
    """
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

    reads = _readable(predictions)
    if not reads:
        return Finding(Verdict.ABSTAIN, "SMILES를 해석할 수 없음", references[0], predictions)

    stable = _stable_reads(reads)
    if not stable:
        return Finding(Verdict.ABSTAIN, "인식기 간 결과 불일치 (스스로도 답이 갈림)",
                       references[0], predictions)

    # 이름 후보가 여럿이면, 그리고 종류가 여럿이면 하나라도 맞으면 통과로 본다.
    for ref in references:
        for r in stable:
            if compare(ref.inchikey, r.key).match is Match.EXACT:
                return Finding(Verdict.OK, f"'{ref.name}'와 일치", ref, predictions, r.key)
    for ref in references:
        for r in stable:
            if compare(ref.inchikey, r.key).match is Match.SKELETON_ONLY:
                return Finding(
                    Verdict.WARN,
                    f"골격은 '{ref.name}'와 같습니다. 입체는 판정하지 않았습니다.",
                    ref, predictions, r.key,
                )

    # 어떤 유효한 답도 이름과 맞지 않는다 - 오류. 등급을 가른다.
    best_ref = references[0]
    n_families = len(stable)
    agreed = _agreed_skeleton(stable)
    if agreed is not None:
        predicted_key = next(r.key for r in stable if (skeleton(r.key) or r.key) == agreed)
        return Finding(
            Verdict.ERROR,
            f"[강] '{best_ref.name}'와 골격이 다름 (인식기 {n_families}종 합의: "
            f"이름 {best_ref.inchikey[:14]} vs 그림 {predicted_key[:14]})",
            best_ref, predictions, predicted_key,
        )
    predicted_key = stable[0].key
    why = (f"유효한 답을 낸 인식기 {n_families}종뿐" if n_families < 2
           else f"인식기 {n_families}종이 서로 다른 골격을 냄 - 합의 없음")
    return Finding(
        Verdict.ERROR,
        f"[약] '{best_ref.name}'와 골격이 다름 ({why}: "
        f"이름 {best_ref.inchikey[:14]} vs 그림 {predicted_key[:14]})",
        best_ref, predictions, predicted_key,
    )
