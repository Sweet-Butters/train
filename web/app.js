// web/app.js (U 소유) - 화면 로직. 서버 없음 - 전부 이 브라우저 안에서 끝난다.
// web/crop.js(C)·web/ocr.js(O)는 아직 없을 수 있다 - 있으면 쓰고 없으면 조용히 건너뛴다.
// 단일 입력 칸(이름/SMILES)은 이 파일 혼자서도 완결된다 - OCR 은 임계 경로가 아니다.

const TABLE = window.CHEMCHECK_TABLE || {};
const ALIASES = window.CHEMCHECK_ALIASES || {};
const BY_KEY = {};
for (const [name, row] of Object.entries(TABLE)) if (row.inchikey) BY_KEY[row.inchikey] = { name, ...row };
const PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound";

const $ = (id) => document.getElementById(id);
let RDKit = null;
let currentCanonical = null;

function status(t) { $("status").textContent = t; }
function fail(msg) { $("err").textContent = msg; }
function clearErr() { $("err").textContent = ""; }

// ============================================================ 이름 정규화
// web/build_table.py 의 normalize_exact 와 규칙을 맞춘다 - 갈라지면 표와 안 맞는다.

const DASH_RE = /[‐‑‒–—−]/g;

function normalizeExact(s) {
  s = s.normalize("NFKC").trim().replace(DASH_RE, "-");
  return s.split(/\s+/).join(" ").toLowerCase();
}
function normalizeLoose(s) {
  return normalizeExact(s).replace(/[\s-]+/g, "");
}

// ============================================================ 이름 -> 정본 / InChIKey -> 이름

async function resolveName(name) {
  const exact = normalizeExact(name);
  if (TABLE[exact]) return { ...TABLE[exact], source: "내장 표", query: name };
  const aliasKey = ALIASES[normalizeLoose(name)];
  if (aliasKey && BY_KEY[aliasKey]) {
    const { name: _drop, ...row } = BY_KEY[aliasKey];
    return { ...row, source: "내장 표(한글/약칭)", query: name };
  }
  const url = `${PUBCHEM}/name/${encodeURIComponent(name.trim())}/property/SMILES,InChIKey,Title/JSON`;
  const res = await fetch(url);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`PubChem 응답 ${res.status}`);
  const p = (await res.json()).PropertyTable.Properties[0];
  return { smiles: p.SMILES, inchikey: p.InChIKey, cid: p.CID, title: p.Title, source: "PubChem", query: name };
}

async function identifyByKey(inchikey) {
  if (BY_KEY[inchikey]) return { ...BY_KEY[inchikey], source: "내장 표" };
  const res = await fetch(`${PUBCHEM}/inchikey/${inchikey}/property/Title/JSON`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`PubChem 응답 ${res.status}`);
  const p = (await res.json()).PropertyTable.Properties[0];
  return { title: p.Title, cid: p.CID, source: "PubChem" };
}

// ============================================================ RDKit 계산 (그림·InChIKey·화학식)

const ELEMENTS = {
  1: "H", 2: "He", 3: "Li", 4: "Be", 5: "B", 6: "C", 7: "N", 8: "O", 9: "F", 10: "Ne",
  11: "Na", 12: "Mg", 13: "Al", 14: "Si", 15: "P", 16: "S", 17: "Cl", 18: "Ar", 19: "K", 20: "Ca",
  24: "Cr", 25: "Mn", 26: "Fe", 27: "Co", 28: "Ni", 29: "Cu", 30: "Zn", 35: "Br", 47: "Ag", 50: "Sn",
  53: "I", 56: "Ba", 78: "Pt", 79: "Au", 80: "Hg", 82: "Pb",
};

