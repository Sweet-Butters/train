"""빌드 단계에서 DECIMER 가중치를 내려받아 이미지 레이어에 굳힌다.

Modal 의 _bake_weights 와 같은 일을 Docker 빌드에서 한다. 실패해도 빌드를 세우지
않는다 - 가중치가 없으면 런타임이 내려받고, 그때는 느릴 뿐 죽지는 않는다.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
import urllib.request
from pathlib import Path

PYSTOW_HOME = os.environ.get("PYSTOW_HOME", "/opt/decimer-data")
WEIGHTS_URL = "https://zenodo.org/record/8300489/files/models.zip"


def fetch(url: str, dest: Path, tries: int = 6) -> bool:
    """zenodo 는 urllib 한 방에 자주 끊긴다. 재시도하고 크기를 검증한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 200_000_000:
        print("BAKE: 가중치가 이미 있다")
        return True
    for i in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "chemcheck/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1024 * 1024)
            size = dest.stat().st_size
            if size < 200_000_000:
                raise IOError(f"너무 작다: {size}")
            print(f"BAKE: 가중치 내려받음 {size / 1e6:.0f}MB ({i}회차)")
            return True
        except Exception as exc:
            print(f"BAKE: 내려받기 실패 {i}/{tries}: {exc}")
            dest.unlink(missing_ok=True)
            time.sleep(5 * i)
    return False


def main() -> int:
    fetch(WEIGHTS_URL, Path(PYSTOW_HOME) / "DECIMER-V2" / "models.zip")
    try:
        from PIL import Image, ImageDraw

        from chemcheck.ocsr import DecimerEngine

        img = Image.new("RGB", (320, 220), "white")
        d = ImageDraw.Draw(img)
        d.line((60, 110, 130, 70), fill="black", width=3)
        d.line((130, 70, 200, 110), fill="black", width=3)
        d.line((200, 110, 260, 80), fill="black", width=3)
        probe = Path("/tmp/_bake.png")
        img.save(probe)

        engine = DecimerEngine()
        if not engine.available():
            print("BAKE: DECIMER 적재 실패:", engine.unavailable_reason)
            return 0
        pred = engine.recognize(probe)
        print("BAKE: 예열 완료, 예측 =", None if pred is None else pred.smiles[:60])
    except Exception:
        traceback.print_exc()
        print("BAKE: 예열 실패 - 런타임이 대신 내려받는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
