"""이름 해석(트랙 C) 검증.

전부 망 없이 돈다. PubChem 을 때리는 테스트는 회선 상태에 따라 결과가 바뀌므로
검증 장치로 쓸 수 없다. 망이 필요한 검증은 scripts/build_offline_table.py 가
표를 만들 때 수행한다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import names  # noqa: E402
from chemcheck.keys import smiles_to_inchikey  # noqa: E402
from chemcheck.names import (  # noqa: E402
    KO_ALIASES,
    PubChemResolver,
    candidates,
    korean_name,
    score,
)

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


if __name__ == "__main__":
    for test in [
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
