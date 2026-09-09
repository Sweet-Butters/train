"""recognize 평가 - '정확한 SMILES 를 준다' 를 숫자로.

    python -m bench.run --task recognize --engine oracle      # 장치 점검
    python -m bench.run --task recognize --engine shaky       # 인식기 하나가 흔들릴 때
    python -m bench.run --task recognize --engine colluding   # 둘이 같은 방향으로 틀릴 때
    python -m bench.run --task recognize --engine real        # 하루 끝 한 번, 게이트로

재는 것은 둘이다. 답이 얼마나 맞는가, 그리고 **틀리면서 자신 있는 경우가 얼마나
되는가**. 후자가 이 도구의 존재 이유다 - LLM 이 하는 실패가 바로 그것이다. 그래서
리포트 첫 줄이 '자신 있게 틀림' 이다.

두 팔로 잰다. 인식 결과는 한 번만 얻고 규칙만 다르게 적용한다:
  합의 게이트   recognize 가 쓰는 규칙. 인식기 종류 둘 이상이 골격에 합의해야 답이다.
  신뢰도 게이트 기준선. 인식기 하나라도 신뢰도 ≥0.8 이면 그 답을 낸다 (verdict.py 의
                단독 경로이자, LLM 이 답하는 방식이다).
두 팔의 '자신 있게 틀림' 차이가 합의 게이트가 사는 것이고, 정확도 차이가 파는 것이다.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from chemcheck.keys import skeleton, smiles_to_inchikey
from chemcheck.ocsr import Engine, Prediction
from chemcheck.verdict import MIN_CONFIDENCE

from .variants import VARIANT_NAMES, Item, index_of

# ── 인식 결과 정리 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Read:
    """인식기 하나가 그림 하나에서 낸 것. 원문 SMILES 는 버리지 않는다."""

    engine: str
    raw: str
    inchikey: str | None
    confidence: float

    @property
    def skeleton(self) -> str | None:
        return skeleton(self.inchikey)

    @property
    def has_confidence(self) -> bool:
        return not math.isnan(self.confidence)

    @property
    def confident(self) -> bool:
        return self.has_confidence and self.confidence >= MIN_CONFIDENCE


def family(engine_name: str) -> str:
    """'decimer/x0.9', 'molscribe@.venv310' -> 'decimer', 'molscribe'.

    SelfConsistent 는 한 인식기를 여러 이름으로 낸다. 합의는 인식기 '종류' 사이의
    일이다. 같은 인식기가 자기와 합의한 것을 둘의 합의로 세지 않는다.
    """
    for sep in ("/", "@"):
        engine_name = engine_name.split(sep)[0]
    return engine_name


def read_all(image: Path, engines: list[Engine]) -> list[Read]:
    reads: list[Read] = []
    for engine in engines:
        try:
            preds = engine.recognize_all(image)
        except Exception as exc:  # 인식기 하나가 죽어도 나머지는 본다
            reads.append(Read(engine.name, f"(실패: {type(exc).__name__}: {exc})",
                              None, float("nan")))
            continue
        for p in preds:
            reads.append(Read(p.engine, p.smiles, smiles_to_inchikey(p.smiles), p.confidence))
    return reads


# ── 두 가지 규칙 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    answer: str | None    # 답으로 낸 InChIKey. None 이면 물러난 것
    reason: str

    @property
    def committed(self) -> bool:
        return self.answer is not None


def consensus_gate(reads: list[Read]) -> Decision:
    """recognize 의 규칙. 종류가 다른 인식기 둘 이상이 한 골격에 모여야 답이다."""
    readable = [r for r in reads if r.skeleton is not None]
    if not readable:
        return Decision(None, "인식 결과 없음" if not reads else "인식기가 낸 SMILES 를 읽을 수 없음")
    skeletons = {r.skeleton for r in readable}
    if len(skeletons) > 1:
        return Decision(None, "인식기끼리 골격이 다름")
    families = {family(r.engine) for r in readable}
    if len(families) < 2:
        return Decision(None, f"인식기 하나({next(iter(families))})의 답뿐")
    best = max(readable, key=lambda r: r.confidence if r.has_confidence else -1.0)
    return Decision(best.inchikey, f"인식기 {len(families)}종 합의")


def confidence_gate(reads: list[Read]) -> Decision:
    """기준선. 신뢰도가 문턱을 넘는 인식기 하나의 답을 그대로 낸다.

    신뢰도를 주지 않는 인식기(DECIMER)는 문턱을 볼 수 없으므로 그 답을 그대로
    믿는다 - verdict.py 가 NaN 을 게이트에서 빼는 것과 같다. 여럿이면 신뢰도가
    가장 높은 것. 이것이 '인식기 하나를 믿는 도구' 가 하는 일이다.
    """
    readable = [r for r in reads if r.skeleton is not None]
    if not readable:
        return Decision(None, "인식 결과 없음" if not reads else "인식기가 낸 SMILES 를 읽을 수 없음")
    scored = [r for r in readable if r.has_confidence]
    if scored:
        best = max(scored, key=lambda r: r.confidence)
        if best.confidence < MIN_CONFIDENCE:
            return Decision(None, f"신뢰도 부족 ({best.confidence:.2f} < {MIN_CONFIDENCE})")
        return Decision(best.inchikey, f"{best.engine} 신뢰도 {best.confidence:.2f}")
    return Decision(readable[0].inchikey, f"{readable[0].engine} (신뢰도 없음, 그대로 믿음)")


ARMS = (
    ("합의 게이트 (recognize 의 규칙)", consensus_gate),
    ("신뢰도 게이트만 (기준선 - 인식기 하나를 믿음)", confidence_gate),
)


# ── 채점 ───────────────────────────────────────────────────────────────────


class Outcome(Enum):
    CORRECT = "correct"                   # 답을 냈고 골격이 맞다
    CONFIDENT_WRONG = "confident_wrong"   # 답을 냈는데 골격이 다르다  <- 가장 나쁘다
    DECLINED = "declined"                 # 확신 없음으로 뺐다


@dataclass
class Row:
    item: Item
    reads: list[Read]
    decision: Decision
    outcome: Outcome

    @property
    def exact(self) -> bool:
        """입체까지 일치. 골격만 맞은 것과 갈라 보인다."""
        return self.decision.answer == self.item.inchikey

    @property
    def truth_was_available(self) -> bool:
        """물러났지만 정답 골격을 낸 인식기가 있었다 - 조심의 값이다."""
        return any(r.skeleton == self.item.skeleton for r in self.reads)


def classify(item: Item, decision: Decision) -> Outcome:
    if decision.answer is None:
        return Outcome.DECLINED
    return Outcome.CORRECT if skeleton(decision.answer) == item.skeleton else Outcome.CONFIDENT_WRONG


@dataclass
class EngineStat:
    """인식기 종류 하나의 성적. 인식기 하나만 믿었을 때 무슨 일이 나는지 보여준다."""

    family: str
    images: int = 0
    answered: int = 0            # 읽을 수 있는 SMILES 를 하나라도 냈다
    correct: int = 0             # 대표 답의 골격이 맞다
    exact: int = 0               # 입체까지 맞다
    wrong: int = 0               # 대표 답의 골격이 다르다
    unreadable: int = 0          # SMILES 를 냈는데 RDKit 이 못 읽음
    confident_wrong: int = 0     # 신뢰도 ≥0.8 인데 골격이 다르다 - LLM 의 실패 그 자체
    gives_confidence: bool = False

    @property
    def accuracy(self) -> float | None:
        return None if not self.images else self.correct / self.images


@dataclass
class Card:
    arm: str
    rows: list[Row] = field(default_factory=list)

    def add(self, item: Item, reads: list[Read], decision: Decision) -> None:
        self.rows.append(Row(item, reads, decision, classify(item, decision)))

    @property
    def total(self) -> int:
        return len(self.rows)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.rows if r.outcome is outcome)

    def rate(self, outcome: Outcome) -> float | None:
        return None if not self.total else self.count(outcome) / self.total

    @property
    def n_exact(self) -> int:
        return sum(1 for r in self.rows if r.outcome is Outcome.CORRECT and r.exact)

    @property
    def n_declined_with_truth(self) -> int:
        return sum(1 for r in self.rows
                   if r.outcome is Outcome.DECLINED and r.truth_was_available)

    @property
    def decline_reasons(self) -> Counter:
        return Counter(r.decision.reason for r in self.rows if r.outcome is Outcome.DECLINED)

    def by_variant(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for r in self.rows:
            out.setdefault(r.item.variant.name, Counter())[r.outcome] += 1
        return out


def engine_stats(rows: list[Row]) -> list[EngineStat]:
    """팔과 무관하다 - 인식 결과 자체의 성적이다."""
    stats: dict[str, EngineStat] = {}
    for row in rows:
        per_family: dict[str, list[Read]] = {}
        for r in row.reads:
            per_family.setdefault(family(r.engine), []).append(r)
        for fam, reads in per_family.items():
            st = stats.setdefault(fam, EngineStat(fam))
            st.images += 1
            if any(r.has_confidence for r in reads):
                st.gives_confidence = True
            readable = [r for r in reads if r.skeleton is not None]
            if len(readable) < len(reads):
                st.unreadable += 1
            if not readable:
                continue
            st.answered += 1
            best = max(readable, key=lambda r: r.confidence if r.has_confidence else -1.0)
            if best.skeleton == row.item.skeleton:
                st.correct += 1
                if best.inchikey == row.item.inchikey:
                    st.exact += 1
            else:
                st.wrong += 1
                if best.confident:
                    st.confident_wrong += 1
    return sorted(stats.values(), key=lambda s: s.family)


# ── 스텁 인식기 - 장치를 검증하는 데 쓴다. 제품 경로가 아니다 ─────────────────


class TruthEngine(Engine):
    """그려진 SMILES 를 그대로 돌려주는 가상 인식기. 정확도 100%."""

    def __init__(self, by_index: dict[int, str], name: str = "oracle",
                 confidence: float = 0.99):
        self.by_index = by_index
        self.name = name
        self.confidence = confidence

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        smiles = self.by_index.get(index_of(image_path))
        return Prediction(smiles, self.confidence, self.name) if smiles else None


class ShakyEngine(TruthEngine):
    """every 장마다 한 번, 엉뚱한 골격을 높은 신뢰도로 낸다.

    실제 인식기가 이렇다 - 틀릴 때도 신뢰도는 높다. 혼자 두면 신뢰도 게이트를 그대로
    통과해 '자신 있게 틀림' 이 된다. 다른 인식기와 짝지으면 합의가 깨져 물러난다.
    """

    WRONG = "c1ccncc1"  # 피리딘. 세트에 있는 분자라 정답과 우연히 겹치는 장 하나는 맞다

    def __init__(self, by_index: dict[int, str], name: str = "shaky", every: int = 3,
                 wrong: str = WRONG):
        super().__init__(by_index, name, confidence=0.93)
        self.every = every
        self.wrong = wrong

    def recognize(self, image_path: Path) -> Prediction | None:
        i = index_of(image_path)
        if i % self.every == 0:
            return Prediction(self.wrong, self.confidence, self.name)
        return super().recognize(image_path)


def stub_engines(kind: str, items: list[Item]) -> list[Engine]:
    truth = {it.index: it.molecule.smiles for it in items}
    if kind == "oracle":
        # 종류가 다른 완벽한 인식기 둘. 합의 게이트가 100% 답을 내야 한다.
        return [TruthEngine(truth, "oracle"), TruthEngine(truth, "mirror")]
    if kind == "shaky":
        # 흔들리는 인식기 혼자. 합의 게이트는 전부 물러나고 신뢰도 게이트는 틀린다.
        return [ShakyEngine(truth)]
    if kind == "colluding":
        # 둘이 같은 장에서 같은 방향으로 틀린다. 합의 게이트도 못 막는다 - 게이트가
        # 이 상황을 '자신 있게 틀림' 으로 잡아 실패로 끝내는지 보는 용도다.
        return [ShakyEngine(truth, "shaky"), ShakyEngine(truth, "echo")]
    if kind == "none":
        return []
    raise ValueError(f"모르는 스텁: {kind}")


STUBS = ("oracle", "shaky", "colluding", "none")


# ── 실행 ───────────────────────────────────────────────────────────────────


def evaluate(items: list[Item], engines: list[Engine]) -> list[Card]:
    """인식은 한 번, 규칙은 팔마다. 두 팔이 같은 인식 결과를 본다."""
    cards = [Card(arm) for arm, _ in ARMS]
    for item in items:
        reads = read_all(item.path, engines)
        for card, (_, rule) in zip(cards, ARMS):
            card.add(item, reads, rule(reads))
    return cards


# ── 리포트 ─────────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return "  --  " if v is None else f"{v * 100:5.1f}%"


def caveat(engine_kind: str) -> str:
    if engine_kind == "real":
        return ""
    lines = ["!! 아래 숫자는 성능이 아니다 !!"]
    why = {
        "oracle": "인식기가 oracle 둘이다 - 그림을 100% 읽는다고 가정한 가상 인식기다",
        "shaky": "인식기가 흔들리는 가상 인식기 하나다 - 3장마다 엉뚱한 골격을 낸다",
        "colluding": "인식기 둘이 같은 장에서 같은 방향으로 틀리는 가상 인식기다",
        "none": "인식기가 없다 - 전부 물러나는 것이 정상이다",
    }
    lines.append(f"   - {why.get(engine_kind, engine_kind)}")
    lines.append("   장치 점검 결과다. 성능으로 인용하지 말 것. 실제 숫자는 --engine real 로만 나온다.")
    return "\n".join(lines)


def scorecard(card: Card) -> str:
    n = card.total
    cw = card.count(Outcome.CONFIDENT_WRONG)
    ok = card.count(Outcome.CORRECT)
    dec = card.count(Outcome.DECLINED)
    out = [
        f"[{card.arm}]",
        # 첫 줄. 이 도구의 존재 이유가 걸린 숫자다.
        f"  자신 있게 틀림 {_pct(card.rate(Outcome.CONFIDENT_WRONG))}  ({cw}/{n} 건 - "
        f"답을 냈는데 골격이 다름)",
        f"  정확도         {_pct(card.rate(Outcome.CORRECT))}  ({ok}/{n} 건 - "
        f"답을 냈고 골격이 맞음, 입체까지 {card.n_exact} 건)",
        f"  물러남         {_pct(card.rate(Outcome.DECLINED))}  ({dec}/{n} 건 - "
        f"확신 없음으로 뺌, 그중 정답을 낸 인식기가 있던 것 {card.n_declined_with_truth} 건)",
    ]
    if card.decline_reasons:
        out.append("    물러난 사유")
        for reason, k in card.decline_reasons.most_common():
            out.append(f"      {k:3d}  {reason}")
    return "\n".join(out)


def engines_table(stats: list[EngineStat], n_images: int) -> str:
    out = [f"엔진별 (그림 {n_images}장) - 인식기 하나만 믿었을 때의 성적",
           f"  {'엔진':<12} {'답함':>4} {'골격맞음':>6} {'입체까지':>6} {'틀림':>4} "
           f"{'못읽음':>5}  {'고신뢰(≥0.8) 틀림':>14}"]
    if not stats:
        out.append("  (인식 결과 없음)")
    for s in stats:
        cw = f"{s.confident_wrong:>14d}" if s.gives_confidence else f"{'신뢰도 없음':>12}"
        out.append(f"  {s.family:<12} {s.answered:>4} {s.correct:>7} {s.exact:>7} "
                   f"{s.wrong:>5} {s.unreadable:>6}  {cw}")
    return "\n".join(out)


def variants_table(card: Card) -> str:
    out = [f"변주별 [{card.arm.split(' (')[0]}]",
           f"  {'변주':<9} {'n':>3} {'맞음':>4} {'자신있게틀림':>8} {'물러남':>5}"]
    table = card.by_variant()
    for name in VARIANT_NAMES:
        c = table.get(name)
        if not c:
            continue
        out.append(f"  {name:<9} {sum(c.values()):>3} {c[Outcome.CORRECT]:>5} "
                   f"{c[Outcome.CONFIDENT_WRONG]:>10} {c[Outcome.DECLINED]:>7}")
    return "\n".join(out)


def contrast(product: Card, baseline: Card) -> str:
    saved = baseline.count(Outcome.CONFIDENT_WRONG) - product.count(Outcome.CONFIDENT_WRONG)
    given_up = baseline.count(Outcome.CORRECT) - product.count(Outcome.CORRECT)
    return "\n".join([
        "대조 - 합의 게이트의 값어치",
        f"  막은 '자신 있게 틀림'  {saved:+d} 건",
        f"  포기한 정답            {given_up:+d} 건",
        "  이 교환비가 recognize 가 인식기 위에 얹은 전부다.",
    ])


def detail(card: Card) -> str:
    mark = {Outcome.CORRECT: "맞음", Outcome.CONFIDENT_WRONG: "!! 자신 있게 틀림",
            Outcome.DECLINED: "물러남"}
    out = [f"[{card.arm}] 장별"]
    for r in card.rows:
        out.append(f"  {r.item.index:2d}. {r.item.molecule.name:16} {r.item.variant.name:8} "
                   f"-> {mark[r.outcome]:16} {r.decision.reason}")
        for rd in r.reads:
            conf = f"{rd.confidence:.2f}" if rd.has_confidence else " nan"
            sk = rd.skeleton or "(못 읽음)"
            hit = "=" if rd.skeleton == r.item.skeleton else "x"
            out.append(f"        {hit} {rd.engine:24} {conf}  {sk}  {rd.raw}")
    return "\n".join(out)


def report(cards: list[Card], engine_kind: str, engine_names: list[str],
           show_detail: bool = False) -> str:
    product, baseline = cards
    banner = caveat(engine_kind)
    parts: list[str] = []
    if banner:
        parts.append(banner)
    parts.append(f"recognize 평가 · 그림 {product.total}장 · 변주 {len(VARIANT_NAMES)}종 · "
                 f"인식기 {', '.join(engine_names) or '없음'}")
    parts.append(scorecard(product))
    parts.append(scorecard(baseline))
    parts.append(contrast(product, baseline))
    parts.append(engines_table(engine_stats(product.rows), product.total))
    parts.append(variants_table(product))
    if show_detail:
        parts.append(detail(product))
    if banner:
        parts.append(banner)
    return "\n\n".join(parts)


def exit_code(cards: list[Card]) -> int:
    """제품 팔에서 '자신 있게 틀림' 이 하나라도 있으면 실패다. 1순위 약속이 깨진 것이다."""
    return 1 if cards[0].count(Outcome.CONFIDENT_WRONG) else 0
