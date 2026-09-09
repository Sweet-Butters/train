"""다른 파이썬 환경에 있는 무거운 모델을 프로세스 너머로 부르는 다리.

이 프로젝트는 한 프로세스에 다 못 올리는 도구들을 쓴다.

    .venv       py3.13 / TF 2.20    DECIMER 인식기
    .venv310    py3.10 / torch 1.x  MolScribe 인식기 (torch<2.0 고정)
    .venv-seg   py3.10 / TF 2.15    DECIMER 분할기 (tensorflow<=2.15.1 고정)

핀이 서로 배타적이라 합칠 방법이 없다. 그래서 각 환경에 워커를 하나씩 상주
시키고 줄 단위 JSON 으로 부른다. 이미지마다 프로세스를 새로 띄우면 수백 MB
짜리 가중치를 매번 읽게 되므로 그렇게 하지 않는다.

워커가 죽거나 대답이 없으면 결과를 내지 않는다. 부르는 쪽이 그 침묵을 보고
판단을 보류하면 된다 - 틀린 답을 내는 것보다 낫다.

프로토콜 (워커 쪽 구현은 scripts/*_worker.py):
    워커 -> 적재 직후 한 번   {"ready": true}  또는 {"error": "..."}
    부름 -> 요청 한 줄        <문자열>
    워커 -> 응답 한 줄        {...}  또는 {"error": "..."}
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def venv_python(name: str) -> Path:
    """워크트리 안의 가상환경 파이썬 경로. 없어도 예외를 내지 않는다.

    윈도우는 Scripts/python.exe, 리눅스는 bin/python 이다. 컨테이너 배포가
    생기면서 둘 다 필요해졌다 - 있는 쪽을 돌려주고, 둘 다 없으면 이 OS 의
    관례대로 돌려준다(없다는 사실은 부르는 쪽이 available() 로 안다).
    """
    windows = ROOT / name / "Scripts" / "python.exe"
    posix = ROOT / name / "bin" / "python"
    if windows.exists():
        return windows
    if posix.exists():
        return posix
    return windows if os.name == "nt" else posix


class Worker:
    """옆 환경에 상주하는 워커 하나. 줄 단위로 주고받는다.

    스레드로 stdout 을 퍼올린다. 그냥 readline 하면 워커가 멎었을 때 부르는
    쪽이 같이 멎는다. 대답이 시간 안에 안 오면 죽은 것으로 보고 접는다.
    """

    def __init__(
        self,
        python: Path,
        script: Path,
        args: list[str] | None = None,
        load_timeout: float = 600.0,
        call_timeout: float = 180.0,
    ):
        self.python = Path(python)
        self.script = Path(script)
        self.args = list(args or [])
        self.load_timeout = load_timeout
        self.call_timeout = call_timeout
        self.unavailable_reason: str | None = None
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()

    # -- 상태 -------------------------------------------------------------

    def available(self) -> bool:
        """띄울 수 있을 법한지만 본다. 무거운 적재는 여기서 하지 않는다.

        실제로 못 띄우면 call 이 None 을 돌려주고, 부르는 쪽이 보류하면 된다.
        """
        if self.unavailable_reason is not None:
            return False
        if not self.python.exists():
            self.unavailable_reason = f"파이썬 환경 없음: {self.python}"
            return False
        if not self.script.exists():
            self.unavailable_reason = f"워커 없음: {self.script}"
            return False
        return True

    @property
    def running(self) -> bool:
        return self._proc is not None

    # -- 수명 -------------------------------------------------------------

    def _pump(self, stdout) -> None:
        try:
            for line in stdout:
                self._replies.put(line)
        finally:
            # 어떤 이유로 읽기가 끝나든 신호는 반드시 남긴다. 여기서 조용히
            # 죽으면 부르는 쪽은 죽은 워커를 타임아웃까지 기다린다.
            self._replies.put(None)

    def start(self) -> bool:
        if self._proc is not None:
            return True
        if not self.available():
            return False
        cmd = [str(self.python), "-u", str(self.script), *self.args]
        # 자식도 UTF-8 로 읽고 쓰게 만든다. 윈도우 자식의 기본 stdio 인코딩은
        # 로케일(여기서는 cp949)이라, 그냥 두면 부모가 UTF-8 로 보낸 요청을
        # 자식이 다른 글자로 읽는다. 경로에 한글이 하나라도 있으면 - 이 기계는
        # 임시 폴더 경로부터 한글이다 - 자식이 없는 폴더를 만들려다 죽는다.
        # 응답 쪽도 같다. 워커들은 ensure_ascii=False 로 사유를 쓰므로 자식이
        # cp949 로 인코딩한 것을 부모가 UTF-8 로 읽는 일이 생긴다.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self.unavailable_reason = f"워커를 띄우지 못함: {exc}"
            return False

        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()

        hello = self._reply(self.load_timeout)  # 모델 적재를 기다린다
        if hello is None or not hello.get("ready"):
            reason = (hello or {}).get("error", "응답 없음")
            self.unavailable_reason = f"워커 적재 실패: {reason}"
            self.stop()
            return False
        return True

    def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            self._proc.stdin.close()
        with contextlib.suppress(Exception):
            self._proc.terminate()
        self._proc = None

    # -- 주고받기 ---------------------------------------------------------

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

    def call(self, request: str) -> dict | None:
        """요청 한 줄을 보내고 응답 한 줄을 받는다.

        None 이면 결과가 없다는 뜻이다. 워커가 죽어서일 수도, 이번 건만
        실패해서일 수도 있다. 죽었으면 unavailable_reason 이 채워진다.
        """
        if not self.start():
            return None
        try:
            self._proc.stdin.write(f"{request}\n")
            self._proc.stdin.flush()
        except OSError as exc:
            self.unavailable_reason = f"워커가 죽었다: {exc}"
            self.stop()
            return None

        reply = self._reply(self.call_timeout)
        if reply is None:
            # 대답이 없다 = 죽었거나 멎었다. 되살릴 수 없으니 접는다.
            self.unavailable_reason = "워커가 응답하지 않음"
            self.stop()
        return reply
