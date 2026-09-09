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

from .keys import principal_smiles, smiles_to_inchikey

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/InChIKey,MolecularFormula,SMILES,IUPACName,Title/JSON"
# 역방향. InChIKey 로 PubChem 이 그 구조에 붙인 IUPAC 명과 대표명(Title)을 받는다.
PUBCHEM_KEY = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{}/property/IUPACName,Title/JSON"
_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
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
    # 'CoA' 는 acetyl-CoA 의 조각인데 단독으로는 coenzyme A 로 해석된다.
    # 부분 이름이 전체 이름 행세를 하면 잘못된 참조가 만들어진다.
    "coa",
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
# 약어(DMSO, THF, CoA). 대문자가 둘 이상이어야 한다 - 그래야 "The" 가 안 걸린다.
_ABBREV = re.compile(r"^[A-Za-z]{2,6}$")
# 입체·기하 접두사. IUPAC 이름의 강한 신호다.
_STEREO = re.compile(
    r"^(\(\d*[RSEZ](?:,\d*[RSEZ])*\)|cis|trans|[DLRSEZ]|alpha|beta|gamma|delta|omega|[onmp])-",
    re.IGNORECASE,
)
# "vitamin C", "vitamin B12". PubChem 이 그대로 해석한다.
_VITAMIN = re.compile(r"^vitamins?\s+[a-k]\d{0,2}$", re.IGNORECASE)
# 줄에서 직접 집는다. "C" 는 한 글자라 일반 토큰 경로에서 버려지기 때문에
# "vitamin C" 라는 구절 자체가 만들어지지 않는다. 한글 표기도 함께 받는다.
_VITAMIN_LINE = re.compile(r"(?:vitamins?|비타민)\s*([A-Ka-k]\d{0,2})\b")

