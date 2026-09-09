"""벤치마크 실행기.

    python -m bench.run --engine oracle     # 인식기 없이 장치 자체를 점검
    python -m bench.run --engine real       # A 가 착지한 뒤 진짜 숫자

--engine oracle 로 나온 숫자는 제품 성능이 아니다. '그림을 100% 읽는 인식기'를
가정했을 때 판정 규칙과 채점기가 제대로 도는지 보는 것이다. 이 상태에서 오탐이
1건이라도 나오면 그건 인식기 탓이 아니라 우리 규칙 탓이다.

--engine none 은 바닥값이다. 인식기가 없으면 전부 침묵해야 한다.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chemcheck.extract import load                      # noqa: E402
from chemcheck.names import PubChemResolver             # noqa: E402
from chemcheck.ocsr import Engine, Prediction, load_engines  # noqa: E402
from chemcheck.pipeline import references_for           # noqa: E402
from chemcheck.verdict import judge                     # noqa: E402

from . import cases as corpus                           # noqa: E402
from . import report                                    # noqa: E402
from .baseline import naive_judge                       # noqa: E402
from .score import Scorecard                            # noqa: E402
from .validate import check_all                         # noqa: E402


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.run", description="chemcheck 평가 실행")
    parser.add_argument("--engine", choices=("oracle", "hallucinating", "real", "none"),
                        default="oracle")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models" / "molscribe.pth")
    parser.add_argument("--deck", type=Path, default=None,
                        help="직접 만든 덱으로 돌린다 (라벨은 CORPUS 순서와 맞아야 함)")
    parser.add_argument("--skip-validate", action="store_true",
                        help="PubChem 을 못 쓰는 자리에서 라벨 검증을 건너뛴다")
    parser.add_argument("--detail", action="store_true", help="케이스별 줄까지 출력")
    args = parser.parse_args(argv)

    resolver = PubChemResolver()

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
