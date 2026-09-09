// web/config.js (U 소유) - 서버 주소는 여기 한 곳에만 둔다.
// 오늘만 서버가 두 번 바뀌었다(Modal -> Cloud Run). 바꿀 때 이 줄 하나만 고친다.
// index.html 의 API 예시와 try.html 의 app.js 가 같은 값을 읽는다.
// 2026-09-10 04:0x - Modal 로 되돌린다. Cloud Run 은 DECIMER 하나뿐이라
// grade 가 항상 weak 이고 합의 게이트가 잠들어 있다. Modal 은 두 엔진이
// 붙어 있어 둘이 골격에 합의하면 strong, 갈리면 판정하지 않는다.
// (Cloud Run 에 MolScribe 를 올리면 다시 이 줄만 바꾸면 된다 - 서울 리전이라 더 빠르다)
window.CHEMCHECK_SERVER = "https://chemcheck-361680004842.asia-northeast3.run.app";