_LATIN = re.compile(r"[A-Za-z0-9(\[α-ω][A-Za-z0-9\-,'()\[\]+α-ω]*")
# PubChem 은 'β-carotene' 을 404 로 돌려주지만 'beta-carotene' 은 해석한다.
# 그리스 문자를 그냥 버리면 'carotene' 이 되는데 이건 다른 화합물이다.
_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
          "ε": "epsilon", "ω": "omega", "μ": "mu"}
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
    for greek, ascii_name in _GREEK.items():
        if greek in t:
            t = t.replace(greek, ascii_name)
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
    if _VITAMIN.match(low):
        return 3.0 + len(words) * 0.5  # "vitamin C" -> PubChem 이 그대로 해석한다
    last = _STEREO.sub("", lows[-1].strip("()[]"))
    if last == "acid" or last.endswith(SUFFIXES):
        value += 3.0
    if _STEREO.match(words[-1]) or _STEREO.match(words[0]):
        value += 2.0  # "(S)-ibuprofen", "trans-cinnamic acid", "beta-carotene"
    if _LOCANT.search(low):
        value += 2.0
    if any(f in low for f in FRAGMENTS):
        value += 2.0
    if (len(words) == 1 and len(phrase) >= 5 and phrase[:1].isupper()
            and "-" not in phrase):
        # 하이픈이 든 이름에는 주지 않는다. "N-acetyl-p-benzoquinone" 이 이 보너스로
        # 더 긴 정답 "N-acetyl-p-benzoquinone imine" 을 눌러 이겼다.
        value += 1.0  # "Aspirin", "Caffeine"
    if (len(words) == 1 and _ABBREV.match(words[0])
            and sum(c.isupper() for c in words[0]) >= 2):
        # 낮게 준다. 진짜 이름이 있으면 그쪽이 먼저 조회돼야 하고, 약어만 있는
        # 슬라이드에서만 예산을 쓴다. 못 풀리면 404 로 싸게 끝난다.
        value += 0.5
    if value == 0.0:
        return -1.0  # 화합물 신호가 하나도 없으면 예산을 쓰지 않는다
    return value + min(len(words), 4) * 0.5  # 같은 점수면 더 구체적인(긴) 이름 먼저


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
    # ── 아래는 교과서 화합물 목록(data/lecture_compounds.json)으로 `draw` 를 재서
    # 실제로 못 찾은 한글 표기다 (scripts/measure_names.py). 추측으로 넣은 것은 없다.
    # 대한화학회 새 표기(메테인·뷰테인·알데하이드·에터)와 옛 표기가 함께 나온다.
    "염화소듐": "sodium chloride", "소금": "sodium chloride",
    "수산화소듐": "sodium hydroxide", "가성소다": "sodium hydroxide",
    "탄산수소나트륨": "sodium bicarbonate", "탄산수소소듐": "sodium bicarbonate",
    "중탄산나트륨": "sodium bicarbonate", "탄산칼슘": "calcium carbonate",
    "과망간산칼륨": "potassium permanganate", "과망가니즈산칼륨": "potassium permanganate",
    "과망가니즈산포타슘": "potassium permanganate",
    "오존": "ozone", "아산화질소": "nitrous oxide", "일산화이질소": "nitrous oxide",
    "메테인": "methane", "에테인": "ethane", "프로페인": "propane",
    "뷰테인": "butane", "노말부탄": "butane",
    "이소부탄": "isobutane", "아이소뷰테인": "isobutane",
    "에텐": "ethylene", "에타인": "acetylene",
    "사이클로헥산": "cyclohexane", "사이클로헥세인": "cyclohexane", "시클로헥산": "cyclohexane",
    "스타이렌": "styrene",
    "에틸알코올": "ethanol", "주정": "ethanol", "메틸알코올": "methanol",
    "아이소프로판올": "isopropyl alcohol", "이소프로필알코올": "isopropyl alcohol",
    "tert-부탄올": "tert-butanol", "삼차부탄올": "tert-butanol",
    "에틸렌글라이콜": "ethylene glycol", "글리세린": "glycerol",
    "폼알데하이드": "formaldehyde", "포름알데하이드": "formaldehyde",
    "아세트알데하이드": "acetaldehyde",
    "벤즈알데히드": "benzaldehyde", "벤즈알데하이드": "benzaldehyde",
    "아세토페논": "acetophenone",
    "다이에틸에터": "diethyl ether", "테트라하이드로퓨란": "tetrahydrofuran",
    "디메틸설폭사이드": "dimethyl sulfoxide",
    "디메틸포름아미드": "dimethylformamide", "다이메틸폼아마이드": "dimethylformamide",
    "아세토나이트릴": "acetonitrile", "클로로폼": "chloroform",
    "디클로로메탄": "dichloromethane", "다이클로로메테인": "dichloromethane",
    "염화메틸렌": "dichloromethane", "사염화탄소": "carbon tetrachloride",
    "에틸아세테이트": "ethyl acetate", "아세트산에틸": "ethyl acetate", "초산에틸": "ethyl acetate",
    "헥산": "hexane", "헥세인": "hexane", "노말헥산": "hexane",
    "니트로벤젠": "nitrobenzene", "나이트로벤젠": "nitrobenzene",
    "아세트산무수물": "acetic anhydride", "무수아세트산": "acetic anhydride",
    "무수초산": "acetic anhydride",
    "폼산": "formic acid", "옥살산": "oxalic acid",
    "피루브산": "pyruvic acid", "피루빈산": "pyruvic acid",
    "숙신산": "succinic acid", "석신산": "succinic acid", "호박산": "succinic acid",
    "푸마르산": "fumaric acid", "퓨마르산": "fumaric acid",
    "말레산": "maleic acid", "말레익산": "maleic acid",
    "타르타르산": "tartaric acid", "주석산": "tartaric acid",
    "우레아": "urea", "메틸아민": "methylamine",
    "트리니트로톨루엔": "trinitrotoluene", "트라이나이트로톨루엔": "trinitrotoluene",
    "비스페놀A": "bisphenol A",
    "에틸렌다이아민테트라아세트산": "ethylenediaminetetraacetic acid",
    "에틸렌디아민사아세트산": "ethylenediaminetetraacetic acid",
    "글루코오스": "glucose", "프룩토스": "fructose",
    "자당": "sucrose", "수크로오스": "sucrose", "유당": "lactose", "락토오스": "lactose",
    "리보오스": "ribose",
    "타이로신": "tyrosine", "페닐알라닌": "phenylalanine", "타이민": "thymine",
    "아데노신삼인산": "adenosine triphosphate",
    "팔미틱산": "palmitic acid", "스테아린산": "stearic acid",
    "올레인산": "oleic acid", "리놀레인산": "linoleic acid",
    "타이레놀": "acetaminophen", "몰핀": "morphine",
    "페니실린G": "penicillin G", "벤질페니실린": "benzylpenicillin",
    "아스코르빈산": "ascorbic acid", "레티놀": "retinol",
    "멘톨": "menthol", "캠퍼": "camphor", "장뇌": "camphor", "캄포르": "camphor",
    "리모넨": "limonene", "바닐린": "vanillin", "캡사이신": "capsaicin",
    "나이트로글리세린": "nitroglycerin", "테스토스테론": "testosterone",
    "에스트라디올": "estradiol", "에스트라다이올": "estradiol",
    "베타카로틴": "beta-carotene", "퀴닌": "quinine", "키니네": "quinine",
    "디디티": "DDT",
}

