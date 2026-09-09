# tests/ 소유권 — 코디네이터 결정 요청 (D 제기)

## 사실

계약상 `tests/`는 D 배타 소유다. 그런데 A가 `tests/test_pipeline.py`에 커밋했다
(`28ce62f`, +31줄, `test_self_consistency_only_when_decimer_is_alone`).

**테스트 자체는 타당하다.** 3배 비용을 그것 말고 방법이 없을 때만 치른다는 것을
잡는 좋은 테스트고, D는 여기에 이견이 없다. 문제는 내용이 아니라 자리다.

## 왜 지금 정해야 하는가

지금은 충돌이 없다. D가 그 파일을 피했기 때문이다 — 계약이 지켜져서가 아니라
D가 우회한 결과다. `git merge-tree` 로 확인한 현재 상태:

    D  : bench/, chemcheck/cli.py, scripts/make_demo.py, tests/test_{cli_summary,score}.py
    A  : chemcheck/ocsr.py, tests/test_pipeline.py
    → 겹침 0, 병합 clean

A가 엔진 테스트를 계속 그 파일에 쌓고 D도 언젠가 파이프라인 계약을 손대면
반드시 부딪힌다. 그때는 두 트랙의 테스트가 한 파일에서 섞여 있어 비싸다.

## D의 제안

**`tests/` 안에서 파일 단위로 소유를 가른다.**

| 파일 | 소유 | 성격 |
|---|---|---|
| `tests/test_pipeline.py` | D | 심어둔 오류를 잡는가 = 파이프라인 전체의 계약 |
| `tests/test_engines.py` (신규) | A | 인식기 구성·적재·프로토콜 |
| `tests/test_cli_summary.py` | D | 요약 출력의 원칙 |
| `tests/test_score.py` | D | 채점기 |
| `tests/test_names.py` | C | 이름 해석 (이미 C 브랜치에 있음) |

즉 `tests/`는 D가 통째로 갖는 대신 **디렉터리를 공유하되 파일은 배타 소유**로
바꾼다. 계약의 "한 파일은 한 트랙이 통째로 갖는다"는 그대로 유지된다.

A가 옮길 대상은 `test_self_consistency_only_when_decimer_is_alone` 하나,
그리고 성격상 `test_broken_engine_does_not_crash` 와
`test_subprocess_bridge_speaks_the_protocol` 도 함께 가는 것이 자연스럽다.

## 대안

`test_pipeline.py` 를 통째로 A에게 넘긴다. D는 자기 테스트만 소유.
D는 이쪽을 권하지 않는다 — 첫 테스트(심어둔 오류 검출)는 D의 측정이 기대는
바닥이라 남의 파일에 두면 D가 못 지킨다.

## 겸사겸사

`docs/CONTRACT.md` 가 베이스 브랜치(`Sweet-Butters/chemcheck`)에 없다.
C 브랜치(`8721ce0`)에만 있다. 모든 트랙이 지켜야 할 문서가 한 트랙 안에만
있는 상태다. 베이스로 올리는 편이 낫다.
