#!/usr/bin/env bash
# 인식기(OCSR) 없이 파이프라인만 세운다.
#
# 추출·이름 해석·판정·리포트는 구조 인식기가 없어도 전부 개발하고 검증할 수 있다.
# tests/ 의 스텁 엔진(oracle/flaky)이 완벽한 인식기와 흔들리는 인식기를 대신한다.
# 그래서 torch 도 TensorFlow 도 1GB 짜리 가중치도 여기서는 받지 않는다.
# 무거운 환경이 필요하면 scripts/setup_ocsr.sh 를 쓴다.
#
# 왜 이게 따로 있어야 하나:
#   이 스크립트가 없던 동안 pdfminer/pypdfium2 가 빠진 채로 테스트가 통과했다.
#   PDF 추출 경로가 지연 import 라서, 의존성이 없으면 조용히 건너뛰고 초록불이 난다.
#   환경을 한 곳에 적어두지 않으면 "통과"가 무엇을 통과한 것인지 알 수 없다.
#
# PyMuPDF(AGPL)는 쓰지 않는다. PDF 는 pdfminer.six + pypdfium2 로 읽는다.
# 다시 실행해도 안전하다.
set -u
cd "$(dirname "$0")/.."

VENV=${VENV:-.venv}
if [ -x "$VENV/Scripts/python.exe" ]; then
  PY="$VENV/Scripts/python.exe"      # Windows
elif [ -x "$VENV/bin/python" ]; then
  PY="$VENV/bin/python"              # macOS / Linux
else
  PY=""
fi

echo "[1/3] 가상환경 ($VENV)"
if [ -z "$PY" ]; then
  (python3 -m venv "$VENV" || python -m venv "$VENV" || py -3 -m venv "$VENV") || {
    echo "가상환경을 만들지 못했다. python3 가 PATH 에 있는지 확인할 것." >&2
    exit 1
  }
  PY="$VENV/Scripts/python.exe"
  [ -x "$PY" ] || PY="$VENV/bin/python"
fi
"$PY" -m pip install -q --upgrade pip

echo "[2/3] 파이프라인 의존성 (인식기 없음)"
"$PY" -m pip install -q \
  rdkit python-pptx requests pillow pdfminer.six pypdfium2 pytest || exit 1

echo "[3/3] 확인"
PYTHONIOENCODING=utf-8 "$PY" - <<'CHECK' || exit 1
import importlib, sys
need = {"rdkit": "구조·InChIKey", "pptx": "PPTX 추출", "pdfminer": "PDF 텍스트",
        "pypdfium2": "PDF 렌더", "PIL": "이미지", "requests": "PubChem 조회"}
missing = []
for mod, why in need.items():
    try:
        importlib.import_module(mod)
        print(f"  {mod:12} ok   ({why})")
    except ImportError:
        missing.append(mod)
        print(f"  {mod:12} 없음 ({why})")
if missing:
    sys.exit(1)
for mod in ("torch", "DECIMER", "molscribe"):
    try:
        importlib.import_module(mod)
        print(f"  {mod:12} (있음 - 이 환경에는 필요 없다)")
    except ImportError:
        print(f"  {mod:12} 없음 - 의도한 것. 그림 판정은 전부 '판정불가'로 나온다")
CHECK

echo
echo "완료. 이렇게 확인한다:"
echo "  $PY -m pytest tests/ -q     # 네 트랙 전부"
echo "  $PY -m bench.run            # 벤치마크"
echo
echo "테스트는 반드시 pytest 로 돌린다. tests/ 안에 실행 방식이 두 가지 섞여 있어서,"
echo "'python tests/test_x.py' 로 돌리면 __main__ 블록이 없는 파일은 조용히 건너뛴다."
