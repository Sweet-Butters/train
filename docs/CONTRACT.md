# 병렬 작업 계약 (포크 시점 고정)

트랙 B·C·D를 별도 워크트리로 갈라내기 직전에 고정한 경계다.
이 문서를 어기면 머지 시점에 서로의 작업을 덮어쓴다.

## 동결 — 어느 트랙도 고치지 않는다

| 대상 | 이유 |
|---|---|
| `chemcheck/pipeline.py` 전체 | 모든 트랙이 통과하는 유일한 합류점 |
| `chemcheck/verdict.py` 전체 | 판정 규칙. 여기가 흔들리면 D의 측정이 무의미해진다 |
| `chemcheck/ocsr.py` 의 `Prediction`, `Engine` | 인식기와 파이프라인 사이의 유일한 인터페이스 |

동결된 것을 바꿔야 하는 상황이 오면 **직접 고치지 말고 코디네이터에게 올린다.**
바꿀 이유가 생겼다는 사실 자체가 트랙 경계가 잘못 그어졌다는 신호다.

### 고정된 인터페이스

```python
@dataclass(frozen=True)
class Prediction:
    smiles: str        # 인식기가 읽어낸 SMILES
    confidence: float  # 신뢰도가 없는 인식기는 float("nan")
    engine: str        # 어느 인식기가 냈는지 (사람이 읽는 이름)

class Engine:
    name: str
    def available(self) -> bool: ...                             # 절대 예외를 올리지 않는다
    def recognize(self, image_path: Path) -> Prediction | None: ...
    def recognize_all(self, image_path: Path) -> list[Prediction]: ...
```

`available()` 이 예외를 올리지 않는다는 것은 계약의 일부다. 인식기가 깨져도
이름 검사까지 같이 멈추면 안 된다 (`tests/test_pipeline.py` 가 이를 지킨다).

## 파일 소유권 — 배타적

| 트랙 | 영역 | 소유 파일 |
|---|---|---|
| **A** 인식기·정확도 | DECIMER/MolScribe 결판, 실제 정확도·오탐률, 신뢰도 게이트 | `ocsr.py` 구현부(`MolScribeEngine`·`DecimerEngine`·`SelfConsistent`·`load_engines`), `scripts/setup_*.sh`, `scripts/fetch_*`, 환경 전부(`.venv`, `.venv310`, `models/`, `.wheels/`) |
| **B** 입력 추출 | PPTX 도형·PDF 벡터로 그린 구조, 장당 다중 그림 | `chemcheck/extract.py` |
| **C** 이름 해석 | 동의어·IUPAC명·약어, PubChem 캐시와 레이트리밋, 오프라인 폴백 | `chemcheck/names.py`, `chemcheck/keys.py`, `chemcheck/data/`, `scripts/build_offline_table.py` |
| **D** 평가·리포트 | 벤치마크 덱, 스코어링, 리포트 | `tests/test_pipeline.py`, `bench/`(신규), `scripts/make_demo.py`, `chemcheck/cli.py` 전체 |

부분 소유는 두지 않는다. 한 파일은 한 트랙이 통째로 갖는다.

`tests/` 는 디렉터리를 D가 갖되, 각 트랙은 **자기 모듈의 테스트 파일 하나**를
`tests/test_<모듈>.py` 로 직접 추가한다(예: C의 `tests/test_names.py`).
파일이 겹치지 않으므로 충돌하지 않고, 트랙이 자기 변경을 스스로 증명하지 못하면
D가 다른 트랙의 내부까지 알아야 하는 문제가 생긴다.

## D 트랙의 범위 — 장치이지 숫자가 아니다

D가 포크 시점에 뽑은 오탐률은 A·B·C가 착지하면 곧바로 stale이 된다.
그러므로 D의 산출물은 **측정 장치**다 — 라벨 붙은 벤치마크 덱, 스코어링 코드,
리포트 골격까지. 숫자는 통합이 끝난 뒤에 다시 돌려서 낸다.

## B·C·D는 인식기를 설치하지 않는다

`tests/test_pipeline.py` 의 스텁 엔진(`OracleEngine`, `FlakyEngine`)이
정확도 100%인 가상 인식기와 흔들리는 인식기를 대신한다. 추출·이름·판정·리포트는
torch도 TF도 가중치도 없이 전부 개발·검증된다.

```bash
bash scripts/setup_lite.sh      # 무거운 것 없이 파이프라인 의존성만
.venv/Scripts/python tests/test_pipeline.py
```

무거운 환경은 A 트랙 안에만 존재한다.
