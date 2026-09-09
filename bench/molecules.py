"""recognize 평가 세트 - 알려진 분자 50개.

여기 적힌 이름은 사람이 읽기 위한 것이고, 정답은 이름이 아니라 SMILES 에서
RDKit 으로 계산한 InChIKey 다. 라벨을 믿지 않는다는 원칙 그대로다 - 이름과
SMILES 가 어긋나도 채점은 SMILES 를 따르고, 그 어긋남은 test 가 잡는다.

고른 기준: 교과서·약전에 흔한 분자를 크기별로 섞었다. 원자 두어 개짜리(에탄올,
요소)는 인식기에게 오히려 어렵다 - 글자가 그림의 대부분이다. 스테로이드·알칼로이드
는 고리가 얽혀 골격을 틀리기 쉽다. 둘 다 있어야 '어디서 틀리는가'가 보인다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Molecule:
    name: str
    smiles: str


MOLECULES: list[Molecule] = [
    # --- 작은 것 - 글자가 그림의 대부분이다 ---------------------------------
    Molecule("Ethanol", "CCO"),
    Molecule("Acetic acid", "CC(=O)O"),
    Molecule("Acetone", "CC(C)=O"),
    Molecule("Urea", "NC(N)=O"),
    Molecule("Glycine", "NCC(=O)O"),
    Molecule("L-Lactic acid", "C[C@H](O)C(=O)O"),
    Molecule("L-Alanine", "C[C@H](N)C(=O)O"),

    # --- 고리 하나 -------------------------------------------------------
    Molecule("Benzene", "c1ccccc1"),
    Molecule("Toluene", "Cc1ccccc1"),
    Molecule("Phenol", "Oc1ccccc1"),
    Molecule("Aniline", "Nc1ccccc1"),
    Molecule("Pyridine", "c1ccncc1"),
    Molecule("Furan", "c1ccoc1"),
    Molecule("Thiophene", "c1ccsc1"),
    Molecule("Imidazole", "c1c[nH]cn1"),
    Molecule("Cyclohexane", "C1CCCCC1"),
    Molecule("Cyclohexanol", "OC1CCCCC1"),
    Molecule("Nitrobenzene", "O=[N+]([O-])c1ccccc1"),
    Molecule("Benzoic acid", "OC(=O)c1ccccc1"),
    Molecule("Salicylic acid", "OC(=O)c1ccccc1O"),
    Molecule("Vanillin", "COc1cc(C=O)ccc1O"),
    Molecule("Eugenol", "C=CCc1ccc(O)c(OC)c1"),
    Molecule("Cinnamaldehyde", "O=C/C=C/c1ccccc1"),
    Molecule("Dopamine", "NCCc1ccc(O)c(O)c1"),

    # --- 약 - 사람이 실제로 그리고 틀리는 것들 --------------------------------
    Molecule("Aspirin", "CC(=O)Oc1ccccc1C(=O)O"),
    Molecule("Paracetamol", "CC(=O)Nc1ccc(O)cc1"),
    Molecule("Ibuprofen", "CC(C)Cc1ccc(C(C)C(=O)O)cc1"),
    Molecule("Caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C"),
    Molecule("Metformin", "CN(C)C(=N)NC(N)=N"),
    Molecule("Adrenaline", "CNC[C@H](O)c1ccc(O)c(O)c1"),
    Molecule("Citric acid", "OC(=O)CC(O)(CC(=O)O)C(=O)O"),

    # --- 고리 여럿·입체 - 골격을 틀리기 쉬운 것 -----------------------------
    Molecule("Naphthalene", "c1ccc2ccccc2c1"),
    Molecule("Anthracene", "c1ccc2cc3ccccc3cc2c1"),
    Molecule("Indole", "c1ccc2[nH]ccc2c1"),
    Molecule("Adenine", "Nc1ncnc2[nH]cnc12"),
    Molecule("Thymine", "Cc1c[nH]c(=O)[nH]c1=O"),
    Molecule("Nicotine", "CN1CCC[C@H]1c1cccnc1"),
    Molecule("Menthol", "CC(C)[C@@H]1CC[C@@H](C)C[C@H]1O"),
    Molecule("Limonene", "CC1=CC[C@H](CC1)C(=C)C"),
    Molecule("Serotonin", "NCCc1c[nH]c2ccc(O)cc12"),
    Molecule("Melatonin", "COc1ccc2[nH]cc(CCNC(C)=O)c2c1"),
    Molecule("L-Tryptophan", "N[C@@H](Cc1c[nH]c2ccccc12)C(=O)O"),
    Molecule("Camphor", "CC1(C)[C@@H]2CC[C@@]1(C)C(=O)C2"),
    Molecule("Warfarin", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O"),
    Molecule("Diazepam", "CN1C(=O)CN=C(c2ccccc2)c2cc(Cl)ccc21"),
    Molecule("Penicillin G", "CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O"),
    Molecule("Cocaine", "COC(=O)[C@H]1[C@@H]2CC[C@@H](C[C@@H]1OC(=O)c1ccccc1)N2C"),
    Molecule("Morphine", "CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5"),
    Molecule("Testosterone",
             "C[C@]12CC[C@H]3[C@@H](CCC4=CC(=O)CC[C@@]34C)[C@@H]1CC[C@@H]2O"),
    Molecule("Ascorbic acid", "OC[C@H](O)[C@H]1OC(=O)C(O)=C1O"),
]

# 평가 세트 크기. 50장 × 두 엔진이 한 번 도는 시간이 하루 끝 게이트의 예산이다.
# 늘리려면 여기와 목록을 함께 바꾼다 - 테스트가 둘이 같은지 본다.
SET_SIZE = 50
