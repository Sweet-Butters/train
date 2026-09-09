"""슬라이드 텍스트에서 화합물 이름 후보를 뽑고 PubChem으로 참조 InChIKey를 얻는다.

PubChem이 이름을 해석하지 못하면 그 후보는 버린다. 여기서 추측하지 않는다.

장당 조회 예산은 25건뿐이다(pipeline.MAX_LOOKUPS_PER_SLIDE). 그래서 이 모듈의
일은 후보를 '뽑는' 것이 아니라 '고르는' 것이다. 예산을 'MW 180' 같은 조각에
써버리면 정작 옆에 적힌 이름이 조회되지 않고 그림은 전부 판정불가로 빠진다.
"""
from __future__ import annotations

import atexit
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/InChIKey,MolecularFormula/JSON"
CACHE_PATH = Path.home() / ".cache" / "chemcheck" / "pubchem.json"
# 망이 없을 때 쓰는 표. scripts/build_offline_table.py 가 PubChem 응답으로 만든다.
OFFLINE_PATH = Path(__file__).resolve().parent / "data" / "compounds.json"

# 슬라이드에 흔하지만 화합물이 아닌 말들. PubChem이 엉뚱하게 해석하는 것을 막는다.
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "what", "how", "why",
    "structure", "molecule", "chemistry", "figure", "table", "slide", "example",
    "result", "method", "data", "test", "demo", "team", "next", "step", "title",
    "name", "names", "iupac", "formula", "molecular", "weight", "mass",
    "reference", "references", "page", "chapter", "lecture", "department",
}

# 이름 조회로 풀리지 않는 계측·서지 조각. 예산만 먹는다.
NOISE = {
    "mw", "mol", "mmol", "nm", "um", "mg", "kg", "ml", "ppm", "ph", "kda",
    "da", "min", "hr", "eq", "wt", "vol", "fig", "ref", "et", "al", "pp",
    # 화합물이 아닌데 대문자 약어라 아래 _ABBREV 에 걸리는 것들.
    # DNA·RNA 는 고분자라 단일 구조가 없고, 나머지는 기기·효소·지표다.
    "nmr", "ir", "uv", "vis", "hplc", "gc", "ms", "tlc", "dna", "rna",
    "cox", "sar", "qsar", "api", "pdf", "ppt", "abs", "obs", "calc",
}

# 홀로 쓰이면 화합물이 아니라 분류어다. 이름 안에서는 정상이다("benzoic acid").
BARE = {"acid", "acids", "ester", "ether", "salt", "base", "ion", "group"}

# 이름 끝에 붙는 화학 접미사.
SUFFIXES = (
    "ol", "al", "one", "ane", "ene", "yne", "ine", "ide", "ate", "ite",
    "amine", "amide", "ose", "yl", "oxide", "phenol", "ether", "ester",
)
# 이름 안에 흔한 조각.
FRAGMENTS = (
    "hydroxy", "methyl", "ethyl", "acetyl", "amino", "nitro", "chloro",
    "bromo", "fluoro", "benzo", "phenyl", "carboxy", "sulfo", "keto",
)

# "2-", "1,3-" 같은 위치번호. IUPAC 이름의 강한 신호다.
_LOCANT = re.compile(r"\b\d+(?:,\d+)*-")
# 분자식(C9H8O4). 숫자가 하나라도 있어야 한다 - 없으면 DMSO 같은 정상 이름이 걸린다.
_FORMULA = re.compile(r"^(?=.*\d)(?:[A-Z][a-z]?\d{0,3})+$")
# 대문자 약어(DMSO, THF, ASA). PubChem 이 흔한 약어는 직접 해석해 준다.
_ABBREV = re.compile(r"^[A-Z]{2,6}$")

_LATIN = re.compile(r"[A-Za-z0-9(\[][A-Za-z0-9\-,'()\[\]+]*")
_HANGUL = re.compile(r"[가-힣]+")

_TRIM = ".,;:!?'”’·\""
_CLOSER = {")": "(", "]": "["}
_OPENER = {"(": ")", "[": "]"}


