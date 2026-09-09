"""DECIMER 를 Modal 위에 서버리스 HTTPS 엔드포인트로 올린다.

    modal deploy server/modal_app.py       # 저장소 루트에서

계약은 docs/WEB_CONTRACT.md 가 동결했다. 여기서는 배송만 한다.

    POST /api/check   multipart/form-data: image, name  -> 계약이 정한 JSON
    GET  /api/health                                    -> 워밍업·생존 확인

배포에서 신경 쓴 것 넷:
  1. **가중치를 이미지에 굽는다.** DECIMER 는 import 시점에 ~600MB 를 내려받는다.
     콜드스타트마다 받으면 느리고, 배포처가 죽으면 우리 서비스도 같이 죽는다.
     빌드 단계(_bake_weights)에서 미리 받아 레이어에 굳힌다.
  2. **손그림 모델을 건너뛴다.** chemcheck.ocsr.DECIMER_SKIP_HANDDRAWN=True 가
     332MB 짜리 HandDrawn 모델 적재를 가로챈다. 콜드스타트가 절반이 된다.
  3. **컨테이너를 따뜻하게.** 기본은 min_containers=0 + scaledown_window 15분이라
     유휴 과금이 없다. 심사 시간대에만 CHEMCHECK_MIN_CONTAINERS=1 로 배포하면
     한 대가 상주한다 (CPU 2코어·4GB 기준 대략 시간당 $0.13).
  4. CPU 로 돈다. GPU 는 쓰지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

REPO = Path(__file__).resolve().parents[1]

# DECIMER 가 가중치를 두는 곳. 기본값(~/.data)은 HOME 에 따라 흔들리므로 못 박는다.
# 빌드 때 여기에 받고, 실행 때 같은 곳을 본다.
PYSTOW_HOME = "/opt/decimer-data"

# 심사 시간대에만 켜는 상주 컨테이너. 평소 0 이면 유휴 과금이 없다.
MIN_CONTAINERS = int(os.environ.get("CHEMCHECK_MIN_CONTAINERS", "0"))
# 마지막 요청 뒤 이만큼은 살려 둔다. 연달아 들어오는 심사에서 콜드스타트를 피한다.
SCALEDOWN_WINDOW = int(os.environ.get("CHEMCHECK_SCALEDOWN", "900"))
# 메모리 스냅샷. TensorFlow 가 스냅샷을 타는지는 배포처마다 다르므로 기본은 끈다.
# 켜면 콜드스타트가 크게 준다: CHEMCHECK_SNAPSHOT=1
SNAPSHOT = os.environ.get("CHEMCHECK_SNAPSHOT", "0") == "1"


def _bake_weights() -> None:
    """빌드 단계에서 DECIMER 가중치를 내려받아 이미지 레이어에 굳힌다.

    한 장을 실제로 추론까지 해 본다. 내려받기뿐 아니라 TensorFlow 의 첫 추적
    (가장 느린 한 번)까지 여기서 치르기 위해서다. 실패해도 빌드를 세우지 않는다 -
    가중치가 없으면 런타임이 내려받고, 그때는 느릴 뿐 죽지는 않는다.
    """
    import sys, traceback
    sys.path.insert(0, "/root")
    try:
        from PIL import Image, ImageDraw
        from chemcheck.ocsr import DecimerEngine

        img = Image.new("RGB", (320, 220), "white")
        d = ImageDraw.Draw(img)
        d.line((60, 110, 130, 70), fill="black", width=3)
        d.line((130, 70, 200, 110), fill="black", width=3)
        d.line((200, 110, 260, 80), fill="black", width=3)
        probe = Path("/tmp/_bake.png")
        img.save(probe)

        engine = DecimerEngine()
        if not engine.available():
            print("BAKE: DECIMER 적재 실패:", engine.unavailable_reason)
            return
        pred = engine.recognize(probe)
        print("BAKE: 예열 완료, 예측 =", None if pred is None else pred.smiles[:60])
    except Exception:
        traceback.print_exc()
        print("BAKE: 예열 실패 - 런타임이 대신 내려받는다")


image = (
    modal.Image.debian_slim(python_version="3.11")
    # opencv/pillow 가 기대하는 시스템 라이브러리. 없으면 import 가 OSError 로 죽는다.
    .apt_install("libgl1", "libglib2.0-0", "libsm6", "libxext6")
    .env({"PYSTOW_HOME": PYSTOW_HOME, "TF_CPP_MIN_LOG_LEVEL": "3", "KERAS_BACKEND": "tensorflow"})
    # 핀의 근거 (PyPI 메타데이터 확인, 2026-09-10):
    #   decimer 2.7.1 은 tensorflow<=2.15.0,>=2.12.0 을 요구한다. 2.15.0 은 Keras 2 계열로
    #   DECIMER V2 가중치가 검증된 자리다. tensorflow-cpu 로 바꿔 달지 않는다 - decimer 가
    #   요구하는 배포판 이름은 'tensorflow' 라서, cpu 판을 깔아도 pip 이 tensorflow 를 또 깐다.
    #   rdkit 2026.3.6 은 server/test_judge.py 를 통과시킨 그 버전이다.
    .pip_install(
        "decimer==2.7.1",
        "tensorflow==2.15.0",
        "rdkit==2026.3.6",
        "pillow",
        "requests",
        "fastapi[standard]",
        "python-multipart",
    )
    # chemcheck/** 는 읽기 전용이다. 복사만 하고 고치지 않는다.
    # copy=True 라야 _bake_weights 가 빌드 중에 import 할 수 있다.
    .add_local_dir(REPO / "chemcheck", "/root/chemcheck", copy=True,
                   ignore=["__pycache__", "*.pyc"])
    .add_local_dir(REPO / "server", "/root/server", copy=True,
                   ignore=["__pycache__", "*.pyc"])
    .run_function(_bake_weights)
)

app = modal.App("chemcheck-web")


@app.cls(
    image=image,
    cpu=2.0,
    memory=4096,
    timeout=600,
    min_containers=MIN_CONTAINERS,
    scaledown_window=SCALEDOWN_WINDOW,
    enable_memory_snapshot=SNAPSHOT,
)
@modal.concurrent(max_inputs=4)
class Api:
    """한 컨테이너 = DECIMER 한 벌 + 이름 해석기 한 벌. 요청마다 다시 올리지 않는다."""

    @modal.enter()
    def load(self):
        import sys
        sys.path.insert(0, "/root")
        from chemcheck.names import PubChemResolver
        from chemcheck.ocsr import DecimerEngine

        # 이름 해석: 캐시 -> 동봉한 표 -> PubChem. 망이 죽어도 흔한 화합물은 계속 나온다.
        self.resolver = PubChemResolver(timeout=8.0, max_retries=1)
        self.engine = DecimerEngine()
        self.ready = self.engine.available()
        self.engine_error = self.engine.unavailable_reason
        print("DECIMER 준비:", self.ready, self.engine_error or "")

    def _read(self, image_bytes: bytes, filename: str):
        """그림 -> 엔진 예측들. 예외를 밖으로 올리지 않는다 - 못 읽은 것도 답이다."""
        import tempfile

        sys_path_suffix = Path(filename or "x.png").suffix or ".png"
        from server.judge import EngineRead

        if not self.ready:
            return [], self.engine_error or "인식기를 쓸 수 없습니다"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"in{sys_path_suffix}"
            path.write_bytes(image_bytes)
            try:
                pred = self.engine.recognize(path)
            except Exception as exc:
                return [], f"{type(exc).__name__}: {exc}"
        if pred is None:
            return [], self.engine.last_error or "예측 없음"
        return [EngineRead("decimer", pred.smiles, pred.confidence)], None

    # label 을 못 박으면 URL 이 https://<workspace>--chemcheck.modal.run 로 고정된다.
    # U 트랙이 프런트에 박을 주소이므로, 클래스·메서드 이름을 바꿔도 흔들리면 안 된다.
    @modal.asgi_app(label="chemcheck")
    def web(self):
        import sys, time
        sys.path.insert(0, "/root")
        from fastapi import FastAPI, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse

        api = FastAPI(title="chemcheck")
        # 정적 페이지가 다른 오리진(GitHub Pages)에서 부른다. 프리플라이트까지 이 미들웨어가 받는다.
        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            max_age=86400,
        )

        @api.get("/api/health")
        def health():
            return {"ok": True, "engine": "decimer", "build": "form-direct-2", "ready": bool(self.ready),
                    "error": self.engine_error}

        @api.post("/api/check")
        async def check(request: Request):
            # 타입 힌트로 UploadFile/Form 을 받지 않는다. 이 라우트가 메서드 안에서
            # 정의되기 때문에 pydantic 이 어노테이션을 모듈 전역에서 찾다가 실패한다
            # (PydanticUserError: UploadFile is not fully defined). 폼을 직접 읽으면
            # 어노테이션 해석이 아예 필요 없다.
            from server.judge import build_result

            started = time.monotonic()
            form = await request.form()
            upload = form.get("image")
            name = form.get("name")
            if upload is None or name is None:
                return JSONResponse(
                    {"verdict": "unreadable", "grade": None, "reference": None,
                     "read": None, "reasons": ["image 와 name 이 모두 필요합니다"]},
                    status_code=400,
                )
            name = str(name)
            data = await upload.read() if hasattr(upload, "read") else bytes(upload)
            ref = None
            try:
                ref = self.resolver.resolve(name)
            except Exception as exc:  # 이름 해석이 죽어도 그림 읽기는 계속한다
                print("이름 해석 실패:", exc)

            reads, err = self._read(data, getattr(upload, "filename", "") or "")
            result = build_result(name, ref, reads)
            if err and not reads:
                result["reasons"].append(f"인식기 사유: {err}")
            result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return JSONResponse(result)

        return api
