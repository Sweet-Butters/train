# Cloud Run 용. 로컬 Docker 없이 `gcloud run deploy --source .` 로 Cloud Build 가 굽는다.
FROM python:3.11-slim

ENV PYSTOW_HOME=/opt/decimer-data \
    TF_CPP_MIN_LOG_LEVEL=3 \
    KERAS_BACKEND=tensorflow \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# opencv/pillow 가 기대하는 시스템 라이브러리. 없으면 import 가 OSError 로 죽는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 핀의 근거는 server/modal_app.py 주석과 같다.
#   decimer 2.7.1 -> tensorflow<=2.15.0. tensorflow-cpu 로 바꾸지 않는다
#   (decimer 가 요구하는 배포판 이름이 'tensorflow' 라 pip 이 또 깐다).
RUN pip install --no-cache-dir \
        "decimer==2.7.1" \
        "tensorflow==2.15.0" \
        "rdkit==2026.3.6" \
        pillow requests \
        "fastapi" "uvicorn[standard]" "python-multipart"

# ── MolScribe 를 옆 환경에 세운다 ────────────────────────────────────────
# molscribe 1.1.1 은 torch<2.0 에 고정돼 있고 torch 1.13 은 py3.11 휠이 없다.
# DECIMER 는 TF 2.15/py3.11 에서 돈다. 한 인터프리터에 같이 못 올린다.
# 그래서 로컬(.venv310)과 똑같이 py3.10 환경을 따로 만들고 SubprocessEngine 이
# 프로세스 너머로 부른다 - chemcheck/ocsr.py 가 이미 그렇게 짜여 있다.
RUN apt-get update && apt-get install -y --no-install-recommends         python3.10 python3.10-venv python3.10-dev build-essential     && rm -rf /var/lib/apt/lists/*     && python3.10 -m venv /opt/venv310     && /opt/venv310/bin/pip install --no-cache-dir --upgrade pip     && /opt/venv310/bin/pip install --no-cache-dir         "torch==1.13.1" --index-url https://download.pytorch.org/whl/cpu     && /opt/venv310/bin/pip install --no-cache-dir         "molscribe==1.1.1" "numpy<1.24" "opencv-python-headless"     && find /opt/venv310 -name "__pycache__" -type d -prune -exec rm -rf {} + || true

# MolScribe 체크포인트(1.13GB)를 이미지에 굽는다. 없으면 SubprocessEngine 이
# '체크포인트 없음' 으로 조용히 빠지고 DECIMER 하나로 돈다.
RUN mkdir -p /opt/molscribe &&     (curl -fL --retry 5 --retry-delay 5 -o /opt/molscribe/swin_base_char_aux_1m.pth       https://huggingface.co/yujieq/MolScribe/resolve/main/swin_base_char_aux_1m.pth      || echo "MolScribe 체크포인트 내려받기 실패 - DECIMER 하나로 돈다")

ENV CHEMCHECK_VENV310_PYTHON=/opt/venv310/bin/python     CHEMCHECK_MOLSCRIBE_CHECKPOINT=/opt/molscribe/swin_base_char_aux_1m.pth

COPY chemcheck /app/chemcheck
COPY server /app/server

# 가중치(299MB)를 이미지에 굽는다. 실패해도 빌드를 세우지 않는다 - 런타임이 대신 받는다.
RUN python -m server.bake || true

ENV PORT=8080
CMD exec uvicorn server.app:api --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75
