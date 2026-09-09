"""Cloud Run 용 ASGI 앱. Modal 판(modal_app.py)과 같은 계약을 제공한다.

    GET  /api/health   준비 상태
    POST /api/check    multipart(image, name) -> docs/WEB_CONTRACT.md 의 JSON

엔진은 프로세스가 뜰 때 한 번만 올린다. Cloud Run 에서 --min-instances=1 로 두면
컨테이너가 살아 있으므로 요청마다 다시 올리지 않는다.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from chemcheck.names import PubChemResolver
from chemcheck.ocsr import load_engines
from server.judge import EngineRead, build_result

BUILD = os.environ.get("CHEMCHECK_BUILD", "cloudrun-3-two-engines")

_resolver = PubChemResolver(timeout=8.0, max_retries=1)

# 인식기 둘을 함께 세운다. 합의가 깨지면 판정을 보류하므로 둘일 때 게이트가 제 일을 한다.
# MolScribe 가 없으면 load_engines 가 DECIMER 하나만 돌려주고, 판정은 grade=weak 로 나간다.
_ckpt_env = os.environ.get("CHEMCHECK_MOLSCRIBE_CHECKPOINT")
_ckpt = Path(_ckpt_env) if _ckpt_env else None
_engines = load_engines(_ckpt)
_ready = bool(_engines)
_names = [e.name for e in _engines]
_engine_error = None if _ready else "쓸 수 있는 인식기가 없습니다"
print("인식기 준비:", _names or "없음", flush=True)


def _read(image_bytes: bytes, filename: str):
    """그림 -> 엔진마다 예측 하나. 예외를 밖으로 올리지 않는다 - 못 읽은 것도 답이다.

    한 엔진이 죽어도 나머지는 그대로 답한다. 둘 다 못 읽으면 사유를 모아 돌려준다.
    """
    if not _ready:
        return [], _engine_error or "인식기를 쓸 수 없습니다"
    suffix = Path(filename or "x.png").suffix or ".png"
    reads, problems = [], []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"in{suffix}"
        path.write_bytes(image_bytes)
        for engine in _engines:
            try:
                pred = engine.recognize(path)
            except Exception as exc:
                problems.append(f"{engine.name}: {type(exc).__name__}: {exc}")
                continue
            if pred is None:
                problems.append(f"{engine.name}: {getattr(engine, 'last_error', None) or '예측 없음'}")
                continue
            reads.append(EngineRead(engine.name, pred.smiles, pred.confidence))
    return reads, ("; ".join(problems) if problems else None)


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
    return {"ok": True, "engines": _names, "build": BUILD,
            "ready": bool(_ready), "error": _engine_error}


async def check(request):
    """폼을 직접 읽는다. Modal 판에서 어노테이션 해석이 깨졌던 경로라 같은 방식을 쓴다."""
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
        ref = _resolver.resolve(name)
    except Exception as exc:  # 이름 해석이 죽어도 그림 읽기는 계속한다
        print("이름 해석 실패:", exc, flush=True)

    reads, err = _read(data, getattr(upload, "filename", "") or "")
    result = build_result(name, ref, reads)
    if err and not reads:
        result["reasons"].append(f"인식기 사유: {err}")
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return JSONResponse(result)


api.add_route("/api/check", check, methods=["POST"])
