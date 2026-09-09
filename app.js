// web/app.js (U 소유) - index.html 의 도구 로직.
//
// 입력은 칸 둘(텍스트·이미지). 모드 선택 UI 는 없다 - 채움 여부로 갈린다.
//   텍스트만        → 정본 카드 (RDKit 이 파싱하면 SMILES, 못 하면 이름. 규칙이지 추론이 아니다)
//   이미지만        → 그림에서 읽은 구조 (서버에 name="" 로 보낸다)
//   텍스트 + 이미지 → 대조 (정본 카드 + 같은 카드 안의 판정)
// 정직함은 문장이 아니라 동작으로: 못 읽으면 크롭 UI 를 띄우고, 엔진이 갈리면 둘을 나란히 놓는다.
// web/crop.js(C)·web/ocr.js(O) 는 있으면 쓰고 없으면(file://) 조용히 건너뛴다. 계약은 docs/WEB_CONTRACT.md.

const TABLE = window.CHEMCHECK_TABLE || {};
const ALIASES = window.CHEMCHECK_ALIASES || {};
const BY_KEY = {};
for (const [name, row] of Object.entries(TABLE)) if (row.inchikey) BY_KEY[row.inchikey] = { name, ...row };
const PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound";
const SERVER = window.CHEMCHECK_SERVER || ""; // web/config.js 한 곳에만 있다
const CHECK_URL = SERVER + "/api/check";
const HEALTH_URL = SERVER + "/api/health";
const CHECK_TIMEOUT_MS = 120000; // 콜드스타트 최대 60초 + 큰 이미지에 두 엔진

const $ = (id) => document.getElementById(id);
let RDKit = null;

// 바로 해보기 - web/evidence/crops 의 실측 7장. 살아 있는 서버가 매번 새로 계산한다.
const EXAMPLES = [
  { group: "AI 가 그린 그림", items: [
    { key: "caffeine_gemini", label: "Gemini 카페인", path: "evidence/crops/caffeine_gemini_crop.png", name: "Caffeine" },
    { key: "caffeine_gpt", label: "GPT 카페인", path: "evidence/crops/caffeine_gpt_crop.png", name: "Caffeine" },
    { key: "alanine_gemini", label: "Gemini 알라닌", path: "evidence/crops/alanine_gemini_crop.png", name: "Alanine" },
    { key: "alanine_gpt", label: "GPT 알라닌", path: "evidence/crops/alanine_gpt_crop.png", name: "Alanine" },
  ] },
  { group: "처음 보는 분자", items: [ // 이름칸을 비워 보낸다 - 그림에서 구조만 읽는다
    { key: "novel_a", label: "신규 A", path: "evidence/crops/novel_a.png", name: "" },
    { key: "novel_b", label: "신규 B", path: "evidence/crops/novel_b.png", name: "" },
    { key: "novel_c", label: "신규 C", path: "evidence/crops/novel_c.png", name: "" },
  ] },
];

// ============================================================ 상태 - 화면은 이 하나에서 그려진다
const state = {
  text: "",          // 입력칸 원문
  forceName: false,  // "SMILES 로 읽었습니다 → 이름으로 찾기" 를 눌렀다
  mol: null,         // 텍스트에서 만든 정본 { svg, smiles, inchikey, formula, cid, title }
  molFrom: null,     // "smiles" | "name"
  image: null,       // Blob
  imageVia: null,    // "cropper" | "basic" | "example" | "camera"
  pending: false,    // 서버 호출 중
  server: null,      // 마지막 /api/check 응답
  serverError: null, // 연결 실패 문장
  readMol: null,     // 이미지만 넣었을 때 그림에서 읽은 구조(정본과 같은 꼴)
  checkedName: null, // 마지막으로 서버에 보낸 이름
};

function status(t) { $("rdkitStatus").textContent = t; }
function fail(msg) { $("err").textContent = msg; }
function clearErr() { $("err").textContent = ""; }
function imageNote(msg) { $("imageNote").textContent = msg || ""; }

// ============================================================ 이름 정규화 - web/build_table.py 의 normalize_exact 와 같은 규칙
const DASH_RE = /[‐‑‒–—−]/g;
function normalizeExact(s) { return s.normalize("NFKC").trim().replace(DASH_RE, "-").split(/\s+/).join(" ").toLowerCase(); }
function normalizeLoose(s) { return normalizeExact(s).replace(/[\s-]+/g, ""); }

