#!/usr/bin/env bash
# OCSR(MolScribe) 설치 전체 과정. 회선이 불안정해도 이어받고 재시도한다.
# 다시 실행해도 안전하다 — 이미 받은 부분은 건너뛴다.
set -u
cd "$(dirname "$0")/.."

PY=./.venv/Scripts/python.exe
WHEEL=.wheels/torch-2.14.0+cpu-cp313-cp313-win_amd64.whl
TORCH_URL="https://download.pytorch.org/whl/cpu/torch-2.14.0%2Bcpu-cp313-cp313-win_amd64.whl"

# 30초 동안 10KB/s 밑이면 끊고 재시도한다. 이게 없으면 curl이 죽은 연결에 매달린다.
STALL="--speed-time 30 --speed-limit 10000 --retry 50 --retry-all-errors --retry-delay 5 --connect-timeout 30"

echo "[1/4] torch 휠 내려받기"
mkdir -p .wheels
curl -L $STALL -C - -o "$WHEEL" "$TORCH_URL" || true
ls -la "$WHEEL"

echo "[2/4] torch 설치"
"$PY" -m pip install --no-cache-dir "$WHEEL" || exit 1

echo "[3/4] molscribe + 의존성 설치"
"$PY" -m pip install --no-cache-dir molscribe rdkit pymupdf python-pptx requests || exit 1

echo "[4/4] 체크포인트 내려받기 (약 1.13GB)"
PYTHONIOENCODING=utf-8 "$PY" scripts/fetch_models.py molscribe

echo "완료"