// 중원자만이 아니라 암시적 수소까지 센다(add_hs_in_place) - Hill 표기(C, H, 나머지 알파벳순).
function hillString(bySymbolCounts) {
  const hasC = !!bySymbolCounts["C"];
  let order;
  if (hasC) {
    order = ["C"];
    if (bySymbolCounts["H"]) order.push("H");
    order = order.concat(Object.keys(bySymbolCounts).filter((s) => s !== "C" && s !== "H").sort());
  } else {
    order = Object.keys(bySymbolCounts).sort();
  }
  return order.map((s) => s + (bySymbolCounts[s] > 1 ? bySymbolCounts[s] : "")).join("");
}
function molecularFormula(smiles) {
  const mol = RDKit.get_mol(smiles);
  if (!mol || !mol.is_valid()) { if (mol) mol.delete(); return null; }
  try {
    mol.add_hs_in_place();
    const j = JSON.parse(mol.get_json());
    const bySymbol = {};
    for (const m of j.molecules) for (const a of m.atoms) {
      const z = a.z === undefined ? 6 : a.z; // commonchem 은 탄소(z=6)를 생략한다
      const sym = ELEMENTS[z] || `#${z}`;
      bySymbol[sym] = (bySymbol[sym] || 0) + 1;
    }
    return hillString(bySymbol);
  } finally { mol.delete(); }
}
function describeFull(smiles) {
  const mol = RDKit.get_mol(smiles);
  if (!mol || !mol.is_valid()) { if (mol) mol.delete(); return null; }
  let base;
  try {
    const inchi = mol.get_inchi();
    base = { smiles: mol.get_smiles(), inchikey: RDKit.get_inchikey_for_inchi(inchi), svg: mol.get_svg(420, 360) };
  } finally { mol.delete(); }
  base.formula = molecularFormula(base.smiles);
  return base;
}

// 단일 칸의 판별 규칙: RDKit 이 파싱하면 SMILES, 못 하면 이름 - 추론이 아니라 규칙이다.
async function resolveSingle(raw) {
  const probe = RDKit.get_mol(raw);
  const validSmiles = !!(probe && probe.is_valid());
  if (probe) probe.delete();
  if (validSmiles) {
    const desc = describeFull(raw);
    const who = await identifyByKey(desc.inchikey);
    return {
      svg: desc.svg, smiles: desc.smiles, inchikey: desc.inchikey, formula: desc.formula,
      cid: who ? who.cid : null, title: who ? who.title : null,
      source: who ? who.source : "SMILES 입력 (PubChem 미등록)",
    };
  }
  const byName = await resolveName(raw);
  if (!byName) return null;
  const desc = describeFull(byName.smiles);
  if (!desc) return null;
  return { svg: desc.svg, smiles: desc.smiles, inchikey: desc.inchikey, formula: desc.formula, cid: byName.cid, title: byName.title || raw, source: byName.source };
}

// ============================================================ 이름 근접 제안 (편집거리)

const TITLE_LIST = (() => {
  const seen = new Set(); const list = [];
  for (const row of Object.values(TABLE)) {
    if (row.title && !seen.has(row.title)) { seen.add(row.title); list.push(row.title); }
  }
  return list;
})();

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = new Array(n + 1);
  for (let j = 0; j <= n; j++) dp[j] = j;
  for (let i = 1; i <= m; i++) {
    let prev = dp[0]; dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]);
      prev = tmp;
    }
  }
  return dp[n];
}
function showSuggestions(raw) {
  const q = normalizeLoose(raw);
  if (!q) return;
  const scored = TITLE_LIST.map((t) => ({ t, d: levenshtein(q, normalizeLoose(t)) })).sort((a, b) => a.d - b.d);
  const top = scored.filter((s) => s.d > 0 && s.d <= Math.max(3, Math.ceil(q.length * 0.5))).slice(0, 5);
  if (!top.length) return;
  const box = $("suggestions"); box.hidden = false; box.innerHTML = "";
  const p = document.createElement("p"); p.textContent = "이 이름을 찾으시나요?";
  const pills = document.createElement("div"); pills.className = "pills";
  for (const { t } of top) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = t;
    b.addEventListener("click", () => { $("queryInput").value = t; attemptResolve(t); });
    pills.appendChild(b);
  }
  box.append(p, pills);
}
function hideSuggestions() { const box = $("suggestions"); box.hidden = true; box.innerHTML = ""; }

// ============================================================ 정본 카드 (복사 넷은 여기에만)

