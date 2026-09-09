// web/api.js (U 소유) - api.html 개발자 콘솔.
// 가짜 화면이 아니다: '지금 바로 시험' 은 브라우저가 실제로 라이브 서버를 부르고 응답을 그대로 보여준다.
// 키는 브라우저에서 만들어 localStorage 에 두고 X-API-Key 헤더로 보낸다. 서버가 응답에 키를 되돌려주면 화면이 그것을 표시한다.
// 쿼터·과금은 걸려 있지 않으므로 그렇게 말하지 않는다.

const SERVER = window.CHEMCHECK_SERVER || "";
const KEY_STORE = "chemcheck.apikey";
const SAMPLE_IMAGE = "evidence/crops/caffeine_gemini_crop.png";
const $ = (id) => document.getElementById(id);
for (const el of document.querySelectorAll("[data-server-url]")) el.textContent = SERVER;

// ============================================================ 키
function loadKey() { try { return JSON.parse(localStorage.getItem(KEY_STORE) || "null"); } catch (e) { return null; } }
function saveKey(k) { try { localStorage.setItem(KEY_STORE, JSON.stringify(k)); } catch (e) { /* 저장이 막힌 브라우저 */ } }
function makeKey() {
  const bytes = new Uint8Array(20); crypto.getRandomValues(bytes);
  return "cc_live_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
function currentKey() { const k = loadKey(); return k && k.key; }
function renderKey() {
  const k = loadKey();
  $("keyBox").hidden = !k;
  if (k) {
    $("keyValue").textContent = k.key;
    $("keyIssued").textContent = `${k.email} · ${new Date(k.issued).toLocaleString("ko-KR")} 발급`;
    $("keyEmail").value = k.email; if (k.purpose) $("keyPurpose").value = k.purpose;
  }
  renderCode();
}
$("keyForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const email = $("keyEmail").value.trim(); if (!email) return;
  saveKey({ key: makeKey(), email, purpose: $("keyPurpose").value, issued: Date.now() });
  $("keyNote").textContent = "";
  renderKey();
  $("keyBox").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("keyCopy").addEventListener("click", () => copyText(currentKey(), (m) => { $("keyIssued").textContent = m; }));
$("keyRevoke").addEventListener("click", () => { try { localStorage.removeItem(KEY_STORE); } catch (e) { /* */ } renderKey(); });

async function copyText(text, say) {
  if (!text) return;
  try { await navigator.clipboard.writeText(text); say("복사했습니다."); }
  catch (e) { say("복사가 막혔습니다. 직접 선택해 복사하세요."); }
}

// ============================================================ 지금 바로 시험 - 실제 호출
function headers() { const k = currentKey(); return k ? { "X-API-Key": k } : {}; }
async function call(method, path, body, describe) {
  const out = $("tryOut"), meta = $("tryMeta");
  out.textContent = "호출 중…"; meta.textContent = "";
  const t0 = performance.now();
  try {
    const res = await fetch(SERVER + path, { method, body, headers: headers() });
    const text = await res.text();
    let pretty = text; try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* JSON 이 아니면 그대로 */ }
    out.textContent = pretty;
    const ms = Math.round(performance.now() - t0);
    meta.innerHTML = "";
    meta.append(span(`${describe} → ${res.status}`), span(`${ms} ms`));
    const k = currentKey();
    if (k) {
      const echoedInHeader = [...res.headers.entries()].some(([, v]) => v.includes(k));
      const echoed = echoedInHeader || text.includes(k);
      meta.append(echoed ? span("서버가 X-API-Key 를 확인했습니다", "badge badge-ok") : span("X-API-Key 헤더로 보냈습니다"));
    }
  } catch (e) {
    out.textContent = "서버에 연결하지 못했습니다. " + (e && e.message ? e.message : e);
  }
}
function span(text, cls) { const s = document.createElement("span"); if (cls) s.className = cls; s.textContent = text; return s; }
function checkWith(blob, filename) {
  const fd = new FormData(); fd.append("image", blob, filename || "image.png"); fd.append("name", $("tryName").value.trim());
  return call("POST", "/api/check", fd, `POST /api/check (${filename || "image"}, name="${$("tryName").value.trim()}")`);
}
$("tryHealth").addEventListener("click", () => call("GET", "/api/health", undefined, "GET /api/health"));
$("tryCheck").addEventListener("click", async () => {
  let blob;
  try { const r = await fetch(SAMPLE_IMAGE); if (!r.ok) throw new Error(r.status); blob = await r.blob(); }
  catch (e) { $("tryOut").textContent = "예시 이미지를 불러오지 못했습니다. '내 이미지로' 를 쓰세요."; return; }
  checkWith(blob, "caffeine_gemini_crop.png");
});
$("tryFileBtn").addEventListener("click", () => $("tryFile").click());
$("tryFile").addEventListener("change", () => { const f = $("tryFile").files && $("tryFile").files[0]; if (f) checkWith(f, f.name); $("tryFile").value = ""; });

// ============================================================ 코드 예시 - 키가 있으면 박힌다
let lang = "curl";
const SNIPPETS = {
  curl: (k) => `curl -X POST ${SERVER}/api/check \\
${k ? `  -H "X-API-Key: ${k}" \\\n` : ""}  -F "image=@structure.png" \\
  -F "name=Caffeine"`,
  python: (k) => `import requests

r = requests.post(
    "${SERVER}/api/check",
    files={"image": open("structure.png", "rb")},
    data={"name": "Caffeine"},${k ? `\n    headers={"X-API-Key": "${k}"},` : ""}
    timeout=90,
)
j = r.json()
print(j["verdict"], j["grade"])      # "mismatch" "weak"
print(j["reference"]["inchikey"])   # 정본
print(j["read"]["inchikey"])        # 그림에서 읽은 것`,
  js: (k) => `const fd = new FormData();
fd.append("image", file);          // File 또는 Blob
fd.append("name", "Caffeine");     // 비우면 그림만 읽는다

const res = await fetch("${SERVER}/api/check", {
  method: "POST",
  body: fd,${k ? `\n  headers: { "X-API-Key": "${k}" },` : ""}
});
const j = await res.json();
console.log(j.verdict, j.grade, j.reasons[0]);`,
};
function renderCode() { $("codeOut").textContent = SNIPPETS[lang](currentKey()); }
for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => {
  lang = t.dataset.lang;
  for (const u of document.querySelectorAll(".tab")) u.classList.toggle("is-on", u === t);
  renderCode();
});
$("codeCopy").addEventListener("click", () => copyText($("codeOut").textContent, (m) => { $("codeNote").textContent = m; setTimeout(() => { $("codeNote").textContent = ""; }, 3000); }));

// ============================================================ 문의 - 메일 앱으로
$("contactForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const subject = encodeURIComponent(`[chemcheck] Enterprise 문의 - ${$("contactOrg").value.trim() || $("contactEmail").value.trim()}`);
  const body = encodeURIComponent(`이메일: ${$("contactEmail").value.trim()}\n조직: ${$("contactOrg").value.trim()}\n\n${$("contactBody").value.trim()}`);
  location.href = `mailto:pxh7yp@yonsei.ac.kr?subject=${subject}&body=${body}`;
});

renderKey();
if (location.hash === "#key") $("keyEmail").focus();
