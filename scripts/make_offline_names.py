"""오프라인 이름표를 만든다.

행사장에서 PubChem에 못 닿을 때 쓰는 최소 대체표다. InChIKey는 여기서 직접 적지 않고
RDKit으로 계산한다 — 손으로 옮겨 적다 틀리면 판정이 조용히 오염된다.
표에 없는 이름은 평소처럼 PubChem으로 간다.
"""
from __future__ import annotations

import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import Descriptors, inchi
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

DEST = Path(__file__).resolve().parents[1] / "chemcheck" / "offline_names.json"

# 강의자료에 흔하고 이름-구조 대응이 모호하지 않은 것만 넣는다.
# 입체화학이 얽힌 화합물(포도당, 니코틴 등)은 표기 관행이 갈리므로 일부러 뺐다.
COMPOUNDS: dict[str, str] = {
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "acetylsalicylic acid": "CC(=O)Oc1ccccc1C(=O)O",
    "salicylic acid": "OC(=O)c1ccccc1O",
    "caffeine": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
    "theobromine": "CN1C=NC2=C1C(=O)NC(=O)N2C",
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "acetaminophen": "CC(=O)Nc1ccc(O)cc1",
    "ibuprofen": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "benzene": "c1ccccc1",
    "toluene": "Cc1ccccc1",
    "phenol": "Oc1ccccc1",
    "aniline": "Nc1ccccc1",
    "naphthalene": "c1ccc2ccccc2c1",
    "benzoic acid": "OC(=O)c1ccccc1",
    "acetic acid": "CC(=O)O",
    "acetone": "CC(C)=O",
    "ethanol": "CCO",
    "methanol": "CO",
    "glycine": "NCC(=O)O",
    "urea": "NC(N)=O",
}


def build() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for name, smiles in COMPOUNDS.items():
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"SMILES 파싱 실패: {name} = {smiles}")
        table[name] = {
            "inchikey": inchi.MolToInchiKey(mol),
            "formula": CalcMolFormula(mol),
            "inchi": inchi.MolToInchi(mol),
        }
    return table


if __name__ == "__main__":
    table = build()
    DEST.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(table)}개 -> {DEST.relative_to(Path.cwd())}")
    for name, rec in sorted(table.items()):
        print(f"  {name:22} {rec['inchikey']}  {rec['formula']}")
