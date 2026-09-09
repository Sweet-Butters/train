"""교과서 화합물 목록으로 `draw` 의 이름 적중률을 잰다.

`draw` 는 이름 -> PubChem 정본 -> 그림이다. 추론이 없어 틀릴 수 없지만 못 찾으면
아무것도 못 그린다. 그래서 draw 의 품질은 곧 names.py 가 이름을 찾는 비율이다.

경로는 structure.draw 와 같다: 한글이면 korean_name() 으로 영문을 얻고,
PubChemResolver.resolve() 로 조회한다. 정답은 lecture_compounds.json 의 answer
(scripts/build_offline_table.py 가 PubChem 에서 받아 적은 것) 이고, InChIKey 골격
14자가 같으면 적중이다. 입체화학만 다른 것은 적중으로 세되 따로 표시한다
('alanine' 과 'L-alanine' 은 같은 그림이 아니지만 틀린 화합물도 아니다).

    python scripts/measure_names.py            # 동봉한 표 없이 (망 + 캐시). 일반화 능력
    python scripts/measure_names.py --offline  # 동봉한 표 포함. 사용자가 실제로 겪는 것

못 찾은 것은 종류별로 묶어 낸다. 가장 많은 종류부터 고친다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck.keys import skeleton  # noqa: E402
from chemcheck.names import PubChemResolver, korean_name  # noqa: E402

LECTURE = ROOT / "chemcheck" / "data" / "lecture_compounds.json"
KINDS = ("en", "iupac", "ko", "abbr")


def is_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def lookup_name(query: str) -> str:
    """structure.lookup_name 과 같다. 그쪽은 MVP 파일이라 여기서 되풀이한다."""
    q = query.strip()
    if is_hangul(q):
        return korean_name(q) or q
    return q


def classify(query: str, resolved_name: str, key: str | None, expected: str) -> str:
    """적중이면 'hit' 또는 'stereo'. 아니면 못 찾은 종류."""
    if key and skeleton(key) == skeleton(expected):
        return "hit" if key == expected else "stereo"
    if is_hangul(query):
        if resolved_name == query:
            return "ko_no_alias"          # 한글 표에 없다
        return "ko_alias_miss" if key is None else "ko_alias_wrong"
    if key is None:
        if any(ch in query for ch in "αβγδ"):
            return "en_greek"
        if len(query) <= 6 and query.upper() == query:
            return "abbr_404"           # 대문자 약어를 PubChem 이 모른다
        if any(ch.isdigit() for ch in query) or "(" in query:
            return "iupac_404"          # 계통명을 PubChem 이 이 표기로는 모른다
        return "en_404"
    return "wrong_compound"


def measure(resolver: PubChemResolver, compounds: list[dict]) -> tuple[Counter, dict[str, list[str]]]:
    tally: Counter = Counter()
    misses: dict[str, list[str]] = defaultdict(list)
    for c in compounds:
        answer = c.get("answer")
        if not answer:
            tally["no_answer"] += len(c["ko"]) + len(c["en"]) + len(c["iupac"]) + len(c["abbr"])
            misses["no_answer"].append(c["id"])
            continue
        expected = answer["inchikey"]
        for kind in KINDS:
            for query in c[kind]:
                name = lookup_name(query)
                ref = resolver.resolve(name)
                verdict = classify(query, name, ref.inchikey if ref else None, expected)
                tally[verdict] += 1
                if verdict not in ("hit",):
                    got = f" -> {ref.inchikey}" if ref else ""
                    via = f" (표: {name})" if name != query else ""
                    misses[verdict].append(f"{c['id']:24} {kind:5} {query}{via}{got}")
    return tally, misses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="동봉한 compounds.json 도 쓴다")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    compounds = json.loads(LECTURE.read_text(encoding="utf-8"))["compounds"]
    resolver = PubChemResolver() if args.offline else PubChemResolver(offline={})
    tally, misses = measure(resolver, compounds)
    resolver.flush()

    total = sum(tally.values())
    hits = tally["hit"] + tally["stereo"]
    print(f"화합물 {len(compounds)}개, 표기 {total}개  "
          f"({'동봉 표 포함' if args.offline else '동봉 표 없이'})")
    print(f"적중 {hits}/{total} = {100 * hits / total:.1f}%   "
          f"(정확 {tally['hit']}, 입체만 다름 {tally['stereo']})\n")

    order = sorted((k for k in tally if k not in ("hit", "stereo")), key=lambda k: -tally[k])
    if order:
        print("못 찾은 종류 (많은 것부터):")
        for k in order:
            print(f"  {tally[k]:3}  {k}")
        print()
    if not args.quiet:
        for k in order:
            print(f"── {k} ({tally[k]})")
            for line in misses[k]:
                print("   ", line)
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