def _clean(token: str) -> str | None:
    """토큰 끝에 붙은 문장부호와 짝 안 맞는 괄호를 떼어낸다.

    이게 없으면 "아스피린(Aspirin)은" 의 "Aspirin)" 이 그대로 조회되어
    PubChem 404 가 되고, 슬라이드에서 가장 중요한 이름이 통째로 버려진다.
    """
    t = token.strip(_TRIM)
    # 통째로 괄호에 싸인 경우 벗긴다: "(Aspirin)" -> "Aspirin"
    while len(t) > 2 and t[0] in _OPENER and t[-1] == _OPENER[t[0]]:
        inner = t[1:-1]
        if inner.count(t[0]) != inner.count(t[-1]):
            break
        t = inner.strip(_TRIM)
    # 짝 없는 닫는 괄호를 뗀다: "Aspirin)" -> "Aspirin". "Fe(III)" 는 그대로 둔다.
    while t and t[-1] in _CLOSER and t.count(_CLOSER[t[-1]]) < t.count(t[-1]):
        t = t[:-1].strip(_TRIM)
    while t and t[0] in _OPENER and t.count(_OPENER[t[0]]) < t.count(t[0]):
        t = t[1:].strip(_TRIM)
    if len(t) < 2 or not any(c.isalpha() for c in t):
        return None
    return t


def score(phrase: str) -> float:
    """화합물 이름일 법한 정도. 높을수록 먼저 조회한다. 0 이하면 조회하지 않는다."""
    words = phrase.split()
    low = phrase.lower()
    lows = [w.lower() for w in words]

    if any(w in STOPWORDS or w in NOISE for w in lows):
        return -1.0
    if len(words) == 1 and low in BARE:
        return -1.0
    if any(w.replace(".", "").replace(",", "").isdigit() for w in words):
        return -1.0
    if any(_FORMULA.match(w) for w in words):
        return -1.0  # 분자식은 이름 조회 대상이 아니다

    value = 0.0
    last = lows[-1].strip("()[]")
    if last == "acid" or last.endswith(SUFFIXES):
        value += 3.0
    if _LOCANT.search(low):
        value += 2.0
    if any(f in low for f in FRAGMENTS):
        value += 2.0
    if len(words) == 1 and len(phrase) >= 5 and phrase[:1].isupper():
        value += 1.0  # "Aspirin", "Caffeine"
    if len(words) == 1 and _ABBREV.match(words[0]):
        # 낮게 준다. 진짜 이름이 있으면 그쪽이 먼저 조회돼야 하고, 약어만 있는
        # 슬라이드에서만 예산을 쓴다. 못 풀리면 404 로 싸게 끝난다.
        value += 0.5
    if value == 0.0:
        return -1.0  # 화합물 신호가 하나도 없으면 예산을 쓰지 않는다
    return value + min(len(words), 4) * 0.25  # 같은 점수면 더 구체적인 쪽 먼저


