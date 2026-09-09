"""장별로 이름과 그림을 짝지어 판정하는 파이프라인.

한 장에 구조가 하나뿐이면 장 전체의 이름을 쓴다.
여럿이면 어느 이름이 어느 그림의 것인지 좌표로 가린다. 가릴 수 없으면 보류한다.
아무 이름에나 맞춰보는 것은 틀린 그림을 통과시키는 지름길이다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .extract import Box, Picture, Slide, TextBox, load
from .names import PubChemResolver, Reference, candidates
from .ocsr import Engine, load_engines
from .verdict import Finding, Verdict, judge

MAX_LOOKUPS_PER_SLIDE = 25
MAX_REFERENCES_PER_SLIDE = 3

# 가장 가까운 상자보다 이 배수 안에 드는 다른 상자가 다른 화합물을 가리키면
# 어느 쪽이 이 그림의 이름인지 단정할 수 없다.
AMBIGUITY_RATIO = 1.3

# 가로로 이만큼 겹치면 그림의 캡션(위/아래 라벨)으로 본다.
CAPTION_OVERLAP = 0.5


@dataclass
class SlideResult:
    slide: Slide
    references: list[Reference]
    findings: list[tuple[Path, Finding]]


class _Budget:
    """장 하나가 쓸 수 있는 이름 조회 횟수. PubChem을 무한정 두드리지 않는다."""

    def __init__(self, total: int = MAX_LOOKUPS_PER_SLIDE):
        self.left = total

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _resolve(text: str, resolver: PubChemResolver, budget: _Budget) -> list[Reference]:
    """텍스트에서 PubChem이 인정한 화합물 이름만 남긴다."""
    found: list[Reference] = []
    seen: set[str] = set()
    for phrase in candidates(text):
        if not budget.take():
            break
        ref = resolver.resolve(phrase)
        if ref is None or ref.inchikey in seen:
            continue
        seen.add(ref.inchikey)
        found.append(ref)
        if len(found) >= MAX_REFERENCES_PER_SLIDE:
            break
    return found


def references_for(slide: Slide, resolver: PubChemResolver) -> list[Reference]:
    """장 전체에서 인식된 화합물 이름 (표시용, 그리고 그림이 하나일 때의 참조)."""
    return _resolve(slide.text, resolver, _Budget())


def _affinity(picture: Box, text: Box) -> float:
    """작을수록 이 그림의 이름일 가능성이 높다.

    중심 거리를 쓰되, 그림과 가로로 겹쳐 위아래에 붙은 상자는 캡션으로 보고 우대한다.
    """
    distance = math.hypot(picture.cx - text.cx, picture.cy - text.cy)
    span = min(picture.w, text.w)
    if span > 0:
        overlap = max(0.0, min(picture.x + picture.w, text.x + text.w) - max(picture.x, text.x))
        if overlap / span >= CAPTION_OVERLAP:
            distance *= 0.5
    return distance


def _name_boxes(
    text_boxes: list[TextBox], resolver: PubChemResolver, budget: _Budget
) -> list[tuple[TextBox, list[Reference]]]:
    """좌표가 있고 화합물 이름이 들어 있는 텍스트 상자만 남긴다."""
    out: list[tuple[TextBox, list[Reference]]] = []
    for text_box in text_boxes:
        if text_box.box is None:
            continue
        found = _resolve(text_box.text, resolver, budget)
        if found:
            out.append((text_box, found))
    return out


def _assign(
    pictures: list[Picture], named: list[tuple[TextBox, list[Reference]]]
) -> dict[int, int]:
    """그림 하나에 이름 상자 하나씩 배타적으로 짝지어 준다.

    캡션 하나는 그림 하나의 것이다. 격자로 배치된 장에서 아래 그림의 캡션이
    위 그림에도 가까워 보이는 문제를, 가까운 짝부터 확정해 해소한다.
    """
    pairs = sorted(
        (
            (_affinity(pic.box, text_box.box), pi, ti)
            for pi, pic in enumerate(pictures)
            if pic.box is not None
            for ti, (text_box, _) in enumerate(named)
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    taken_pic: set[int] = set()
    taken_txt: set[int] = set()
    chosen: dict[int, int] = {}
    for _, pi, ti in pairs:
        if pi in taken_pic or ti in taken_txt:
            continue
        chosen[pi] = ti
        taken_pic.add(pi)
        taken_txt.add(ti)
    return chosen


def _references_near(
    index: int,
    picture: Picture,
    named: list[tuple[TextBox, list[Reference]]],
    assignment: dict[int, int],
) -> tuple[list[Reference], str | None]:
    """배타적으로 짝지어진 이름을 돌려준다.

    짝을 못 얻었거나, 아직 아무 그림도 가져가지 않은 다른 이름이 비슷한 거리에
    있으면 어느 쪽이 이 그림의 이름인지 단정할 수 없으므로 보류한다.
    """
    if picture.box is None:
        return [], "그림의 위치를 알 수 없어 이름과 짝지을 수 없음"
    if index not in assignment:
        return [], "이 그림과 짝지을 이름을 찾지 못함"

    ti = assignment[index]
    text_box, chosen = named[ti]
    best = _affinity(picture.box, text_box.box)
    claimed = set(assignment.values())
    for other_i, (other_box, other_refs) in enumerate(named):
        if other_i == ti or other_i in claimed:
            continue
        if _affinity(picture.box, other_box.box) > best * AMBIGUITY_RATIO:
            continue
        if {r.inchikey for r in other_refs} != {r.inchikey for r in chosen}:
            return [], "이 그림과 짝지을 이름이 모호함 (비슷한 거리에 다른 이름이 있음)"
    return chosen, None


def run(
    path: Path,
    work_dir: Path,
    engines: list[Engine] | None = None,
    molscribe_checkpoint: Path | None = None,
) -> list[SlideResult]:
    engines = load_engines(molscribe_checkpoint) if engines is None else engines
    resolver = PubChemResolver()
    results: list[SlideResult] = []

    for slide in load(path, work_dir):
        budget = _Budget()
        slide_refs = _resolve(slide.text, resolver, budget)
        single = len(slide.pictures) <= 1
        findings: list[tuple[Path, Finding]] = []

        named: list[tuple[TextBox, list[Reference]]] = []
        assignment: dict[int, int] = {}
        if not single and slide.has_layout:
            named = _name_boxes(slide.text_boxes, resolver, budget)
            assignment = _assign(slide.pictures, named)

        for index, picture in enumerate(slide.pictures):
            if single:
                refs, blocked = slide_refs, None
            elif not slide.has_layout:
                refs, blocked = [], "한 장에 구조가 여럿인데 좌표가 없어 짝지을 수 없음"
            else:
                refs, blocked = _references_near(index, picture, named, assignment)

            if blocked is not None:
                findings.append((picture.path, Finding(Verdict.ABSTAIN, blocked)))
                continue

            predictions = []
            for engine in engines:
                predictions.extend(engine.recognize_all(picture.path))
            findings.append((picture.path, judge(refs, predictions)))

        results.append(SlideResult(slide, slide_refs, findings))
    return results


def summarize(results: list[SlideResult]) -> dict[Verdict, int]:
    counts = {v: 0 for v in Verdict}
    for r in results:
        for _, finding in r.findings:
            counts[finding.verdict] += 1
    return counts
