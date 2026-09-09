# Cloud Run 용. 로컬 Docker 없이 `gcloud run deploy --source .` 로 Cloud Build 가 굽는다.
FROM python:3.11-slim

ENV PYSTOW_HOME=/opt/decimer-data \
    TF_CPP_MIN_LOG_LEVEL=3 \
    KERAS_BACKEND=tensorflow \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# opencv/pillow 가 기대하는 시스템 라이브러리. 없으면 import 가 OSError 로 죽는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 \
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

COPY chemcheck /app/chemcheck
COPY server /app/server

# 가중치(299MB)를 이미지에 굽는다. 실패해도 빌드를 세우지 않는다 - 런타임이 대신 받는다.
RUN python -m server.bake || true

ENV PORT=8080
CMD exec uvicorn server.app:api --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75
