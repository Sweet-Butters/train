#!/usr/bin/env bash
# DECIMER 가중치를 pystow 기본 위치에 미리 내려받는다.
#
# DECIMER 는 import 시점에 pystow.ensure 로 zip 을 받는데, 실패하면 그냥 죽는다.
# pystow.ensure 는 파일이 이미 있으면 받지 않으므로, 여기서 먼저 받아두면
# 그 다음 import 는 압축만 풀고 넘어간다.
#
# Zenodo 는 자주 504 를 낸다. 회선이 아니라 서버 쪽이므로 기다렸다 다시 친다.
# 다시 실행해도 안전하다 - 받다 만 파일은 이어받는다.
set -u

DEST="${DECIMER_HOME:-$HOME/.data/DECIMER-V2}"
DEADLINE_MIN="${DEADLINE_MIN:-60}"

MODELS_URL="https://zenodo.org/record/8300489/files/models.zip"
HAND_URL="https://zenodo.org/records/10781330/files/DECIMER_HandDrawn_model.zip"

# 30초 동안 10KB/s 밑이면 끊고 다시 잡는다. 죽은 연결에 매달리지 않기 위한 것.
STALL="--speed-time 30 --speed-limit 10000 --connect-timeout 30"

mkdir -p "$DEST"
end=$(( $(date +%s) + DEADLINE_MIN * 60 ))

get() {  # get <url> <파일명>
  local url="$1" out="$DEST/$2"
  # saved_model 이 이미 풀려 있으면 할 일이 없다.
  case "$2" in
    models.zip)   [ -f "$DEST/DECIMER_model/saved_model.pb" ] && return 0 ;;
    DECIMER_HandDrawn_model.zip)
                  [ -f "$DEST/DECIMER_HandDrawn_model/saved_model.pb" ] && return 0 ;;
  esac
  while [ "$(date +%s)" -lt "$end" ]; do
    echo "[$(date +%H:%M:%S)] $2 받는 중"
    if curl -fL $STALL -C - -o "$out" "$url"; then
      # 받다 만 zip 을 남기면 안 된다. pystow 는 파일이 있으면 그냥 넘어가고,
      # DECIMER 가 그걸 풀다가 BadZipFile 로 죽는다. 성치 않으면 지우고 다시 받는다.
      if unzip -tqq "$out" >/dev/null 2>&1; then
        echo "[$(date +%H:%M:%S)] $2 완료 ($(stat -c %s "$out") bytes)"
        return 0
      fi
      echo "[$(date +%H:%M:%S)] $2 받았으나 zip 이 깨졌다. 지우고 다시 받는다"
      rm -f "$out"
    fi
    echo "[$(date +%H:%M:%S)] $2 실패 - Zenodo 응답 없음. 60초 뒤 재시도"
    sleep 60
  done
  echo "[$(date +%H:%M:%S)] $2 시간 초과 (${DEADLINE_MIN}분)"
  return 1
}

get "$MODELS_URL" models.zip || exit 1
get "$HAND_URL"   DECIMER_HandDrawn_model.zip || exit 1
echo "받기 완료. 이제 DECIMER 를 import 하면 압축을 풀고 바로 쓴다."
