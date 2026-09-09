# 웹 제품 병렬 작업 계약 (해커톤)

한 화면. 이미지를 붙여넣거나 이름을 치면, **정본은 항상 나오고** 그림을 읽을 수 있으면 대조까지 한다.
화면이 비는 경우가 없어야 한다 - 그것이 이 설계의 원칙이다.

    붙여넣기 ─┬─ [즉시·브라우저] 이름 -> 정본 그림·SMILES·InChIKey   (실패하지 않음)
              └─ [~20초·서버]    그림 -> 읽은 구조 -> 대조 -> 판정   (실패해도 위는 살아 있음)

## 파일 소유 — 배타적. 남의 파일은 열지 않는다

| 트랙 | 소유 파일 | 무거운 환경 |
|---|---|---|
| **U** 화면 | `web/index.html`, `web/app.js`, `web/style.css` | 없음 |
| **C** 크롭 | `web/crop.js` | 없음 |
| **O** OCR | `web/ocr.js` | 없음 |
| **S** 서버 | `server/` 전부 | 있음 (엔진 3GB) |
| 손대지 않음 | `web/build_table.py`, `web/compounds.js` (B 가 구움), `chemcheck/**` | |

## 동결 인터페이스 — 커밋 뒤 바꾸지 않는다. 바꿔야 하면 코디네이터에게 올린다

```js
// web/crop.js  (C 소유)
export function mountCropper(container, { onCrop });
//   container: HTMLElement.  붙여넣기(Ctrl+V)·드래그앤드롭·파일선택을 모두 받는다.
//   onCrop(blob, meta): 사용자가 영역을 확정할 때마다 호출. meta = {full: Blob} 원본도 준다.
//   크롭을 건너뛰면 원본 전체를 그대로 onCrop 에 준다.

// web/ocr.js  (O 소유)
export async function readLabels(blob);
//   -> { names: string[], formula: string|null, smiles: string|null }
//   names 는 영문 우선, 없으면 한글. 못 찾으면 빈 배열. 절대 예외를 던지지 않는다.
```

```
POST /api/check          (S 소유)   multipart/form-data: image, name
-> 200 application/json
{
  "verdict": "match" | "mismatch" | "unreadable",
  "grade":   "strong" | "weak" | null,        // 인식기 둘 합의=strong, 하나뿐=weak
  "reference": {"name","smiles","inchikey","formula"} | null,
  "read":      {"smiles","inchikey","heavy_formula","engines":[{"engine","smiles","inchikey","confidence"}]},
  "reasons":   ["골격이 다릅니다", "중원자 조성 C9 vs C8"]
}
```
`verdict` 는 **둘**이다(match/mismatch). 읽지 못한 것은 판정이 아니라 `unreadable` 이고,
그때도 화면은 정본을 보여준다. 확신의 정도는 `grade` 로만 표현한다.

## 화면 원칙 (U)

1. **점진적 렌더.** 정본 카드는 0.5 초 안에 뜬다. 서버 결과는 나중에 채운다. 스피너로 화면을 덮지 않는다.
2. **복사 버튼은 정본에만 둔다.** 사용자가 올린 그림 쪽에는 복사를 두지 않는다 -
   틀린 구조가 슬라이드로 나가는 통로를 우리가 열면 안 된다. 정본 카드에는 출처(PubChem CID)를 박는다.
3. 정본 카드의 복사 대상 넷: **PNG**(클립보드 이미지) · **SVG**(다운로드) · **SMILES**(텍스트) · **InChIKey**(텍스트).
   SMILES 는 **RDKit canonical 로 통일**한다 - PubChem 원본과 다를 수 있으므로 한쪽으로 고정한다.
4. `unreadable` 이어도 빈 화면이 아니다: "그림을 확실히 읽지 못했습니다. 요청하신 구조는 이것입니다 - 나란히 놓고 보십시오."
5. 이름을 못 찾으면 `compounds.js` 423 개에서 **편집거리 근접 제안**("Coffeine → Caffeine 을 찾으시나요?").
   **자동 선택 금지** - 사용자가 클릭해야 한다. 이건 입력 보정이지 판정이 아니다.

## 오늘 범위 밖 — 건드리지 않는다

입체화학 판정 · 세 번째 엔진 · 자동 분할기 · guard()/MCP · 신규 분자 변환 · 90 장 코퍼스.

## 근거 (docs/DIRECTION.md 실측 6·7)

- 슬라이드를 통째로 인식기에 먹이면 쓰레기가 나온다(탄소 100 개 폴리인, 신뢰도 0.793).
  그래서 **크롭은 오류 경로가 아니라 정상 흐름**이다.
- 이미지 안 텍스트는 6/6 정확했고 그림은 4 장 중 1 장만 정확했다.
  그래서 **텍스트가 정답이고 그림이 용의자**다.