// ============================================================ 이름 → 정본 / InChIKey → 이름
async function resolveName(name) {
  const exact = normalizeExact(name);
  if (TABLE[exact]) return { ...TABLE[exact] };
  const aliasKey = ALIASES[normalizeLoose(name)];
  if (aliasKey && BY_KEY[aliasKey]) { const { name: _n, ...row } = BY_KEY[aliasKey]; return row; }
  const res = await fetch(`${PUBCHEM}/name/${encodeURIComponent(name.trim())}/property/SMILES,InChIKey,Title/JSON`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`PubChem 응답 ${res.status}`);
  const p = (await res.json()).PropertyTable.Properties[0];
  return { smiles: p.SMILES, inchikey: p.InChIKey, cid: p.CID, title: p.Title };
}
async function identifyByKey(inchikey) {
  if (BY_KEY[inchikey]) return { ...BY_KEY[inchikey] };
  try {
    const res = await fetch(`${PUBCHEM}/inchikey/${inchikey}/property/Title/JSON`);
    if (!res.ok) return null;
    const p = (await res.json()).PropertyTable.Properties[0];
    return { title: p.Title, cid: p.CID };
  } catch (e) { return null; }
}

// ============================================================ RDKit 계산 (그림·InChIKey·화학식)
const ELEMENTS = { 1: "H", 5: "B", 6: "C", 7: "N", 8: "O", 9: "F", 11: "Na", 12: "Mg", 14: "Si", 15: "P", 16: "S", 17: "Cl", 19: "K", 20: "Ca", 26: "Fe", 29: "Cu", 30: "Zn", 35: "Br", 53: "I", 78: "Pt" };
function hillString(c) {
  const order = c["C"] ? ["C"].concat(c["H"] ? ["H"] : [], Object.keys(c).filter((s) => s !== "C" && s !== "H").sort()) : Object.keys(c).sort();
  return order.map((s) => s + (c[s] > 1 ? c[s] : "")).join("");
}
function molecularFormula(smiles) {
  const mol = RDKit.get_mol(smiles);
  if (!mol || !mol.is_valid()) { if (mol) mol.delete(); return null; }
  try {
    mol.add_hs_in_place();
    const bySymbol = {};
    for (const m of JSON.parse(mol.get_json()).molecules) for (const a of m.atoms) {
      const sym = ELEMENTS[a.z === undefined ? 6 : a.z] || `#${a.z}`; // commonchem 은 탄소(z=6)를 생략한다
      bySymbol[sym] = (bySymbol[sym] || 0) + 1;
    }
    return hillString(bySymbol);
  } finally { mol.delete(); }
}
function describeFull(smiles) {
  const mol = RDKit.get_mol(smiles);
  if (!mol || !mol.is_valid()) { if (mol) mol.delete(); return null; }
  let base;
  try { base = { smiles: mol.get_smiles(), inchikey: RDKit.get_inchikey_for_inchi(mol.get_inchi()), svg: mol.get_svg(420, 360) }; }
  finally { mol.delete(); }
  base.formula = molecularFormula(base.smiles);
  return base;
}
function isSmiles(raw) {
  const probe = RDKit.get_mol(raw);
  const ok = !!(probe && probe.is_valid());
  if (probe) probe.delete();
  return ok;
}

// 규칙: RDKit 이 파싱하면 SMILES, 못 하면 이름. forceName 이면 이름으로만.
async function resolveText(raw, forceName) {
  if (!forceName && isSmiles(raw)) {
    const desc = describeFull(raw);
    const who = await identifyByKey(desc.inchikey);
    return { from: "smiles", mol: { ...desc, cid: who ? who.cid : null, title: who ? who.title : null } };
  }
  const byName = await resolveName(raw);
  if (!byName) return null;
  const desc = describeFull(byName.smiles);
  if (!desc) return null;
  return { from: "name", mol: { ...desc, cid: byName.cid, title: byName.title || raw } };
}

