# chemcheck

**LLM(Gemini·GPT 등)에게 "이 분자 그려줘"라고 받은 이미지를 넣으면, 그 이름의 PubChem 정본과 골격이 같은지 검사합니다 - 다르면 어디가 다른지 그림으로 보여줍니다.**

```
python -m chemcheck check --name "카페인" caffeine_generated_gemini_pro.jpeg --out out/
```

**이것은 심어둔 오류가 아니라 실제로 Gemini 가 만든 이미지에서 나온 진짜 오탐입니다**
(`docs/DIRECTION.md` 실측 6·7) - 카페인은 1,3,7-트리메틸인데 이 그림은 N9 에 메틸이
하나 더 있어 중성 분자로 존재할 수 없습니다. 두 인식기(MolScribe·DECIMER)가 각자 다른
방식으로 그 이상을 봤고(하나는 원자가가 깨지고, 하나는 짝이온을 지어냈습니다), 이름 대조가
그중 읽을 수 있는 답 하나만으로도 오류를 잡아냅니다 - 결정 6 전에는 "판정 불가"로 물러났던
바로 그 케이스입니다. 실제 출력은 [아래](#실측---gemini-가-그린-카페인)에 그대로 있습니다.

그 밖에 `python -m chemcheck recognize 그림.png`(그림 → SMILES) 와
`python -m chemcheck draw "아스피린"`(이름 → 구조)도 있습니다. (PDF/PPTX 추출·분할기·
실측 벤치는 오늘 범위 밖 - 아래는 그 이전 내용입니다.)

---

AI가 그린 화학 구조 그림이 맞는지 검사합니다. **판정에 LLM을 쓰지 않습니다.**

## 문제

ChatGPT에게 "아스피린 그려줘"라고 하면 그럴듯하지만 틀린 구조가 나옵니다.
GPT-4는 문법적으로 유효한 SMILES를 89% 생성하지만, **요구한 구조와 일치하는 것은 20% 미만**입니다.
문제는 틀렸다는 걸 아무도 모른 채 강의자료·논문·포스터에 들어간다는 점입니다.

화학자들은 이미 공개적으로 [AI 구조 생성 금지를 요구](https://www.chemistryworld.com/news/the-chemistry-community-should-ban-drawing-chemical-structures-with-generative-ai-chemists-warn/4022242.article)했습니다.

## 접근

슬라이드에는 보통 **이름 텍스트와 그림이 함께** 있습니다. 이 둘을 각각 InChIKey로 바꿔 문자열로 비교합니다.

```
"Aspirin" ──PubChem──> BSYNRYMUTXBXSQ-UHFFFAOYSA-N
                                    ≠           → 오류
 [그림]  ──OCSR──> SMILES ──RDKit──> YGSDEFSMJLZEOE-UHFFFAOYSA-N
```

## 왜 LLM에게 안 맡기나

가장 가까운 선행연구([PWP prompting](https://arxiv.org/pdf/2505.12257))는 LLM에게 "이 그림 맞아?"라고 묻습니다.
그런데 **그 LLM이 화학 구조를 못 읽는다는 게 애초의 문제**였고, 실제로 Gemini는 자기 실수를 4.34%밖에 잡지 못합니다.
검사기가 같은 병에 걸려 있는 것입니다.

chemcheck의 판정 단계에는 추론이 없습니다. InChIKey 문자열 비교뿐입니다.

## 틀렸다고 잘못 말하지 않는 것이 최우선

OCSR 정확도는 76~93%라 오탐이 납니다. 확신이 없으면 반드시 **판정 불가**로 빠집니다.

**50장 합성 렌더의 성공 기준을 숫자를 보기 전에 적어 둡니다 (2026-09-09):** 50장에서 '자신 있게 틀림' 0건이면 상한 오탐률 약 6% 이하(95% 신뢰)라는 뜻일 뿐이고, 실제 그림에 대해서는 아무 말도 아닙니다.

**이름이 있으면 이름 대조가 1순위입니다 (`docs/DIRECTION.md` 결정 6·8).** 인식기 둘의
합의는 이름이 없을 때만 쓰는 최후 수단이 아니라 등급을 가르는 데 씁니다 - 예전에는
인식기 하나가 SMILES 를 못 읽으면(원자가가 깨진 답 등) 다른 인식기가 낸 멀쩡한 답까지
묻혔습니다. 지금은 읽을 수 있는 답부터 이름과 대조합니다.

| 상황 | 결과 |
|---|---|
| 읽을 수 있는 답 중 하나라도 이름의 InChIKey 와 일치 | 일치 |
| 골격은 같고 입체화학만 다름 | 입체는 판정하지 않았습니다 (사람이 확인) |
| 유효한 구조를 낸 인식기가 하나뿐인데 그 골격이 이름과 다름 | **오류 [약]** |
| 종류가 다른 인식기 둘 이상이 합의한 골격이 이름과 다름 | **오류 [강]** |
| 이름을 못 찾음 / 인식기 없음 / 유효한 구조를 낸 인식기가 하나도 없음 | **판정 불가** |

여러 인식기가 서로 다른 답을 내면(어느 쪽도 이름과 맞지 않을 때) 판정하지 않고 후보만 냅니다.
`--out` 에 정본(과 물러났을 때는 후보 쌍)의 **diff 그림**을 남깁니다 - 최대 공통 부분구조
(RDKit rdFMCS) 밖의 원자·결합을 빨강, 그 조각이 붙는 자리를 반대쪽에 주황으로 칠한 한
장입니다. 질문이 '둘 중 뭐가 맞나'(불가능)에서 '원본 그림의 주황 자리에 빨강 조각이
있었나'(가능)로 바뀝니다. 조성(중원자 개수)도 함께 보여줍니다 - `Chem.MolFromSmiles(s,
sanitize=False)` 로 원자가 검사 없이 세므로 원자가가 깨진 답도 셀 수 있습니다. 일방향
신호입니다 - 다르면 오류 옆에 곁들이는 근거고, 같다고 판정을 바꾸지 않습니다.
[VERDICT](https://arxiv.org/html/2608.22183)가 왕복 검증보다 다중 엔진 합의가 낫다고 보고한 방식입니다.

## 사용

```bash
bash scripts/setup_lite.sh      # 인식기 없이 파이프라인만
python -m chemcheck check 슬라이드.pdf   # .pptx 도 가능 (부명령 없이 파일만 줘도 됨)

# 그림 한 장 + 이름 - LLM 이 방금 그려준 이미지를 바로 검사합니다
python -m chemcheck check --name "카페인" 그림.png --out out/
```

구조 인식기(OCSR)를 설치하면 그림 판정이 켜집니다. 없으면 이름 추출까지만 동작하고 그림은 전부 판정 불가로 보고합니다.

### 인식기 두 개를 함께 세웁니다

합의가 깨지면 판정을 보류하므로, 인식기가 둘일 때 비로소 합의 게이트가 제 일을 합니다. 게다가 둘은 서로의 결핍을 메웁니다 — MolScribe는 신뢰도 점수를 주고, DECIMER는 주지 않습니다.

```bash
bash scripts/fetch_decimer.sh   # DECIMER 가중치 (약 600MB)
bash scripts/setup_ocsr.sh      # MolScribe 환경 + 체크포인트 (1.13GB)
```

둘은 **한 프로세스에 같이 올릴 수 없습니다.** MolScribe 1.1.1은 `torch<2.0`에 고정돼 있고 torch 1.x는 Python 3.11 이후 휠이 없는 반면, DECIMER는 Python 3.13/TensorFlow에서 돕니다. 그래서 MolScribe는 `.venv310`에 따로 두고 `scripts/ocsr_worker.py`를 통해 프로세스 너머로 부릅니다. 워커는 한 번만 띄웁니다 — 이미지마다 새로 띄우면 1.13GB 체크포인트를 매번 읽게 됩니다.

한쪽만 설치해도 동작합니다. 없는 인식기는 없다고 보고할 뿐입니다. **모든 예측의 신뢰도가 문턱(0.80) 밑이면** 이름 대조까지 가지 않고 판정 불가로 접습니다 - 아무도 자신 없을 때뿐이고, 하나라도 문턱을 넘으면 이름 대조로 갑니다. 합성 렌더 50장을 새 규칙(결정 6·8)으로 재채점(`python -m bench.rescore_name_first`, 엔진 재실행 없음)한 결과 정확도 90%(45/50)·물러남 10%(5/50)·자신 있게 틀림 0%(0/50) - 옛 합의 게이트(정확도 88%·물러남 12%) 대비 물러난 6건 중 4건이 이름 대조로 회수됐고, 나머지 2건(과 새로 물러난 3건)은 신뢰도 문턱에 걸렸습니다.

## 데모 자료 만들기

```bash
python scripts/make_demo.py     # 의도적으로 틀린 구조가 든 4장짜리 pptx
python tests/test_pipeline.py   # 심어둔 오류를 잡는지 검증
```

2장은 아스피린이라 써놓고 살리실산을 그립니다. 아세틸기 하나 차이라 눈으로는 거의 구분되지 않습니다.

### 데모 실측 (2026-09-09, `recognize` 로 4장, `draw` 와 대조)

| 장 | 써놓은 이름 | `draw` 이름의 골격 | `recognize` 합의 골격 (MolScribe 신뢰도) | 판정 |
|---|---|---|---|---|
| 1 | Aspirin | BSYNRYMUTXBXSQ | BSYNRYMUTXBXSQ (0.887) | 같다 |
| 2 | Aspirin | BSYNRYMUTXBXSQ | **YGSDEFSMJLZEOE** (0.857) = 살리실산 | **다르다** - 심어둔 오류 |
| 3 | Caffeine | RYYVLZVUVIJVGH | RYYVLZVUVIJVGH (0.882) | 같다 |
| 4 | Caffeine | RYYVLZVUVIJVGH | **YAPQBXQYLJRXSA** (0.902) = 메틸기 하나 빠짐 | **다르다** - 심어둔 오류 |

4장 모두 두 인식기(MolScribe·DECIMER)가 골격에 합의했고, 심어둔 오류 2건은 이름의 골격과 다른 키로 나왔습니다. 골격이 같은지는 InChIKey 앞 14자 비교뿐이고, 마지막 판정은 `--out` 에 남는 왕복 그림을 사람이 봅니다. 두 엔진 적재 약 160초, 장당 30~100초 (16GB 기계, CPU).

## 구조

| 파일 | 역할 |
|---|---|
| `keys.py` | InChIKey 생성·비교. 추론 없음 |
| `names.py` | 텍스트에서 이름 후보 추출 + PubChem 조회 |
| `ocsr.py` | 인식기 플러그인 층. 없으면 없다고 보고 |
| `verdict.py` | 판정과 보류 규칙 |
| `extract.py` | PDF/PPTX에서 장별 텍스트·그림 추출 |
| `pipeline.py` | 장 단위로 이름과 그림을 짝지어 판정 |
