#!/usr/bin/env bash
# 페이지 분할기(decimer-segmentation) 환경 구성.
#
# 왜 또 별도 환경인가:
#   decimer-segmentation 은 tensorflow<=2.15.1 에 고정돼 있는데 TF 2.15 는
#   2023년판이라 py3.13 휠이 없다. 반대로 .venv 의 DECIMER 인식기는
#   py3.13/TF 2.20 을 쓴다. 핀이 배타적이라 합칠 방법이 없다.
#
# 왜 .venv310 에 얹지 않는가:
#   거기는 MolScribe(torch 1.x)가 겨우 도는 환경이다. TF 2.15 를 밀어넣으면
#   numpy 핀이 부딪쳐 인식기가 깨질 수 있다. 어렵게 세운 것을 위험에 두지 않는다.
#
# 이 환경이 없어도 파이프라인은 돈다. 래스터 페이지에서 구조를 못 찾을 뿐이다.
set -u
cd "$(dirname "$0")/.."

VENV=.venv-seg
PY="$VENV/Scripts/python.exe"

echo "[1/2] Python 3.10 가상환경"
if [ ! -x "$PY" ]; then
  py -3.10 -m venv "$VENV" || exit 1
fi
"$PY" -m pip install -q --upgrade pip

echo "[2/2] decimer-segmentation (TF 2.15 포함, 약 400MB)"
"$PY" -m pip install --no-cache-dir decimer-segmentation || exit 1

echo "확인"
"$PY" -c "import decimer_segmentation, tensorflow as tf; print('  TF', tf.__version__)" || exit 1

echo "완료. 가중치는 첫 실행 때 내려받는다."
