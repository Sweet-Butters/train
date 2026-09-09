#!/usr/bin/env bash
# recognize 평가를 실제 인식기로 - 하루 끝에 한 번, 게이트로.
#
#   bash D:/orca/workspaces/train/track-d-bench/bench/gate_recognize.sh [--detail 등 bench.run 옵션]
#
# 50장 × 두 엔진이라 오래 걸린다 (MolScribe 장당 수십 초). 그 전엔 스텁으로 장치만
# 검증한다:  python -m bench.run --task recognize --engine oracle|shaky|colluding|none
#
# 환경(.venv, .venv310, models/)은 chemcheck 워크트리에만 있다. bench 코드는 이 파일이
# 있는 워크트리 것을 쓰고, chemcheck 패키지와 모델은 CHEMCHECK_ROOT 에서 가져온다.
set -u
TRAIN="D:/orca/workspaces/train"
ENV_ROOT="$TRAIN/chemcheck"
BENCH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ENV_ROOT/.venv/Scripts/python.exe"

[ -x "$PY" ] || { echo "파이썬이 없다: $PY"; exit 2; }
cd "$BENCH_ROOT" || exit 2

mkdir -p bench/decks/recognize_set
LOG="bench/decks/recognize_set/report_$(date +%Y%m%d_%H%M%S).txt"
echo "[recognize] bench=$BENCH_ROOT env=$ENV_ROOT -> $LOG"

CHEMCHECK_ROOT="$ENV_ROOT" PYTHONUTF8=1 \
  bash "$TRAIN/heavy_gate.sh" "D-recognize-bench" -- \
  "$PY" -m bench.run --task recognize --engine real "$@" 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
