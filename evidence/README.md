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

## 처음 보는 분자 (crops/novel_a·b·c.png)

PubChem InChIKey 조회에서 **셋 다 404(미등재)**이고, 내장 표 423개에도 없다.
그림에서만 읽을 수 있는 분자다 - 표 조회로는 답이 나오지 않는다.

| | 정답 InChIKey (그린 SMILES 에서 계산) | 배포된 서버가 읽은 것 | 신뢰도 |
|---|---|---|---|
| novel_a | CLKVWDQPINYNBP-UHFFFAOYSA-N | 동일 (전체 일치) | 0.996 |
| novel_b | OXZHGZQHCDMXCG-SFHVURJKSA-N | 동일 (입체까지 일치) | 0.998 |
| novel_c | XCCISAURMADZTN-UHFFFAOYSA-N | 동일 (전체 일치) | 0.998 |

**한계를 함께 적는다.** 이 셋은 **RDKit 이 그린 그림**이고, 두 인식기는 그런 렌더로
학습됐다. 그러므로 3/3 이 증명하는 것은 "신규 분자를 읽는다"가 아니라
**"익숙한 렌더 스타일에서 신규 분자를 읽는다"**다. 실제 스캔·손그림·ChemDraw
내보내기에서의 성능은 아직 재지 않았다 (선행연구 VERDICT: 단일 인식기가 렌더 94%
→ 실제 문서 23.1%). 신뢰도 0.99대도 근거로 쓰지 않는다 - 오늘 실측에서 신뢰도는
정확도가 아니라 렌더 친숙도를 재는 것으로 보였다(쓰레기 구조에 0.793).
