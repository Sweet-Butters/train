"""양방향 구조 도구의 알맹이. 그림 -> SMILES(recognize), 이름 -> 구조(draw).

LLM 은 구조 그림을 못 읽고, 못 그리고, 이상한 SMILES 를 준다. 이 모듈은 그
세 가지의 대체재다. 여기에는 추론이 없다:

- recognize: 인식기 둘이 InChIKey 골격(앞 14자)에서 합의해야 답이다. 갈리면
  '확신 없음' 과 후보 전부를 돌려준다. 인식기 하나의 답을 그냥 내보내는 순간
  LLM 과 똑같이 자신 있게 틀리는 도구가 된다. 답이든 후보든 RDKit 으로 다시
  그려 PNG 로 남긴다 - 사람 눈이 마지막 판정이다.
- draw: PubChem 정본 SMILES 를 RDKit 으로 그린다. 못 찾으면 못 찾았다고
  말한다. 비슷한 것을 추측하지 않는다.
"""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rdkit import Chem

from .inputs import UnreadableImage, prepare_image
from .keys import skeleton, smiles_to_inchikey
from . import names as _names
from .names import PubChemResolver, Reference, korean_name
from .ocsr import Engine, Prediction
from .render import describe, draw_diff, draw_pair, draw_png


@dataclass(frozen=True)
class EngineResult:
    """인식기 하나가 낸 것. 원문을 버리지 않는다 - 정규화가 실패하면 원문이 증거다."""

    engine: str
    raw_smiles: str
    canonical: str | None      # RDKit canonical. 못 읽으면 None
    inchikey: str | None       # 못 읽으면 None
    confidence: float          # 인식기가 안 주면 nan

    @property
    def skeleton(self) -> str | None:
        return skeleton(self.inchikey)

    @property
    def has_confidence(self) -> bool:
        return not math.isnan(self.confidence)


@dataclass
class Candidate:
    """골격 하나. 같은 골격을 낸 결과들을 묶는다."""

    skeleton: str
    smiles: str                 # 대표 canonical SMILES
    inchikey: str
    results: list[EngineResult] = field(default_factory=list)
    image: Path | None = None   # 다시 그린 그림
    name: str | None = None     # PubChem 이 아는 이름. 없으면 None

    @property
    def engines(self) -> list[str]:
        return [r.engine for r in self.results]

    @property
    def confidence(self) -> float:
        scored = [r.confidence for r in self.results if r.has_confidence]
        return max(scored) if scored else float("nan")


@dataclass
class DiffImage:
    """물러났을 때 후보 두 개의 다른 곳을 칠한 한 장. 질문을 바꾸는 그림이다:
    '둘 중 뭐가 맞나' 가 아니라 '원본의 이 자리에 이 조각이 있었나'."""

    left: int            # 후보 번호 (1부터)
    right: int
    image: Path
    lines: list[str]     # 사람의 말로 적은 차이


def name_of(inchikey: str) -> str | None:
    """InChIKey 의 이름. C 의 names.name_for_inchikey 가 있으면 부르고, 없거나 죽으면 None.

    돌려주는 문장은 사람이 읽는 한 줄이다: '아스피린 (aspirin; 2-acetyloxybenzoic acid)'.
    이름을 지어내지 않는다 - PubChem 이 모르면 None 이다.
    """
    lookup = getattr(_names, "name_for_inchikey", None)
    if lookup is None:
        return None
    try:
        found = lookup(inchikey)
    except Exception:
        return None
    if not found:
        return None
    korean = getattr(found, "korean", None)
    common = getattr(found, "common", None)
    iupac = getattr(found, "iupac", None)
    latin = "; ".join(x for x in (common, iupac) if x)
    if korean and latin:
        return f"{korean} ({latin})"
    return korean or latin or None


@dataclass
class Recognition:
    image: Path
    status: str          # agreed | uncertain | single | no_result | no_engine | unreadable
    reason: str
    results: list[EngineResult] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    diffs: list[DiffImage] = field(default_factory=list)   # 물러남일 때 후보 쌍별 diff
    prepared: Path | None = None          # 인식기에 실제로 넘긴 그림 (inputs.prepare_image)
    notes: list[str] = field(default_factory=list)      # 입력을 어떻게 다듬었는가
    warnings: list[str] = field(default_factory=list)   # 다듬지 못해 품질이 의심되는 것

    @property
    def answer(self) -> Candidate | None:
        """합의된 답. 합의가 아니면 None - 후보를 답으로 내보내지 않는다."""
        return self.candidates[0] if self.status == "agreed" else None

    @property
    def has_name_lookup(self) -> bool:
        return getattr(_names, "name_for_inchikey", None) is not None