# 사용자가 `draw "물"` 처럼 통째로 물었을 때만 믿는 이름. 슬라이드 본문에서는 쓰지
# 않는다 - '물' 은 아무 데나 나오고, '요소' 는 '구성 요소', '수산' 은 '수산화-',
# '에테르' 는 작용기 분류어다. 본문에서 잘못 잡으면 멀쩡한 그림이 '오류' 가 된다.
KO_WHOLE_ONLY = {
    "물": "water", "요소": "urea", "수산": "oxalic acid", "에테르": "diethyl ether",
}

# 조사가 붙은 채로 나온다("아스피린은"). 긴 것부터 떼어봐야 '으로'가 '로'보다 먼저 걸린다.
_PARTICLES = sorted(
    ["이라는", "라는", "으로", "에서", "에게", "부터", "까지", "보다", "처럼",
     "이나", "이란", "란", "은", "는", "이", "가", "을", "를", "의", "에",
     "와", "과", "도", "로", "만", "나"],
    key=len, reverse=True,
)

# 한글 표기 안의 그리스 문자. 'β-카로틴' 은 '베타카로틴' 과 같은 표기다.
_KO_GREEK = {"α": "알파", "β": "베타", "γ": "감마", "δ": "델타", "ω": "오메가"}
# 한글 표기 안의 비타민: '비타민 C', '비타민C'. PubChem 은 'vitamin C' 를 해석한다.
_KO_VITAMIN = re.compile(r"^비타민\s*([A-Ka-k]\d{0,2})$")


def _ko_key(text: str) -> str:
    """표 조회용 정규화. 띄어쓰기·하이픈 차이('비스페놀 A'/'비스페놀A')는 같은 이름이다."""
    for greek, ko in _KO_GREEK.items():
        text = text.replace(greek, ko)
    return re.sub(r"[\s\-]+", "", text).lower()


_KO_TABLE = {_ko_key(k): v for k, v in KO_ALIASES.items()}
_KO_WHOLE_TABLE = {_ko_key(k): v for k, v in KO_WHOLE_ONLY.items()}
# 영문 -> 한글 (역방향 조회용). 한 영문 이름에 한글 표기가 여럿이면 표에 먼저 적힌
# 것(옛 표기, 예: '메탄')을 쓴다. 표 순서가 곧 선택이다.
_KO_REVERSE: dict[str, str] = {}
for _ko, _en in {**KO_ALIASES, **KO_WHOLE_ONLY}.items():
    _KO_REVERSE.setdefault(_en.lower(), _ko)


