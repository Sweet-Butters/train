"""OCSR 모델 가중치를 미리 받아둔다.

행사장 네트워크는 항상 최악이므로 전날 받아두는 용도다.
불안정한 회선을 전제로 이어받기와 재시도를 한다. 중간에 끊겨도 다시 실행하면 이어받는다.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

CHECKPOINTS = {
    "molscribe": (
        "https://huggingface.co/yujieq/MolScribe/resolve/main/swin_base_char_aux_1m.pth",
        "molscribe.pth",
        1_134_940_406,
    ),
}

CHUNK = 1 << 20  # 1MB


def fetch(url: str, dest: Path, expected: int, attempts: int = 30) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= expected:
            print(f"  완료 ({have:,} bytes)")
            return True
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, dest.open("ab") as out:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    have += len(chunk)
                    pct = 100.0 * have / expected
                    print(f"\r  {have:,} / {expected:,} bytes ({pct:5.1f}%)", end="", flush=True)
        except Exception as exc:
            print(f"\n  끊김 (시도 {attempt}/{attempts}): {type(exc).__name__} — 이어받습니다")
            continue
        print()
    have = dest.stat().st_size if dest.exists() else 0
    ok = have >= expected
    if not ok:
        print(f"  미완료: {have:,} / {expected:,} bytes. 다시 실행하면 이어받습니다.")
    return ok


def main(argv: list[str]) -> int:
    wanted = argv or list(CHECKPOINTS)
    failed = False
    for name in wanted:
        if name not in CHECKPOINTS:
            print(f"알 수 없는 모델: {name}")
            failed = True
            continue
        url, filename, size = CHECKPOINTS[name]
        print(f"{name} -> models/{filename}")
        if not fetch(url, MODELS / filename, size):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
