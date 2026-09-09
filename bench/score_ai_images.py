"""AI 가 생성한 구조 그림을 배포된 엔드포인트로 채점한다.

    python bench/score_ai_images.py bench/aiimages

파일 이름이 곧 라벨이다:

    <모델>__<화합물>.png          원본 (모델이 준 그대로)
    <모델>__<화합물>__crop.png    구조 영역만 잘라낸 것

예) gemini__caffeine.png  ·  gemini__caffeine__crop.png

두 벌을 따로 집계한다. 원본은 "슬라이드 통째로 넣으면 어떻게 되나" 를 재고,
크롭은 "AI 가 구조를 제대로 그렸나" 를 잰다 - 후자가 우리가 주장할 숫자다.
사람 손은 크롭 한 번뿐이고 나머지는 이 스크립트가 한다.

정답 라벨은 파일 이름의 화합물명이다. 우리가 그 이름으로 요청했으므로,
정답은 요청한 그것이지 그림에서 읽은 것이 아니다.
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path

API = "https://pxh7yp--chemcheck.modal.run/api/check"


def post(path: Path, name: str, timeout: float = 400.0) -> dict:
    data = path.read_bytes()
    b = "----b" + uuid.uuid4().hex
    body = io.BytesIO()

    def w(x):
        body.write(x.encode("utf-8") if isinstance(x, str) else x)

    w(f'--{b}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{name}\r\n')
    w(f'--{b}\r\nContent-Disposition: form-data; name="image"; '
      f'filename="{path.name}"\r\nContent-Type: image/png\r\n\r\n')
    w(data)
    w(f"\r\n--{b}--\r\n")
    req = urllib.request.Request(
        API, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_name(stem: str) -> tuple[str, str, bool]:
    """<모델>__<화합물>[__crop] -> (모델, 화합물, 크롭인가)"""
    crop = stem.endswith("__crop")
    if crop:
        stem = stem[: -len("__crop")]
    model, _, compound = stem.partition("__")
    return model, compound.replace("_", " "), crop


def main(folder: str) -> int:
    root = Path(folder)
    files = sorted(p for p in root.glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not files:
        print(f"{root} 에 이미지가 없다. <모델>__<화합물>.png 로 넣어라.")
        return 1

    rows = []
    for i, p in enumerate(files, 1):
        model, compound, crop = parse_name(p.stem)
        t = time.time()
        try:
            r = post(p, compound)
        except Exception as exc:
            print(f"[{i}/{len(files)}] {p.name}  요청 실패: {exc}")
            continue
        el = time.time() - t
        read = r.get("read") or {}
        engines = read.get("engines") or []
        parse_fail = sum(1 for e in engines if not e.get("inchikey"))
        rows.append({
            "file": p.name, "model": model, "compound": compound, "crop": crop,
            "verdict": r.get("verdict"), "grade": r.get("grade"),
            "engines": len(engines), "parse_fail": parse_fail,
            "read_key": read.get("inchikey"), "read_formula": read.get("heavy_formula"),
            "ref_key": (r.get("reference") or {}).get("inchikey"),
            "reasons": r.get("reasons") or [], "sec": round(el, 1),
        })
        print(f"[{i}/{len(files)}] {p.name:<40} {r.get('verdict'):<10} "
              f"grade={r.get('grade')}  파싱실패 {parse_fail}/{len(engines)}  {el:.1f}s")

    out = root / "scored.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def summarize(label: str, sel: list[dict]) -> None:
        if not sel:
            return
        n = len(sel)
        v = Counter(x["verdict"] for x in sel)
        pf = sum(x["parse_fail"] for x in sel)
        reads = sum(x["engines"] for x in sel)
        print(f"\n── {label}  (n={n}) ─────────────────────────")
        print(f"  🔴 mismatch (AI 가 틀리게 그림)  {v['mismatch']}/{n}  ({100*v['mismatch']/n:.0f}%)")
        print(f"  🟢 match                        {v['match']}/{n}")
        print(f"  ⚪ 판정 불가                     {v['unreadable']}/{n}")
        print(f"  파싱 실패                        {pf}/{reads} 읽기")
        strong = sum(1 for x in sel if x["grade"] == "strong")
        print(f"  두 엔진 합의(strong)             {strong}/{n}")
        by = defaultdict(Counter)
        for x in sel:
            by[x["model"]][x["verdict"]] += 1
        for m, c in sorted(by.items()):
            tot = sum(c.values())
            print(f"    {m:<10} mismatch {c['mismatch']}/{tot}  match {c['match']}/{tot}  불가 {c['unreadable']}/{tot}")

    summarize("크롭본 - AI 가 구조를 제대로 그렸나", [x for x in rows if x["crop"]])
    summarize("원본 - 슬라이드 통째로 넣으면", [x for x in rows if not x["crop"]])
    print(f"\n결과 전체: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "bench/aiimages"))
