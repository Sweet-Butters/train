"""교과서 화합물 100개 목록의 원본. 실행하면 lecture_compounds.json 을 쓴다.

답(InChIKey·SMILES)은 scripts/build_offline_table.py 가 PubChem 에서 받아 채운다.
여기에 InChIKey 를 손으로 적지 않는다 - 손으로 적은 키는 오타 하나로 틀린 정답이 된다.

칸:
  id     PubChem 에 정답을 물을 때 쓰는 영문 이름. 이 이름의 응답이 정답이다.
  en     영문 관용명·상품명 (id 포함)
  iupac  IUPAC 계통명 또는 그에 준하는 체계적 이름
  ko     한글 표기. 옛 표기(메탄)와 대한화학회 새 표기(메테인)를 함께 둔다 - 강의자료엔 둘 다 나온다.
  abbr   약어·기호
"""
from __future__ import annotations

import json
from pathlib import Path

C: list[dict] = []


def add(id_, en=(), iupac=(), ko=(), abbr=()):
    C.append({"id": id_, "en": [id_, *en], "iupac": list(iupac), "ko": list(ko), "abbr": list(abbr)})


# ── 일반화학: 무기·소분자 ──────────────────────────────────────────────
add("water", (), ("oxidane", "dihydrogen monoxide"), ("물",), ("H2O",))
add("ammonia", (), ("azane",), ("암모니아",), ("NH3",))
add("carbon dioxide", (), (), ("이산화탄소",), ("CO2",))
add("carbon monoxide", (), (), ("일산화탄소",), ("CO",))
add("hydrogen peroxide", (), (), ("과산화수소",), ("H2O2",))
add("sulfuric acid", ("oil of vitriol",), (), ("황산",), ("H2SO4",))
add("hydrochloric acid", ("muriatic acid",), (), ("염산",), ("HCl",))
add("nitric acid", (), (), ("질산",), ("HNO3",))
add("phosphoric acid", ("orthophosphoric acid",), (), ("인산",), ("H3PO4",))
add("sodium chloride", ("table salt", "halite"), (), ("염화나트륨", "염화소듐", "소금"), ("NaCl",))
add("sodium hydroxide", ("caustic soda", "lye"), (), ("수산화나트륨", "수산화소듐", "가성소다"), ("NaOH",))
add("sodium bicarbonate", ("baking soda", "sodium hydrogen carbonate"), (), ("탄산수소나트륨", "탄산수소소듐", "중탄산나트륨"), ("NaHCO3",))
add("calcium carbonate", ("calcite",), (), ("탄산칼슘",), ("CaCO3",))
add("potassium permanganate", (), (), ("과망간산칼륨", "과망가니즈산칼륨", "과망가니즈산포타슘"), ("KMnO4",))
add("ozone", (), ("trioxygen",), ("오존",), ("O3",))
add("nitrous oxide", ("laughing gas",), ("dinitrogen monoxide",), ("아산화질소", "일산화이질소"), ("N2O",))

