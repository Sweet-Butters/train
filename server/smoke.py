"""배포된 엔드포인트에 4장을 실제로 던져 본다.

    python server/smoke.py https://<workspace>--chemcheck-web-api-web.modal.run

콜드스타트를 재려고 첫 장 시간을 따로 찍는다. 판정은 서버가 하고, 여기서는
계약대로의 JSON 이 오는지와 시간만 본다.

testdata/ 는 슬라이드에서 **구조 영역만 잘라낸** 것이다. 통째로 먹이면 쓰레기가
나온다(실측: 탄소 100개 폴리인). 실전에서 자르는 일은 web/crop.js(C 트랙)가 한다.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CASES = [
    ("caffeine_gemini_crop.png", "caffeine",
     "틀린 그림 - 이미다졸 질소 둘 다 메틸(C9). mismatch 또는 unreadable 기대"),
    ("caffeine_gpt_crop.png", "caffeine", "맞는 그림 - match 기대"),
    ("alanine_gemini_crop.png", "L-alanine", "두 엔진 다 파싱 실패했던 것"),
    ("alanine_gpt_crop.png", "L-alanine", "골격은 맞음 - match 기대"),
]


def main(base: str) -> int:
    base = base.rstrip("/")
    t0 = time.monotonic()
    health = requests.get(f"{base}/api/health", timeout=600)
    print(f"health {health.status_code} {health.text}  ({time.monotonic()-t0:.1f}s, 콜드스타트 포함)\n")

    for filename, name, note in CASES:
        path = HERE / "testdata" / filename
        t = time.monotonic()
        resp = requests.post(
            f"{base}/api/check",
            files={"image": (filename, path.read_bytes(), "image/png")},
            data={"name": name},
            timeout=600,
        )
        elapsed = time.monotonic() - t
        print(f"── {filename}  (name={name})\n   {note}")
        if resp.status_code != 200:
            print(f"   HTTP {resp.status_code}: {resp.text[:300]}\n")
            continue
        out = resp.json()
        print(f"   verdict={out['verdict']}  grade={out['grade']}  {elapsed:.1f}s")
        print(f"   read.smiles = {out['read']['smiles']}")
        print(f"   read.heavy_formula = {out['read']['heavy_formula']}")
        ref = out.get("reference")
        print(f"   reference = {ref['inchikey'] if ref else None}")
        for r in out["reasons"]:
            print(f"   - {r}")
        print()

    # CORS 프리플라이트. 정적 페이지가 다른 오리진에서 부르므로 이게 되어야 한다.
    pre = requests.options(
        f"{base}/api/check",
        headers={"Origin": "https://example.github.io",
                 "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
        timeout=60,
    )
    print("CORS 프리플라이트:", pre.status_code,
          json.dumps({k: v for k, v in pre.headers.items()
                      if k.lower().startswith("access-control")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
