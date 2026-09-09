"""양방향 구조 도구(recognize/draw) 검증. 인식기 없이 스텁으로 돈다.

지키려는 원칙:
- 인식기 둘이 골격에서 합의해야 답이다. 갈리면 '확신 없음' 과 후보 전부.
- 인식기 하나뿐이면 그 답을 답으로 내보내지 않는다.
- 인식기가 없으면 또렷이 말하고 죽지 않는다.
- draw 는 PubChem 정본만 그린다. 못 찾으면 None. 추측하지 않는다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chemcheck import cli  # noqa: E402
from chemcheck.names import PubChemResolver  # noqa: E402
from chemcheck.ocsr import Engine, Prediction  # noqa: E402
from chemcheck.structure import draw, engine_family, lookup_name, recognize  # noqa: E402

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ASPIRIN_KEKULE = "CC(=O)OC1=CC=CC=C1C(=O)O"   # 같은 분자, 다른 표기
SALICYLIC = "OC(=O)c1ccccc1O"                  # 아세틸기 하나 차이
ASPIRIN_SKELETON = "BSYNRYMUTXBXSQ"


class StubEngine(Engine):
    """정해진 SMILES 를 돌려주는 가상 인식기."""

    def __init__(self, name: str, smiles: str | None, confidence: float = float("nan")):
        self.name = name
        self.smiles = smiles
        self.confidence = confidence

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        if self.smiles is None:
            return None
        return Prediction(self.smiles, self.confidence, self.name)


class BrokenEngine(Engine):
    name = "broken"

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        raise RuntimeError("worker died")


def _image(tmp: str) -> Path:
    from PIL import Image

    path = Path(tmp) / "demo_02.png"
    Image.new("RGB", (10, 10), "white").save(path)
    return path


def test_two_engines_agree_on_skeleton_is_an_answer() -> None:
    engines = [StubEngine("molscribe@.venv310", ASPIRIN_KEKULE, 0.93),
               StubEngine("decimer", ASPIRIN)]
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), engines, Path(tmp) / "out")
        assert rec.status == "agreed", rec.reason
        assert rec.answer is not None
        assert rec.answer.skeleton == ASPIRIN_SKELETON
        assert rec.answer.smiles == ASPIRIN            # 표기가 달라도 canonical 로 합친다
        assert rec.answer.confidence == 0.93            # 신뢰도는 준 쪽 것
        assert rec.answer.image is not None and rec.answer.image.exists()
        assert rec.answer.image.name == "demo_02.answer.png"
        # 엔진별 원문은 남는다
        raw = {r.engine: r.raw_smiles for r in rec.results}
        assert raw["molscribe@.venv310"] == ASPIRIN_KEKULE
        assert raw["decimer"] == ASPIRIN


def test_disagreement_is_uncertain_with_both_candidates() -> None:
    engines = [StubEngine("molscribe@.venv310", ASPIRIN, 0.95), StubEngine("decimer", SALICYLIC)]
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), engines, Path(tmp) / "out")
        assert rec.status == "uncertain"
        assert rec.answer is None                       # 후보를 답으로 내보내지 않는다
        assert "확신 없음" in rec.reason
        assert len(rec.candidates) == 2
        skeletons = {c.skeleton for c in rec.candidates}
        assert ASPIRIN_SKELETON in skeletons and len(skeletons) == 2
        for c in rec.candidates:
            assert c.image is not None and c.image.exists()
        names = sorted(c.image.name for c in rec.candidates)
        assert names == ["demo_02.cand1.png", "demo_02.cand2.png"]


def test_single_engine_is_never_an_answer() -> None:
    # SelfConsistent 처럼 한 인식기가 여러 이름으로 같은 답을 내도 마찬가지다.
    engines = [StubEngine("decimer/x1", ASPIRIN), StubEngine("decimer/x0.9", ASPIRIN)]
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), engines, Path(tmp) / "out")
        assert rec.status == "single"
        assert rec.answer is None
        assert len(rec.candidates) == 1                 # 그래도 후보와 그림은 남긴다
        assert rec.candidates[0].image is not None and rec.candidates[0].image.exists()


def test_no_engine_says_so_and_does_not_crash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), [], Path(tmp) / "out")
        assert rec.status == "no_engine"
        assert "인식기 없음" in rec.reason
        assert rec.candidates == [] and rec.answer is None


def test_unreadable_smiles_and_dead_engine_do_not_crash() -> None:
    engines = [StubEngine("molscribe@.venv310", "C1CC", 0.5),   # 닫히지 않은 고리 -> 파싱 실패
               BrokenEngine(),
               StubEngine("decimer", None)]
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), engines, Path(tmp) / "out")
        assert rec.status == "no_result"
        assert "읽을 수 없음" in rec.reason
        assert any(r.engine == "broken" for r in rec.results)


def test_stereo_difference_within_same_skeleton_is_still_agreed_but_noted() -> None:
    engines = [StubEngine("molscribe@.venv310", "C[C@H](N)C(=O)O", 0.9),
               StubEngine("decimer", "CC(N)C(=O)O")]
    with tempfile.TemporaryDirectory() as tmp:
        rec = recognize(_image(tmp), engines)
        assert rec.status == "agreed"
        assert "입체화학" in rec.reason


def test_engine_family() -> None:
    assert engine_family("molscribe@.venv310") == "molscribe"
    assert engine_family("decimer/x0.9") == "decimer"
    assert engine_family("decimer-selfconsistent") == "decimer"


# ── draw ───────────────────────────────────────────────────────────────────

OFFLINE = {
    "aspirin": {"inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "formula": "C9H8O4",
                "smiles": ASPIRIN_KEKULE},
}


def _resolver(tmp: str, cache: dict | None = None) -> PubChemResolver:
    import json

    path = Path(tmp) / "cache.json"
    if cache is not None:
        path.write_text(json.dumps(cache), encoding="utf-8")
    r = PubChemResolver(cache_path=path, offline=OFFLINE, max_retries=0, timeout=0.001)
    r._fetch = lambda key: None  # 망을 쓰지 않는다
    return r


def test_draw_korean_name_uses_pubchem_record() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        drawn = draw("아스피린", Path(tmp) / "out", _resolver(tmp))
        assert drawn is not None
        assert drawn.name == "aspirin"
        assert drawn.smiles == ASPIRIN_KEKULE            # 정본 그대로
        assert drawn.canonical == ASPIRIN
        assert drawn.inchikey.startswith(ASPIRIN_SKELETON)
        assert drawn.source == "offline"
        assert drawn.image is not None and drawn.image.exists()


def test_draw_unknown_name_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert draw("없는화합물xyz", Path(tmp), _resolver(tmp)) is None
        assert draw("aspirinn", Path(tmp), _resolver(tmp)) is None   # 오타를 고쳐 추측하지 않는다


def test_draw_ignores_old_cache_entry_without_smiles() -> None:
    """옛 캐시(키·분자식만)는 그릴 수 없다. 표로 내려가야 한다."""
    old = {"aspirin": {"inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "formula": "C9H8O4"}}
    with tempfile.TemporaryDirectory() as tmp:
        drawn = draw("aspirin", None, _resolver(tmp, old))
        assert drawn is not None and drawn.smiles == ASPIRIN_KEKULE


def test_lookup_name() -> None:
    assert lookup_name("아스피린") == "aspirin"
    assert lookup_name("아스피린은") == "aspirin"
    assert lookup_name("Ibuprofen ") == "Ibuprofen"
    assert lookup_name("모르는한글") == "모르는한글"   # 표에 없으면 그대로 조회 (404 가 답)


# ── CLI ────────────────────────────────────────────────────────────────────

def test_cli_recognize_without_engines(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_engines", lambda ckpt: [])
    with tempfile.TemporaryDirectory() as tmp:
        code = cli.main(["recognize", str(_image(tmp)), "--out", tmp])
    out = capsys.readouterr().out
    assert code == 3
    assert "인식기 없음" in out


def test_cli_recognize_reports_each_engine_and_verdict(monkeypatch, capsys) -> None:
    engines = [StubEngine("molscribe@.venv310", ASPIRIN, 0.91), StubEngine("decimer", SALICYLIC)]
    monkeypatch.setattr(cli, "load_engines", lambda ckpt: engines)
    with tempfile.TemporaryDirectory() as tmp:
        code = cli.main(["recognize", str(_image(tmp)), "--out", tmp])
        out = capsys.readouterr().out
        assert code == 1
        assert "molscribe@.venv310" in out and "decimer" in out
        assert "0.910" in out and "nan" in out
        assert "[확신없음]" in out and "후보 1" in out and "후보 2" in out
        assert (Path(tmp) / "demo_02.cand1.png").exists()


def test_cli_legacy_file_argument_still_routes_to_check(capsys) -> None:
    code = cli.main(["없는파일.pdf"])
    assert code == 2
    assert "파일이 없습니다" in capsys.readouterr().err


# ── 입력 정규화 배선 (inputs.prepare_image 는 B 소유, 여기서는 부르기만) ──────

def test_recognize_feeds_prepared_image_and_carries_notes() -> None:
    from PIL import Image

    engines = [StubEngine("molscribe@.venv310", ASPIRIN, 0.9), StubEngine("decimer", ASPIRIN)]
    with tempfile.TemporaryDirectory() as tmp:
        # 투명 배경 PNG - prepare_image 가 손을 대고 노트를 남길 입력
        path = Path(tmp) / "demo_02.png"
        Image.new("RGBA", (300, 200), (0, 0, 0, 0)).save(path)
        rec = recognize(path, engines, Path(tmp) / "out")
        assert rec.status == "agreed"
        assert rec.prepared is not None and rec.prepared.exists()
        assert rec.prepared.suffix == ".png" and rec.prepared.is_absolute()
        assert rec.notes, "투명 PNG 를 다듬었으면 노트가 있어야 한다"
        assert rec.answer.image.name == "demo_02.answer.png"   # 이름은 원본 그림을 따른다


def test_recognize_unreadable_file_does_not_crash() -> None:
    engines = [StubEngine("molscribe@.venv310", ASPIRIN, 0.9), StubEngine("decimer", ASPIRIN)]
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "not_an_image.png"
        bad.write_bytes(b"this is not a png")
        rec = recognize(bad, engines, Path(tmp) / "out")
        assert rec.status == "unreadable"
        assert rec.candidates == [] and rec.answer is None


def test_cli_prints_input_notes(monkeypatch, capsys) -> None:
    from PIL import Image

    engines = [StubEngine("molscribe@.venv310", ASPIRIN, 0.9), StubEngine("decimer", ASPIRIN)]
    monkeypatch.setattr(cli, "load_engines", lambda ckpt: engines)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "demo.png"
        Image.new("RGBA", (300, 200), (0, 0, 0, 0)).save(path)
        code = cli.main(["recognize", str(path), "--out", tmp])
        out = capsys.readouterr().out
        assert code == 0
        assert "   입력  " in out and "[ 합의 ]" in out
