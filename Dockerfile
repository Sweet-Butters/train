# Cloud Run 용. 로컬 Docker 없이 `gcloud run deploy --source .` 로 Cloud Build 가 굽는다.
#
# 파이썬을 3.10 으로 고른 이유가 이 파일의 핵심이다:
#   molscribe 1.1.1 -> torch<2.0. torch 1.13 은 py3.11 휠이 없다 (py3.10 이 마지막)
#   decimer 2.7.1   -> tensorflow<=2.15.0. TF 2.15 는 py3.9~3.11 을 지원한다
#   numpy           -> TF 2.15 는 >=1.23.5,<2.0, torch 1.13 은 <1.24 를 선호 -> 1.23.5 가 교집합
# 그래서 3.10 에서는 **두 인식기가 한 프로세스에 같이 산다.** 로컬(윈도우)에서는
# py3.13/TF2.20 이라 그게 안 돼 .venv310 을 따로 두고 프로세스 너머로 불렀는데,
# 컨테이너는 우리가 버전을 고르므로 그 다리가 필요 없다.
FROM python:3.10-slim

ENV PYSTOW_HOME=/opt/decimer-data \
    TF_CPP_MIN_LOG_LEVEL=3 \
    KERAS_BACKEND=tensorflow \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CHEMCHECK_MOLSCRIBE_CHECKPOINT=/opt/molscribe/swin_base_char_aux_1m.pth

# opencv/pillow 가 기대하는 시스템 라이브러리. 없으면 import 가 OSError 로 죽는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# numpy 를 먼저 못박는다. 나중에 깔면 의존성 해결이 1.26 으로 올려버린다.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir "numpy==1.23.5" "typing_extensions==4.13.2"

# torch 는 CPU 판. --index-url 은 PyPI 를 **대체**하므로, 그 상태에서 pip 이
# typing_extensions 를 찾으면 torch 인덱스의 소스 배포를 집고 빌드 백엔드
# (flit_core)를 못 찾아 죽는다 - 2026-09-10 첫 빌드 실패의 원인이 이것이었다.
# 의존성은 위에서 PyPI 로 미리 깔고, 여기서는 --no-deps 로 torch 만 가져온다.
RUN pip install --no-cache-dir --no-deps "torch==1.13.1+cpu" \
        --index-url https://download.pytorch.org/whl/cpu
# (다른 갈래는 --extra-index-url 로 같은 문제를 풀었다. 더 짧지만, 지금 도는
#  Cloud Run 두 엔진 서비스를 실제로 구운 것은 위 조합이라 그대로 둔다.)

RUN pip install --no-cache-dir \
        "decimer==2.7.1" \
        "tensorflow==2.15.0" \
        "molscribe==1.1.1" \
        "rdkit==2026.3.6" \
        pillow requests \
        "fastapi" "uvicorn[standard]" "python-multipart"

# MolScribe 체크포인트(1.13GB). 못 받아도 빌드는 선다 - DECIMER 하나로 돈다.
RUN mkdir -p /opt/molscribe \
    && (curl -fL --retry 5 --retry-delay 5 \
          -o /opt/molscribe/swin_base_char_aux_1m.pth \
          https://huggingface.co/yujieq/MolScribe/resolve/main/swin_base_char_aux_1m.pth \
        || echo "BAKE: MolScribe 체크포인트 실패 - DECIMER 하나로 돈다")

COPY chemcheck /app/chemcheck
COPY server /app/server

# DECIMER 가중치(299MB)를 굽고 TF 첫 추적까지 치른다. 실패해도 빌드를 세우지 않는다.
RUN python -m server.bake || true

ENV PORT=8080
CMD exec uvicorn server.app:api --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75
