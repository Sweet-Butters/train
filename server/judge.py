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
GRADE_CONSENSUS = "strong"


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



def _lenient_formula(smiles: str | None) -> str | None:
    """원자가 검사 없이 파싱해 **가장 큰 조각**의 중원자 조성을 센다.

    chemcheck.keys.heavy_atom_formula 는 조각을 sanitize=True 로 가르는데,
    주 조각의 원자가가 깨져 있으면 조각 분리를 포기하고 전체를 센다. 그래서
    Gemini 카페인에서 MolScribe 가 낸 'CC.CN1C=N(C)C2=C1...' 이 C11N4O2 로
    나온다 - 앞의 CC 는 인식기가 지어낸 부스러기다.

    여기서는 조각도 sanitize=False 로 갈라 가장 큰 것만 센다. '가장 큰 조각'
    은 규칙이지 판단이 아니다. 그래야 파싱에 실패한 인식기도 자기가 무엇을
    봤는지 말할 수 있다 - 오늘 그 한 건이 '탄소 하나 많음' 의 두 번째 증인이었다.
    """
    if not smiles or not smiles.strip():
        return None
    from collections import Counter

    from rdkit import Chem

    best = None
    for part in smiles.strip().split("."):
        mol = Chem.MolFromSmiles(part, sanitize=False)
        if mol is None:
            continue
        n = mol.GetNumAtoms()
        if best is None or n > best[0]:
            best = (n, mol)
    if best is None:
        return None
    counts = Counter(a.GetSymbol() for a in best[1].GetAtoms())
    if not counts:
        return None

    def part_of(sym: str) -> str:
        k = counts[sym]
        return "" if not k else (sym if k == 1 else f"{sym}{k}")

    head = part_of("C")
    rest = "".join(part_of(sym) for sym in sorted(counts) if sym != "C")
    return (head + rest) or None


