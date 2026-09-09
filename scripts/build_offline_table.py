"""동봉용 이름->InChIKey 표를 PubChem 응답으로 만든다.

망이 없을 때(KTX, 강의실 와이파이) chemcheck 가 이름을 하나도 해석하지 못하면
모든 그림이 판정불가로 빠져 도구가 무용해진다. 흔한 화합물만이라도 들고 다닌다.

여기서 구조를 지어내지 않는다. PubChem 이 준 InChIKey 를 그대로 받아 적고,
어디서 언제 받았는지 함께 남긴다. 해석되지 않은 이름은 표에 넣지 않고 보고한다
(한국어 별칭의 영문 대응이 틀렸다면 여기서 드러난다).

    python scripts/build_offline_table.py            # 이미 표에 있는 이름은 다시 묻지 않는다
    python scripts/build_offline_table.py --refresh  # 전부 다시 받는다
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.keys import skeleton  # noqa: E402
from chemcheck.names import KO_ALIASES, KO_WHOLE_ONLY, PUBCHEM, variants  # noqa: E402

OUT = ROOT / "chemcheck" / "data" / "compounds.json"
# 교과서 화합물 목록. id 이름의 PubChem 응답이 정답(answer)이 되고, 그 화합물의
# 영문 표기 중 PubChem 이 같은 골격으로 해석한 것은 전부 동봉 표에 들어간다.
LECTURE = ROOT / "chemcheck" / "data" / "lecture_compounds.json"

# 한국어 별칭이 가리키는 영문 이름은 전부 들어가야 한다. 하나라도 빠지면
# 한국어 슬라이드가 망 없이는 해석되지 않는다.
# 그 밖에 강의자료에 흔한 것들을 더한다.
EXTRA = [
    "water", "acetic anhydride", "sodium bicarbonate", "potassium permanganate",
    "hydrogen", "oxygen", "nitrogen", "ozone", "methylamine", "urea",
    "adenosine triphosphate", "adenosine", "guanosine", "cytidine", "thymidine",
    # starch/glycogen 같은 고분자와 toluidine 처럼 이성질체가 갈리는 이름은
    # 단일 InChIKey 로 떨어지지 않는다. 넣지 않는다.
    "deoxyribose", "maltose", "pyruvic acid",
    "oxaloacetic acid", "succinic acid", "fumaric acid", "malic acid",
    "alpha-ketoglutaric acid", "acetyl-coa", "cholic acid", "testosterone",
    "estradiol", "progesterone", "cortisol", "thyroxine", "retinol",
    "ascorbic acid", "folic acid", "riboflavin", "thiamine", "biotin",
    "niacin", "pyridoxine", "cyanocobalamin", "tocopherol", "cholecalciferol",
    "quinine", "atropine", "ephedrine", "lidocaine", "warfarin", "metformin",
    "omeprazole", "simvastatin", "atorvastatin", "diazepam", "penicillin g",
    "tetracycline", "streptomycin", "vancomycin", "capsaicin", "menthol",
    "camphor", "limonene", "vanillin", "eugenol", "aspartame", "saccharin",
    "citronellal", "geraniol", "linalool", "indigo", "azobenzene",
    "anthracene", "phenanthrene", "cyclohexane", "cyclohexanone",
    "nitrobenzene", "benzaldehyde", "acetophenone", "ethyl acetate",
    "methyl salicylate", "dichloromethane", "carbon tetrachloride", "hexane",
]


# 브라우저(web/)는 이 표를 그대로 실어서 문자열 그대로 대조한다 - names.py 의
# korean_name()/variants()/_ko_key() 를 못 부른다(파이썬 없음). 그 규칙을 JS 로
# 다시 짜면 코드가 두 언어에 흩어져 한쪽만 고치는 일이 생긴다. 대신 정규화가
# 지웠을 표기 차이를 여기서 표에 미리 다 구워 넣는다 - 소비자는 그대로 정확
# 일치만 하면 된다 (브라우저는 trim()+toLowerCase(), 아래서 그 결과를 미리 낸다).
_FULLWIDTH = str.maketrans({
    **{chr(c): chr(c - 0x41 + 0xFF21) for c in range(0x41, 0x5B)},  # A-Z
    **{chr(c): chr(c - 0x61 + 0xFF41) for c in range(0x61, 0x7B)},  # a-z
    **{chr(c): chr(c - 0x30 + 0xFF10) for c in range(0x30, 0x3A)},  # 0-9
})
# "비스페놀A" 처럼 한글 뒤에 라틴 접미사가 바로 붙는 표기. 공백 유무 둘 다 나온다.
_TRAILING_LATIN = re.compile(r"^(.*[가-힣])([A-Za-z0-9]+)$")


def ko_key_variants(alias: str) -> set[str]:
    """한 한글 표기에서 나올 수 있는 다른 표기. 새 이름을 짓지 않고 표기만 넓힌다.

    1. 원래 표기
    2. 내부 공백·하이픈을 뗀 것 ('tert-부탄올' <-> 'tert부탄올')
    3. 한글 뒤 라틴 접미사에 공백을 끼운 것 ('비스페놀A' <-> '비스페놀 A')
    4. 위 표기들의 전각 라틴 문자 버전 ('비타민C' -> '비타민Ｃ') - 학생 자판·IME 가
       가끔 전각으로 낸다. Python str.lower() 와 JS toLowerCase() 둘 다 전각
       대문자를 전각 소문자로 접는다(확인됨) - 대소문자는 소비자가 그대로 접는다.
    """
    out = {alias}
    compact = re.sub(r"[\s\-]+", "", alias)
    out.add(compact)
    m = _TRAILING_LATIN.match(compact)
    if m:
        out.add(f"{m.group(1)} {m.group(2)}")
    for base in list(out):
        if re.search(r"[A-Za-z0-9]", base):
            out.add(base.translate(_FULLWIDTH))
    return out


def add_korean_keys(entries: dict[str, dict], compounds: list[dict]) -> tuple[int, list[str]]:
    """entries 에 한글 표기를 키로 더한다. 새 화합물을 추가하지 않는다 - 이미 영문으로
    풀린 레코드를 한글 키로도 찾게 할 뿐이다.

    원천은 둘이다: KO_ALIASES/KO_WHOLE_ONLY(영문 대상이 이미 entries 에 있어야 한다)와
    lecture_compounds.json 의 ko 배열(그 화합물의 answer 가 정답). 겹치면 answer 를
    믿는다 - 실제로 PubChem 에 그 이름으로 물어 받은 레코드이기 때문이다.
    """
    sources: dict[str, dict] = {}
    for c in compounds:
        answer = c.get("answer")
        if not answer:
            continue
        for ko in c.get("ko", []):
            sources[ko] = answer
    for ko, en in {**KO_ALIASES, **KO_WHOLE_ONLY}.items():
        record = entries.get(en.strip().lower())
        if record is not None:
            sources.setdefault(ko, record)

    added = 0
    conflicts: list[str] = []
    for ko, record in sources.items():
        for variant in ko_key_variants(ko):
            key = variant.lower()
            existing = entries.get(key)
            if existing is None:
                entries[key] = record
                added += 1
            elif existing.get("inchikey") != record.get("inchikey"):
                conflicts.append(f"{key!r}: 기존 {existing.get('inchikey')} vs {ko!r} -> {record.get('inchikey')}")
    return added, conflicts


def fetch(session: requests.Session, name: str) -> dict | None:
    """한 이름. 일시적 오류는 물러섰다가 다시 시도한다."""
    for attempt in range(4):
        try:
            resp = session.get(PUBCHEM.format(quote(name, safe="")), timeout=20)
        except requests.RequestException as exc:
            print(f"    망 실패 ({exc.__class__.__name__}), 재시도 {attempt + 1}")
            time.sleep(min(0.5 * 2**attempt, 8.0))
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 200:
            try:
                props = resp.json()["PropertyTable"]["Properties"][0]
            except (KeyError, IndexError, ValueError):
                return None
            return {
                "inchikey": props["InChIKey"],
                "formula": props.get("MolecularFormula", ""),
                "smiles": props.get("SMILES") or props.get("ConnectivitySMILES") or "",
                "iupac": props.get("IUPACName", ""),
                "title": props.get("Title", ""),
                "cid": props.get("CID"),
            }
        time.sleep(min(0.5 * 2**attempt, 8.0))
    return None


def fill_lecture_answers(session: requests.Session) -> list[dict]:
    """목록의 각 화합물에 대해 id 이름의 PubChem 응답을 answer 로 적는다. 이미 있으면 둔다."""
    doc = json.loads(LECTURE.read_text(encoding="utf-8"))
    compounds = doc["compounds"]
    todo = [c for c in compounds if not c.get("answer")]
    if todo:
        print(f"목록 {len(compounds)}개 중 정답이 없는 {len(todo)}개를 PubChem 에 조회한다")
    for i, c in enumerate(todo, start=1):
        record = fetch(session, c["id"])
        if record is None:
            print(f"  {i:3}/{len(todo)}  해석 못함  {c['id']}  <- id 를 PubChem 이 아는 이름으로 바꿔야 한다")
        else:
            c["answer"] = record
            print(f"  {i:3}/{len(todo)}  {record['inchikey']}  {c['id']}")
        time.sleep(0.25)
    if todo:
        LECTURE.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{LECTURE.relative_to(ROOT)} 갱신\n")
    return compounds


def main() -> int:
    refresh = "--refresh" in sys.argv[1:]
    session = requests.Session()
    compounds = fill_lecture_answers(session)
    # 목록의 영문 표기(관용명·IUPAC·약어)와 그 변형(입체 접두사를 뗀 것 등). 변형을
    # 함께 넣어야 '(R)-...' 를 망 없이도 뗀 형태로 풀 수 있다.
    # 한글은 KO_ALIASES 를 거치므로 여기 넣지 않는다.
    lecture_names = {
        v.lower(): c["answer"]["inchikey"]
        for c in compounds if c.get("answer")
        for n in c["en"] + c["iupac"] + c["abbr"]
        for v in variants(n)
    }
    ko_targets = {v.lower() for v in KO_ALIASES.values()} | {v.lower() for v in KO_WHOLE_ONLY.values()}
    names = sorted(ko_targets | {n.lower() for n in EXTRA} | set(lecture_names))
    print(f"{len(names)}개 이름을 PubChem 에 조회한다 (한국어 별칭 대응 "
          f"{len(ko_targets)}개, 목록 영문 표기 {len(lecture_names)}개 포함)\n")

    previous: dict[str, dict] = {}
    if not refresh:
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8")).get("entries", {})
        except (OSError, json.JSONDecodeError):
            previous = {}

    entries: dict[str, dict] = {}
    missing: list[str] = []
    wrong: list[str] = []

    for i, name in enumerate(names, start=1):
        if name in previous and previous[name].get("smiles"):
            entries[name] = previous[name]  # 지난번 PubChem 응답. --refresh 면 다시 받는다
            continue
        record = fetch(session, name)
        if record is None:
            missing.append(name)
            print(f"  {i:3}/{len(names)}  해석 못함  {name}")
        elif name in lecture_names and skeleton(record["inchikey"]) != skeleton(lecture_names[name]):
            # PubChem 이 이 표기를 다른 화합물로 읽는다. 표에 넣으면 그 이름으로
            # 엉뚱한 그림이 나온다. 넣지 않고 보고한다 - 목록 쪽 표기가 틀렸을 수 있다.
            wrong.append(f"{name} -> {record['inchikey']} (기대 {lecture_names[name]})")
            print(f"  {i:3}/{len(names)}  다른 화합물  {name}")
        else:
            entries[name] = record
            print(f"  {i:3}/{len(names)}  {record['inchikey']}  {name}")
        time.sleep(0.25)  # PubChem 은 초당 5건까지 허용한다

    ko_added, ko_conflicts = add_korean_keys(entries, compounds)
    print(f"\n한글 키 {ko_added}건 추가 (KO_ALIASES/KO_WHOLE_ONLY + 교과서 목록 ko 배열의 "
          f"표기 확장 - 공백·하이픈·전각 차이)")
    if ko_conflicts:
        print(f"경고: 한글 표기가 이미 있는 다른 화합물 키와 겹친다 (넣지 않음):")
        for line in ko_conflicts:
            print(f"      {line}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "source": "PubChem PUG REST /compound/name/{}/property/InChIKey,MolecularFormula,SMILES,IUPACName,Title",
                "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "PubChem 응답을 그대로 받아 적은 것. 여기서 구조를 추론하지 않는다.",
                "count": len(entries),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"\n{OUT.relative_to(ROOT)} 에 {len(entries)}건 기록")

    # 한국어 별칭이 가리키는데 해석되지 않은 영문 이름 = 별칭이 틀렸다는 뜻이다.
    bad = {ko: en for ko, en in {**KO_ALIASES, **KO_WHOLE_ONLY}.items() if en.lower() in missing}
    if bad:
        print("\n경고: 아래 한국어 별칭의 영문 대응이 PubChem 에서 해석되지 않았다.")
        print("      names.py 의 KO_ALIASES 를 고쳐야 한다.")
        for ko, en in sorted(bad.items()):
            print(f"      {ko} -> {en!r}")
    if wrong:
        print(f"\n목록과 다른 화합물로 해석된 표기 {len(wrong)}건 (표에 넣지 않음):")
        for line in wrong:
            print(f"      {line}")
    if missing:
        print(f"\n해석 안 된 이름 {len(missing)}건: {', '.join(missing)}")
    return 1 if bad or ko_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