# 한국어 화합물 이름 -> PubChem 이 아는 영문 이름.
# PubChem 은 한글을 해석하지 못한다(아스피린 -> 404). 한국어 강의자료에서
# 이 표가 없으면 이름 쪽 참조가 통째로 비고 모든 그림이 판정불가가 된다.
#
# 일부러 뺀 것들: 물, 산, 당, 알코올, 요소 - 일상어와 겹쳐서 오해석 위험이 크다.
# 특히 '요소'는 '구성 요소'와 충돌한다. 잘못된 참조는 멀쩡한 그림을 '오류'로
# 만들기 때문에, 애매하면 넣지 않는다.
KO_ALIASES = {
    "아스피린": "aspirin", "아세틸살리실산": "acetylsalicylic acid",
    "살리실산": "salicylic acid", "아세트아미노펜": "acetaminophen",
    "파라세타몰": "paracetamol", "이부프로펜": "ibuprofen", "나프록센": "naproxen",
    "카페인": "caffeine", "테오브로민": "theobromine", "니코틴": "nicotine",
    "모르핀": "morphine", "코데인": "codeine", "페니실린": "penicillin",
    "아목시실린": "amoxicillin", "도파민": "dopamine", "세로토닌": "serotonin",
    "에피네프린": "epinephrine", "아드레날린": "adrenaline", "히스타민": "histamine",
    "아세틸콜린": "acetylcholine", "콜레스테롤": "cholesterol",
    "에탄올": "ethanol", "메탄올": "methanol", "이소프로판올": "isopropyl alcohol",
    "글리세롤": "glycerol", "에틸렌글리콜": "ethylene glycol", "아세톤": "acetone",
    "아세트알데히드": "acetaldehyde", "포름알데히드": "formaldehyde",
    "아세트산": "acetic acid", "초산": "acetic acid",
    "포름산": "formic acid", "개미산": "formic acid",
    "벤조산": "benzoic acid", "안식향산": "benzoic acid",
    "시트르산": "citric acid", "구연산": "citric acid",
    "젖산": "lactic acid", "락트산": "lactic acid",
    "아스코르브산": "ascorbic acid", "팔미트산": "palmitic acid",
    "스테아르산": "stearic acid", "올레산": "oleic acid", "리놀레산": "linoleic acid",
    # 자일렌/크실렌은 뺐다. o-/m-/p- 이성질체가 있어 'xylene' 단독으로는
    # PubChem 도 해석하지 못한다. 임의로 한 이성질체를 고르면 멀쩡한 그림을
    # '오류'로 만든다. 모호한 이름은 판정불가로 두는 편이 낫다.
    "벤젠": "benzene", "톨루엔": "toluene",
    "스티렌": "styrene", "나프탈렌": "naphthalene", "페놀": "phenol",
    "아닐린": "aniline", "피리딘": "pyridine", "인돌": "indole",
    "퓨린": "purine", "피리미딘": "pyrimidine", "아데닌": "adenine",
    "구아닌": "guanine", "사이토신": "cytosine", "시토신": "cytosine",
    "티민": "thymine", "우라실": "uracil",
    "포도당": "glucose", "글루코스": "glucose", "과당": "fructose",
    "프럭토스": "fructose", "갈락토스": "galactose", "설탕": "sucrose",
    "수크로스": "sucrose", "젖당": "lactose", "락토스": "lactose", "리보스": "ribose",
    "글리신": "glycine", "알라닌": "alanine", "발린": "valine", "류신": "leucine",
    "트립토판": "tryptophan", "티로신": "tyrosine", "메티오닌": "methionine",
    "시스테인": "cysteine",
    "클로로포름": "chloroform", "디에틸에테르": "diethyl ether",
    "테트라히드로푸란": "tetrahydrofuran", "아세토니트릴": "acetonitrile",
    "디메틸술폭시드": "dimethyl sulfoxide", "다이메틸설폭사이드": "dimethyl sulfoxide",
    "메탄": "methane", "에탄": "ethane", "프로판": "propane", "부탄": "butane",
    "에틸렌": "ethylene", "아세틸렌": "acetylene",
    "이산화탄소": "carbon dioxide", "일산화탄소": "carbon monoxide",
    "과산화수소": "hydrogen peroxide", "암모니아": "ammonia",
    "황산": "sulfuric acid", "염산": "hydrochloric acid", "질산": "nitric acid",
    "인산": "phosphoric acid", "탄산": "carbonic acid",
    "수산화나트륨": "sodium hydroxide", "염화나트륨": "sodium chloride",
    "니트로글리세린": "nitroglycerin",
}

# 조사가 붙은 채로 나온다("아스피린은"). 긴 것부터 떼어봐야 '으로'가 '로'보다 먼저 걸린다.
_PARTICLES = sorted(
    ["이라는", "라는", "으로", "에서", "에게", "부터", "까지", "보다", "처럼",
     "이나", "이란", "란", "은", "는", "이", "가", "을", "를", "의", "에",
     "와", "과", "도", "로", "만", "나"],
    key=len, reverse=True,
)


def korean_name(run: str) -> str | None:
    """한글 덩어리에서 화합물 이름을 찾아 영문 이름으로 돌려준다. 없으면 None."""
    if run in KO_ALIASES:
        return KO_ALIASES[run]
    for particle in _PARTICLES:
        if run.endswith(particle):
            stem = run[: -len(particle)]
            if stem in KO_ALIASES:
                return KO_ALIASES[stem]
    return None