def _engine_entry(read: EngineRead) -> dict:
    return {
        "engine": read.engine,
        "smiles": read.smiles,
        "inchikey": smiles_to_inchikey(read.smiles),
        # 파싱에 실패한 인식기도 자기가 센 원자는 말할 수 있어야 한다.
        "heavy_formula": _lenient_formula(read.smiles),
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


# 읽은 구조가 정본보다 이만큼 크면 "다른 분자" 가 아니라 "못 읽은 것" 으로 본다.
# 근거(실측 6·8): 슬라이드를 통째로 먹이면 제목·3D 렌더·설명이 섞여 인식기가
# 탄소 100~256 개짜리 사슬을 지어낸다. GPT 가 **맞게** 그린 카페인도 통째로 넣으면
# C256 을 읽고 '오류' 로 판정됐다 - 이 프로젝트가 가장 하면 안 되는 실패다.
# 판단은 추론이 아니라 원자 수 비교다.
NONSENSE_ATOM_RATIO = 3.0
NONSENSE_ATOM_FLOOR = 40


def _heavy_atom_count(formula: str | None) -> int | None:
    """'C9N4O2' -> 15. 못 세면 None."""
    if not formula:
        return None
    import re
    total = 0
    for sym, num in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
        if not sym:
            continue
        total += int(num) if num else 1
    return total or None


def _nonsense_reason(ref_smiles: str | None, read_formula: str | None) -> str | None:
    """읽은 것이 정본보다 터무니없이 크면 그 사유를 돌려준다. 아니면 None."""
    if not ref_smiles or not read_formula:
        return None
    ref_n = _heavy_atom_count(heavy_atom_formula(ref_smiles))
    read_n = _heavy_atom_count(read_formula)
    if not ref_n or not read_n:
        return None
    if read_n >= NONSENSE_ATOM_FLOOR and read_n >= ref_n * NONSENSE_ATOM_RATIO:
        return (f"읽은 구조가 중원자 {read_n} 개로 정본({ref_n} 개)보다 지나치게 큽니다 - "
                f"그림에서 구조를 제대로 읽지 못한 것으로 봅니다")
    return None


def build_result(name: str, ref, reads: list[EngineRead]) -> dict:
    """동결 계약대로의 응답 하나. 여기서 예외를 올리지 않는다.

    ref 는 chemcheck.names.Reference 또는 None (이름을 못 찾았을 때).
    reads 는 엔진이 낸 예측들. 비어 있으면 그림을 읽지 못한 것이다.
    """
    reasons: list[str] = []
    engines = [_engine_entry(r) for r in reads]

    # 엔진마다 (읽은 것, InChIKey, 골격). RDKit 이 분자로 못 받는 답은 유효하지 않다.
    parsed = []
    for r in reads:
        key = smiles_to_inchikey(r.smiles) if r.smiles else None
        parsed.append((r, key, skeleton(key) if key else None))
    valid = [x for x in parsed if x[1]]

    # ── 합의 게이트: 유효한 읽기가 둘 이상인데 골격이 갈리면 판정하지 않는다 ──
    # README 가 약속한 그것이다. 어느 쪽이 맞는지 우리가 모르므로, 이름과 대조해
    # 맞는 쪽을 고르면 그건 확증 편향이지 판정이 아니다.
    skels = {x[2] for x in valid}
    if len(valid) >= 2 and len(skels) > 1:
        for r, key, sk in valid:
            reasons.append(f"{r.engine} 는 {sk} 로 읽었습니다")

        # 갈렸어도 **이름이 심판을 본다.** 한 인식기가 이름의 정본과 같은 골격을
        # 읽었다면, 그건 서로 다른 계보의 정보원 둘(픽셀 하나 + 텍스트 하나)이
        # 일치한 것이다 - 다른 인식기가 오독했을 가능성이 높다.
        # 어느 엔진을 주(主)로 삼는 것과 다르다: 실측에서 molscribe 가 맞은 적도
        # (phenol·ethanol) decimer 가 맞은 적도 있어 고정 서열은 근거가 없다.
        # 둘 다 정본과 다르면 그때는 진짜로 모르는 것이므로 판정하지 않는다.
        ref_skel_now = skeleton(ref.inchikey) if ref else None
        agreeing = [x for x in valid if ref_skel_now and x[2] == ref_skel_now]
        if agreeing:
            winner = agreeing[0]
            others = " · ".join(x[0].engine for x in valid if x is not winner)
            reasons.append(
                f"{winner[0].engine} 가 이름의 정본과 같은 골격({ref_skel_now})을 읽었습니다 - "
                f"{others} 는 다르게 읽었으므로 확신 등급은 weak 입니다")
            return {"verdict": "match", "grade": GRADE_SINGLE_ENGINE,
                    "reference": _reference_block(ref),
                    "read": {"smiles": winner[0].smiles, "inchikey": winner[1],
                             "heavy_formula": heavy_atom_formula(winner[0].smiles),
                             "engines": engines},
                    "reasons": reasons}

        reasons.append("인식기들이 서로 다른 골격을 읽었고 어느 쪽도 이름의 정본과 맞지 않아 판정하지 않습니다")
        return {"verdict": "unreadable", "grade": None,
                "reference": _reference_block(ref),
                "read": {"smiles": valid[0][0].smiles, "inchikey": valid[0][1],
                         "heavy_formula": heavy_atom_formula(valid[0][0].smiles),
                         "engines": engines},
                "reasons": reasons}

    # 대표 예측: 유효한 것이 있으면 그중 첫째, 없으면 첫 엔진의 답(파싱 실패 진단용).
    primary = valid[0][0] if valid else (reads[0] if reads else None)
    read_smiles = primary.smiles if primary else None
    read_key = valid[0][1] if valid else None
    read_formula = heavy_atom_formula(read_smiles) if read_smiles else None
    grade = GRADE_CONSENSUS if len(valid) >= 2 else GRADE_SINGLE_ENGINE

    read_block = {
        "smiles": read_smiles,
        "inchikey": read_key,
        "heavy_formula": read_formula,
        "engines": engines,
    }

    if ref is None:
        reasons.append(f"이름 '{name}' 에 해당하는 정본을 표에서 찾지 못했습니다")

    # ── 터무니없는 크기: 판정하지 않는다 ──────────────────────────────────
    # 슬라이드를 통째로 넣으면 인식기가 거대한 사슬을 지어낸다. 그것을 '오류' 라
    # 부르면 맞게 그린 그림을 틀렸다고 말하게 된다.
    nonsense = _nonsense_reason(ref.smiles if ref else None, read_formula)
    if nonsense:
        reasons.append(nonsense)
        reasons.append("구조 부분만 잘라서 다시 시도해 보세요")
        return {"verdict": "unreadable", "grade": None,
                "reference": _reference_block(ref), "read": read_block, "reasons": reasons}

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
        # 다만 **읽기에 대한 확신**은 별개다 - 두 인식기가 독립적으로 같은 골격을
        # 읽었다면 그것은 정보이고, 버리면 안 된다. grade 는 판정이 아니라 읽기의 등급이다.
        if grade == GRADE_CONSENSUS:
            names = " · ".join(x[0].engine for x in valid)
            reasons.append(
                f"다만 인식기 {len(valid)}개({names})가 서로 다른 구조로 학습됐는데도 "
                f"같은 골격({skeleton(read_key)})을 독립적으로 읽었습니다")
        else:
            reasons.append("인식기 하나가 읽은 것이고, 대조할 두 번째 정보원이 없어 확인되지 않았습니다")
        return {"verdict": "unreadable", "grade": grade,
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

    if grade == GRADE_CONSENSUS:
        names = " · ".join(x[0].engine for x in valid)
        reasons.append(f"인식기 {len(valid)}개({names})가 같은 골격에 합의했습니다 - 확신 등급 strong")
    else:
        reasons.append("유효한 구조를 낸 인식기가 하나뿐이라 확신 등급은 weak 입니다")
    return {"verdict": verdict, "grade": grade,
            "reference": _reference_block(ref), "read": read_block, "reasons": reasons}