// ============================================================ 이름 근접 제안 (편집거리). 자동 선택은 하지 않는다.
const TITLE_LIST = [...new Set(Object.values(TABLE).map((r) => r.title).filter(Boolean))];
function levenshtein(a, b) {
  const dp = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let prev = dp[0]; dp[0] = i;
    for (let j = 1; j <= b.length; j++) { const t = dp[j]; dp[j] = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]); prev = t; }
  }
  return dp[b.length];
}
function showSuggestions(raw) {
  const q = normalizeLoose(raw); if (!q) return;
  const top = TITLE_LIST.map((t) => ({ t, d: levenshtein(q, normalizeLoose(t)) })).sort((a, b) => a.d - b.d)
    .filter((s) => s.d > 0 && s.d <= Math.max(3, Math.ceil(q.length * 0.5))).slice(0, 5);
  if (!top.length) return;
  const box = $("suggestions"); box.hidden = false; box.innerHTML = "";
  const pills = el("div", "pills");
  for (const { t } of top) { const b = el("button", null, t); b.type = "button"; b.addEventListener("click", () => { $("queryInput").value = t; onText(t); }); pills.appendChild(b); }
  box.append(el("p", null, "이 이름을 찾으시나요?"), pills);
}
function hideSuggestions() { const box = $("suggestions"); box.hidden = true; box.innerHTML = ""; }

// ============================================================ 텍스트 입력
async function onText(raw) {
  state.text = raw.trim(); state.forceName = false;
  await resolveAndRender();
}
// PubChem 조회가 섞이면 늦게 온 옛 응답이 새 결과를 덮어쓴다 - 순번으로 막는다.
let resolveSeq = 0;
async function resolveAndRender() {
  const seq = ++resolveSeq;
  hideSuggestions(); clearErr();
  if (!state.text) { state.mol = null; state.molFrom = null; render(); }
  else if (!RDKit) { status("구조 엔진을 불러오는 중…"); return; }
  else {
    try {
      const r = await resolveText(state.text, state.forceName);
      if (seq !== resolveSeq) return; // 그 사이 입력이 바뀌었다
      if (!r) { state.mol = null; state.molFrom = null; render(); showSuggestions(state.text); fail(`"${state.text}" 을(를) 찾지 못했습니다.`); }
      else { state.mol = r.mol; state.molFrom = r.from; render(); }
    } catch (e) {
      if (seq !== resolveSeq) return;
      state.mol = null; render(); fail("조회에 실패했습니다: " + (e && e.message ? e.message : e));
    }
  }
  // 이미지가 있고 보낼 이름이 바뀌었으면 대조를 다시 돈다
  if (state.image && !state.pending && nameForServer() !== state.checkedName) checkServer();
}
function nameForServer() { return state.mol ? (state.mol.title || "") : state.text; }