def candidates(text: str, max_words: int = 4) -> list[str]:
    """텍스트에서 화합물 이름일 수 있는 구절을 점수 순으로 뽑는다."""
    best: dict[str, tuple[float, str]] = {}

    def offer(phrase: str, value: float) -> None:
        key = phrase.lower()
        if key not in best or value > best[key][0]:
            best[key] = (value, phrase)

    for line in text.splitlines():
        for run in _HANGUL.findall(line):
            name = korean_name(run)
            if name:
                offer(name, 100.0)  # 사전 적중은 추측이 아니므로 최우선

        words = [w for w in (_clean(t) for t in _LATIN.findall(line)) if w]
        for n in range(1, min(max_words, len(words)) + 1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                if not 3 <= len(phrase) <= 120:
                    continue
                value = score(phrase)
                if value > 0:
                    offer(phrase, value)

    ordered = sorted(best.values(), key=lambda pair: -pair[0])
    return [phrase for _, phrase in ordered]


@dataclass(frozen=True)
class Reference:
    name: str
    inchikey: str
    formula: str
    source: str = "pubchem"  # pubchem | cache | offline


def _load_offline(path: Path = OFFLINE_PATH) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("entries", {})
    except (OSError, json.JSONDecodeError):
        return {}


RETRY_STATUS = {429, 500, 502, 503, 504}


class PubChemResolver:
    """이름 -> InChIKey. 디스크 캐시를 쓰고 초당 요청을 제한한다.

    조회 순서는 캐시 -> 동봉한 표 -> 망이다. 망이 죽어도 흔한 화합물은
    계속 해석된다. 동봉한 표는 PubChem 응답을 그대로 받아 적은 것이며
    (scripts/build_offline_table.py), 여기서 구조를 지어내지 않는다.
    """

    def __init__(
        self,
        cache_path: Path = CACHE_PATH,
        min_interval: float = 0.25,
        offline: dict[str, dict] | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
    ):
        self.cache_path = cache_path
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.offline = _load_offline() if offline is None else offline
        self._last_call = 0.0
        self._dirty = False
        self._last_save = 0.0
        self._cache: dict[str, dict | None] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        atexit.register(self.flush)

    def flush(self) -> None:
        """캐시를 디스크에 쓴다. 실패해도 판정에는 영향이 없다."""
        if not self._dirty:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
            self._dirty = False
            self._last_save = time.monotonic()
        except OSError:
            pass

    def _touch(self) -> None:
        """조회마다 전체 캐시를 다시 쓰면 장이 길수록 느려진다. 간격을 둔다."""
        self._dirty = True
        if time.monotonic() - self._last_save >= 2.0:
            self.flush()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.monotonic()

    @staticmethod
    def _pause(resp: requests.Response, attempt: int) -> float:
        after = resp.headers.get("Retry-After")
        if after:
            try:
                return min(float(after), 30.0)
            except ValueError:
                pass
        return min(0.5 * (2**attempt), 8.0)

    def _fetch(self, key: str) -> requests.Response | None:
        """PubChem 한 건. 일시적 오류(503 등)는 물러섰다가 다시 시도한다."""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = requests.get(PUBCHEM.format(quote(key, safe="")), timeout=self.timeout)
            except requests.RequestException:
                return None  # 네트워크 실패는 캐시하지 않는다
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                time.sleep(self._pause(resp, attempt))
                continue
            return resp
        return None

    def resolve(self, name: str) -> Reference | None:
        key = name.strip().lower()
        if not key:
            return None

        if key in self._cache:
            hit = self._cache[key]
            return Reference(name, hit["inchikey"], hit["formula"], "cache") if hit else None

        shipped = self.offline.get(key)
        if shipped:
            return Reference(name, shipped["inchikey"], shipped["formula"], "offline")

        resp = self._fetch(key)
        if resp is None:
            return None
        if resp.status_code == 404:
            self._cache[key] = None
            self._touch()
            return None
        if resp.status_code != 200:
            return None

        try:
            props = resp.json()["PropertyTable"]["Properties"][0]
            record = {"inchikey": props["InChIKey"], "formula": props.get("MolecularFormula", "")}
        except (KeyError, IndexError, ValueError):
            return None

        self._cache[key] = record
        self._touch()
        return Reference(name, record["inchikey"], record["formula"])
