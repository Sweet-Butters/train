# chemcheck

**지금 범위 (2026-09-09, `docs/DIRECTION.md` 결정 2) - 양방향 구조 도구.** LLM 은 구조 그림을 못 읽고, 못 그리고, 이상한 SMILES 를 줍니다. 그 대체재 둘입니다.
`python -m chemcheck recognize 그림.png` - 그림 → SMILES. 인식기 둘(MolScribe·DECIMER)이 InChIKey 골격에서 합의해야 답이고, 갈리면 '확신 없음' 과 후보 전부를 RDKit 으로 다시 그려 보여줍니다.
`python -m chemcheck draw "아스피린"` - 이름 → 구조. PubChem 정본 SMILES 를 RDKit 으로 그립니다. 추론이 없어 틀릴 수 없고, 못 찾으면 못 찾았다고 말합니다. (PDF/PPTX 추출·분할기·실측 벤치는 오늘 범위 밖 - 아래는 그 이전 내용입니다.)

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

| 상황 | 결과 |
|---|---|
| 이름과 그림의 InChIKey 일치 | 일치 |
| 골격(앞 14자)이 다름 | **오류** |
| 골격은 같고 입체화학만 다름 | 주의 (사람이 확인) |
| 이름을 못 찾음 / 인식기 없음 / 신뢰도 < 0.80 / 인식기 간 불일치 | **판정 불가** |

여러 인식기가 서로 다른 답을 내면 판정하지 않습니다.
[VERDICT](https://arxiv.org/html/2608.22183)가 왕복 검증보다 다중 엔진 합의가 낫다고 보고한 방식입니다.

## 사용

```bash
bash scripts/setup_lite.sh      # 인식기 없이 파이프라인만
python -m chemcheck check 슬라이드.pdf   # .pptx 도 가능 (부명령 없이 파일만 줘도 됨)
```

구조 인식기(OCSR)를 설치하면 그림 판정이 켜집니다. 없으면 이름 추출까지만 동작하고 그림은 전부 판정 불가로 보고합니다.

### 인식기 두 개를 함께 세웁니다

합의가 깨지면 판정을 보류하므로, 인식기가 둘일 때 비로소 합의 게이트가 제 일을 합니다. 게다가 둘은 서로의 결핍을 메웁니다 — MolScribe는 신뢰도 점수를 주고, DECIMER는 주지 않습니다.

```bash
bash scripts/fetch_decimer.sh   # DECIMER 가중치 (약 600MB)
bash scripts/setup_ocsr.sh      # MolScribe 환경 + 체크포인트 (1.13GB)
```

둘은 **한 프로세스에 같이 올릴 수 없습니다.** MolScribe 1.1.1은 `torch<2.0`에 고정돼 있고 torch 1.x는 Python 3.11 이후 휠이 없는 반면, DECIMER는 Python 3.13/TensorFlow에서 돕니다. 그래서 MolScribe는 `.venv310`에 따로 두고 `scripts/ocsr_worker.py`를 통해 프로세스 너머로 부릅니다. 워커는 한 번만 띄웁니다 — 이미지마다 새로 띄우면 1.13GB 체크포인트를 매번 읽게 됩니다.

한쪽만 설치해도 동작합니다. 없는 인식기는 없다고 보고할 뿐입니다.

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