let debounceTimer = null;
$("queryInput").addEventListener("input", () => { clearTimeout(debounceTimer); debounceTimer = setTimeout(() => onText($("queryInput").value), 400); });
$("queryInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(debounceTimer); onText($("queryInput").value); } });

// ============================================================ 이미지 입력 - crop.js 가 있으면 그것이 드롭 영역을 맡는다
let usingCropper = false;
let cropper = null; // crop.js 가 돌려주는 { recrop, reset }
async function setupImageInput() {
  const dropzone = $("dropzone");
  try {
    const mod = await import("./crop.js");
    if (mod && typeof mod.mountCropper === "function") {
      cropper = mod.mountCropper(dropzone, { onCrop: (blob) => handleImage(blob, "cropper") }) || null;
      dropzone.classList.add("has-cropper");
      usingCropper = true;
      return;
    }
  } catch (e) { /* file:// 등에서는 crop.js 를 못 불러온다 - 기본 입력으로 */ }
  const fileInput = $("fileInput");
  $("fileBtn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { const f = fileInput.files && fileInput.files[0]; if (f) handleImage(f, "basic"); fileInput.value = ""; });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => { e.preventDefault(); dropzone.classList.remove("drag"); const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (f) handleImage(f, "basic"); });
  document.addEventListener("paste", (e) => {
    for (const item of (e.clipboardData && e.clipboardData.items) || []) {
      if (item.type && item.type.startsWith("image/")) { const f = item.getAsFile(); if (f) { handleImage(f, "basic"); break; } }
    }
  });
}
// 카메라(폰) - 찍은 사진은 크롭 UI 로 넘긴다. crop.js 가 없으면 바로 읽는다.
$("cameraBtn").addEventListener("click", () => $("cameraInput").click());
$("cameraInput").addEventListener("change", () => {
  const f = $("cameraInput").files && $("cameraInput").files[0];
  if (f && !feedCropper(f)) handleImage(f, "camera");
  $("cameraInput").value = "";
});
// crop.js 의 파일 입력에 넣어 정상 경로로 태운다(계약 밖 내부 함수를 부르지 않는다).
// 이걸로 예제 버튼도 원본을 드롭 영역에 그대로 띄운다 - 무엇을 보고 판정했는지 보여야 한다.
// crop.js 의 크롭 캔버스는 폭이 800px 고정이라 좁은 화면을 밀어낸다(C 소유 파일이라 손대지 않는다).
// 그래서 폰에서는 크롭 UI 대신 큰 미리보기로 원본을 보여준다.
function canFeedCropper() { return usingCropper && window.innerWidth >= 640; }
function feedCropper(file, scroll) {
  const input = $("dropzone").querySelector(".cropx-input-file");
  if (!canFeedCropper() || !input) return false;
  try {
    const dt = new DataTransfer();
    dt.items.add(file instanceof File ? file : new File([file], "image.png", { type: file.type || "image/png" }));
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (scroll) $("dropzone").scrollIntoView({ behavior: "smooth", block: "nearest" });
    return true;
  } catch (e) { return false; }
}
// 못 읽었을 때: 판정하지 않고 크롭 UI 를 띄운다
function openCropper() {
  if (cropper && typeof cropper.recrop === "function" && cropper.recrop()) return true;
  return state.image ? feedCropper(state.image, true) : false;
}
function canRecrop() { return usingCropper && (state.imageVia === "cropper" || canFeedCropper()); }

function showPreview(blob) { $("imagePreviewImg").src = URL.createObjectURL(blob); $("imagePreview").hidden = false; }
function hidePreview() { $("imagePreview").hidden = true; }
$("imageClear").addEventListener("click", () => {
  Object.assign(state, { image: null, imageVia: null, server: null, serverError: null, readMol: null, checkedName: null });
  hidePreview(); imageNote(""); render();
});

async function handleImage(blob, via) {
  Object.assign(state, { image: blob, imageVia: via, server: null, serverError: null, readMol: null });
  if (via === "cropper") hidePreview(); else showPreview(blob); // crop.js 는 자기 미리보기를 보여준다
  imageNote("");
  if (!$("queryInput").value.trim()) {
    imageNote("이미지에서 이름을 읽는 중…");
    let hints = null;
    try { const mod = await import("./ocr.js"); if (mod && typeof mod.readLabels === "function") hints = await mod.readLabels(blob); } catch (e) { /* 임계 경로 아님 */ }
    const name = hints && hints.names && hints.names[0];
    if (name && !$("queryInput").value.trim()) {
      imageNote(`이미지에서 이름 "${name}" 을(를) 읽었습니다.`);
      $("queryInput").value = name; state.text = name; state.forceName = false;
      if (RDKit) { try { const r = await resolveText(name, false); if (r) { state.mol = r.mol; state.molFrom = r.from; } } catch (e) { /* 정본 없이도 대조는 간다 */ } }
    } else imageNote("");
  }
  render();
  checkServer();
}

// ============================================================ 서버 대조
async function checkServer() {
  if (!state.image) return;
  const blob = state.image, name = nameForServer();
  Object.assign(state, { pending: true, server: null, serverError: null, readMol: null, checkedName: name });
  render();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS);
  try {
    const fd = new FormData(); fd.append("image", blob, "image.png"); fd.append("name", name || "");
    const res = await fetch(CHECK_URL, { method: "POST", body: fd, signal: controller.signal });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    const json = await res.json();
    if (blob !== state.image || name !== state.checkedName) return; // 그 사이 입력이 바뀌었다
    state.server = json;
    if (!json.reference && !state.mol && json.read && json.read.smiles && RDKit) state.readMol = await buildReadMol(json.read.smiles);
  } catch (e) {
    if (blob !== state.image) return;
    state.serverError = (e && e.name === "AbortError") ? "서버가 응답하지 않습니다." : "서버에 연결하지 못했습니다.";
  } finally { clearTimeout(timer); if (blob === state.image) { state.pending = false; render(); } }
}
async function buildReadMol(smiles) {
  const desc = describeFull(smiles); if (!desc) return null;
  const who = await identifyByKey(desc.inchikey);
  return { ...desc, cid: who ? who.cid : null, title: who ? who.title : null };
}

// ============================================================ 결과 카드 - 하나. 상태에서 통째로 그린다.
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
const skel = (k) => (k || "").slice(0, 14);
function linkButton(text, fn) { const b = el("button", "link", text); b.type = "button"; b.addEventListener("click", fn); return b; }

function render() {
  const card = $("result"); card.innerHTML = "";
  const has = state.mol || state.image;
  card.hidden = !has; if (!has) return;

  // 자동 판별이 틀렸을 때 - 한 줄로 뒤집는다
  if (state.mol && state.text) {
    if (state.molFrom === "smiles") { const f = el("p", "flip"); f.append("SMILES 로 읽었습니다 · ", linkButton("이름으로 찾기 →", () => { state.forceName = true; resolveAndRender(); })); card.appendChild(f); }
    else if (state.forceName) { const f = el("p", "flip"); f.append("이름으로 찾았습니다 · ", linkButton("SMILES 로 읽기 →", () => { state.forceName = false; resolveAndRender(); })); card.appendChild(f); }
  }

  if (state.mol) card.appendChild(molBlock(state.mol, { eyebrow: "정본" }));

  if (!state.image) return;
  if (state.pending) { card.appendChild(noteBlock("그림을 읽는 중… 처음이면 최대 1분 걸립니다.")); return; }
  if (state.serverError) { card.appendChild(noteBlock(state.serverError + (state.mol ? " 정본을 나란히 놓고 비교해 보세요." : ""))); return; }
  if (!state.server) return;
  const json = state.server;
  const reference = json.reference || (state.mol ? { inchikey: state.mol.inchikey, formula: state.mol.formula, name: state.mol.title } : null);
  card.appendChild(reference ? verdictBlock(json, reference) : readBlock(json));
}
function noteBlock(text) { const d = el("div", "verdict"); d.appendChild(el("p", "verdict-note", text)); return d; }

// 구조 카드 - 정본이든 그림에서 읽은 것이든 같은 꼴. 복사 버튼은 여기서만.
function molBlock(mol, opt) {
  const wrap = el("div", "canon");
  const draw = el("div", "mol-draw"); draw.innerHTML = mol.svg || ""; wrap.appendChild(draw);
  const side = el("div");
  const eyebrow = el("p", "eyebrow"); eyebrow.append(opt.eyebrow);
  if (opt.badge) eyebrow.append(" ", opt.badge);
  side.appendChild(eyebrow);
  side.appendChild(el("h2", "mol-name", mol.title || "PubChem 미등재"));
  const dl = el("dl", "facts");
  const rows = [["PubChem", mol.cid ? { href: `https://pubchem.ncbi.nlm.nih.gov/compound/${mol.cid}`, text: `CID ${mol.cid}` } : null], ["화학식", mol.formula], ["SMILES", mol.smiles], ["InChIKey", mol.inchikey]];
  for (const [k, v] of rows) {
    if (!v) continue;
    dl.appendChild(el("dt", null, k)); const dd = el("dd");
    if (v.href) { const a = el("a", null, v.text); a.href = v.href; a.target = "_blank"; a.rel = "noopener"; dd.appendChild(a); } else dd.textContent = v;
    dl.appendChild(dd);
  }
  side.appendChild(dl);
  side.appendChild(copyRow(mol, opt));
  wrap.appendChild(side);
  return wrap;
}
function badge(text, cls) { return el("span", "badge " + (cls || ""), text); }

// ---- 복사 (PNG·SVG·SMILES·InChIKey)
function copyRow(mol, opt) {
  const row = el("div", "copy-row"); const note = el("p", "copy-note");
  const say = (msg) => { note.textContent = msg; setTimeout(() => { if (note.textContent === msg) note.textContent = ""; }, 3000); };
  const mk = (label, fn) => { const b = el("button", "btn btn-sm", label); b.type = "button"; b.addEventListener("click", fn); row.appendChild(b); };
  mk("PNG 복사", async () => {
    let blob; try { blob = await svgToPngBlob(mol.svg, 420, 360); } catch (e) { say("PNG 생성 실패: " + e.message); return; }
    try { await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]); say("PNG 를 복사했습니다."); }
    catch (e) { downloadBlob(blob, safeFileName(mol) + ".png"); say("파일로 내려받았습니다."); }
  });
  mk("SVG 다운로드", () => { downloadBlob(new Blob([mol.svg], { type: "image/svg+xml" }), safeFileName(mol) + ".svg"); say("SVG 를 내려받았습니다."); });
  mk("SMILES 복사", () => copyText(mol.smiles, "SMILES", say));
  mk("InChIKey 복사", () => copyText(mol.inchikey, "InChIKey", say));
  if (opt && opt.copyBadge) row.appendChild(badge(opt.copyBadge, "badge-na"));
  const wrap = el("div"); wrap.append(row, note); return wrap;
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob); const a = el("a"); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function safeFileName(c) { return (c.title || c.inchikey || "structure").replace(/[^\w.-]+/g, "_").slice(0, 60); }
async function copyTextImpl(text) {
  try { await navigator.clipboard.writeText(text); return; } catch (e) { /* 아래 대체 경로 */ }
  const ta = el("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.focus(); ta.select(); const ok = document.execCommand("copy"); ta.remove();
  if (!ok) throw new Error("복사 실패");
}
function copyText(text, label, say) { if (!text) return; copyTextImpl(text).then(() => say(`${label} 를 복사했습니다.`)).catch((e) => say(`${label} 복사 실패: ` + e.message)); }
function svgToPngBlob(svg, w, h) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    const img = new Image();
    img.onload = () => {
      const canvas = el("canvas"); canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h); ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url); canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG 변환 실패"))), "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("SVG 로드 실패")); };
    img.src = url;
  });
}