# ── 유기화학: 탄화수소·용매 ───────────────────────────────────────────
add("methane", (), (), ("메탄", "메테인"), ("CH4",))
add("ethane", (), (), ("에탄", "에테인"), ())
add("propane", (), (), ("프로판", "프로페인"), ())
add("butane", ("n-butane",), (), ("부탄", "뷰테인", "노말부탄"), ())
add("isobutane", (), ("2-methylpropane",), ("이소부탄", "아이소뷰테인"), ())
add("ethylene", (), ("ethene",), ("에틸렌", "에텐"), ())
add("acetylene", (), ("ethyne",), ("아세틸렌", "에타인"), ())
add("benzene", ("benzol",), ("cyclohexa-1,3,5-triene",), ("벤젠",), ())
add("toluene", (), ("methylbenzene",), ("톨루엔",), ())
add("phenol", ("carbolic acid",), ("hydroxybenzene", "benzenol"), ("페놀",), ())
add("aniline", (), ("aminobenzene", "benzenamine", "phenylamine"), ("아닐린",), ())
add("pyridine", (), ("azabenzene", "azine"), ("피리딘",), ())
add("naphthalene", (), (), ("나프탈렌",), ())
add("cyclohexane", (), (), ("사이클로헥산", "사이클로헥세인", "시클로헥산"), ())
add("styrene", (), ("vinylbenzene", "ethenylbenzene", "phenylethene"), ("스티렌", "스타이렌"), ())
add("ethanol", ("ethyl alcohol", "grain alcohol"), (), ("에탄올", "에틸알코올", "주정"), ("EtOH",))
add("methanol", ("methyl alcohol", "wood alcohol"), (), ("메탄올", "메틸알코올"), ("MeOH",))
add("isopropyl alcohol", ("isopropanol", "rubbing alcohol"), ("propan-2-ol", "2-propanol"), ("이소프로판올", "아이소프로판올", "이소프로필알코올"), ("IPA",))
add("tert-butanol", ("tert-butyl alcohol", "t-butanol"), ("2-methylpropan-2-ol", "2-methyl-2-propanol"), ("tert-부탄올", "삼차부탄올"), ("t-BuOH",))
add("ethylene glycol", (), ("ethane-1,2-diol", "1,2-ethanediol"), ("에틸렌글리콜", "에틸렌글라이콜"), ())
add("glycerol", ("glycerin", "glycerine"), ("propane-1,2,3-triol", "1,2,3-propanetriol"), ("글리세롤", "글리세린"), ())
add("acetone", ("dimethyl ketone",), ("propan-2-one", "2-propanone", "propanone"), ("아세톤",), ())
add("formaldehyde", ("formalin",), ("methanal",), ("포름알데히드", "폼알데하이드", "포름알데하이드"), ())
add("acetaldehyde", (), ("ethanal",), ("아세트알데히드", "아세트알데하이드"), ())
add("benzaldehyde", (), ("benzenecarbaldehyde",), ("벤즈알데히드", "벤즈알데하이드"), ())
add("acetophenone", ("methyl phenyl ketone",), ("1-phenylethanone", "1-phenylethan-1-one"), ("아세토페논",), ())
add("diethyl ether", ("ethyl ether", "ether"), ("ethoxyethane", "1,1'-oxybisethane"), ("디에틸에테르", "다이에틸에터", "에테르"), ("Et2O",))
add("tetrahydrofuran", (), ("oxolane",), ("테트라히드로푸란", "테트라하이드로퓨란"), ("THF",))
add("dimethyl sulfoxide", (), ("methylsulfinylmethane", "(methylsulfinyl)methane"), ("디메틸술폭시드", "다이메틸설폭사이드", "디메틸설폭사이드"), ("DMSO",))
add("dimethylformamide", ("N,N-dimethylformamide",), ("N,N-dimethylmethanamide",), ("디메틸포름아미드", "다이메틸폼아마이드"), ("DMF",))
add("acetonitrile", ("methyl cyanide",), ("ethanenitrile", "cyanomethane"), ("아세토니트릴", "아세토나이트릴"), ("MeCN", "ACN"))
add("chloroform", (), ("trichloromethane",), ("클로로포름", "클로로폼"), ("CHCl3",))
add("dichloromethane", ("methylene chloride",), (), ("디클로로메탄", "다이클로로메테인", "염화메틸렌"), ("DCM",))
add("carbon tetrachloride", (), ("tetrachloromethane",), ("사염화탄소",), ("CCl4",))
add("ethyl acetate", (), ("ethyl ethanoate",), ("에틸아세테이트", "아세트산에틸", "초산에틸"), ("EtOAc",))
add("hexane", ("n-hexane",), (), ("헥산", "헥세인", "노말헥산"), ())
add("nitrobenzene", (), (), ("니트로벤젠", "나이트로벤젠"), ())
add("acetic anhydride", (), ("ethanoic anhydride",), ("아세트산무수물", "무수아세트산", "무수초산"), ("Ac2O",))

# ── 유기화학: 산·염기·작은 작용기 화합물 ──────────────────────────────
add("acetic acid", ("glacial acetic acid",), ("ethanoic acid",), ("아세트산", "초산"), ("AcOH", "HOAc"))
add("formic acid", (), ("methanoic acid",), ("포름산", "폼산", "개미산"), ())
add("benzoic acid", (), ("benzenecarboxylic acid",), ("벤조산", "안식향산"), ())
add("oxalic acid", (), ("ethanedioic acid",), ("옥살산", "수산"), ())
add("citric acid", (), ("2-hydroxypropane-1,2,3-tricarboxylic acid",), ("시트르산", "구연산"), ())
add("lactic acid", ("milk acid",), ("2-hydroxypropanoic acid",), ("젖산", "락트산"), ())
add("pyruvic acid", (), ("2-oxopropanoic acid",), ("피루브산", "피루빈산"), ())
add("succinic acid", (), ("butanedioic acid",), ("숙신산", "석신산", "호박산"), ())
add("fumaric acid", (), ("(E)-butenedioic acid", "trans-butenedioic acid"), ("푸마르산", "퓨마르산"), ())
add("maleic acid", (), ("(Z)-butenedioic acid", "cis-butenedioic acid"), ("말레산", "말레익산"), ())
add("tartaric acid", (), ("2,3-dihydroxybutanedioic acid",), ("타르타르산", "주석산"), ())
add("salicylic acid", (), ("2-hydroxybenzoic acid",), ("살리실산",), ())
add("urea", ("carbamide",), ("carbonyl diamide",), ("요소", "우레아"), ())
add("methylamine", (), ("methanamine", "aminomethane"), ("메틸아민",), ("MeNH2",))
add("trinitrotoluene", ("TNT",), ("2,4,6-trinitrotoluene", "2-methyl-1,3,5-trinitrobenzene"), ("트리니트로톨루엔", "트라이나이트로톨루엔"), ())
add("bisphenol A", (), ("4,4'-(propane-2,2-diyl)diphenol", "2,2-bis(4-hydroxyphenyl)propane"), ("비스페놀 A", "비스페놀A"), ("BPA",))
add("ethylenediaminetetraacetic acid", ("edetic acid",), (), ("에틸렌다이아민테트라아세트산", "에틸렌디아민사아세트산"), ("EDTA",))