def normalize(pred: Prediction) -> EngineResult:
    mol = Chem.MolFromSmiles(pred.smiles) if pred.smiles and pred.smiles.strip() else None
    canonical = Chem.MolToSmiles(mol) if mol is not None else None
    return EngineResult(
        pred.engine, pred.smiles, canonical, smiles_to_inchikey(pred.smiles), pred.confidence,
    )


def engine_family(name: str) -> str:
    """'decimer/x0.9', 'molscribe@.venv310' -> 'decimer', 'molscribe'.

    SelfConsistent 는 한 인식기를 여러 이름으로 낸다. 합의는 인식기 '종류' 사이의
    일이다. 같은 인식기가 자기와 합의한 것을 두 인식기의 합의로 세지 않는다.
    """
    for sep in ("/", "@", "-"):
        name = name.split(sep)[0]
    return name


def _representative(results: list[EngineResult]) -> EngineResult:
    """신뢰도가 있는 결과 중 가장 높은 것. 없으면 처음 것."""
    scored = [r for r in results if r.has_confidence]
    return max(scored, key=lambda r: r.confidence) if scored else results[0]


def _group(results: list[EngineResult]) -> list[Candidate]:
    groups: dict[str, Candidate] = {}
    for r in results:
        if r.skeleton is None or r.canonical is None or r.inchikey is None:
            continue
        cand = groups.get(r.skeleton)
        if cand is None:
            cand = groups[r.skeleton] = Candidate(r.skeleton, r.canonical, r.inchikey)
        cand.results.append(r)
        best = _representative(cand.results)
        cand.smiles, cand.inchikey = best.canonical or cand.smiles, best.inchikey or cand.inchikey
    # 인식기 종류가 많이 든 골격부터, 그 다음 신뢰도 순.
    return sorted(
        groups.values(),
        key=lambda c: (-len({engine_family(e) for e in c.engines}),
                       -(c.confidence if not math.isnan(c.confidence) else -1.0)),
    )


def recognize(image: Path, engines: list[Engine], out_dir: Path | None = None,
              work_dir: Path | None = None) -> Recognition:
    """그림 하나를 다듬어(inputs.prepare_image) 인식기 전부에 먹이고 합의를 본다.

    다시 그린 그림은 out_dir 에, 다듬은 입력은 work_dir 에 남긴다 (기본: out_dir/prepared).
    """
    image = Path(image)
    if not engines:
        return Recognition(image, "no_engine", "인식기 없음 - 설치된 구조 인식기(OCSR)가 없습니다")

    # 입력 정규화는 B 의 inputs.py 몫이다. 여기서는 부르고 결과만 싣는다.
    if work_dir is None:
        work_dir = (Path(out_dir) / "prepared") if out_dir is not None else Path(tempfile.mkdtemp(prefix="chemcheck-"))
    try:
        prep = prepare_image(image, Path(work_dir))
    except (UnreadableImage, FileNotFoundError, OSError) as exc:
        return Recognition(image, "unreadable", f"그림을 읽을 수 없음 - {exc}")

    results: list[EngineResult] = []
    for engine in engines:
        try:
            preds = engine.recognize_all(prep.path)
        except Exception as exc:  # 인식기 하나가 죽어도 나머지는 본다
            preds = []
            results.append(EngineResult(engine.name, f"(실패: {type(exc).__name__}: {exc})",
                                        None, None, float("nan")))
        results.extend(normalize(p) for p in preds)
        if not preds and getattr(engine, "last_error", None):
            # 결과가 없는 이유를 표에 남긴다. 죽은 인식기와 못 읽은 인식기는 다르다.
            results.append(EngineResult(engine.name, f"(실패: {engine.last_error})",
                                        None, None, float("nan")))

    candidates = _group(results)
    families = {engine_family(r.engine) for r in results if r.skeleton is not None}
    unreadable = [r for r in results if r.skeleton is None]

    if not candidates:
        if unreadable:
            why = ("인식기가 낸 SMILES 를 구조로 읽을 수 없음: "
                   + ", ".join(f"{r.engine}={r.raw_smiles}" for r in unreadable))
        else:
            why = "인식 결과 없음 - 인식기가 아무것도 내지 못했습니다"
        rec = Recognition(image, "no_result", why, results)
    elif len(candidates) > 1:
        detail = ", ".join(f"{'+'.join(c.engines)}={c.skeleton}" for c in candidates)
        rec = Recognition(image, "uncertain",
                          f"확신 없음 - 인식기끼리 골격이 다릅니다 ({detail})", results, candidates)
    elif len(families) < 2:
        rec = Recognition(image, "single",
                          f"확신 없음 - 인식기 하나({', '.join(sorted(families))})의 답뿐이라 "
                          "합의를 볼 수 없습니다", results, candidates)
    else:
        cand = candidates[0]
        note = ""
        if len({r.inchikey for r in cand.results}) > 1:
            note = " (골격은 같으나 입체화학은 갈림 - 그림에서 확인할 것)"
        if unreadable:
            note += " (" + ", ".join(f"{r.engine} 결과는 읽지 못함" for r in unreadable) + ")"
        rec = Recognition(image, "agreed",
                          f"인식기 {len(families)}개가 골격 {cand.skeleton} 에 합의{note}",
                          results, candidates)

    rec.prepared, rec.notes, rec.warnings = prep.path, list(prep.notes), list(prep.warnings)
    for c in rec.candidates:
        c.name = name_of(c.inchikey)
    if out_dir is not None and candidates:
        _render_candidates(rec, Path(out_dir))
    return rec