// ---- 판정 (텍스트 + 이미지)
function gradeBadge(grade) {
  if (!grade) return null;
  return badge(grade === "strong" ? "확신: 강함 (인식기 둘 합의)" : "확신: 약함 (인식기 하나)", grade === "strong" ? "badge-ok" : "badge-na");
}
function validEngines(json) { return ((json.read && json.read.engines) || []).filter((e) => e.inchikey); }
function enginesDisagree(json) { return new Set(validEngines(json).map((e) => skel(e.inchikey))).size >= 2; }

function verdictBlock(json, ref) {
  let verdict = json.verdict;
  if (!json.reference && json.read && json.read.inchikey) verdict = skel(json.read.inchikey) === skel(ref.inchikey) ? "match" : "mismatch"; // 서버가 이름을 몰라도 여기서 대조한다
  if (verdict === "unreadable") return unreadableBlock(json);

  const box = el("div", "verdict " + verdict);
  const label = el("p", "verdict-label"); label.append(el("span", "dot " + verdict), verdict === "match" ? "일치" : "다름");
  const g = gradeBadge(json.grade); if (g) label.append(g);
  box.appendChild(label);

  const read = json.read || {};
  if (verdict === "mismatch") {
    const rows = [[`"${ref.name || ""}" 의 정본`, escapeHtml(ref.inchikey || "-")], ["그림에서 읽은 것", markDiff(ref.inchikey, read.inchikey)]];
    const note = formulaDiffNote(ref.formula, read.heavy_formula);
    if (note) rows.push(["조성", `${escapeHtml(read.heavy_formula)} <span class="note">${escapeHtml(note)}</span>`]);
    box.appendChild(evidenceRows(rows));
  }
  const reasons = json.reasons || [];
  if (reasons.length) box.appendChild(el("p", "verdict-note", reasons[0]));
  const fold = foldBlock("근거 보기", reasons.slice(1), json);
  if (fold) box.appendChild(fold);
  return box;
}
function unreadableBlock(json) {
  const box = el("div", "verdict unreadable");
  const label = el("p", "verdict-label");
  if (enginesDisagree(json)) {
    label.append(el("span", "dot unreadable"), "인식기 둘이 다르게 읽었습니다"); box.appendChild(label);
    box.appendChild(pairBlock(validEngines(json)));
    return box;
  }
  label.append(el("span", "dot unreadable"), "그림에서 구조를 읽지 못했습니다"); box.appendChild(label);
  const row = el("div", "action-row");
  if (canRecrop()) { const b = el("button", "btn btn-sm", "구조 부분만 잘라서 다시 시도"); b.type = "button"; b.addEventListener("click", openCropper); row.appendChild(b); }
  else row.appendChild(el("span", "verdict-note", "구조 부분만 잘라서 다시 넣어 보세요."));
  box.appendChild(row);
  return box;
}
// 두 인식기가 읽은 것을 나란히 - 각각 그려서 보여준다
function pairBlock(engines) {
  const pair = el("div", "pair");
  for (const e of engines.slice(0, 2)) {
    const col = el("div", "pair-col");
    col.appendChild(el("p", "eyebrow", e.engine || "-"));
    const draw = el("div", "mol-draw");
    if (RDKit && e.smiles) { const m = RDKit.get_mol(e.smiles); if (m && m.is_valid()) draw.innerHTML = m.get_svg(300, 240); if (m) m.delete(); }
    col.appendChild(draw);
    col.appendChild(el("p", "mono small", e.inchikey || ""));
    pair.appendChild(col);
  }
  return pair;
}
function foldBlock(title, rest, json) {
  const engines = (json.read && json.read.engines) || [];
  if (!rest.length && !engines.length) return null;
  const d = el("details", "fold"); d.appendChild(el("summary", null, title));
  if (rest.length) { const ul = el("ul", "reasons"); for (const r of rest) ul.appendChild(el("li", null, r)); d.appendChild(ul); }
  if (engines.length) d.appendChild(evidenceRows(engines.map((e) => [e.engine || "-", escapeHtml(e.inchikey || "(파싱 실패)") + (typeof e.confidence === "number" ? ` <span class="note">신뢰도 ${e.confidence.toFixed(3)}</span>` : "")])));
  return d;
}
function evidenceRows(rows) {
  const box = el("div", "evidence");
  for (const [k, html] of rows) { const row = el("div", "row"); row.appendChild(el("div", "k", k)); const v = el("div", "v"); v.innerHTML = html; row.appendChild(v); box.appendChild(row); }
  return box;
}