# ── 생화학: 당·아미노산·염기·지질 ────────────────────────────────────
add("glucose", ("D-glucose", "dextrose", "grape sugar"), (), ("포도당", "글루코스", "글루코오스"), ("Glc",))
add("fructose", ("D-fructose", "levulose", "fruit sugar"), (), ("과당", "프럭토스", "프룩토스"), ())
add("sucrose", ("table sugar", "saccharose", "cane sugar"), (), ("설탕", "수크로스", "자당", "수크로오스"), ())
add("lactose", ("milk sugar",), (), ("젖당", "락토스", "유당", "락토오스"), ())
add("ribose", ("D-ribose",), (), ("리보스", "리보오스"), ())
add("glycine", (), ("aminoacetic acid", "2-aminoacetic acid", "aminoethanoic acid"), ("글리신",), ("Gly",))
add("alanine", ("L-alanine",), ("(S)-2-aminopropanoic acid", "2-aminopropanoic acid"), ("알라닌",), ("Ala",))
add("cysteine", ("L-cysteine",), ("(R)-2-amino-3-sulfanylpropanoic acid",), ("시스테인",), ("Cys",))
add("tryptophan", ("L-tryptophan",), (), ("트립토판",), ("Trp",))
add("tyrosine", ("L-tyrosine",), ("4-hydroxyphenylalanine",), ("티로신", "타이로신"), ("Tyr",))
add("phenylalanine", ("L-phenylalanine",), ("2-amino-3-phenylpropanoic acid",), ("페닐알라닌",), ("Phe",))
add("adenine", (), ("6-aminopurine", "9H-purin-6-amine"), ("아데닌",), ())
add("guanine", (), ("2-amino-6-oxopurine", "2-aminohypoxanthine"), ("구아닌",), ())
add("cytosine", (), ("4-aminopyrimidin-2(1H)-one", "4-amino-2-hydroxypyrimidine"), ("사이토신", "시토신"), ())
add("thymine", (), ("5-methyluracil", "5-methylpyrimidine-2,4-dione"), ("티민", "타이민"), ())
add("uracil", (), ("pyrimidine-2,4-dione", "2,4-dihydroxypyrimidine"), ("우라실",), ())
add("adenosine triphosphate", ("adenosine 5'-triphosphate",), (), ("아데노신삼인산", "아데노신 삼인산"), ("ATP",))
add("cholesterol", (), (), ("콜레스테롤",), ())
add("palmitic acid", (), ("hexadecanoic acid",), ("팔미트산", "팔미틱산"), ())
add("stearic acid", (), ("octadecanoic acid",), ("스테아르산", "스테아린산"), ())
add("oleic acid", (), ("(9Z)-octadec-9-enoic acid", "cis-9-octadecenoic acid"), ("올레산", "올레인산"), ())
add("linoleic acid", (), ("(9Z,12Z)-octadeca-9,12-dienoic acid",), ("리놀레산", "리놀레인산"), ())

