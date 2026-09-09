"""구조 그림 -> SMILES 인식기(OCSR) 플러그인 층.

인식기는 무겁고(torch, 가중치) 설치 환경을 탄다. 그래서 여기서 격리한다.
설치돼 있지 않으면 '없다'고 정직하게 보고하고, 파이프라인은 판정을 보류한다.
절대 없는 인식기를 있는 척하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prediction:
    smiles: str
    confidence: float
    engine: str


class Engine:
    """OCSR 백엔드 공통 인터페이스."""

    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def recognize(self, image_path: Path) -> Prediction | None:
        raise NotImplementedError


class MolScribeEngine(Engine):
    name = "molscribe"

    def __init__(self, checkpoint: Path | None = None):
        self.checkpoint = checkpoint
        self._model = None

    def available(self) -> bool:
        try:
            import molscribe  # noqa: F401
        except ImportError:
            return False
        return self.checkpoint is not None and self.checkpoint.exists()

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self.available():
            return None
        if self._model is None:
            import torch
            from molscribe import MolScribe

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = MolScribe(str(self.checkpoint), device=device)
        out = self._model.predict_image_file(
            str(image_path), return_atoms_bonds=False, return_confidence=True
        )
        smiles = out.get("smiles")
        if not smiles:
            return None
        return Prediction(smiles, float(out.get("confidence", 0.0)), self.name)


class DecimerEngine(Engine):
    name = "decimer"

    def __init__(self):
        self._predict = None

    def available(self) -> bool:
        try:
            from DECIMER import predict_SMILES  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self.available():
            return None
        if self._predict is None:
            from DECIMER import predict_SMILES

            self._predict = predict_SMILES
        smiles = self._predict(str(image_path))
        if not smiles:
            return None
        # DECIMER 기본 경로는 신뢰도를 주지 않는다. 없는 값을 지어내지 않고 표시만 한다.
        return Prediction(smiles, float("nan"), self.name)


def load_engines(molscribe_checkpoint: Path | None = None) -> list[Engine]:
    """설치돼 있고 실제로 쓸 수 있는 인식기만 돌려준다."""
    engines = [MolScribeEngine(molscribe_checkpoint), DecimerEngine()]
    return [e for e in engines if e.available()]