// ---- 이미지만: 그림에서 읽은 구조. 정본이 없으므로 이 경로에서만 읽은 SMILES 를 복사할 수 있다(확인되지 않음 배지).
function readBlock(json) {
  if (!state.readMol) return unreadableBlock(json);
  const wrap = el("div");
  wrap.appendChild(molBlock(state.readMol, { eyebrow: "그림에서 읽은 구조", badge: gradeBadge(json.grade), copyBadge: "확인되지 않음" }));
  const fold = foldBlock("인식기별 결과", [], json);
  if (fold) { fold.classList.add("fold-pad"); wrap.appendChild(fold); }
  return wrap;
}

// ---- 차이 표시
function markDiff(ref, read) {
  if (!read) return "";
  if (!ref) return escapeHtml(read);
  let out = "";
  for (let i = 0; i < read.length; i++) { const c = read[i]; out += (i < ref.length && ref[i] === c) ? escapeHtml(c) : `<mark>${escapeHtml(c)}</mark>`; }
  return out;
}
const KO_ELEM = { C: "탄소", N: "질소", O: "산소", S: "황", P: "인", F: "불소", Cl: "염소", Br: "브롬", I: "아이오딘" };
function formulaDiffNote(refF, readF) {
  if (!refF || !readF) return null;
  const parseF = (f) => { const out = {}; for (const m of f.matchAll(/([A-Z][a-z]?)(\d*)/g)) out[m[1]] = (out[m[1]] || 0) + (m[2] ? parseInt(m[2], 10) : 1); return out; };
  const a = parseF(refF), b = parseF(readF), parts = [];
  for (const e of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (e === "H") continue; // heavy_formula 는 중원자만 센다
    const d = (b[e] || 0) - (a[e] || 0); if (!d) continue;
    parts.push(`${KO_ELEM[e] || e} ${Math.abs(d) === 1 ? "하나" : Math.abs(d) + "개"} ${d > 0 ? "많음" : "적음"}`);
  }
  return parts.length ? parts.join(", ") : null;
}

