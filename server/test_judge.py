"""판정 층만 따로 검증한다. 엔진 없이 돈다 - rdkit 만 있으면 된다.

    python server/test_judge.py

엔진을 띄우지 않고 '엔진이 이렇게 읽었다면' 을 넣어 판정을 본다. 실제 4장의
DECIMER 출력이 어떻든, 판정 규칙이 계약대로 도는지는 여기서 잠근다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chemcheck.names import PubChemResolver  # noqa: E402
from server.judge import EngineRead, build_result  # noqa: E402

CASES = [
    # (설명, 이름, 엔진이 읽은 SMILES 또는 None, 기대 verdict)
    ("맞는 카페인", "caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C", "match"),
    ("N9 메틸 하나 더 (양이온 + 요오드화물 2개)", "caffeine",
     "C[n+]1cn(C)c2c1c(=O)n(C)c(=O)n2C.[I-].[I-]", None),
    ("골격이 다른 것 (아스피린을 카페인이라 부름)", "caffeine",
     "CC(=O)Oc1ccccc1C(=O)O", "mismatch"),
    ("RDKit 이 못 읽는 SMILES", "caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C)))", "unreadable"),
    ("맞는 L-알라닌 (입체 무시, 골격만)", "L-alanine", "CC(N)C(=O)O", "match"),
    ("엔진이 아무 것도 못 냄", "L-alanine", None, "unreadable"),
    ("표에 없는 이름", "완전히없는화합물이름", "CC(N)C(=O)O", "unreadable"),
]


def main() -> int:
    resolver = PubChemResolver(timeout=8.0, max_retries=1)
    failures = 0
    for label, name, smiles, expect in CASES:
        ref = resolver.resolve(name)
        reads = [EngineRead("decimer", smiles, 0.93)] if smiles else []
        out = build_result(name, ref, reads)
        ok = expect is None or out["verdict"] == expect
        failures += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {label}")
        print(f"       verdict={out['verdict']} grade={out['grade']} "
              f"heavy={out['read']['heavy_formula']}")
        for r in out["reasons"]:
            print(f"       - {r}")
        # 계약이 정한 키가 다 있는지
        for key in ("verdict", "grade", "reference", "read", "reasons"):
            assert key in out, f"계약 위반: {key} 없음"
        for key in ("smiles", "inchikey", "heavy_formula", "engines"):
            assert key in out["read"], f"계약 위반: read.{key} 없음"
        json.dumps(out, ensure_ascii=False)  # 직렬화 가능해야 한다 (NaN 금지)
    print("\n실패:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