def _render_candidates(rec: Recognition, out_dir: Path) -> None:
    """답 또는 후보 각각을 다시 그린다. 후보가 둘이면 서로 다른 곳을 칠한다."""
    stem = rec.image.stem
    cands = rec.candidates
    if rec.status == "agreed":
        cands[0].image = draw_png(cands[0].smiles, out_dir / f"{stem}.answer.png",
                                  legend=f"{stem}: {cands[0].smiles}")
        return
    if len(cands) == 2:
        a, b = cands
        a.image, b.image = draw_pair(
            a.smiles, b.smiles,
            out_dir / f"{stem}.cand1.png", out_dir / f"{stem}.cand2.png",
            (f"cand1 {'+'.join(a.engines)}", f"cand2 {'+'.join(b.engines)}"),
        )
    else:
        for i, c in enumerate(cands, start=1):
            c.image = draw_png(c.smiles, out_dir / f"{stem}.cand{i}.png",
                               legend=f"cand{i} {'+'.join(c.engines)}")

    # 후보 쌍마다 다른 곳을 칠한 한 장. 후보가 둘이면 한 장, 셋이면 세 장.
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            suffix = "diff" if len(cands) == 2 else f"diff{i + 1}v{j + 1}"
            la, lb = f"cand{i + 1} {'+'.join(a.engines)}", f"cand{j + 1} {'+'.join(b.engines)}"
            path, d = draw_diff(a.smiles, b.smiles, out_dir / f"{stem}.{suffix}.png", (la, lb))
            if path is not None and d is not None:
                rec.diffs.append(DiffImage(i + 1, j + 1, path,
                                           describe(d, f"후보 {i + 1}", f"후보 {j + 1}")))


# ── 이름 -> 구조 ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Drawn:
    query: str
    name: str            # 실제로 조회한 (영문) 이름
    smiles: str          # PubChem 정본
    canonical: str       # RDKit canonical
    inchikey: str
    formula: str
    source: str          # pubchem | cache | offline
    image: Path | None


def lookup_name(query: str) -> str:
    """한글 이름은 표로 영문으로 바꾼다. 표에 없으면 그대로 조회한다 (PubChem 404 가 답)."""
    q = query.strip()
    if any("가" <= ch <= "힣" for ch in q):
        return korean_name(q) or q
    return q


def draw(query: str, out_dir: Path | None = None,
         resolver: PubChemResolver | None = None) -> Drawn | None:
    """이름으로 PubChem 정본 구조를 얻어 그린다. 못 찾으면 None. 추측하지 않는다."""
    name = lookup_name(query)
    resolver = resolver or PubChemResolver()
    ref: Reference | None = resolver.resolve(name)
    if ref is None or not ref.smiles:
        return None
    mol = Chem.MolFromSmiles(ref.smiles)
    if mol is None:
        return None
    canonical = Chem.MolToSmiles(mol)
    image = None
    if out_dir is not None:
        safe = "".join(ch if ch.isalnum() else "_" for ch in query.strip()) or "structure"
        image = draw_png(ref.smiles, Path(out_dir) / f"{safe}.png", legend=f"{query} ({name})")
    return Drawn(query, name, ref.smiles, canonical, ref.inchikey, ref.formula, ref.source, image)