// ============================================================ 바로 해보기
function buildExamples() {
  const box = $("examples");
  for (const g of EXAMPLES) {
    const row = el("div", "example-row"); row.appendChild(el("span", "example-group", g.group));
    for (const it of g.items) { const b = el("button", "chip", it.label); b.type = "button"; b.addEventListener("click", () => runExample(it)); row.appendChild(b); }
    box.appendChild(row);
  }
}
async function runExample(it) {
  $("queryInput").value = it.name;
  Object.assign(state, { text: it.name, forceName: false, mol: null, molFrom: null, image: null, imageVia: null, server: null, serverError: null, readMol: null, checkedName: null });
  hidePreview(); imageNote(""); clearErr(); hideSuggestions();
  if (it.name && RDKit) { try { const r = await resolveText(it.name, false); if (r) { state.mol = r.mol; state.molFrom = r.from; } } catch (e) { /* 정본 없이도 간다 */ } }
  render();
  let blob;
  try { const res = await fetch(it.path); if (!res.ok) throw new Error(res.status); blob = await res.blob(); }
  catch (e) { imageNote("예시 이미지를 불러오지 못했습니다. 이미지를 직접 붙여넣으세요."); return; }
  // 드롭 영역에 원본을 띄운다 - 검사한 그림을 눈으로 보고, 필요하면 거기서 다시 자를 수 있다.
  const inCropper = feedCropper(new File([blob], it.key + ".png", { type: blob.type || "image/png" }));
  await handleImage(blob, inCropper ? "cropper" : "example");
}

