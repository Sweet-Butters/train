# 실측 자료 — AI 가 생성한 화학 구조 이미지 (2026-09-09)

사용자가 Gemini Pro · ChatGPT 에게 직접 요청해 받은 것이다. 우리가 만들거나 손댄 것이 아니다.

| 파일 | 모델 | 그림이 맞나 | 이미지 안 텍스트 |
|---|---|---|---|
| `caffeine_gemini.jpeg` | Gemini Pro | **틀림** — N9 에 메틸이 하나 더 있어 탄소가 9 개. 중성 분자로 존재할 수 없다 | 제목·화학식(C8H10N4O2)·IUPAC명 전부 정확 |
| `caffeine_gpt.png` | ChatGPT | 맞음 | 정확. **SMILES 까지 인쇄** (검증함: 정본과 InChIKey 일치) |
| `alanine_gemini.jpeg` | Gemini Pro | 미확인 (인식기가 주석선을 결합으로 읽어 실패) | 정확 |
| `alanine_gpt.png` | ChatGPT | 미확인 (두 인식기 모두 입체를 못 읽음) | 정확 |

**여섯 장 모두 텍스트는 정확했고, 그림은 넷 중 하나만 확실히 맞았다.**
`docs/DIRECTION.md` 의 실측 6·7 이 전문이다.

카페인 케이스가 이 프로젝트의 표본이다 — 그럴듯하고, 캡션도 맞고, **그림이 자기 캡션과도
모순된다.** 이름 정본 `RYYVLZVUVIJVGH` vs 그림에서 읽은 `CSXLFNRQOLIQAN`.
