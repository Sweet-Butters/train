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

    def recognize_all(self, image_path: Path) -> list[Prediction]:
        """이 인식기가 내놓는 모든 예측. 기본은 한 개."""
        pred = self.recognize(image_path)
        return [pred] if pred is not None else []


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
    engines: list[Engine] = [MolScribeEngine(molscribe_checkpoint)]
    decimer = DecimerEngine()
    # DECIMER는 신뢰도를 주지 않으므로 자체 일관성 검사로 감싼다.
    engines.append(SelfConsistent(decimer) if decimer.available() else decimer)
    return [e for e in engines if e.available()]


class SelfConsistent(Engine):
    """한 인식기를 살짝 변형한 이미지들에 반복 적용해 스스로 합의하는지 본다.

    DECIMER처럼 신뢰도를 주지 않는 인식기는 단독으로 쓰면 오인식이 그대로
    '오류' 오판이 된다. 크기를 조금 바꾼 이미지에서 답이 흔들리면 그 인식은
    믿을 수 없다는 뜻이므로, 서로 다른 예측을 그대로 내보내 합의 게이트에서
    보류로 걸리게 한다.
    """

    SCALES = (1.0, 0.9, 1.15)

    def __init__(self, base: Engine, scales: tuple[float, ...] = SCALES):
        self.base = base
        self.scales = scales
        self.name = f"{base.name}-selfconsistent"

    def available(self) -> bool:
        return self.base.available()

    def recognize(self, image_path: Path) -> Prediction | None:
        preds = self.recognize_all(image_path)
        return preds[0] if preds else None

    def recognize_all(self, image_path: Path) -> list[Prediction]:
        from tempfile import TemporaryDirectory

        from PIL import Image

        out: list[Prediction] = []
        with TemporaryDirectory() as tmp:
            with Image.open(image_path) as src:
                base_img = src.convert("RGB")
                for i, scale in enumerate(self.scales, start=1):
                    if scale == 1.0:
                        target = image_path
                    else:
                        size = (max(1, int(base_img.width * scale)),
                                max(1, int(base_img.height * scale)))
                        variant = base_img.resize(size, Image.LANCZOS)
                        target = Path(tmp) / f"v{i}{image_path.suffix or '.png'}"
                        variant.save(target)
                    pred = self.base.recognize(Path(target))
                    if pred is not None:
                        out.append(Prediction(pred.smiles, pred.confidence,
                                              f"{self.base.name}/x{scale:g}"))
        return out
