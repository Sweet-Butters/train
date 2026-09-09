# Cloud Run 배포 — 심사위원이 계속 눌러도 되는 판

Modal 판(`modal_app.py`)은 서버리스라 유휴 과금이 없는 대신 **콜드스타트가 55초**다.
심사위원이 반복해서 누르는 상황에는 Cloud Run 을 `--min-instances=1` 로 두는 편이 맞다 -
컨테이너가 항상 살아 있어 **콜드스타트가 0** 이고, 무료 체험 크레딧($300/90일) 안에서 돈다.

같은 계약(`docs/WEB_CONTRACT.md`)을 제공하므로 웹 화면은 URL 만 바꾸면 된다.

    GET  /api/health   {"ok","engine","build","ready","error"}
    POST /api/check    multipart(image, name)

## 준비 — 사람이 해야 하는 부분

1. **gcloud CLI 설치**: https://cloud.google.com/sdk/docs/install (Windows 설치 관리자)
2. **로그인과 프로젝트** (브라우저가 열린다):

```bash
gcloud auth login
gcloud projects create chemcheck-demo --name="chemcheck"   # 이름은 전역 고유여야 한다
gcloud config set project chemcheck-demo
```

3. **결제 계정 연결** — 무료 체험이어도 필요하다.
   https://console.cloud.google.com/billing 에서 체험을 활성화하고 프로젝트에 연결한다.

4. **API 켜기**:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## 배포 — 로컬 Docker 가 필요 없다

Cloud Build 가 대신 굽는다. 저장소 루트(이 파일의 상위)에서:

```bash
gcloud run deploy chemcheck \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --min-instances 1 \
  --max-instances 3 \
  --concurrency 4 \
  --timeout 300
```

- `--memory 4Gi` : TensorFlow + DECIMER 가 2.2GB 를 쓴다. 4Gi 가 안전한 하한이다.
- `--min-instances 1` : **콜드스타트 0.** 심사 끝나면 `--min-instances 0` 으로 다시 배포해 과금을 멈춘다.
- `--concurrency 4` : 한 컨테이너가 동시에 받는 요청 수. 모델이 하나라 크게 잡지 않는다.
- 첫 빌드는 TensorFlow 설치 + 가중치 299MB 굽기라 **10~20분** 걸린다. 이후는 캐시로 빨라진다.

## 배포 뒤 확인

```bash
URL=$(gcloud run services describe chemcheck --region asia-northeast3 --format='value(status.url)')
curl -s "$URL/api/health"          # {"ok":true,...,"ready":true}
curl -s -X POST "$URL/api/check" \
  -F "image=@web/evidence/caffeine_gemini.jpeg" \
  -F "name=<name.utf8.txt"          # 윈도우에서는 한글을 파일로 넘긴다
```

기대값(오늘 실측): `verdict=mismatch`, `reference.inchikey=RYYVLZVUVIJVGH`,
`read.inchikey=CSXLFNRQOLIQAN`, `read.heavy_formula=C9N4O2`.

## 화면 배선

`web/app.js` 의 엔드포인트를 Cloud Run URL 로 바꾼다. CORS 는 열려 있다(`allow_origins=["*"]`).
Modal 판은 그대로 두어도 된다 - 둘 중 하나가 죽어도 다른 쪽으로 넘길 수 있다.

## 주의

- **끝나면 `--min-instances 0`.** 상주는 크레딧을 계속 먹는다.
- 프로젝트 이름은 전역 고유다. `chemcheck-demo` 가 이미 있으면 뒤에 숫자를 붙인다.
- 리전은 `asia-northeast3`(서울)이 지연이 가장 낮다.

## 미해결 — 2026-09-10 05:00, 아침에 이어서

`chemcheck-00003-vul` (build `cloudrun-3-two-engines`) 은 **빌드에 성공했지만
DECIMER 가 안 올라온다.** 트래픽은 0% 로 두었고 라이브는 옛 리비전 그대로다.

```
/api/health  ->  {"engines":["molscribe"], "build":"cloudrun-3-two-engines"}
런타임 로그  ->  "Downloading trained model to /opt/decimer-data/DECIMER-V2"
                 "인식기 준비: ['molscribe']"
```

런타임에 가중치를 내려받으려 한다는 것은 **빌드 단계의 `python -m server.bake`
가 실패했다**는 뜻이다 (`|| true` 라 빌드는 통과했다). Modal 쪽도 같은 자리에서
두 번 끊겼고 재시도를 붙여 해결했다 (`server/modal_app.py` 의 `_fetch`).

아침에 볼 것, 순서대로:
1. Cloud Build 로그에서 `BAKE:` 줄을 찾아 실패 사유 확인
2. `server/bake.py` 의 재시도가 modal_app.py 수준인지 (zenodo 는 큰 파일에서 자주 끊긴다)
3. 굽기가 계속 실패하면 Dockerfile 에서 `curl` 로 직접 받아 제자리에 두는 방식으로
   바꾼다 - MolScribe 체크포인트를 그렇게 받고 있고 그건 성공했다
4. 고친 뒤 `/api/health` 의 engines 가 둘인지 확인하고 나서 트래픽을 옮긴다:
   `gcloud run services update-traffic chemcheck --to-latest --region asia-northeast3`
5. 그다음 `web/config.js` 를 Cloud Run 으로 바꾼다

**그때까지 `web/config.js` 는 Modal 을 가리킨다** - 거기는 두 엔진이 확인됐다
(`build: two-engines-1`, `engines: ["decimer","molscribe"]`).

심사 끝나면 과금을 멈춘다:
```
gcloud run services update chemcheck --min-instances 0 --region asia-northeast3
```
