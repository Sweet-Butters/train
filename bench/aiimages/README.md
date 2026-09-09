# AI 생성 구조 그림 - 수집 지침

목표는 한 줄입니다: **"AI 에게 화학 구조를 그려달라고 하면 몇 %가 틀리는가."**

## 프롬프트 - 하나로 고정합니다

모델마다 문장을 바꾸면 비교가 안 됩니다. 아래를 그대로 씁니다.

```
<화합물명>의 화학 구조를 그려줘.
```

## 화합물 10개 (전부 내장 표에 있어 정본이 즉시 나옵니다)

| # | 이름 | 파일명에 쓸 것 | 난이도 |
|---|---|---|---|
| 1 | 에탄올 | `ethanol` | 쉬움 |
| 2 | 아세트산 | `acetic_acid` | 쉬움 |
| 3 | 톨루엔 | `toluene` | 쉬움 |
| 4 | 페놀 | `phenol` | 쉬움 |
| 5 | 아세톤 | `acetone` | 쉬움 |
| 6 | 카페인 | `caffeine` | 중간 |
| 7 | 아스피린 | `aspirin` | 중간 |
| 8 | 니코틴 | `nicotine` | 중간 |
| 9 | 글루코스 | `glucose` | 어려움 (입체) |
| 10 | 페니실린 G | `penicillin_g` | 어려움 |

## 모델 둘

`gemini`, `gpt` (또는 `claude`). 모델당 10장 = **총 20장**.

## 파일 이름 규칙

```
gemini__caffeine.png          모델이 준 그대로
gemini__caffeine__crop.png    구조 영역만 잘라낸 것
```

**크롭이 핵심입니다.** 슬라이드를 통째로 인식기에 먹이면 쓰레기가 나옵니다
(실측: 탄소 100개짜리 폴리인). 원본도 같이 넣으면 그 차이가 숫자로 나옵니다 -
사람 손은 크롭 한 번뿐이고 채점은 스크립트가 합니다.

## 채점

```
python bench/score_ai_images.py bench/aiimages
```

크롭본과 원본을 따로 집계합니다. 크롭본의 `mismatch` 비율이 우리가 주장할 숫자입니다.

---

## 실측 결과 (2026-09-10 새벽)

같은 프롬프트(`<화합물명>의 화학 구조를 그려줘`)로 Gemini·GPT 에게 10 개씩, 총 **20 장**.
구조 영역만 오려내(`*__crop.png`) 인식기에 넣고 `check --name` 규칙으로 판정했다.
원자료와 채점 결과가 이 폴더에 그대로 있다 - `scored.json` 이 판정 하나하나를 담는다.

    판정 불가   10 / 20      그림에서 구조를 확실히 읽지 못했다
    다름         6 / 20      이름의 정본과 골격이 다르다
    일치         4 / 20

**판정률 50%.** 절반을 못 읽었다는 사실을 먼저 말해야 한다 - 침묵하는 검사기는 오탐률
0% 짜리 완벽한 도구가 되고 동시에 쓸모없다(`bench/README.md` 의 원칙).

**판정한 10 장 중 6 장이 요청한 분자와 달랐다.**

| 모델 | 화합물 | 읽은 골격 | 정본 골격 | 등급 |
|---|---|---|---|---|
| gemini | acetone | IFVHXUDNYZLJED | CSCPPACGZOOCGX | weak |
| gemini | aspirin | YMHMQRQFHHILOO | BSYNRYMUTXBXSQ | weak |
| gemini | glucose | CDXKFLTVUHFKMC | WQZGKKKJIJFFOK | weak |
| gpt | aspirin | ZDUPYLSHMKCSJG | BSYNRYMUTXBXSQ | weak |
| gpt | caffeine | UUGMJQVJWVCSIT | RYYVLZVUVIJVGH | weak |
| gpt | nicotine | DIKDZTJPTFJYAI | SNICXCGAKADSCV | **strong** |

`strong` 은 인식기 둘(DECIMER·MolScribe)이 **독립적으로 같은 골격을 읽었다**는 뜻이다.
그 한 건은 인식기 오독으로 설명되지 않는다.

### 이 숫자로 말할 수 있는 것과 없는 것

- **말할 수 있다**: 이 20 장에서, 판정된 것의 다수가 요청한 분자가 아니었다.
- **말할 수 없다**: "AI 가 화학 구조를 N% 틀린다". 표본이 20 장이고 모델이 둘이며
  프롬프트가 하나다. 그리고 크롭 품질이 판정 불가 10 건에 섞여 있다.
- **재현 방법**: 이 폴더의 `*__crop.png` 를 `python -m chemcheck check --name <이름> <파일>`
  로 돌리면 같은 판정이 나온다. 인식기가 둘 다 설치돼 있어야 `strong` 이 재현된다.
