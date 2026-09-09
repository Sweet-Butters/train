"""이름 해석(트랙 C) 검증.

전부 망 없이 돈다. PubChem 을 때리는 테스트는 회선 상태에 따라 결과가 바뀌므로
검증 장치로 쓸 수 없다. 망이 필요한 검증은 scripts/build_offline_table.py 가
표를 만들 때 수행한다.

`draw` 적중률 (교과서 화합물 119개, 표기 588개, scripts/measure_names.py, 2026-09-09):

    동봉 표 없이 (망+캐시)   전 444/589 = 75.4%   ->   후 579/588 = 98.5%
    못 찾은 종류 (전)        한글 표에 없음 131 · IUPAC 4 · 약어 4 · 엉뚱한 화합물 3 · 영문 2 · 그리스문자 1
    못 찾은 종류 (후)        엉뚱한 화합물 3 · IUPAC 3 · 약어 2 · 영문 1  (아래 KNOWN_GAPS)

고친 것: 한글 표 131건(실측으로 못 찾은 것만), 입체 접두사·그리스 문자·약어 폴백.
남은 9건은 PubChem 쪽 한계이거나 일부러 안 넣은 모호한 약어다 - 여기서 지어내지 않는다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import names  # noqa: E402
from chemcheck.keys import skeleton, smiles_to_inchikey  # noqa: E402
from chemcheck.names import (  # noqa: E402
    KO_ALIASES,
    KO_WHOLE_ONLY,
    PubChemResolver,
    candidates,
    korean_name,
    score,
    variants,
)

LECTURE = ROOT / "chemcheck" / "data" / "lecture_compounds.json"

# `draw` 가 못 찾거나 다르게 찾는 표기. 전부 원인이 PubChem 쪽이거나 일부러 안 넣은 것이다.
# 하나가 여기서 빠지면(=찾게 되면) 테스트가 알려준다 - 그때 이 표에서 지운다.
KNOWN_GAPS = {
    "CO",                                   # PubChem 이 코발트(Co)로 읽는다
    "2-amino-6-oxopurine",                  # PubChem 동의어 오류: 다른 레코드(C5H3N5O)를 준다
    "5-methylpyrimidine-2,4-dione",         # PubChem 동의어 오류: 스테로이드 배당체를 준다
    "cyclohexa-1,3,5-triene",               # PubChem 이 모르는 표기
    "4-hydroxyphenylalanine",               # PubChem 이 모르는 표기
    "1-methyl-4-(prop-1-en-2-yl)cyclohexene",  # PubChem 은 '...cyclohex-1-ene' 만 안다
    "IPA",                                  # 이소프탈산과 겹친다. 넣지 않는다
    "DA",                                   # 달톤과 겹친다. 넣지 않는다
    "oil of vitriol",                       # 옛 이름. PubChem 이 모른다
}

# 실제 강의 슬라이드 한 장의 모양. 한글 본문 + 영문 이름 + IUPAC 명 + 분자식 + 서지.
SLIDE = """2장. 해열진통제의 구조와 작용
아스피린(Aspirin)은 살리실산의 아세틸 유도체이다.
IUPAC name: 2-acetyloxybenzoic acid
Molecular formula C9H8O4, MW 180.16
COX-1 과 COX-2 를 비가역적으로 저해한다.
Reference: Vane JR, Nature New Biology 1971"""


def _scratch_cache() -> Path:
    """테스트가 진짜 캐시 파일을 만들어 서로를 오염시키지 않게 한다."""
    return Path(tempfile.mkdtemp(prefix="chemcheck-test-")) / "pubchem.json"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _no_network(*args, **kwargs):
    raise AssertionError("망을 타면 안 되는 경로에서 요청이 나갔다")


def test_trailing_paren_does_not_kill_the_name() -> None:
    """"아스피린(Aspirin)은" 의 "Aspirin)" 이 그대로 조회되면 PubChem 404 다."""
    found = [c.lower() for c in candidates(SLIDE)]
    assert "aspirin" in found, f"aspirin 을 못 뽑았다: {found}"
    assert not any(c.endswith(")") for c in found), f"괄호가 남았다: {found}"
    print("  '아스피린(Aspirin)은' -> 'aspirin'")
    print("통과: 짝 안 맞는 괄호 때문에 이름을 잃지 않는다.")


def test_budget_goes_to_real_names() -> None:
    """조회 예산은 25건뿐이다. 계측·서지 조각에 쓰면 정작 이름을 못 찾는다."""
    from chemcheck.pipeline import MAX_LOOKUPS_PER_SLIDE as CAP

    found = candidates(SLIDE)
    lowered = [c.lower() for c in found]

    for want in ("aspirin", "salicylic acid", "2-acetyloxybenzoic acid"):
        assert want in lowered, f"{want!r} 가 후보에 없다: {found}"
        assert lowered.index(want) < CAP, f"{want!r} 가 예산 밖으로 밀렸다"

    for junk in ("mw 180", "180 16", "molecular formula", "c9h8o4", "iupac name"):
        assert junk not in lowered, f"쓰레기 후보가 예산을 먹는다: {junk!r}"

    print(f"  후보 {len(found)}개 (예산 {CAP}) — 상위 3: {found[:3]}")
    print("통과: 예산이 진짜 이름에 쓰인다.")


def test_korean_names_survive_particles() -> None:
    """한국어는 조사가 붙어 나온다. PubChem 은 한글 자체를 해석하지 못한다."""
    assert korean_name("아스피린") == "aspirin"
    assert korean_name("아스피린은") == "aspirin"
    assert korean_name("살리실산의") == "salicylic acid"
    assert korean_name("카페인이") == "caffeine"
    assert korean_name("포도당으로") == "glucose"
    assert korean_name("해열진통제") is None, "화합물이 아닌 말을 이름으로 봤다"
    assert korean_name("작용") is None
    print("  '살리실산의' -> 'salicylic acid',  '포도당으로' -> 'glucose'")
    print("통과: 조사가 붙어도 한국어 이름을 찾는다.")


def test_ambiguous_korean_words_stay_out() -> None:
    """일상어와 겹치는 이름은 넣지 않는다. 잘못된 참조는 멀쩡한 그림을 '오류'로 만든다.

    '요소'는 urea 이자 '구성 요소'다. '자일렌'은 o-/m-/p- 가 갈려 단일 구조가 없다.
    """
    for word in ("물", "산", "당", "알코올", "요소", "자일렌", "크실렌"):
        assert word not in KO_ALIASES, f"모호한 말이 별칭에 들어왔다: {word!r}"
    print("  물·산·당·알코올·요소·자일렌 모두 별칭에 없음")
    print("통과: 모호한 이름은 해석하지 않고 판정불가로 둔다.")


def test_formula_is_not_a_name() -> None:
    """분자식은 이름 조회로 풀리지 않는다. 반면 DMSO 같은 약어는 살아야 한다."""
    assert score("C9H8O4") <= 0
    assert score("MW 180") <= 0
    assert score("1971") <= 0
    assert score("acid") <= 0, "'acid' 단독은 화합물이 아니라 분류어다"
    assert score("DMSO") > 0, "숫자 없는 대문자 약어를 분자식으로 오인했다"
    assert score("benzoic acid") > 0
    assert score("2-acetyloxybenzoic acid") > 0
    print("  C9H8O4/MW 180 -> 버림,  DMSO/benzoic acid -> 조회")
    print("통과: 분자식과 이름을 가른다.")


def test_offline_table_resolves_without_network() -> None:
    """망이 죽어도 흔한 화합물은 해석된다. KTX 안에서도 도구가 돌아야 한다."""
    resolver = PubChemResolver(cache_path=_scratch_cache())
    assert resolver.offline, "동봉한 표가 비었다 (build_offline_table.py 를 돌려야 한다)"

    real_get = names.requests.get
    names.requests.get = _no_network
    try:
        ref = resolver.resolve("Aspirin")
    finally:
        names.requests.get = real_get

    assert ref is not None, "망 없이 aspirin 을 해석하지 못했다"
    assert ref.source == "offline"
    assert ref.inchikey == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    print(f"  망 차단 상태에서 'Aspirin' -> {ref.inchikey} ({ref.source})")
    print("통과: 망 없이도 이름 참조가 나온다.")


def test_offline_table_agrees_with_rdkit() -> None:
    """동봉한 표가 실제 구조와 맞는지 RDKit 으로 대조한다.

    표가 틀리면 멀쩡한 그림이 '오류'로 보고된다. 이 프로젝트에서 가장 나쁜 실패다.
    make_demo 가 '맞다'고 못박은 구조를 정답으로 쓴다.
    """
    from scripts.make_demo import CASES

    offline = names._load_offline()
    checked = 0
    for name, smiles, correct in CASES:
        if not correct:
            continue
        entry = offline.get(name.lower())
        assert entry, f"표에 {name!r} 이 없다"
        expected = smiles_to_inchikey(smiles)
        assert entry["inchikey"] == expected, (
            f"{name}: 표 {entry['inchikey']} != 구조 {expected}"
        )
        print(f"  {name:9} {entry['inchikey']}  (RDKit 과 일치)")
        checked += 1
    assert checked >= 2, "대조한 항목이 너무 적다"
    print("통과: 동봉한 표가 실제 구조와 어긋나지 않는다.")


def test_backs_off_and_retries_on_throttling() -> None:
    """PubChem 이 503 으로 밀어내면 물러섰다 다시 묻는다. 한 번 실패로 버리지 않는다."""
    calls: list[str] = []
    payload = {"PropertyTable": {"Properties": [
        {"InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "MolecularFormula": "C9H8O4"}
    ]}}

    def flaky(url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            return FakeResponse(503, headers={"Retry-After": "0"})
        return FakeResponse(200, payload)

    resolver = PubChemResolver(cache_path=_scratch_cache(), min_interval=0.0, offline={})
    real_get = names.requests.get
    names.requests.get = flaky
    try:
        ref = resolver.resolve("aspirin")
    finally:
        names.requests.get = real_get

    assert len(calls) == 3, f"재시도를 하지 않았다 (요청 {len(calls)}회)"
    assert ref is not None and ref.inchikey == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    print(f"  503 두 번 -> 재시도 -> 200 (요청 {len(calls)}회)")
    print("통과: 일시적 스로틀링에 이름을 잃지 않는다.")


def test_network_failure_is_not_cached_as_missing() -> None:
    """망 실패를 '없는 이름'으로 캐시하면 회선이 돌아와도 계속 못 찾는다."""
    import requests as real_requests

    resolver = PubChemResolver(cache_path=_scratch_cache(), min_interval=0.0, offline={})

    def dead(url, **kwargs):
        raise real_requests.RequestException("연결 끊김")

    real_get = names.requests.get
    names.requests.get = dead
    try:
        assert resolver.resolve("aspirin") is None
    finally:
        names.requests.get = real_get

    assert "aspirin" not in resolver._cache, "망 실패를 404 처럼 캐시했다"
    print("  망 실패 -> 캐시에 남기지 않음")
    print("통과: 회선이 돌아오면 다시 조회한다.")


def test_greek_prefix_is_not_dropped() -> None:
    """그리스 문자를 버리면 다른 화합물이 된다.

    PubChem 에서 'beta-carotene' 은 OENHQHLEOONYIE, 'carotene' 은 ANVAOWXLWRTKGA 다.
    접두사를 잃은 채 조회하면 멀쩡한 그림이 '오류'로 보고된다.
    """
    found = [c.lower() for c in candidates("베타카로틴: β-carotene 은 비타민 A 의 전구체다")]
    assert "beta-carotene" in found, f"그리스 접두사를 살리지 못했다: {found}"
    assert "carotene" not in found, f"접두사를 잃은 이름이 후보에 들어왔다: {found}"
    print("  'β-carotene' -> 'beta-carotene' (PubChem 이 해석하는 형태)")
    print("통과: 그리스 접두사를 잃고 다른 화합물이 되지 않는다.")


def test_stereo_prefix_survives() -> None:
    """'(S)-ibuprofen' 처럼 괄호로 시작하는 이름이 통째로 탈락하고 있었다."""
    for text, want in [
        ("(S)-ibuprofen 이 활성 이성질체다", "(s)-ibuprofen"),
        ("trans-cinnamic acid 를 가열한다", "trans-cinnamic acid"),
        ("L-alanine 과 D-alanine", "l-alanine"),
    ]:
        found = [c.lower() for c in candidates(text)]
        assert want in found, f"{want!r} 를 못 뽑았다: {found}"
    print("  (S)-/trans-/L- 접두사 모두 후보로 남음")
    print("통과: 입체·기하 접두사가 붙어도 이름을 잃지 않는다.")


def test_vitamin_names_are_found() -> None:
    """'C' 는 한 글자라 일반 토큰 경로에서 버려진다. 줄에서 직접 집어야 한다."""
    assert "vitamin C" in candidates("vitamin C 결핍은 괴혈병을 유발한다")
    assert "vitamin B12" in candidates("비타민 B12 는 코발트를 포함한다")
    print("  'vitamin C', '비타민 B12' 모두 후보로 나옴")
    print("통과: 비타민 표기를 영문·한글 모두 잡는다.")


def test_longer_name_outranks_its_own_prefix() -> None:
    """더 짧은 접두부가 먼저 조회되면 다른 화합물이 참조로 박힌다."""
    found = [c.lower() for c in candidates("N-acetyl-p-benzoquinone imine (NAPQI) 축적")]
    full, prefix = "n-acetyl-p-benzoquinone imine", "n-acetyl-p-benzoquinone"
    assert full in found and prefix in found
    assert found.index(full) < found.index(prefix), f"짧은 접두부가 먼저다: {found[:4]}"
    print(f"  {full!r} 가 {prefix!r} 보다 먼저")
    print("통과: 더 구체적인 이름을 먼저 조회한다.")


def test_fragment_name_does_not_masquerade() -> None:
    """'CoA' 단독은 coenzyme A 로 해석된다. acetyl-CoA 와 다른 화합물이다.

    부분 이름이 전체 이름 행세를 하면 잘못된 참조가 되고, 그림은 '오류'가 된다.
    이름을 못 찾아 판정불가로 빠지는 쪽이 낫다.
    """
    found = [c.lower() for c in candidates("아세틸-CoA 가 회로로 들어간다")]
    assert "coa" not in found, f"조각 이름이 후보에 들어왔다: {found}"
    assert score("DMSO") > 0, "정상 약어까지 같이 막혔다"
    print("  'CoA' -> 후보에서 제외,  'DMSO' -> 유지")
    print("통과: 조각 이름이 전체 이름 행세를 하지 않는다.")


def _draw_lookup(query: str) -> str:
    """structure.lookup_name 과 같은 경로. 한글이면 표로, 아니면 그대로."""
    q = query.strip()
    if any("가" <= ch <= "힣" for ch in q):
        return korean_name(q) or q
    return q


def test_lecture_compounds_resolve_offline() -> None:
    """교과서 화합물 목록의 모든 표기가 망 없이 정답 골격으로 풀린다 (KNOWN_GAPS 제외).

    `draw` 의 품질은 곧 이 적중률이다. 한글은 KO_ALIASES 를, 영문·IUPAC·약어는 동봉한
    표(compounds.json)를 거친다. 둘 다 PubChem 응답을 받아 적은 것이며 여기서 구조를
    지어내지 않는다.
    """
    import json

    import requests as real_requests

    def offline_only(url, **kwargs):
        # 표에 없는 이름(KNOWN_GAPS)은 망으로 내려간다. 회선이 끊긴 것처럼 군다.
        raise real_requests.RequestException("망 없음")

    doc = json.loads(LECTURE.read_text(encoding="utf-8"))
    resolver = PubChemResolver(cache_path=_scratch_cache())
    real_get = names.requests.get
    names.requests.get = offline_only

    total = hit = stereo = 0
    misses: list[str] = []
    surprises: list[str] = []
    try:
        for c in doc["compounds"]:
            expected = c["answer"]["inchikey"]
            for kind in ("en", "iupac", "ko", "abbr"):
                for query in c[kind]:
                    total += 1
                    ref = resolver.resolve(_draw_lookup(query))
                    ok = ref is not None and skeleton(ref.inchikey) == skeleton(expected)
                    if ok and query in KNOWN_GAPS:
                        surprises.append(query)
                    if ok:
                        hit += 1
                        stereo += ref.inchikey != expected
                    elif query not in KNOWN_GAPS:
                        misses.append(f"{c['id']}/{kind}: {query!r} -> {ref}")
    finally:
        names.requests.get = real_get

    assert len(doc["compounds"]) >= 100, "목록이 100개에 못 미친다"
    assert not misses, "망 없이 못 푼 표기:\n  " + "\n  ".join(misses)
    assert not surprises, f"KNOWN_GAPS 인데 풀렸다. 표에서 지울 것: {surprises}"
    print(f"  화합물 {len(doc['compounds'])}개, 표기 {total}개: 망 없이 {hit}개 적중 "
          f"({100 * hit / total:.1f}%, 입체만 다름 {stereo}), 알려진 공백 {len(KNOWN_GAPS)}개")
    print("통과: 교과서 화합물은 KTX 안에서도 그려진다.")


def test_whole_query_words_stay_out_of_slide_text() -> None:
    """'물'·'요소' 는 `draw "물"` 처럼 통째로 물을 때만 화합물이다.

    슬라이드 본문에서 '구성 요소' 의 '요소' 를 urea 로 잡으면 잘못된 참조가 되고
    멀쩡한 그림이 '오류' 가 된다. 그래서 본문 경로(in_text)에서는 쓰지 않는다.
    """
    for word, en in KO_WHOLE_ONLY.items():
        assert word not in KO_ALIASES
        assert korean_name(word) == en
        assert korean_name(word, in_text=True) is None
    found = [c.lower() for c in candidates("물 분자의 구성 요소는 수소와 산소다")]
    assert "water" not in found and "urea" not in found, found
    print("  '물' -> water (통째로 물을 때),  본문 '물 분자의 구성 요소' -> 아무것도 안 잡음")
    print("통과: 일상어와 겹치는 이름은 draw 에서만 믿는다.")


def test_korean_spelling_variants() -> None:
    """대한화학회 새 표기·띄어쓰기·그리스 문자·비타민 표기가 모두 같은 영문 이름으로 간다."""
    for ko, en in [
        ("메테인", "methane"), ("뷰테인", "butane"), ("폼알데하이드", "formaldehyde"),
        ("다이에틸에터", "diethyl ether"), ("비스페놀 A", "bisphenol A"),
        ("비스페놀A", "bisphenol A"), ("아데노신 삼인산", "adenosine triphosphate"),
        ("β-카로틴", "beta-carotene"), ("베타-카로틴", "beta-carotene"),
        ("비타민 C", "vitamin C"), ("비타민C", "vitamin C"), ("비타민 B12", "vitamin B12"),
        ("타이레놀은", "acetaminophen"),
    ]:
        assert korean_name(ko) == en, f"{ko!r} -> {korean_name(ko)!r} (기대 {en!r})"
    assert korean_name("비타민") is None, "'비타민' 만으로는 화합물이 아니다"
    print("  메테인/뷰테인/폼알데하이드, '비스페놀 A'='비스페놀A', 'β-카로틴', '비타민 C'")
    print("통과: 새 표기와 띄어쓰기 차이에 이름을 잃지 않는다.")


def test_variants_only_remove_stereo_and_notation() -> None:
    """다시 묻는 표기는 원래 이름에서 기계적으로 나온다. 비슷한 이름을 찾는 것이 아니다."""
    assert variants("aspirin") == ["aspirin"]
    assert variants("β-carotene") == ["β-carotene", "beta-carotene"]
    assert variants("DCM") == ["DCM", "dichloromethane"]
    assert variants("(R)-2-amino-3-sulfanylpropanoic acid")[-1] == "2-amino-3-sulfanylpropanoic acid"
    assert variants("(R)-(+)-limonene")[-1] == "limonene"
    assert variants("L-alanine")[-1] == "alanine"
    # 괄호가 입체 표시가 아니면 건드리지 않는다.
    assert variants("(methylsulfinyl)methane") == ["(methylsulfinyl)methane"]
    # 'IPA'·'DA' 처럼 뜻이 여럿인 약어는 풀지 않는다.
    assert variants("IPA") == ["IPA"] and variants("DA") == ["DA"]
    print("  '(R)-(+)-limonene' -> 'limonene',  '(methylsulfinyl)methane' -> 그대로")
    print("통과: 입체 접두사와 표기 차이만 걷어낸다.")


def test_resolver_reports_the_spelling_it_actually_found() -> None:
    """입체 접두사를 떼고 찾았으면 Reference.name 에 그 사실이 드러나야 한다.

    '(R)-...' 를 물었는데 라세미체 그림이 나오면 사용자는 이름을 보고 알아야 한다.
    """
    calls: list[str] = []
    payload = {"PropertyTable": {"Properties": [
        {"InChIKey": "XUJNEKJLAYXESH-UHFFFAOYSA-N", "MolecularFormula": "C3H7NO2S", "SMILES": "NC(CS)C(=O)O"}
    ]}}

    def fake(url, **kwargs):
        calls.append(url)
        return FakeResponse(200, payload) if "2-amino-3-sulfanylpropanoic%20acid" in url and "%28r%29" not in url.lower() else FakeResponse(404)

    resolver = PubChemResolver(cache_path=_scratch_cache(), min_interval=0.0, offline={})
    real_get = names.requests.get
    names.requests.get = fake
    try:
        ref = resolver.resolve("(R)-2-amino-3-sulfanylpropanoic acid")
    finally:
        names.requests.get = real_get

    assert ref is not None, "입체 접두사를 떼고 다시 묻지 않았다"
    assert ref.name == "2-amino-3-sulfanylpropanoic acid", ref.name
    assert len(calls) == 2, f"요청 {len(calls)}회 (원래 표기 1 + 뗀 표기 1 이어야 한다)"
    print(f"  '(R)-2-amino-3-sulfanylpropanoic acid' -> 404 -> '{ref.name}' 로 해석 (요청 {len(calls)}회)")
    print("통과: 실제로 해석된 표기를 이름으로 돌려준다.")


if __name__ == "__main__":
    for test in [
        test_lecture_compounds_resolve_offline,
        test_whole_query_words_stay_out_of_slide_text,
        test_korean_spelling_variants,
        test_variants_only_remove_stereo_and_notation,
        test_resolver_reports_the_spelling_it_actually_found,
        test_trailing_paren_does_not_kill_the_name,
        test_budget_goes_to_real_names,
        test_korean_names_survive_particles,
        test_ambiguous_korean_words_stay_out,
        test_formula_is_not_a_name,
        test_offline_table_resolves_without_network,
        test_offline_table_agrees_with_rdkit,
        test_backs_off_and_retries_on_throttling,
        test_network_failure_is_not_cached_as_missing,
        test_greek_prefix_is_not_dropped,
        test_stereo_prefix_survives,
        test_vitamin_names_are_found,
        test_longer_name_outranks_its_own_prefix,
        test_fragment_name_does_not_masquerade,
    ]:
        test()
        print()
