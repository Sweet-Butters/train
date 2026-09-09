"""chemcheck/data/*.json 을 브라우저가 통째로 싣는 JS 로 굽는다.

정적 페이지는 서버가 없으므로 JSON 을 fetch 할 수 없다 (file:// 에서는 막힌다).
그래서 표를 스크립트 태그 하나로 실어 둔다.

    python web/build_table.py                 # 이 저장소의 표로

두 표를 낸다.

  CHEMCHECK_TABLE    "정규화한 영문 이름" -> {smiles, inchikey, cid, title}
                     chemcheck/data/compounds.json 이 그대로 원천. 필드는
                     페이지가 쓰는 것만 남긴다.
  CHEMCHECK_ALIASES  "느슨하게 정규화한 이름" -> inchikey
                     한글 표기(chemcheck/names.py 의 KO_ALIASES/KO_WHOLE_ONLY,
                     chemcheck/data/lecture_compounds.json 의 ko/en/iupac/abbr)와
                     공백·하이픈이 다른 영문 표기. CHEMCHECK_TABLE 에 이미 정확히
                     있는 키는 중복으로 넣지 않는다 - 그쪽이 먼저 걸린다.

이 스크립트는 chemcheck 패키지를 import 하지 않는다 (rdkit·requests 없이도
표를 구울 수 있게). names.py 의 KO_ALIASES 는 AST 로 리터럴만 읽는다 - 코드는
실행하지 않는다.

data/ 는 C 트랙 소유다 - 이 스크립트는 읽기만 한다.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "chemcheck" / "data"
DEFAULT_COMPOUNDS = DATA_DIR / "compounds.json"
DEFAULT_LECTURE = DATA_DIR / "lecture_compounds.json"
DEFAULT_NAMES_PY = HERE.parent / "chemcheck" / "names.py"
TARGET = HERE / "compounds.js"

_DASHES = str.maketrans({d: "-" for d in "‐‑‒–—−"})


def normalize_exact(s: str) -> str:
    """대소문자·앞뒤 공백·전각 문자·유니코드 대시 차이를 지운다. 내부 공백은 하나로 줄인다."""
    s = unicodedata.normalize("NFKC", s).strip().translate(_DASHES)
    return " ".join(s.split()).lower()


def normalize_loose(s: str) -> str:
    """normalize_exact 에 더해 공백·하이픈을 아예 없앤다. 한글·약칭 매칭용."""
    return re.sub(r"[\s\-]+", "", normalize_exact(s))


def load_ko_aliases(path: Path) -> dict[str, str]:
    """chemcheck/names.py 의 KO_ALIASES·KO_WHOLE_ONLY 를 AST 로 읽는다 (import 안 함)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("KO_ALIASES", "KO_WHOLE_ONLY"):
                    out.update(ast.literal_eval(node.value))
    return out


def build(
    compounds_path: Path = DEFAULT_COMPOUNDS,
    lecture_path: Path = DEFAULT_LECTURE,
    names_py_path: Path = DEFAULT_NAMES_PY,
    target: Path = TARGET,
) -> tuple[int, int, list[str]]:
    data = json.loads(compounds_path.read_text(encoding="utf-8"))
    entries = data.get("entries", data)

    table: dict[str, dict] = {}
    by_inchikey: dict[str, str] = {}  # inchikey -> 첫 정확 키 (별칭 해석용)
    for name, row in entries.items():
        if not isinstance(row, dict) or not row.get("smiles"):
            continue
        key = normalize_exact(name)
        table[key] = {
            "smiles": row["smiles"],
            "inchikey": row.get("inchikey", ""),
            "cid": row.get("cid"),
            "title": row.get("title", name),
        }
        by_inchikey.setdefault(row.get("inchikey", ""), key)

    aliases: dict[str, str] = {}
    missing: list[str] = []  # 표에서 구조를 못 찾은 한글 표기 (보고용)

    def offer_alias(alias: str, inchikey: str) -> None:
        if not alias or not inchikey:
            return
        loose = normalize_loose(alias)
        if not loose or loose in table:
            return  # 정확 표에 이미 있는 키 - 중복 안 실음
        aliases.setdefault(loose, inchikey)

    # 1) chemcheck/names.py 의 한글 표. 영문 대상이 compounds.json 에 있어야 쓴다.
    if names_py_path.exists():
        for ko, en in load_ko_aliases(names_py_path).items():
            key = normalize_exact(en)
            if key in table:
                offer_alias(ko, table[key]["inchikey"])
            else:
                missing.append(f"{ko} -> {en} (compounds.json 에 없음)")

    # 2) lecture_compounds.json. ko·en·iupac·abbr 전부 별칭으로 싣는다.
    if lecture_path.exists():
        lecture = json.loads(lecture_path.read_text(encoding="utf-8"))
        for compound in lecture.get("compounds", []):
            answer = compound.get("answer") or {}
            inchikey = answer.get("inchikey", "")
            if not inchikey:
                continue
            if inchikey not in by_inchikey and answer.get("smiles"):
                # compounds.json 에 이 InChIKey 가 없다 - 표에 직접 더한다(교과서 화합물이 빠지면 안 된다).
                key = normalize_exact(compound.get("id") or answer["smiles"])
                if key not in table:
                    table[key] = {
                        "smiles": answer["smiles"],
                        "inchikey": inchikey,
                        "cid": answer.get("cid"),
                        "title": (compound.get("en") or [compound.get("id", "")])[0],
                    }
                    by_inchikey.setdefault(inchikey, key)
            for field in ("ko", "en", "iupac", "abbr"):
                for alias in compound.get(field, []):
                    # 이 표기가 compounds.json 에 그 자체로(정확히) 이미 있으면 그쪽 키를
                    # 믿는다. lecture_compounds.json 의 answer 는 대표 이름(예: "menthol")
                    # 하나로 PubChem 을 부른 결과라 입체 표기("(-)-menthol")가 compounds.json
                    # 에서 가리키는 화합물과 다를 수 있다 - 둘 다 C 의 자료이므로 여기서
                    # 고치지 않고, 더 구체적인 표기(정확 매치)를 우선한다.
                    exact = normalize_exact(alias)
                    offer_alias(alias, table[exact]["inchikey"] if exact in table else inchikey)

    body_table = json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    body_aliases = json.dumps(aliases, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    target.write_text(
        "// 생성 파일. web/build_table.py 가 chemcheck/data/*.json 에서 굽는다. 손으로 고치지 않는다.\n"
        f"window.CHEMCHECK_TABLE = {body_table};\n"
        f"window.CHEMCHECK_ALIASES = {body_aliases};\n",
        encoding="utf-8",
    )
    return len(table), len(aliases), missing


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COMPOUNDS
    n_table, n_aliases, missing = build(src)
    print(f"{n_table} compounds, {n_aliases} aliases(한글·약칭) -> {TARGET} ({TARGET.stat().st_size} bytes)")
    if missing:
        print(f"{len(missing)}건은 compounds.json 에 구조가 없어 별칭을 못 실었다:")
        for m in missing:
            print(f"  {m}")
