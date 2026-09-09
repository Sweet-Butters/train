"""장별로 이름과 그림을 짝지어 판정하는 파이프라인."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .extract import Slide, load
from .names import PubChemResolver, Reference, candidates
from .ocsr import Engine, load_engines
from .verdict import Finding, Verdict, judge

MAX_LOOKUPS_PER_SLIDE = 25
MAX_REFERENCES_PER_SLIDE = 3


@dataclass
class SlideResult:
    slide: Slide
    references: list[Reference]
    findings: list[tuple[Path, Finding]]


def references_for(slide: Slide, resolver: PubChemResolver) -> list[Reference]:
    """장 텍스트에서 PubChem이 인정한 화합물 이름만 남긴다."""
    found: list[Reference] = []
    seen_keys: set[str] = set()
    for phrase in candidates(slide.text)[:MAX_LOOKUPS_PER_SLIDE]:
        ref = resolver.resolve(phrase)
        if ref is None or ref.inchikey in seen_keys:
            continue
        seen_keys.add(ref.inchikey)
        found.append(ref)
        if len(found) >= MAX_REFERENCES_PER_SLIDE:
            break
    return found


def run(path: Path, work_dir: Path, engines: list[Engine] | None = None) -> list[SlideResult]:
    engines = load_engines() if engines is None else engines
    resolver = PubChemResolver()
    results: list[SlideResult] = []

    for slide in load(path, work_dir):
        refs = references_for(slide, resolver)
        findings: list[tuple[Path, Finding]] = []
        for image in slide.images:
            predictions = []
            for engine in engines:
                pred = engine.recognize(image)
                if pred is not None:
                    predictions.append(pred)
            findings.append((image, judge(refs, predictions)))
        results.append(SlideResult(slide, refs, findings))
    return results


def summarize(results: list[SlideResult]) -> dict[Verdict, int]:
    counts = {v: 0 for v in Verdict}
    for r in results:
        for _, finding in r.findings:
            counts[finding.verdict] += 1
    return counts
