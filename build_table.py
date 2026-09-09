"""chemcheck/data/compounds.json 을 브라우저가 통째로 싣는 JS 로 굽는다.

정적 페이지는 서버가 없으므로 JSON 을 fetch 할 수 없다 (file:// 에서는 막힌다).
그래서 표를 스크립트 태그 하나로 실어 둔다. 이름 → {smiles, inchikey, cid} 만
남기고 나머지 필드는 뺀다 - 페이지가 쓰는 것이 그 셋이다.

    python web/build_table.py                 # 이 저장소의 표로
    python web/build_table.py 다른/compounds.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parent / "chemcheck" / "data" / "compounds.json"
TARGET = HERE / "compounds.js"


def build(source: Path = DEFAULT_SOURCE, target: Path = TARGET) -> int:
    data = json.loads(source.read_text(encoding="utf-8"))
    entries = data.get("entries", data)
    table = {}
    for name, row in entries.items():
        if not isinstance(row, dict) or not row.get("smiles"):
            continue
        table[name.strip().lower()] = {
            "smiles": row["smiles"],
            "inchikey": row.get("inchikey", ""),
            "cid": row.get("cid"),
            "title": row.get("title", name),
        }
    body = json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    target.write_text(
        "// 생성 파일. web/build_table.py 가 chemcheck/data/compounds.json 에서 굽는다. 손으로 고치지 않는다.\n"
        f"window.CHEMCHECK_TABLE = {body};\n",
        encoding="utf-8",
    )
    return len(table)


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    n = build(src)
    print(f"{n} compounds -> {TARGET} ({TARGET.stat().st_size} bytes)")
