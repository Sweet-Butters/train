"""인식기 구성·적재·프로토콜 검증 (A 트랙).

판정 로직은 `tests/test_pipeline.py` 가 본다. 여기는 그 앞단이다 - 어떤 인식기를
몇 개 세우는지, 적재가 깨졌을 때 어떻게 물러나는지, 옆 환경에 있는 인식기를
프로세스 너머로 부르는 다리가 약속을 지키는지.

진짜 인식기는 부르지 않는다. MolScribe 는 1.13GB 에 장당 30초고 DECIMER 는
import 시점에 가중치를 내려받는다. 무거운 것을 켜지 않고도 이 셋은 전부 볼 수
있다 - 셋 다 모델이 아니라 그 둘레의 약속에 관한 것이기 때문이다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_broken_engine_does_not_crash() -> None:
    """인식기 적재가 깨져도 도구가 죽으면 안 된다. 없는 것으로 보고하고 보류한다.

    DECIMER는 import 시점에 가중치를 내려받는다. 배포처(Zenodo)가 죽어 있거나
    받다 만 zip이 남아 있으면 ImportError가 아닌 예외(DownloadError, BadZipFile)로
    죽는데, 그게 그대로 올라오면 이름 검사까지 같이 멈춘다.
    """
    import builtins

    from chemcheck.ocsr import DecimerEngine

    real_import = builtins.__import__

    def exploding_import(name, *args, **kwargs):
        if name == "DECIMER":
            raise RuntimeError("가중치 압축 파일이 깨졌다 (BadZipFile 흉내)")
        return real_import(name, *args, **kwargs)

    engine = DecimerEngine()
    builtins.__import__ = exploding_import
    try:
        available = engine.available()
    finally:
        builtins.__import__ = real_import

    assert available is False, "깨진 인식기를 쓸 수 있다고 보고했다"
    assert engine.unavailable_reason, "못 쓰는 이유를 남기지 않았다"
    print(f"  깨진 인식기 -> 사용 불가로 보고: {engine.unavailable_reason}")
    print("통과: 인식기가 깨져도 터지지 않고 판정만 보류한다.")


STUB_WORKER = '''
import json, sys

def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()

emit({"ready": True})
for line in sys.stdin:
    path = line.strip()
    if not path:
        continue
    if "die" in path:          # 워커가 죽는 상황
        break
    if "noconf" in path:       # 신뢰도를 주지 않는 인식기
        emit({"smiles": "CC(=O)Oc1ccccc1C(=O)O", "confidence": None})
    else:
        emit({"smiles": "c1ccccc1", "confidence": 0.91})
'''


def test_subprocess_bridge_speaks_the_protocol() -> None:
    """옆 환경의 인식기를 프로세스 너머로 쓰는 다리 검증.

    진짜 MolScribe 는 1.13GB 에 장당 30초라 테스트에서 부를 수 없다. 프로토콜만
    스텁 워커로 본다: 적재 악수, 요청-응답, 신뢰도 없는 응답, 그리고 워커가
    죽었을 때 조용히 접히는지.
    """
    import math

    from chemcheck.ocsr import SubprocessEngine

    with tempfile.TemporaryDirectory() as tmp:
        worker = Path(tmp) / "stub_worker.py"
        worker.write_text(STUB_WORKER, encoding="utf-8")

        engine = SubprocessEngine(Path(sys.executable), "stub",
                                  load_timeout=30.0, call_timeout=10.0)
        engine.WORKER = worker

        assert engine.available(), f"다리를 못 씀: {engine.unavailable_reason}"

        pred = engine.recognize(Path("slide.png"))
        assert pred is not None and pred.smiles == "c1ccccc1", f"예측 없음: {pred}"
        assert abs(pred.confidence - 0.91) < 1e-9, f"신뢰도 어긋남: {pred.confidence}"

        pred = engine.recognize(Path("noconf.png"))
        assert pred is not None and math.isnan(pred.confidence), \
            "신뢰도를 주지 않는 인식기의 값을 지어냈다"

        assert engine.recognize(Path("die.png")) is None, "죽은 워커가 예측을 냈다"
        assert engine.unavailable_reason, "워커가 죽은 이유를 남기지 않았다"

    print(f"  다리 -> 예측 전달·NaN 보존·죽으면 접힘: {engine.unavailable_reason}")
    print("통과: 인식기를 옆 환경에서 돌려도 프로토콜이 지켜진다.")


ECHO_WORKER = '''
import json, sys

def emit(o):
    # 저장소의 워커 둘이 쓰는 방식 그대로다. 사유를 한글로 쓰기 때문에
    # ensure_ascii=False 이고, 그래서 응답 쪽도 인코딩이 맞아야 한다.
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\\n")
    sys.stdout.flush()

emit({"ready": True})
for line in sys.stdin:
    req = line.strip()
    if not req:
        continue
    emit({"echo": req, "note": "받은 그대로 돌려준다"})
'''


def test_bridge_carries_non_ascii_paths_both_ways() -> None:
    """다리가 한글 경로와 한글 사유를 양방향으로 온전히 나른다.

    부모는 stdin/stdout 을 UTF-8 로 열지만 윈도우 자식의 기본 stdio 인코딩은
    로케일(이 기계는 cp949)이다. 그냥 두면 자식이 요청을 다른 글자로 읽는다.
    이 기계는 임시 폴더 경로부터 `C:\\Users\\정회광\\...` 라 늘 걸린다 - 자식이
    깨진 경로를 받아 `mkdir(parents=True)` 로 거슬러 올라가다 죽었고, 부르는
    쪽에는 그것이 빈 응답으로만 보였다. '구조 0개'와 구분되지 않았다.

    그래서 경로가 ASCII 라는 가정을 여기서 고정한다. 이 테스트는 로케일이
    UTF-8 인 기계에서는 고치기 전에도 통과한다 - 원인이 로케일이기 때문이다.
    """
    from chemcheck.bridge import Worker

    with tempfile.TemporaryDirectory() as tmp:
        korean = Path(tmp) / "한글 폴더"
        korean.mkdir()
        worker_script = korean / "echo_worker.py"
        worker_script.write_text(ECHO_WORKER, encoding="utf-8")

        worker = Worker(Path(sys.executable), worker_script,
                        load_timeout=30.0, call_timeout=10.0)
        assert worker.available(), f"다리를 못 씀: {worker.unavailable_reason}"

        request = str(korean / "6쪽.png")
        reply = worker.call(request)

        assert reply is not None, f"응답이 없다: {worker.unavailable_reason}"
        assert reply.get("echo") == request, \
            f"요청이 가는 길에 깨졌다: {reply.get('echo')!r} != {request!r}"
        assert reply.get("note") == "받은 그대로 돌려준다", \
            f"응답이 오는 길에 깨졌다: {reply.get('note')!r}"

        worker.stop()
    print(f"  한글 경로 왕복 -> {request}")
    print("통과: 경로에 한글이 있어도 다리가 글자를 잃지 않는다.")


def test_self_consistency_only_when_decimer_is_alone() -> None:
    """자체 일관성 검사는 DECIMER 가 혼자일 때만 켠다.

    추론을 3배로 늘리는 장치다. 다른 인식기가 있으면 합의 게이트가 이미
    불일치를 보류로 잡으므로 그 값을 치를 이유가 없다.
    """
    from unittest.mock import patch

    from chemcheck import ocsr

    with patch.object(ocsr.DecimerEngine, "available", lambda self: True):
        with patch.object(ocsr.MolScribeEngine, "available", lambda self: False), \
             patch.object(ocsr.SubprocessEngine, "available", lambda self: False):
            alone = ocsr.load_engines(None)

        with patch.object(ocsr.MolScribeEngine, "available", lambda self: True):
            paired = ocsr.load_engines(None)

    assert len(alone) == 1 and isinstance(alone[0], ocsr.SelfConsistent), \
        f"혼자인데 자체 일관성 검사가 없다: {[e.name for e in alone]}"
    assert len(paired) == 2, f"인식기가 둘이 아니다: {[e.name for e in paired]}"
    assert not any(isinstance(e, ocsr.SelfConsistent) for e in paired), \
        f"둘인데도 3배로 돌린다: {[e.name for e in paired]}"

    print(f"  혼자 -> {[e.name for e in alone]}")
    print(f"  둘   -> {[e.name for e in paired]}")
    print("통과: 3배 비용은 그것 말고 방법이 없을 때만 치른다.")


# 진짜로 올리면 안 되는 것들. 테스트 안에서 이 이름들의 import 는 언제나 막는다 -
# 안 막으면 DECIMER 가 TensorFlow 와 가중치를 올리며 2분 반을 먹는다.
HEAVY_MODULES = ("molscribe", "DECIMER", "torch", "tensorflow")


def _exploding_import(target: str, exc: BaseException):
    """`target` 의 import 는 `exc` 로, 다른 무거운 모듈은 ImportError 로 터뜨린다."""
    import builtins

    real_import = builtins.__import__

    def fake(name, *args, **kwargs):
        top = name.split(".")[0]
        if top == target:
            raise exc
        if top in HEAVY_MODULES:
            raise ImportError(f"{top} 없음 (테스트가 막음)")
        return real_import(name, *args, **kwargs)

    return fake


def test_available_never_raises() -> None:
    """`Engine.available()` 은 어떤 상황에서도 예외를 올리지 않는다.

    MVP 의 `recognize` 는 `load_engines` 를 부르기만 한다. 그 안에서 인식기 하나가
    터지면 다른 인식기까지 같이 죽고 사용자는 '도구가 깨졌다' 만 본다. 인식기가
    없는 것과 도구가 죽는 것은 다른 일이다.

    ImportError 만 잡으면 부족하다. 윈도우의 torch 는 DLL 을 못 찾으면 OSError 로,
    DECIMER 는 가중치 zip 이 깨지면 BadZipFile 로, 회선이 끊기면 그 밖의 무엇으로든
    죽는다. 전부 '없다' 로 접혀야 한다.
    """
    import builtins
    import zipfile

    from chemcheck import ocsr

    broken_imports = [
        ("molscribe", OSError("DLL load failed while importing torch (흉내)")),
        ("molscribe", RuntimeError("CUDA 초기화 실패 (흉내)")),
        ("DECIMER", zipfile.BadZipFile("받다 만 가중치 (흉내)")),
        ("DECIMER", OSError("회선 끊김 (흉내)")),
        ("torch", ImportError("torch 없음 (흉내)")),
    ]
    engines = [
        lambda: ocsr.MolScribeEngine(None),
        lambda: ocsr.MolScribeEngine(Path("없는/체크포인트.pth")),
        lambda: ocsr.DecimerEngine(),
        lambda: ocsr.SubprocessEngine(Path("없는/python.exe"), "molscribe"),
        lambda: ocsr.SubprocessEngine(Path(sys.executable), "molscribe",
                                      Path("없는/체크포인트.pth")),
        lambda: ocsr.SelfConsistent(ocsr.DecimerEngine()),
    ]

    real_import = builtins.__import__
    checked = 0
    for module, exc in broken_imports:
        for make in engines:
            engine = make()
            builtins.__import__ = _exploding_import(module, exc)
            try:
                available = engine.available()   # 예외가 올라오면 여기서 실패
                engines_loaded = ocsr.load_engines(None)
            finally:
                builtins.__import__ = real_import
            assert available is False,                 f"{engine.name}: {module} 가 {type(exc).__name__} 로 터졌는데 있다고 했다"
            assert engine.unavailable_reason,                 f"{engine.name}: 못 쓰는 이유를 남기지 않았다"
            assert all(e.available() for e in engines_loaded),                 "load_engines 가 못 쓰는 인식기를 돌려줬다"
            checked += 1

    # 워커 스크립트가 없어도 마찬가지다.
    engine = ocsr.SubprocessEngine(Path(sys.executable), "molscribe")
    engine.WORKER = Path("없는/워커.py")
    assert engine.available() is False and engine.unavailable_reason
    checked += 1

    print(f"  {checked} 가지 고장에서 available() 이 조용히 False 를 돌려줬다")
    print("통과: 인식기가 어떻게 깨지든 도구는 죽지 않는다.")


if __name__ == "__main__":
    test_broken_engine_does_not_crash()
    print()
    test_subprocess_bridge_speaks_the_protocol()
    print()
    test_bridge_carries_non_ascii_paths_both_ways()
    print()
    test_self_consistency_only_when_decimer_is_alone()
    print()
    test_available_never_raises()