// ============================================================ 요금제 - 눌린다. 월/연 토글, 선택 카드, 요약 한 줄.
(function pricing() {
  const plans = [...document.querySelectorAll("#plans .plan")]; if (!plans.length) return;
  let cycle = "monthly";
  const priceOf = (p) => { const a = p.querySelector(".amount"), per = p.querySelector(".per"); return (a.dataset[cycle] || a.textContent) + (per ? (per.dataset[cycle] || per.textContent).replace(" ", "") : ""); };
  function select(p, focus) {
    for (const q of plans) { const on = q === p; q.setAttribute("aria-checked", on); q.tabIndex = on ? 0 : -1; q.classList.toggle("is-selected", on); }
    $("planSummary").textContent = p.dataset.summary.replace("{price}", priceOf(p));
    if (focus) p.focus();
  }
  function applyCycle() {
    for (const s of document.querySelectorAll("#plans [data-monthly]")) s.textContent = s.dataset[cycle];
    for (const b of document.querySelectorAll(".toggle-btn")) { const on = b.dataset.cycle === cycle; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", on); }
    select(plans.find((p) => p.getAttribute("aria-checked") === "true") || plans[1]);
  }
  for (const p of plans) {
    p.addEventListener("click", (e) => { if (!e.target.closest(".plan-cta")) select(p); });
    p.addEventListener("keydown", (e) => {
      const i = plans.indexOf(p);
      if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); select(plans[(i + 1) % plans.length], true); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); select(plans[(i - 1 + plans.length) % plans.length], true); }
      else if ((e.key === " " || e.key === "Enter") && e.target === p) { e.preventDefault(); select(p); }
    });
  }
  for (const b of document.querySelectorAll(".toggle-btn")) b.addEventListener("click", () => { cycle = b.dataset.cycle; applyCycle(); });
  document.querySelector('[data-action="start"]').addEventListener("click", (e) => { e.preventDefault(); $("queryInput").scrollIntoView({ behavior: "smooth", block: "center" }); $("queryInput").focus(); });
  applyCycle();
})();

// ============================================================ 시작
function warmupServer() { // 콜드스타트를 제출 전에 흡수한다. 실패해도 무시.
  const e = $("serverStatus");
  fetch(HEALTH_URL).then((r) => { e.textContent = r.ok ? "그림 검사 준비됨" : "그림 검사 서버를 깨우는 중…"; }).catch(() => { e.textContent = "그림 검사 서버를 깨우는 중…"; });
}
setupImageInput();
buildExamples();
warmupServer();

const q = new URLSearchParams(location.search);
if (q.get("name")) $("queryInput").value = q.get("name");

window.initRDKitModule().then((m) => {
  RDKit = m; status("");
  const sample = q.get("sample"); // 옛 링크(try.html?sample=…) 호환
  const it = sample && EXAMPLES.flatMap((g) => g.items).find((x) => x.key === sample);
  if (it) runExample(it);
  else if ($("queryInput").value.trim()) onText($("queryInput").value);
}).catch(() => { status(""); fail("구조 엔진을 불러오지 못했습니다. 새로고침해 보세요."); });
