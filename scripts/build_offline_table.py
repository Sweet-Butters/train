"""동봉용 이름->InChIKey 표를 PubChem 응답으로 만든다.

망이 없을 때(KTX, 강의실 와이파이) chemcheck 가 이름을 하나도 해석하지 못하면
모든 그림이 판정불가로 빠져 도구가 무용해진다. 흔한 화합물만이라도 들고 다닌다.

여기서 구조를 지어내지 않는다. PubChem 이 준 InChIKey 를 그대로 받아 적고,
어디서 언제 받았는지 함께 남긴다. 해석되지 않은 이름은 표에 넣지 않고 보고한다
(한국어 별칭의 영문 대응이 틀렸다면 여기서 드러난다).

    python scripts/build_offline_table.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.names import KO_ALIASES, PUBCHEM  # noqa: E402

OUT = ROOT / "chemcheck" / "data" / "compounds.json"

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
                "cid": props.get("CID"),
            }
        time.sleep(min(0.5 * 2**attempt, 8.0))
    return None


def main() -> int:
    names = sorted({v.lower() for v in KO_ALIASES.values()} | {n.lower() for n in EXTRA})
    print(f"{len(names)}개 이름을 PubChem 에 조회한다 (한국어 별칭 대응 "
          f"{len({v.lower() for v in KO_ALIASES.values()})}개 포함)\n")

    entries: dict[str, dict] = {}
    missing: list[str] = []
    session = requests.Session()

    for i, name in enumerate(names, start=1):
        record = fetch(session, name)
        if record is None:
            missing.append(name)
            print(f"  {i:3}/{len(names)}  해석 못함  {name}")
        else:
            entries[name] = record
            print(f"  {i:3}/{len(names)}  {record['inchikey']}  {name}")
        time.sleep(0.25)  # PubChem 은 초당 5건까지 허용한다

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "source": "PubChem PUG REST /compound/name/{}/property/InChIKey,MolecularFormula",
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
    bad = {ko: en for ko, en in KO_ALIASES.items() if en.lower() in missing}
    if bad:
        print("\n경고: 아래 한국어 별칭의 영문 대응이 PubChem 에서 해석되지 않았다.")
        print("      names.py 의 KO_ALIASES 를 고쳐야 한다.")
        for ko, en in sorted(bad.items()):
            print(f"      {ko} -> {en!r}")
    if missing:
        print(f"\n해석 안 된 이름 {len(missing)}건: {', '.join(missing)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
