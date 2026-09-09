# server/ — DECIMER on Modal (S 트랙)

정적 페이지(GitHub Pages)가 부르는 HTTPS 엔드포인트. 계약은 `docs/WEB_CONTRACT.md`.

## 파일

| 파일 | 하는 일 |
|---|---|
| `judge.py` | 판정. **추론 0** - 골격 14자 문자열 비교와 중원자 세기뿐. 모델·네트워크 없음 |
| `modal_app.py` | Modal 배포. 이미지 정의, 가중치 굽기, `/api/check` ASGI 앱 |
| `test_judge.py` | 엔진 없이 도는 판정 검증 (`python server/test_judge.py`) |

## 배포

```bash
pip install modal
modal setup                      # ← 브라우저를 여는 대화형 인증. 사람이 직접 해야 한다
modal deploy server/modal_app.py # 저장소 루트에서
```

첫 배포는 이미지 빌드(TensorFlow + DECIMER 가중치 ~600MB 굽기) 때문에 10~20분 걸린다.

### 심사 시간대 설정

```bash
CHEMCHECK_MIN_CONTAINERS=1 modal deploy server/modal_app.py   # 한 대 상주 (콜드스타트 0)
modal deploy server/modal_app.py                              # 심사 끝나면 0 으로 되돌린다
```

`min_containers=1` 은 CPU 2코어·4GB 기준 대략 시간당 $0.13, 하루 $3 이다. 켜 두고 잊으면
$30 크레딧이 열흘에 사라진다. **심사가 끝나면 반드시 0 으로 되돌린다.**

기본값(`min_containers=0`, `scaledown_window=900`)은 유휴 과금이 없고, 첫 요청 뒤 15분간
따뜻하다. 심사 직전에 `/api/health` 를 한 번 때려 깨워 두는 것으로도 충분하다.

`CHEMCHECK_SNAPSHOT=1` 을 주면 Modal 메모리 스냅샷을 켠다. 콜드스타트가 크게 주는 대신
TensorFlow 가 스냅샷을 타는지는 확인이 필요하다. 기본은 꺼져 있다.

## 계약

엔드포인트 주소는 `label="chemcheck"` 로 못 박아 두었다. 배포하면 언제나

```
https://<workspace>--chemcheck.modal.run/api/check    POST  multipart: image, name
https://<workspace>--chemcheck.modal.run/api/health   GET   -> {"ok":true,...}
```

`<workspace>` 는 Modal 계정 이름이다. 클래스·메서드 이름을 바꿔도 이 주소는 흔들리지 않는다.

CORS 는 `Access-Control-Allow-Origin: *` 이고 OPTIONS 프리플라이트는 FastAPI 의
`CORSMiddleware` 가 받는다.

`grade` 는 언제나 `"weak"` 다 — 인식기가 DECIMER 하나뿐이다. MolScribe 는
torch<2.0 + py3.10 이 필요해 이 배포에 올리지 않았다. 두 번째 정보원은 **이름**이다.

## 주의

**슬라이드를 통째로 먹이지 않는다.** 실측에서 탄소 100개짜리 폴리인이 나왔다.
구조 영역만 잘라 보내야 한다 — 크롭은 프런트(C 트랙 `web/crop.js`)가 맡는다.

## 콜드스타트 — 실측과 대응 (2026-09-10)

    따뜻할 때   0.7 초 (health) / 2.3 초 (check)
    콜드스타트  약 55 초

가중치를 이미지에 구워도 55 초다. 내려받기가 아니라 **TensorFlow import + 모델 적재**가
지배적이기 때문이다. 굽기는 콜드스타트를 없애지는 못하지만 zenodo 가 죽어도 컨테이너가
뜬다는 보장을 준다 (빌드에서 두 번 끊겼던 그 문제).

### 대응 둘 — 같이 쓴다

**1. 페이지가 열릴 때 `/api/health` 를 한 번 부른다 (무료, 기본)**

사용자가 이미지를 붙여넣고 이름을 고치는 동안 컨테이너가 뜬다. 제출할 때쯤이면 따뜻하다.
크레딧을 쓰지 않는다.

**2. 심사 시간대에만 컨테이너를 상주시킨다 (크레딧 소모)**

```bash
CHEMCHECK_MIN_CONTAINERS=1 modal deploy server/modal_app.py    # 심사 직전
CHEMCHECK_MIN_CONTAINERS=0 modal deploy server/modal_app.py    # 끝나면 되돌린다
```

상주시키면 콜드스타트가 0 이 된다. 유휴 과금이 생기므로 **끝나면 반드시 0 으로 되돌린다.**

### 배포 시 주의 — 배포만으로는 컨테이너가 안 바뀐다

새 코드를 올려도 옛 컨테이너가 계속 서빙한다. 확실히 바꾸려면:

```bash
modal app stop -y <app-id> && modal deploy server/modal_app.py
```

어느 코드가 살아 있는지는 `/api/health` 의 `build` 필드로 확인한다.

### 윈도우에서 배포할 때

```bash
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 TERM=dumb modal deploy server/modal_app.py
```

없으면 진행바의 유니코드 문자가 cp949 에서 터져 배포가 중단된다.

### 한글 이름

서버는 UTF-8 을 정확히 처리한다(`name=카페인` → 정본 `RYYVLZVUVIJVGH`). 브라우저 `FormData`
는 UTF-8 이라 문제없다. **윈도우 curl 로 시험할 때만** 명령줄 인자가 cp949 로 바뀌므로
파일에서 읽어야 한다: `-F "name=<name.utf8.txt"`.