function renderCanonical(data) {
  currentCanonical = data;
  $("canonCard").hidden = false;
  $("canonDraw").innerHTML = data.svg || "";
  $("canonTitle").textContent = data.title || "(PubChem 에 이름 없음)";
  const dl = $("canonFacts"); dl.innerHTML = "";
  const cidLink = data.cid ? { href: `https://pubchem.ncbi.nlm.nih.gov/compound/${data.cid}`, text: `CID ${data.cid}` } : null;
  const rows = [["출처", data.source], ["PubChem", cidLink], ["화학식", data.formula], ["SMILES", data.smiles], ["InChIKey", data.inchikey]];
  for (const [k, val] of rows) {
    if (val === undefined || val === null || val === "") continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd");
    if (val && val.href) { const a = document.createElement("a"); a.href = val.href; a.textContent = val.text; a.target = "_blank"; a.rel = "noopener"; dd.appendChild(a); }
    else dd.textContent = val;
    dl.append(dt, dd);
  }
  $("copyNote").textContent = "";
}
function hideCanonical() { currentCanonical = null; $("canonCard").hidden = true; }

async function attemptResolve(raw) {
  raw = raw.trim();
  hideSuggestions();
  if (!raw) { hideCanonical(); clearErr(); return; }
  if (!RDKit) { status("RDKit 로딩 중… 준비되면 자동으로 확인한다."); return; }
  status("정본 확인 중…");
  clearErr();
  try {
    const res = await resolveSingle(raw);
    if (!res) {
      hideCanonical();
      showSuggestions(raw);
      fail(`"${raw}" - 내장 표 423개에도, PubChem 에도, 유효한 SMILES 로도 못 찾았다.`);
      status(`RDKit ${RDKit.version()} 준비됨`);
      return;
    }
    renderCanonical(res);
    status(`RDKit ${RDKit.version()} 준비됨`);
  } catch (e) {
    hideCanonical();
    fail("실패: " + (e && e.message ? e.message : e) + " (PubChem 조회는 네트워크가 필요하다)");
    status(RDKit ? `RDKit ${RDKit.version()} 준비됨` : "");
  }
}

let debounceTimer = null;
$("queryInput").addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { attemptResolve($("queryInput").value); }, 400);
});
$("queryInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { clearTimeout(debounceTimer); attemptResolve($("queryInput").value); }
});

// ============================================================ 정본 복사 (넷) - 정본에만 둔다. 사용자가 올린 그림 쪽엔 두지 않는다.

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function safeFileName(c) { return (c.title || c.inchikey || "structure").replace(/[^\w.-]+/g, "_").slice(0, 60); }

let copyNoteTimer = null;
function copyNote(msg) {
  const el = $("copyNote"); el.textContent = msg;
  clearTimeout(copyNoteTimer);
  copyNoteTimer = setTimeout(() => { el.textContent = ""; }, 3000);
}
async function copyTextImpl(text) {
  try { await navigator.clipboard.writeText(text); return; } catch (e) { /* 아래 대체 경로로 */ }
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.focus(); ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  if (!ok) throw new Error("execCommand 복사 실패");
}
function copyText(text, label) {
  if (!text) return;
  copyTextImpl(text).then(() => copyNote(`${label} 를 클립보드에 복사했다.`))
    .catch((e) => copyNote(`${label} 복사 실패: ` + (e && e.message ? e.message : e)));
}
function svgToPngBlob(svg, w, h) {
  return new Promise((resolve, reject) => {
    const svgBlob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG 변환 실패"))), "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("SVG 로드 실패")); };
    img.src = url;
  });
}

$("copyPng").addEventListener("click", async () => {
  if (!currentCanonical) return;
  let blob;
  try { blob = await svgToPngBlob(currentCanonical.svg, 420, 360); }
  catch (e) { copyNote("PNG 생성 실패: " + (e && e.message ? e.message : e)); return; }
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    copyNote("PNG 를 클립보드에 복사했다.");
  } catch (e) {
    downloadBlob(blob, safeFileName(currentCanonical) + ".png");
    copyNote("클립보드 복사가 막혀 파일로 내려받았다.");
  }
});
$("copySvg").addEventListener("click", () => {
  if (!currentCanonical) return;
  downloadBlob(new Blob([currentCanonical.svg], { type: "image/svg+xml" }), safeFileName(currentCanonical) + ".svg");
  copyNote("SVG 를 내려받았다.");
});
$("copySmiles").addEventListener("click", () => copyText(currentCanonical && currentCanonical.smiles, "SMILES"));
$("copyKey").addEventListener("click", () => copyText(currentCanonical && currentCanonical.inchikey, "InChIKey"));

