"""벤치마크 실행기.

    python -m bench.run --task recognize --engine oracle   # 오늘의 제품: 그림 -> SMILES (스텁)
    python -m bench.run --task recognize --engine real     # 하루 끝 한 번, 게이트로
    python -m bench.run --engine oracle     # 덱 판정 장치를 인식기 없이 점검 (결정 2 이후 보류)
    python -m bench.run --engine real       # 덱 판정의 진짜 숫자

--engine real 은 chemcheck.ocsr.load_engines() 를 그대로 쓴다. 인식기 환경(.venv310,
models/)은 chemcheck 워크트리에만 있으므로 CHEMCHECK_ROOT 로 그 워크트리를 가리키면
chemcheck 패키지와 모델을 거기서 가져온다 - bench 는 이 워크트리 것을 쓴다.
bench/gate_recognize.sh 가 그 조합을 절대경로로 적어 두었다.

--engine oracle 로 나온 숫자는 제품 성능이 아니다. '그림을 100% 읽는 인식기'를
가정했을 때 판정 규칙과 채점기가 제대로 도는지 보는 것이다. 이 상태에서 오탐이
1건이라도 나오면 그건 인식기 탓이 아니라 우리 규칙 탓이다.

--engine none 은 바닥값이다. 인식기가 없으면 전부 침묵해야 한다.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 인식기 환경이 있는 워크트리. 없으면 이 워크트리다.
CHEMCHECK_ROOT = Path(os.environ.get("CHEMCHECK_ROOT") or ROOT).resolve()
for _p in ("", str(ROOT), str(CHEMCHECK_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
# chemcheck 는 환경 쪽에서, bench 는 이쪽에서. 순서가 곧 우선순위다.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CHEMCHECK_ROOT))

from chemcheck.extract import load                      # noqa: E402
from chemcheck.names import PubChemResolver             # noqa: E402
from chemcheck.ocsr import Engine, Prediction, load_engines  # noqa: E402
from chemcheck.pipeline import references_for           # noqa: E402
from chemcheck.verdict import judge                     # noqa: E402

from . import cases as corpus                           # noqa: E402
from . import report                                    # noqa: E402
from .baseline import naive_judge                       # noqa: E402
from .labeled import LabeledOracle, load_manifest        # noqa: E402
from .score import Scorecard                            # noqa: E402
from .validate import check_all                         # noqa: E402
from . import recog                                     # noqa: E402
from .variants import build_set                         # noqa: E402

RECOG_SET = ROOT / "bench" / "decks" / "recognize_set"


class OracleEngine(Engine):
    """장 번호로 그려진 SMILES 를 되돌려주는 가상 인식기 (정확도 100%).

    tests/test_pipeline.py 의 것과 같은 발상이다. 제품 경로가 아니다.
    """

    name = "oracle"

    def __init__(self, by_slide: dict[int, str]):
        self.by_slide = by_slide

    def available(self) -> bool:
        return True

    def recognize(self, image_path: Path) -> Prediction | None:
        slide_no = int(image_path.stem.split("_")[0].lstrip("s"))
        smiles = self.by_slide.get(slide_no)
        return Prediction(smiles, 0.99, self.name) if smiles else None


class Hallucinating(OracleEngine):
    """구조식은 제대로 읽고, 구조식이 아닌 그림에도 자신 있게 답하는 인식기.

    실제 OCSR 이 이렇게 행동한다 - MolScribe 든 DECIMER 든 무엇을 주든 SMILES 를
    낸다. 클립아트에 '모르겠다'고 말하는 경로가 없다. 오라클로는 이 상황을 잴 수
    없어서(오라클은 클립아트에 답을 내지 않는다) 따로 둔다.

    이것으로 재려는 것: 이름이 적힌 장에 장식 그림이 섞여 있을 때, 도구가 멀쩡한
    자료를 '오류'라고 말하는가.
    """

    name = "hallucinating"

    def recognize(self, image_path: Path) -> Prediction | None:
        pred = super().recognize(image_path)
        if pred is not None:
            return pred
        # 구조식이 아닌 그림에서 벤젠을 봤다고 우긴다. 신뢰도도 높게 준다.
        return Prediction("c1ccccc1", 0.95, self.name)


def engines_for(kind: str, checkpoint: Path | None,
                by_slide: dict[int, str]) -> list[Engine]:
    if kind == "oracle":
        return [OracleEngine(by_slide)]
    if kind == "hallucinating":
        return [Hallucinating(by_slide)]
    if kind == "none":
        return []
    return load_engines(checkpoint if checkpoint and checkpoint.exists() else None)


def run_labeled(args, resolver) -> int:
    """실제 자료 + 사람이 붙인 라벨로 잰다.

    인식기는 라벨에서 답을 읽는 오라클이다. 그러므로 여기 나오는 오탐은 인식기
    탓이 아니라 나머지 파이프라인 탓이다 - 추출, 이름 해석, 참조 예산, 판정 규칙.
    """
    from .score import Outcome

    manifest = load_manifest(args.manifest)
    if not args.deck:
        print("--manifest 는 --deck 과 함께 써야 합니다 (자료는 저장소에 없습니다).",
              file=sys.stderr)
        return 2
    if not args.deck.exists():
        print(f"덱을 찾을 수 없습니다: {args.deck}", file=sys.stderr)
        return 2

    src = manifest.source
    print(f"자료 {src.get('file','?')} · 라벨 {manifest.size}건 · 인식기 labeled-oracle")
    print(f"  {manifest.labeling.get('scope','')}")
    print("  인식기는 라벨에서 답을 읽는다. 여기 나오는 오탐은 인식기 탓이 아니다.\n")

    engines = [LabeledOracle(manifest)]
    mine = Scorecard("chemcheck")
    base = Scorecard("baseline (보류 규칙 없음)")
    seen = 0

    with tempfile.TemporaryDirectory() as tmp:
        for slide in load(args.deck, Path(tmp)):
            labeled = [i for i in slide.images if i.name in manifest.by_image]
            if not labeled:
                continue
            refs = references_for(slide, resolver)
            for image in labeled:
                case = manifest.by_image[image.name]
                preds = [p for e in engines for p in e.recognize_all(image)]
                mine.add(case, judge(refs, preds))
                base.add(case, naive_judge(refs, preds))
                seen += 1

    print(f"라벨 {manifest.size}건 중 덱에서 {seen}건을 찾았다.\n")
    print(report.scorecard(mine))
    print()
    print(report.scorecard(base))
    print()
    print(report.contrast(mine, base))
    if args.detail:
        print()
        print(report.detail(mine))
    return 1 if mine.count(Outcome.FALSE_ALARM) else 0


def run_recognize(args) -> int:
    """오늘의 제품 - 그림 하나를 넣으면 SMILES 하나가 나오는 경로를 잰다.

    슬라이드·덱·이름 해석은 여기 없다. 그림 50장을 렌더 변주로 그리고 인식기에
    먹인 뒤, 답을 냈는지·맞았는지·틀리면서 자신 있었는지를 센다.
    """
    items = build_set(args.set_dir)
    if args.engine in recog.STUBS:
        engines = recog.stub_engines(args.engine, items)
    else:
        import chemcheck
        # 어느 워크트리의 패키지와 모델로 잰 숫자인지 리포트에 남는다.
        missing = "" if args.checkpoint.exists() else " (없음)"
        print(f"chemcheck 패키지 {Path(chemcheck.__file__).parent} · "
              f"체크포인트 {args.checkpoint}{missing}\n")
        engines = load_engines(args.checkpoint if args.checkpoint.exists() else None)
        if not engines:
            print("인식기를 하나도 못 올렸다. 숫자를 낼 수 없다.", file=sys.stderr)
            from chemcheck.ocsr import LAST_DIAGNOSTICS
            for line in LAST_DIAGNOSTICS:
                print(f"  {line}", file=sys.stderr)
            return 2
    cards = recog.evaluate(items, engines)
    print(recog.report(cards, args.engine, [e.name for e in engines], show_detail=args.detail))
    return recog.exit_code(cards)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.run", description="chemcheck 평가 실행")
    parser.add_argument("--task", choices=("deck", "recognize"), default="deck",
                        help="deck: 슬라이드 판정 장치 (보류) · recognize: 그림 -> SMILES")
    parser.add_argument("--engine",
                        choices=("oracle", "hallucinating", "shaky", "colluding", "real", "none"),
                        default="oracle",
                        help="스텁(oracle/hallucinating/shaky/colluding/none) 또는 real")
    parser.add_argument("--checkpoint", type=Path,
                        default=CHEMCHECK_ROOT / "models" / "molscribe.pth")
    parser.add_argument("--set-dir", type=Path, default=RECOG_SET,
                        help="recognize 평가 세트 그림을 둘 폴더 (다시 그린다)")
    parser.add_argument("--deck", type=Path, default=None,
                        help="직접 만든 덱으로 돌린다 (라벨은 CORPUS 순서와 맞아야 함)")
    parser.add_argument("--skip-validate", action="store_true",
                        help="PubChem 을 못 쓰는 자리에서 라벨 검증을 건너뛴다")
    parser.add_argument("--detail", action="store_true", help="케이스별 줄까지 출력")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="실제 자료에 붙인 라벨 (bench/decks/*.json). --deck 과 함께 쓴다")
    args = parser.parse_args(argv)

    if args.task == "recognize":
        return run_recognize(args)
    if args.engine in ("shaky", "colluding"):
        parser.error(f"--engine {args.engine} 는 --task recognize 의 스텁이다")

    resolver = PubChemResolver()

    if args.manifest:
        return run_labeled(args, resolver)

    # 1) 코퍼스가 스스로 옳은가. 여기가 깨지면 아래 숫자는 의미가 없다.
    checks = None
    if not args.skip_validate:
        checks = check_all(corpus.CORPUS, resolver)
        print(report.labels(checks))
        print()

    deck = args.deck or corpus.build_deck(corpus.CORPUS, corpus.DECKS / "synthetic.pptx")
    by_slide = {i: c.smiles for i, c in enumerate(corpus.CORPUS, start=1)}
    engines = engines_for(args.engine, args.checkpoint, by_slide)
    # 단서는 리포트 위아래 양쪽에 찍는다. 잘라 붙여도 따라가도록.
    banner = report.caveat(args.engine, synthetic_deck=args.deck is None)
    if banner:
        print(banner, end="\n\n")
    print(f"덱 {deck.name} · 인식기 {', '.join(e.name for e in engines) or '없음'}\n")

    mine = Scorecard("chemcheck")
    base = Scorecard("baseline (보류 규칙 없음)")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for slide in load(deck, work):
            case = corpus.CORPUS[slide.index - 1]
            refs = references_for(slide, resolver)
            for image in slide.images:
                # 두 판정기는 같은 인식 결과를 본다. 인식기를 두 번 돌리지 않는다.
                preds = [p for e in engines for p in e.recognize_all(image)]
                mine.add(case, judge(refs, preds))
                base.add(case, naive_judge(refs, preds))

    print(report.scorecard(mine))
    print()
    print(report.scorecard(base))
    print()
    print(report.contrast(mine, base))

    if args.detail:
        print()
        print(report.detail(mine))

    # 단서를 아래에도 한 번 더. 위만 잘라 붙이는 경우와 아래만 보는 경우 둘 다 막는다.
    if banner:
        print()
        print(banner)

    # 오탐이 있으면 실패로 끝난다. 이 도구의 1순위 약속이 깨진 것이므로.
    from .score import Outcome
    return 1 if mine.count(Outcome.FALSE_ALARM) else 0


if __name__ == "__main__":
    raise SystemExit(main())
