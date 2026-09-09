"""실제 자료에 붙인 라벨을 읽는다.

자료 자체는 저작물이라 저장소에 없다. 여기 있는 것은 (장, 그림 파일명) -> 라벨
매핑뿐이고, 덱은 실행할 때 로컬 경로로 준다.

라벨은 자료의 사실을 적는다 - 도구가 무엇을 볼 수 있었는지는 넣지 않는다.
그래야 '도구가 못 본 것' 자체가 측정 대상이 된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from chemcheck.ocsr import Engine, Prediction

from .cases import Case, Truth


@dataclass(frozen=True)
class Manifest:
    source: dict
    labeling: dict
    by_image: dict[str, Case]     # 그림 파일명 -> 케이스
    smiles_by_image: dict[str, str]

    @property
    def size(self) -> int:
        return len(self.by_image)


def load_manifest(path: Path) -> Manifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    by_image: dict[str, Case] = {}
    smiles: dict[str, str] = {}
    for e in raw["entries"]:
        truth = Truth(e["truth"])
        name = f"p{e['slide']}/{e['image']}"
        by_image[e["image"]] = Case(name, e.get("smiles", ""), truth, e.get("note", ""))
        if e.get("smiles"):
            smiles[e["image"]] = e["smiles"]
    return Manifest(raw.get("source", {}), raw.get("labeling", {}), by_image, smiles)


class LabeledOracle(Engine):
    """라벨에 적힌 구조를 그대로 돌려주는 가상 인식기 - 실제 덱 위에서 돈다.

    이것으로 재려는 것은 인식기 성능이 아니다. '그림을 100% 읽는 인식기가 있어도
    나머지 파이프라인이 실제 자료에서 오탐을 내는가'이다. 여기서 나온 오탐은
    전부 인식기 탓이 아니다.
    """

    name = "labeled-oracle"

    def __init__(self, manifest: Manifest):
        self.smiles = manifest.smiles_by_image

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        s = self.smiles.get(image_path.name)
        return Prediction(s, 0.99, self.name) if s else None
