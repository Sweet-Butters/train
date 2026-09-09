#!/usr/bin/env bash
# MolScribe(OCSR) 환경 구성.
#
# 왜 Python 3.10인가:
#   MolScribe 1.1.1 은 torch<2.0 과 2021년대 라이브러리에 고정돼 있다.
#   torch 1.x 는 Python 3.11 이후 휠이 없으므로 3.10 으로 별도 환경을 만든다.
# DECIMER 를 대신하는 게 아니라 옆에 세우는 것이다:
#   판정은 여러 인식기의 합의로 한다. 한 쪽이 오인식해도 합의가 깨지면 보류된다.
#   특히 DECIMER 는 신뢰도 점수를 주지 않는데 MolScribe 는 준다. 서로를 메운다.
#   DECIMER 쪽 환경은 .venv 와 scripts/fetch_decimer.sh 가 맡는다.
#
# 두 환경을 나누는 이유는 순전히 의존성 충돌 때문이다. 한 프로세스에서
# py3.10/torch1.x 와 py3.13/TF 를 같이 올릴 수 없다.
#
# 회선이 불안정해도 이어받는다. 다시 실행해도 안전하다.
set -u
cd "$(dirname "$0")/.."

VENV=.venv310
PY="$VENV/Scripts/python.exe"

echo "[1/3] Python 3.10 가상환경"
if [ ! -x "$PY" ]; then
  py -3.10 -m venv "$VENV" || exit 1
fi
"$PY" -m pip install -q --upgrade pip

echo "[2/3] molscribe + 파이프라인 의존성"
"$PY" -m pip install --no-cache-dir molscribe || exit 1
"$PY" -m pip install --no-cache-dir rdkit pymupdf python-pptx requests pillow || exit 1

echo "[3/3] 체크포인트 (약 1.13GB, HuggingFace)"
PYTHONIOENCODING=utf-8 "$PY" scripts/fetch_models.py molscribe

echo "완료"