// ============================================================ 이미지 입력 - crop.js(C)·ocr.js(O) 있으면 쓰고 없으면 대체. 임계 경로 아님.

async function setupImageInput() {
  const dropzone = $("dropzone");
  try {
    const mod = await import("./crop.js");
    if (mod && typeof mod.mountCropper === "function") {
      mod.mountCropper(dropzone, { onCrop: handleImage });
      return;
    }
  } catch (e) { /* web/crop.js 아직 없다 - 조용히 건너뛴다 */ }
  setupBasicImageInput(dropzone);
}
function setupBasicImageInput(dropzone) {
  const fileInput = $("fileInput");
  $("fileBtn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) handleImage(f, { full: f });
  });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault(); dropzone.classList.remove("drag");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) handleImage(f, { full: f });
  });
  document.addEventListener("paste", (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) { handleImage(f, { full: f }); break; }
      }
    }
  });
}
function showImagePreview(blob) {
  const box = $("imagePreview");
  box.innerHTML = ""; box.hidden = false;
  const img = document.createElement("img");
  img.src = URL.createObjectURL(blob);
  img.alt = "붙여넣은 이미지";
  box.appendChild(img);
}
function imageNote(msg) { $("imageNote").textContent = msg || ""; }

// OCR(O)이 뽑는 건 이름뿐이다(계약 개정 2 - 화학식·SMILES 는 사전이 없어 오독을 보정할
// 수 없어 접었다). 이름칸을 채우는 것뿐이라 실패해도 사용자가 3초면 직접 친다 - 임계 경로 아님.
async function handleImage(blob) {
  showImagePreview(blob);
  imageNote("OCR 로 이름을 읽는 중…");

  let hints = null;
  try {
    const mod = await import("./ocr.js");
    if (mod && typeof mod.readLabels === "function") hints = await mod.readLabels(blob);
  } catch (e) { /* web/ocr.js 아직 없다 - 임계 경로가 아니므로 조용히 건너뛴다 */ }

  const name = hints && hints.names && hints.names[0];
  if (!name) {
    imageNote("이미지에서 이름을 읽지 못했다 - OCR(web/ocr.js)이 아직 없거나 찾지 못했다. 위 칸에 이름이나 SMILES 를 직접 입력하라.");
    return;
  }
  imageNote(`OCR 이 이름 "${name}" 을 읽어 아래 칸에 채웠다 - 확인하고 필요하면 고쳐라.`);
  $("queryInput").value = name;
  await attemptResolve(name);
}

// ============================================================ 야생에서 잡은 오류 - 정본과 바로 비교하는 버튼

$("wildcatchCompareBtn").addEventListener("click", () => {
  $("queryInput").value = "Caffeine";
  attemptResolve("Caffeine");
  $("canonCard").scrollIntoView({ behavior: "smooth", block: "start" });
});

// ============================================================ 시작

$("foot").textContent = `내장 표 ${Object.keys(TABLE).length}개 화합물 (web/build_table.py 가 chemcheck/data 에서 구움). ` +
  `이름/SMILES 판별과 InChIKey·화학식 계산은 이 브라우저 안에서 표·PubChem·RDKit(WASM) 만으로 끝난다 - 서버가 없다. ` +
  `이미지는 web/crop.js·web/ocr.js 가 있으면 이름을 자동으로 채운다(오늘은 없을 수 있다) - 없어도 이 칸은 그대로 동작한다. ` +
  `그림 자체를 읽는 검사(OCSR)는 로컬 CLI 의 몫이다 - 위 "야생에서 잡은 오류" 참고.`;

setupImageInput();

const q = new URLSearchParams(location.search);
if (q.get("name")) $("queryInput").value = q.get("name");

const t0 = performance.now();
window.initRDKitModule().then((m) => {
  RDKit = m;
  status(`RDKit ${m.version()} 준비됨 (${Math.round(performance.now() - t0)}ms)`);
  const initial = $("queryInput").value.trim();
  if (initial) attemptResolve(initial);
}).catch((e) => { status(""); fail("RDKit 을 불러오지 못했다: " + e); });
