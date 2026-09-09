"""구조 그림 -> SMILES 인식기(OCSR) 플러그인 층.

인식기는 무겁고(torch, 가중치) 설치 환경을 탄다. 그래서 여기서 격리한다.
설치돼 있지 않으면 '없다'고 정직하게 보고하고, 파이프라인은 판정을 보류한다.
절대 없는 인식기를 있는 척하지 않는다.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from .bridge import Worker


@dataclass(frozen=True)
class Prediction:
    smiles: str
    confidence: float
    engine: str


class Engine:
    """OCSR 백엔드 공통 인터페이스."""

    name = "base"
    # 마지막 recognize 가 결과를 못 낸 이유. 조용히 None 만 돌려주면 사용자는
    # 인식기가 못 읽은 것인지 죽은 것인지 알 수 없다.
    last_error: str | None = None
    # available() 이 False 인 이유. 없으면 None.
    unavailable_reason: str | None = None

    def available(self) -> bool:
        """이 인식기를 쓸 수 있는가. **절대 예외를 올리지 않는다.**

        인식기 하나가 터져서 도구 전체가 죽으면 안 된다. import 가 어떤 예외로
        죽든 (ImportError 뿐 아니라 DLL 을 못 찾은 OSError, 깨진 가중치의
        BadZipFile, 끊긴 회선) '없다' 로 접고 unavailable_reason 에 이유를 남긴다.
        tests/test_engines.py::test_available_never_raises 가 이 계약을 지킨다.
        """
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
        self.unavailable_reason: str | None = None

    def available(self) -> bool:
        if self._model is not None:
            return True
        if self.checkpoint is None or not self.checkpoint.exists():
            self.unavailable_reason = f"체크포인트 없음: {self.checkpoint}"
            return False
        try:
            import molscribe  # noqa: F401
        except Exception as exc:
            # ImportError 만이 아니다. 윈도우의 torch 는 DLL 을 못 찾으면 OSError
            # 로 죽는다. 어느 쪽이든 인식기가 없는 것이지 도구가 죽을 일은 아니다.
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            return False
        self.unavailable_reason = None
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self.available():
            return None
        if self._model is None:
            import torch
            from molscribe import MolScribe

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = MolScribe(str(self.checkpoint), device=device)
        # predict_image_file 은 cv2.imread 라 윈도우에서 한글 경로를 못 연다.
        # PIL 로 읽어 배열로 넘긴다 (scripts/ocsr_worker.py 와 같은 이유).
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as img:
            rgb = np.asarray(img.convert("RGB"))
        out = self._model.predict_image(
            rgb, return_atoms_bonds=False, return_confidence=True
        )
        smiles = out.get("smiles")
        if not smiles:
            return None
        return Prediction(smiles, float(out.get("confidence", 0.0)), self.name)


# DECIMER 는 import 시점에 인쇄용 모델과 손그림 모델(각 332MB) 을 둘 다 올린다.
# 우리는 인쇄된 구조식만 읽으므로 손그림 쪽은 쓰지 않는다. 그 적재를 건너뛰면
# 콜드 스타트가 절반 가까이 준다. 끄고 싶으면 False.
DECIMER_SKIP_HANDDRAWN = True


@contextlib.contextmanager
def _skip_handdrawn_model():
    """DECIMER 를 import 하는 동안 손그림 모델 적재만 건너뛴다.

    tf.saved_model.load 를 잠깐 가로채 경로에 HandDrawn 이 든 것만 None 으로
    돌려준다. predict_SMILES(hand_drawn=False) 는 그 값을 만지지 않는다.
    DECIMER 가 적재 방식을 바꾸면 가로채기가 빗나가고 전처럼 둘 다 올라온다 -
    느려질 뿐 깨지지는 않는다.
    """
    if not DECIMER_SKIP_HANDDRAWN:
        yield
        return
    try:
        import tensorflow as tf
        real_load = tf.saved_model.load
    except Exception:
        yield  # TF 가 없으면 DECIMER import 가 알아서 실패하고 available 이 접는다
        return

    def load(path, *args, **kwargs):
        if "HandDrawn" in str(path):
            return None
        return real_load(path, *args, **kwargs)

    tf.saved_model.load = load
    try:
        yield
    finally:
        tf.saved_model.load = real_load


class DecimerEngine(Engine):
    name = "decimer"

    def __init__(self):
        self._predict = None
        self.unavailable_reason: str | None = None

    def available(self) -> bool:
        if self._predict is not None:
            return True
        if self.unavailable_reason is not None:
            return False
        try:
            with _skip_handdrawn_model():
                from DECIMER import predict_SMILES
        except Exception as exc:
            # DECIMER 는 import 시점에 가중치를 내려받는다. 배포처가 죽어 있거나
            # 받다 만 zip 이 남아 있으면 ImportError 가 아닌 예외로 죽는다.
            # 그대로 터뜨리면 도구 전체가 멈춘다. 인식기가 없는 것으로 본다.
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            return False
        self._predict = predict_SMILES
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self.available():
            return None
        smiles = self._predict(str(image_path))
        if not smiles:
            return None
        # DECIMER 기본 경로는 신뢰도를 주지 않는다. 없는 값을 지어내지 않고 표시만 한다.
        return Prediction(smiles, float("nan"), self.name)


class SubprocessEngine(Engine):
    """다른 파이썬 환경에 있는 인식기를 프로세스 너머로 쓴다.

    MolScribe 는 torch<2.0 에 고정돼 있어 py3.10 환경이 필요하고, DECIMER 는
    py3.13/TF 에서 돈다. 한 프로세스에 같이 올릴 수 없다. 전송은 bridge.Worker
    가 맡고, 여기서는 인식기다운 겉모습만 씌운다.

    워커가 죽거나 대답이 없으면 인식 결과를 내지 않는다. 그러면 판정은
    보류된다 - 틀린 답을 내는 것보다 낫다.
    """

    WORKER = Path(__file__).resolve().parents[1] / "scripts" / "ocsr_worker.py"

    # 타임아웃의 근거 (2026-09-09, 16GB 기계, CPU, 게이트 아래 실측):
    #   MolScribe 워커 적재(콜드)   17~33s   피크 1.7GB
    #   MolScribe 한 장            14~22s   (첫 장이 가장 길다)
    #   DECIMER  같은 프로세스 적재  140s    (손그림 모델 건너뛰고. 둘 다 올리면 254s)
    #   DECIMER  한 장             20~44s   (첫 장은 TF 추적 때문에 길다)
    # 적재 600s, 호출 180s 는 그 몇 배다. 메모리가 눌리면 시간이 두 배까지 늘어나므로
    # 더 줄이지 않는다.
    def __init__(
        self,
        python: Path,
        engine: str,
        checkpoint: Path | None = None,
        load_timeout: float = 600.0,
        call_timeout: float = 180.0,
    ):
        self.python = Path(python)
        self.engine = engine
        self.checkpoint = checkpoint
        self.name = f"{engine}@{self.python.parents[1].name}"
        args = ["--checkpoint", str(checkpoint)] if checkpoint is not None else []
        self._worker = Worker(
            self.python,
            self.WORKER,
            [engine, *args],
            load_timeout=load_timeout,
            call_timeout=call_timeout,
        )

    @property
    def unavailable_reason(self) -> str | None:
        return self._worker.unavailable_reason

    def available(self) -> bool:
        # 무거운 적재를 여기서 하지 않는다. 있을 법한지만 본다.
        # 실제로 못 띄우면 recognize 가 아무 것도 내지 않고, 판정은 보류된다.
        if self.checkpoint is not None and not self.checkpoint.exists():
            self._worker.unavailable_reason = f"체크포인트 없음: {self.checkpoint}"
            return False
        # 워커 경로를 뒤늦게 바꿔 끼우는 경우(테스트)를 위해 여기서 맞춘다.
        self._worker.script = self.WORKER
        return self._worker.available()

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self.available():
            return None
        self.last_error = None
        reply = self._worker.call(str(image_path))
        if reply is None:
            self.last_error = self._worker.unavailable_reason or "응답 없음"
            return None  # 죽었거나 멎었다. 판정하지 않는다.
        if "smiles" not in reply:
            self.last_error = str(reply.get("error", reply))
            return None  # 이 장만 실패했다. 사유는 남기고 판정하지 않는다.

        confidence = reply.get("confidence")
        # 신뢰도를 주지 않는 인식기는 NaN 으로 남긴다. 없는 값을 지어내지 않는다.
        return Prediction(
            reply["smiles"],
            float("nan") if confidence is None else float(confidence),
            self.name,
        )


# 인식기를 못 쓴 이유. 조용히 사라지면 사용자가 원인을 알 수 없다.
LAST_DIAGNOSTICS: list[str] = []


# MolScribe 전용 환경. torch<2.0 고정 때문에 py3.10 으로 따로 만든다.
# scripts/setup_ocsr.sh 가 여기에 깐다.
VENV310_PYTHON = Path(__file__).resolve().parents[1] / ".venv310" / "Scripts" / "python.exe"


def load_engines(molscribe_checkpoint: Path | None = None) -> list[Engine]:
    """설치돼 있고 실제로 쓸 수 있는 인식기만 돌려준다.

    두 인식기를 함께 세우는 것이 목적이다. 합의가 깨지면 판정을 보류하므로,
    인식기가 둘일 때 비로소 합의 게이트가 제 일을 한다.
    """
    LAST_DIAGNOSTICS.clear()
    engines: list[Engine] = []

    # MolScribe: 같은 프로세스에 올릴 수 있으면 그렇게 하고, 아니면 옆 환경에 맡긴다.
    local = MolScribeEngine(molscribe_checkpoint)
    if local.available():
        engines.append(local)
    else:
        bridged = SubprocessEngine(VENV310_PYTHON, "molscribe", molscribe_checkpoint)
        if bridged.available():
            engines.append(bridged)
        elif bridged.unavailable_reason:
            LAST_DIAGNOSTICS.append(f"molscribe: {bridged.unavailable_reason}")

    decimer = DecimerEngine()
    if decimer.available():
        # DECIMER는 신뢰도를 주지 않는다. 그래서 혼자일 때는 크기를 바꿔가며
        # 스스로 합의하는지 보는 것 외에 오인식을 걸러낼 방법이 없다.
        #
        # 다른 인식기가 있으면 이야기가 다르다. 합의 게이트가 이미 불일치를
        # 보류로 잡고, MolScribe 는 신뢰도 점수까지 준다. 그때도 자체 일관성
        # 검사를 돌리면 추론을 3배로 늘리면서 얻는 것은 적다.
        #
        # 공짜는 아니다. 두 인식기가 같은 방향으로 함께 틀리면 합의로는 못 잡고
        # 자체 일관성 검사라면 흔들림으로 잡았을 수도 있다. 다만 안정적으로
        # 틀리는 오인식은 크기를 바꿔도 흔들리지 않으므로 그 경우는 어차피 놓친다.
        engines.append(decimer if engines else SelfConsistent(decimer))
    elif decimer.unavailable_reason:
        LAST_DIAGNOSTICS.append(f"decimer: {decimer.unavailable_reason}")

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

    @property
    def unavailable_reason(self) -> str | None:
        return self.base.unavailable_reason

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