# ── 신경전달물질·호르몬·약물·천연물 ──────────────────────────────────
add("acetylcholine", (), (), ("아세틸콜린",), ("ACh",))
add("dopamine", (), ("4-(2-aminoethyl)benzene-1,2-diol", "3,4-dihydroxyphenethylamine"), ("도파민",), ("DA",))
add("serotonin", ("5-hydroxytryptamine",), ("3-(2-aminoethyl)-1H-indol-5-ol",), ("세로토닌",), ("5-HT",))
add("epinephrine", ("adrenaline",), (), ("에피네프린", "아드레날린"), ())
add("histamine", (), ("2-(1H-imidazol-4-yl)ethanamine", "2-(1H-imidazol-5-yl)ethanamine"), ("히스타민",), ())
add("aspirin", ("acetylsalicylic acid",), ("2-acetoxybenzoic acid", "2-(acetyloxy)benzoic acid", "2-acetyloxybenzoic acid"), ("아스피린", "아세틸살리실산"), ("ASA",))
add("acetaminophen", ("paracetamol", "Tylenol"), ("N-(4-hydroxyphenyl)acetamide", "4-acetamidophenol"), ("아세트아미노펜", "파라세타몰", "타이레놀"), ("APAP",))
add("ibuprofen", ("Advil",), ("2-[4-(2-methylpropyl)phenyl]propanoic acid", "(RS)-2-(4-isobutylphenyl)propanoic acid"), ("이부프로펜",), ())
add("naproxen", (), ("(S)-6-methoxy-alpha-methyl-2-naphthaleneacetic acid", "(2S)-2-(6-methoxynaphthalen-2-yl)propanoic acid"), ("나프록센",), ())
add("caffeine", (), ("1,3,7-trimethylxanthine", "1,3,7-trimethylpurine-2,6-dione"), ("카페인",), ())
add("nicotine", (), ("3-(1-methylpyrrolidin-2-yl)pyridine", "(S)-3-(1-methylpyrrolidin-2-yl)pyridine"), ("니코틴",), ())
add("morphine", (), (), ("모르핀", "몰핀"), ())
add("penicillin G", ("benzylpenicillin",), (), ("페니실린 G", "페니실린", "벤질페니실린"), ())
add("ascorbic acid", ("vitamin C", "L-ascorbic acid"), (), ("아스코르브산", "비타민 C", "비타민C", "아스코르빈산"), ())
add("retinol", ("vitamin A",), (), ("레티놀", "비타민 A"), ())
add("menthol", ("L-menthol", "(-)-menthol"), ("(1R,2S,5R)-2-isopropyl-5-methylcyclohexanol", "2-isopropyl-5-methylcyclohexanol"), ("멘톨",), ())
add("camphor", (), ("1,7,7-trimethylbicyclo[2.2.1]heptan-2-one",), ("캠퍼", "장뇌", "캄포르"), ())
add("limonene", ("D-limonene", "(R)-(+)-limonene"), ("1-methyl-4-(prop-1-en-2-yl)cyclohexene", "4-isopropenyl-1-methylcyclohexene"), ("리모넨",), ())
add("vanillin", (), ("4-hydroxy-3-methoxybenzaldehyde",), ("바닐린",), ())
add("capsaicin", (), (), ("캡사이신",), ())
add("nitroglycerin", ("glyceryl trinitrate", "nitroglycerine"), ("propane-1,2,3-triyl trinitrate", "1,2,3-propanetriol trinitrate"), ("니트로글리세린", "나이트로글리세린"), ("GTN",))
add("testosterone", (), (), ("테스토스테론",), ())
add("estradiol", ("oestradiol", "17beta-estradiol"), (), ("에스트라디올", "에스트라다이올"), ("E2",))
add("beta-carotene", ("β-carotene", "provitamin A"), (), ("베타카로틴", "베타-카로틴", "β-카로틴"), ())
add("quinine", (), (), ("퀴닌", "키니네"), ())
add("DDT", ("dichlorodiphenyltrichloroethane",), ("1,1,1-trichloro-2,2-bis(4-chlorophenyl)ethane",), ("디디티",), ())


def main() -> None:
    assert len(C) == len({c["id"] for c in C}), "id 중복"
    out = Path(__file__).with_name("lecture_compounds.json")
    old: dict[str, dict | None] = {}
    if out.exists():
        old = {c["id"]: c.get("answer") for c in json.loads(out.read_text(encoding="utf-8"))["compounds"]}
    for c in C:
        if old.get(c["id"]):
            c["answer"] = old[c["id"]]  # 이미 받은 정답은 유지한다
    doc = {
        "note": "유기·일반화학 강의에 나오는 화합물 목록. answer 는 PubChem 이 id 이름에 준 응답을 "
                "그대로 받아 적은 것이다 (scripts/build_offline_table.py). 손으로 적은 키는 없다.",
        "count": len(C),
        "compounds": C,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(c["en"]) + len(c["iupac"]) + len(c["ko"]) + len(c["abbr"]) for c in C)
    print(f"{out.name}: 화합물 {len(C)}개, 표기 {total}개")


if __name__ == "__main__":
    main()