def korean_name(run: str, *, in_text: bool = False) -> str | None:
    """한글 덩어리에서 화합물 이름을 찾아 영문 이름으로 돌려준다. 없으면 None.

    in_text=True 는 슬라이드 본문에서 뽑은 조각일 때다. 그때는 일상어와 겹치는
    KO_WHOLE_ONLY 를 쓰지 않는다. 사용자가 이름을 통째로 넣는 `draw` 는 기본값.
    """
    run = run.strip()
    hit = _KO_VITAMIN.match(run)
    if hit:
        return f"vitamin {hit.group(1).upper()}"
    tables = [_KO_TABLE] if in_text else [_KO_TABLE, _KO_WHOLE_TABLE]
    key = _ko_key(run)
    for table in tables:
        if key in table:
            return table[key]
    for particle in _PARTICLES:
        if run.endswith(particle):
            stem = _ko_key(run[: -len(particle)])
            for table in tables:
                if stem in table:
                    return table[stem]
    return None


def candidates(text: str, max_words: int = 4) -> list[str]:
    """텍스트에서 화합물 이름일 수 있는 구절을 점수 순으로 뽑는다."""
    best: dict[str, tuple[float, str]] = {}

    def offer(phrase: str, value: float) -> None:
        key = phrase.lower()
        if key not in best or value > best[key][0]:
            best[key] = (value, phrase)

    for line in text.splitlines():
        for hit in _VITAMIN_LINE.finditer(line):
            # 스쳐 지나가는 비타민 언급이 그 장의 실제 화합물 이름을
            # 누르면 안 된다. score() 의 비타민 경로와 같은 값을 준다.
            offer(f"vitamin {hit.group(1).upper()}", 4.0)

        for run in _HANGUL.findall(line):
            name = korean_name(run, in_text=True)
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
class Names:
    """PubChem 이 한 InChIKey 에 붙인 이름들. recognize 결과에 이름을 붙일 때 쓴다."""
    inchikey: str
    iupac: str              # PubChem IUPACName. PubChem 에 없으면 ""
    common: str             # PubChem 대표명(Title). 예: 'Aspirin', 'DL-alanine'
    korean: str | None      # 이 모듈의 한글 표에 있을 때만. 없으면 None
    source: str = "pubchem"  # pubchem | cache | offline


@dataclass(frozen=True)
class Reference:
    name: str
    inchikey: str
    formula: str
    source: str = "pubchem"  # pubchem | cache | offline
    smiles: str = ""        # PubChem 이 준 구조. 염 정규화의 근거


# 화학에서 뜻이 하나뿐인데 PubChem 이 이름으로 해석하지 못하는 영문 약어.
# 교과서 목록으로 재서 실제로 못 찾은 것만 둔다. 'IPA'(이소프탈산·맥주)·'DA'(달톤)
# 처럼 다른 뜻이 있는 약어는 넣지 않는다 - 못 찾는 쪽이 엉뚱한 그림보다 낫다.
ABBREVIATIONS = {"acn": "acetonitrile", "dcm": "dichloromethane"}

# 유니코드 대시·하이픈 변종. 슬라이드 편집기가 '-' 를 '–' 로 바꿔 놓는다.
_DASHES = str.maketrans({d: "-" for d in "‐‑‒–—−"})
# 이름 앞의 입체·기하 접두사: (R)-, (2S)-, (9Z,12Z)-, (+)-, (-)-, (±)-, (RS)-, L-, D-, cis-, trans-.
# 괄호 안은 위치번호·R/S/E/Z·부호만 허용해서 '(methylsulfinyl)methane' 은 건드리지 않는다.
_STEREO_PREFIX = re.compile(r"^(?:\([0-9RSEZrsez,+\-±/ ]+\)|cis|trans|rac|meso|[DLRSEZ])-")


