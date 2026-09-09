"""구조 그림 -> SMILES 인식기(OCSR) 플러그인 층.

인식기는 무겁고(torch, 가중치) 설치 환경을 탄다. 그래서 여기서 격리한다.
설치돼 있지 않으면 '없다'고 정직하게 보고하고, 파이프라인은 판정을 보류한다.
절대 없는 인식기를 있는 척하지 않는다.
"""
from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
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
        self.unavailable_reason: str | None = None

    def available(self) -> bool:
        if self._predict is not None:
            return True
        if self.unavailable_reason is not None:
            return False
        try:
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
    py3.13/TF 에서 돈다. 한 프로세스에 같이 올릴 수 없다. 그렇다고 이미지마다
    프로세스를 새로 띄우면 1.13GB 체크포인트를 매번 읽는다. 그래서 워커를
    한 번 띄워 두고 줄 단위로 주고받는다 (scripts/ocsr_worker.py).

    워커가 죽거나 대답이 없으면 인식 결과를 내지 않는다. 그러면 판정은
    보류된다 - 틀린 답을 내는 것보다 낫다.
    """

    WORKER = Path(__file__).resolve().parents[1] / "scripts" / "ocsr_worker.py"

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
        self.load_timeout = load_timeout
        self.call_timeout = call_timeout
        self.name = f"{engine}@{self.python.parents[1].name}"
        self.unavailable_reason: str | None = None
        self._proc = None
        self._replies: "queue.Queue[str | None]" = queue.Queue()

    def available(self) -> bool:
        # 무거운 적재를 여기서 하지 않는다. 있을 법한지만 본다.
        # 실제로 못 띄우면 recognize 가 아무 것도 내지 않고, 판정은 보류된다.
        if self.unavailable_reason is not None:
            return False
        if not self.python.exists():
            self.unavailable_reason = f"파이썬 환경 없음: {self.python}"
            return False
        if not self.WORKER.exists():
            self.unavailable_reason = f"워커 없음: {self.WORKER}"
            return False
        if self.checkpoint is not None and not self.checkpoint.exists():
            self.unavailable_reason = f"체크포인트 없음: {self.checkpoint}"
            return False
        return True

    def _pump(self, stdout) -> None:
        for line in stdout:
            self._replies.put(line)
        self._replies.put(None)  # 워커가 죽었다

    def _start(self) -> bool:
        if self._proc is not None:
            return True
        if not self.available():
            return False
        cmd = [str(self.python), "-u", str(self.WORKER), self.engine]
        if self.checkpoint is not None:
            cmd += ["--checkpoint", str(self.checkpoint)]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.WORKER.parents[1]),
                text=True,
                encoding="utf-8",
            )
        except OSError as exc:
            self.unavailable_reason = f"워커를 띄우지 못함: {exc}"
            return False

        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()

        hello = self._reply(self.load_timeout)  # 모델 적재를 기다린다
        if hello is None or not hello.get("ready"):
            reason = (hello or {}).get("error", "응답 없음")
            self.unavailable_reason = f"워커 적재 실패: {reason}"
            self._stop()
            return False
        return True

    def _reply(self, timeout: float) -> dict | None:
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            self._proc.stdin.close()
        with contextlib.suppress(Exception):
            self._proc.terminate()
        self._proc = None

    def recognize(self, image_path: Path) -> Prediction | None:
        if not self._start():
            return None
        try:
            self._proc.stdin.write(f"{image_path}\n")
            self._proc.stdin.flush()
        except OSError as exc:
            self.unavailable_reason = f"워커가 죽었다: {exc}"
            self._stop()
            return None

        reply = self._reply(self.call_timeout)
        if reply is None:
            # 대답이 없다 = 죽었거나 멎었다. 되살릴 수 없으니 접는다.
            self.unavailable_reason = "워커가 응답하지 않음"
            self._stop()
            return None
        if "smiles" not in reply:
            return None  # 이 장만 실패. 워커는 살아 있다.

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
