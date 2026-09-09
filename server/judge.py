"""판정 층. **추론이 0이다** - 문자열 비교와 원자 세기뿐이다.

이 파일에는 모델도 네트워크 호출도 없다. 입력은 이미 읽어낸 SMILES 와 이름이고,
출력은 docs/WEB_CONTRACT.md 가 동결한 JSON 이다. 그래서 엔진 없이도 테스트된다.

판정 규칙 (바꾸지 않는다):
  1. name -> 정본 SMILES/InChIKey (오프라인 표 우선, 없으면 PubChem)
  2. image -> DECIMER -> SMILES -> RDKit -> InChIKey
  3. 대조는 **InChIKey 앞 14자(골격)** 문자열 동일성뿐이다
  4. 읽은 SMILES 가 RDKit 파싱에 실패하면 판정이 아니라 unreadable.
     그때도 sanitize=False 로 중원자를 세서 조성을 보고한다 (수소는 세지 않는다 -
     원자가가 깨진 구조에서 암시적 수소는 의미가 없다)
  5. reasons 는 관찰만 적는다. 추측하지 않는다

입체화학은 판정하지 않는다. 골격 14자만 본다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from chemcheck.keys import heavy_atom_formula, skeleton, smiles_to_inchikey

# 엔진이 DECIMER 하나뿐이므로 등급은 언제나 weak 다. 두 엔진이 합의해야 strong 인데
# MolScribe(torch<2.0, py3.10)는 이 배포에 올리지 않았다. 없는 확신을 지어내지 않는다.
GRADE_SINGLE_ENGINE = "weak"


@dataclass(frozen=True)
class EngineRead:
    """엔진 하나가 그림 한 장에 낸 답. confidence 는 엔진마다 다른 자다."""
    engine: str
    smiles: str
    confidence: float = float("nan")


def _conf(value: float) -> float | None:
    """NaN 은 JSON 이 담지 못한다. 없는 값은 null 로 - 0.0 으로 지어내지 않는다."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 4)


def _engine_entry(read: EngineRead) -> dict:
    return {
        "engine": read.engine,
        "smiles": read.smiles,
        "inchikey": smiles_to_inchikey(read.smiles),
        "confidence": _conf(read.confidence),
    }


def _reference_block(ref) -> dict | None:
    """chemcheck.names.Reference -> 계약이 정한 reference 블록."""
    if ref is None:
        return None
    return {
        "name": ref.name,
        "smiles": ref.smiles or None,
        "inchikey": ref.inchikey,
        "formula": ref.formula or None,
    }


def _formula_reason(ref_smiles: str | None, read_formula: str | None) -> str | None:
    """중원자 조성이 다를 때만 한 문장. 같으면 굳이 말하지 않는다."""
    if not read_formula or not ref_smiles:
        return None
    ref_formula = heavy_atom_formula(ref_smiles)
    if not ref_formula or ref_formula == read_formula:
        return None
    return f"중원자 조성 {read_formula} vs {ref_formula}"


def build_result(name: str, ref, reads: list[EngineRead]) -> dict:
    """동결 계약대로의 응답 하나. 여기서 예외를 올리지 않는다.

    ref 는 chemcheck.names.Reference 또는 None (이름을 못 찾았을 때).
    reads 는 엔진이 낸 예측들. 비어 있으면 그림을 읽지 못한 것이다.
    """
    reasons: list[str] = []
    engines = [_engine_entry(r) for r in reads]

    # 대표 예측: 첫 엔진의 답. 엔진이 하나뿐이므로 고를 것이 없다.
    primary = reads[0] if reads else None
    read_smiles = primary.smiles if primary else None
    read_key = smiles_to_inchikey(read_smiles) if read_smiles else None
    read_formula = heavy_atom_formula(read_smiles) if read_smiles else None

    read_block = {
        "smiles": read_smiles,
        "inchikey": read_key,
        "heavy_formula": read_formula,
        "engines": engines,
    }

    if ref is None:
        reasons.append(f"이름 '{name}' 에 해당하는 정본을 표에서 찾지 못했습니다")

    # ── 읽지 못한 경우: 판정이 아니라 unreadable ──────────────────────────
    if primary is None:
        reasons.append("그림에서 구조를 읽지 못했습니다")
        return {"verdict": "unreadable", "grade": None,
                "reference": _reference_block(ref), "read": read_block, "reasons": reasons}

    if read_key is None:
        # SMILES 는 나왔지만 RDKit 이 분자로 받아들이지 못했다. 원자가가 깨졌거나
        # 엔진이 문법만 그럴듯한 문자열을 지어낸 것이다. 판정하지 않는다.
        reasons.append("읽은 SMILES 를 RDKit 이 분자로 해석하지 못했습니다")
        if read_formula:
            reasons.append(f"원자가 검사를 끄고 세면 중원자 조성은 {read_formula} 입니다")
            hint = _formula_reason(ref.smiles if ref else None, read_formula)
            if hint:
                reasons.append(hint)
        return {"verdict": "unreadable", "grade": None,
                "reference": _reference_block(ref), "read": read_block, "reasons": reasons}

    if ref is None:
        # 읽기는 했으나 대조할 정본이 없다. 대조하지 않은 것을 판정이라 부르지 않는다.
        return {"verdict": "unreadable", "grade": None,
                "reference": None, "read": read_block, "reasons": reasons}

    # ── 대조: 골격 14자 문자열 비교뿐 ───────────────────────────────────
    read_skel, ref_skel = skeleton(read_key), skeleton(ref.inchikey)
    if read_skel and ref_skel and read_skel == ref_skel:
        reasons.append(f"골격이 같습니다 (InChIKey 앞 14자 {read_skel})")
        verdict = "match"
    else:
        reasons.append(f"골격이 다릅니다 ({read_skel} vs {ref_skel})")
        hint = _formula_reason(ref.smiles, read_formula)
        if hint:
            reasons.append(hint)
        verdict = "mismatch"

    reasons.append("인식기가 하나뿐이라 확신 등급은 weak 입니다")
    return {"verdict": verdict, "grade": GRADE_SINGLE_ENGINE,
            "reference": _reference_block(ref), "read": read_block, "reasons": reasons}