def variants(name: str) -> list[str]:
    """같은 화합물을 뜻하는 표기들. 원래 이름이 먼저고, 뒤로 갈수록 정보를 덜어낸 것이다.

    1. 원래 이름
    2. 그리스 문자·유니코드 대시·공백 정리 ('β-carotene' -> 'beta-carotene')
    3. 약어 풀기 ('DCM' -> 'dichloromethane')
    4. 입체 접두사 떼기 ('(R)-2-amino-3-sulfanylpropanoic acid' -> '2-amino-...')
       골격은 같고 입체 정보만 잃는다. 그래서 맨 뒤다 - 원래 표기로 찾으면 그것을 쓴다.
    """
    out: list[str] = []

    def push(candidate: str) -> None:
        candidate = " ".join(candidate.split())
        if candidate and candidate.lower() not in {v.lower() for v in out}:
            out.append(candidate)

    push(name)
    plain = name.translate(_DASHES)
    for greek, ascii_name in _GREEK.items():
        plain = plain.replace(greek, ascii_name)
    push(plain)
    expanded = ABBREVIATIONS.get(plain.strip().lower())
    if expanded:
        push(expanded)
    stripped = plain.strip()
    while True:
        shorter = _STEREO_PREFIX.sub("", stripped, count=1)
        if shorter == stripped:
            break
        stripped = shorter
    if stripped and stripped != plain.strip():
        push(stripped)
    return out


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
        self._offline_by_key: dict[str, list[tuple[str, dict]]] | None = None
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

    def _fetch(self, key: str, url: str = PUBCHEM) -> requests.Response | None:
        """PubChem 한 건. 일시적 오류(503 등)는 물러섰다가 다시 시도한다."""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = requests.get(url.format(quote(key, safe="")), timeout=self.timeout)
            except requests.RequestException:
                return None  # 네트워크 실패는 캐시하지 않는다
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                time.sleep(self._pause(resp, attempt))
                continue
            return resp
        return None

    def resolve(self, name: str) -> Reference | None:
        """이름 -> 참조. 그대로 못 찾으면 같은 화합물을 뜻하는 다른 표기로 다시 묻는다.

        다시 묻는 표기는 전부 원래 이름에서 기계적으로 나온다(variants 참고).
        비슷한 이름을 찾아 주는 것이 아니다. 돌려주는 Reference.name 은 실제로
        해석된 표기다 - 입체 접두사를 떼고 찾았으면 그 사실이 이름에 드러난다.
        """
        for variant in variants(name):
            ref = self._resolve_exact(variant)
            if ref is not None:
                return ref
        return None

    def _resolve_exact(self, name: str) -> Reference | None:
        key = name.strip().lower()
        if not key:
            return None

        if key in self._cache:
            hit = self._cache[key]
            if not hit:
                return None
            # 옛 캐시에는 SMILES 가 없다(키·분자식만 저장하던 시절). 구조를 그리려면
            # SMILES 가 있어야 하므로 그런 항목은 적중으로 치지 않고 표·망으로 내려간다.
            if hit.get("smiles"):
                return self._reference(name, hit, "cache")

        shipped = self.offline.get(key)
        if shipped:
            return self._reference(name, shipped, "offline")

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
            record = {
                "inchikey": props["InChIKey"],
                "formula": props.get("MolecularFormula", ""),
                "smiles": props.get("SMILES") or props.get("ConnectivitySMILES") or "",
                "iupac": props.get("IUPACName", ""),
                "title": props.get("Title", ""),
            }
        except (KeyError, IndexError, ValueError):
            return None

        self._cache[key] = record
        self._touch()
        return self._reference(name, record, "pubchem")

    @staticmethod
    def _reference(name: str, record: dict, source: str) -> Reference:
        """염·수화물이면 주성분의 키로 바꾼다.

        'morphine sulfate' 의 InChIKey 는 morphine 과 골격부터 다르다. 그대로
        두면 morphine 을 정확히 그린 그림이 '오류'로 보고된다. 단일 성분일 때는
        PubChem 이 준 키를 그대로 쓴다 - 우리가 다시 계산할 이유가 없다.
        """
        key = record["inchikey"]
        smiles = record.get("smiles") or ""
        if "." in smiles:
            principal = principal_smiles(smiles)
            if principal != smiles:
                recomputed = smiles_to_inchikey(principal)
                if recomputed:
                    key = recomputed
        return Reference(name, key, record.get("formula", ""), source, smiles)

    # ── 역방향: InChIKey -> 이름 ─────────────────────────────────────────

    def names_for(self, inchikey: str) -> Names | None:
        """InChIKey -> PubChem 이 그 키에 붙인 이름들. PubChem 에 없는 분자면 None.

        recognize 가 낸 구조에 이름을 붙이는 용도다. 정확히 같은 키만 묻는다 -
        골격만 같은 다른 입체이성질체의 이름을 빌려 오지 않는다. 그건 추측이다.
        조회 순서는 캐시 -> 동봉한 표 -> 망. 한글은 이 모듈의 표(KO_ALIASES)에 있을 때만.
        """
        key = inchikey.strip().upper()
        if not _INCHIKEY.match(key):
            return None

        cache_key = f"inchikey:{key}"
        if cache_key in self._cache:
            hit = self._cache[cache_key]
            return self._names(key, hit, "cache") if hit else None

        shipped = self._offline_names(key)
        if shipped:
            return self._names(key, shipped, "offline")

        resp = self._fetch(key, PUBCHEM_KEY)
        if resp is None:
            return None
        if resp.status_code == 404:
            self._cache[cache_key] = None
            self._touch()
            return None
        if resp.status_code != 200:
            return None
        try:
            # 같은 키에 레코드가 여럿일 수 있다(호변이성체 등). 첫 것이 가장 낮은 CID 다.
            props = resp.json()["PropertyTable"]["Properties"][0]
            record = {"iupac": props.get("IUPACName", ""), "title": props.get("Title", "")}
        except (KeyError, IndexError, ValueError):
            return None
        self._cache[cache_key] = record
        self._touch()
        return self._names(key, record, "pubchem")

    def _offline_names(self, key: str) -> dict | None:
        """동봉한 표에서 같은 키를 가진 항목의 이름들. 표는 이름 -> 키 방향이라 뒤집어 둔다."""
        if self._offline_by_key is None:
            index: dict[str, list[tuple[str, dict]]] = {}
            for name, entry in self.offline.items():
                index.setdefault(entry.get("inchikey", ""), []).append((name, entry))
            self._offline_by_key = index
        found = self._offline_by_key.get(key)
        if not found:
            return None
        # PubChem 대표명·IUPAC 명이 기록된 항목이 있으면 그것을, 없으면(옛 표) 이름 중
        # 가장 짧은 것을 대표명으로 쓴다. 어느 쪽이든 PubChem 이 이 키에 붙인 이름이다.
        with_title = [e for _, e in found if e.get("title")]
        if with_title:
            entry = with_title[0]
            return {"iupac": entry.get("iupac", ""), "title": entry["title"]}
        shortest = min((n for n, _ in found), key=len)
        return {"iupac": "", "title": shortest}

    def _names(self, key: str, record: dict, source: str) -> Names:
        iupac = record.get("iupac") or ""
        common = record.get("title") or ""
        return Names(key, iupac, common, self._korean_for(key, common, iupac), source)

    def _korean_for(self, key: str, *english: str) -> str | None:
        """한글 표에 있는 이름이면 한글. PubChem 대표명·IUPAC 명, 그리고 동봉한 표에서
        같은 키를 가진 영문 이름 전부를 표와 대조한다."""
        names = [e.lower() for e in english if e]
        if self._offline_by_key is None:
            self._offline_names(key)
        # 표의 이름은 짧은 것부터 - 'aspirin' 이 'acetylsalicylic acid' 보다 먼저.
        # 관용명이 계통명보다 짧은 것이 보통이고, 사람이 읽고 싶은 것도 그쪽이다.
        names += sorted((n for n, _ in (self._offline_by_key or {}).get(key, [])),
                        key=lambda n: (len(n), n))
        for name in names:
            if name in _KO_REVERSE:
                return _KO_REVERSE[name]
        return None


_default_resolver: PubChemResolver | None = None


def name_for_inchikey(inchikey: str, resolver: PubChemResolver | None = None) -> Names | None:
    """InChIKey -> Names(iupac, common, korean). PubChem 에 없으면 None. 추측하지 않는다.

    recognize 출력에 이름을 붙일 때 부른다. resolver 를 주지 않으면 모듈 공용
    resolver 를 쓴다(캐시·레이트리밋 공유).
    """
    global _default_resolver
    if resolver is None:
        if _default_resolver is None:
            _default_resolver = PubChemResolver()
        resolver = _default_resolver
    return resolver.names_for(inchikey)
