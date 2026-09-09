"""슬라이드 텍스트에서 화합물 이름 후보를 뽑고 PubChem으로 참조 InChIKey를 얻는다.

PubChem이 이름을 해석하지 못하면 그 후보는 버린다. 여기서 추측하지 않는다.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/InChIKey,MolecularFormula/JSON"
CACHE_PATH = Path.home() / ".cache" / "chemcheck" / "pubchem.json"

# 슬라이드에 흔하지만 화합물이 아닌 말들. PubChem이 엉뚱하게 해석하는 것을 막는다.
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "what", "how", "why",
    "structure", "molecule", "chemistry", "figure", "table", "slide", "example",
    "result", "method", "data", "test", "demo", "team", "next", "step", "title",
}

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-,'()\[\]]*")


@dataclass(frozen=True)
class Reference:
    name: str
    inchikey: str
    formula: str


class PubChemResolver:
    """이름 -> InChIKey. 디스크 캐시를 쓰고 초당 요청을 제한한다."""

    def __init__(self, cache_path: Path = CACHE_PATH, min_interval: float = 0.25):
        self.cache_path = cache_path
        self.min_interval = min_interval
        self._last_call = 0.0
        self._cache: dict[str, dict | None] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # 캐시 실패는 판정에 영향을 주지 않는다

    def resolve(self, name: str) -> Reference | None:
        key = name.strip().lower()
        if not key:
            return None
        if key in self._cache:
            hit = self._cache[key]
            return Reference(name, hit["inchikey"], hit["formula"]) if hit else None

        gap = time.monotonic() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.monotonic()

        try:
            resp = requests.get(PUBCHEM.format(quote(key, safe="")), timeout=20)
        except requests.RequestException:
            return None  # 네트워크 실패는 캐시하지 않는다

        if resp.status_code == 404:
            self._cache[key] = None
            self._save()
            return None
        if resp.status_code != 200:
            return None

        try:
            props = resp.json()["PropertyTable"]["Properties"][0]
            record = {"inchikey": props["InChIKey"], "formula": props.get("MolecularFormula", "")}
        except (KeyError, IndexError, ValueError):
            return None

        self._cache[key] = record
        self._save()
        return Reference(name, record["inchikey"], record["formula"])


def candidates(text: str, max_words: int = 4) -> list[str]:
    """텍스트에서 화합물 이름일 수 있는 구절을 뽑는다 (긴 것 우선)."""
    found: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        words = _WORD.findall(line)
        for n in range(min(max_words, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                low = phrase.lower()
                if len(phrase) < 3 or len(phrase) > 60:
                    continue
                if low in seen or low in STOPWORDS:
                    continue
                if phrase.replace(".", "").isdigit():
                    continue
                if n == 1 and low in STOPWORDS:
                    continue
                seen.add(low)
                found.append(phrase)
    return found
