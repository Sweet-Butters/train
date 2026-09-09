// web/app.js (U 소유) - 화면 로직. 서버 없음 - 전부 이 브라우저 안에서 끝난다.
// web/crop.js(C)·web/ocr.js(O)는 아직 없을 수 있다 - 있으면 쓰고 없으면 조용히 건너뛴다.
//
// 화면의 중심은 이름+SMILES 대조다: LLM 이 준 SMILES 를 이름의 정본과 대조해 InChIKey 로
// 빨강/초록을 낸다 - 임의의 입력에 대해 실제로 도는 판정이고 하드코딩이 아니다.
// 이미지는 OCR 로 이름칸만 채우는 보조 경로다 - 그림 자체를 읽는 것은 로컬 CLI 의 몫이다.

const TABLE = window.CHEMCHECK_TABLE || {};
const ALIASES = window.CHEMCHECK_ALIASES || {};
const BY_KEY = {};
for (const [name, row] of Object.entries(TABLE)) if (row.inchikey) BY_KEY[row.inchikey] = { name, ...row };
const PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound";
const CHECK_URL = "https://pxh7yp--chemcheck.modal.run/api/check";
const HEALTH_URL = "https://pxh7yp--chemcheck.modal.run/api/health";
const CHECK_TIMEOUT_MS = 65000; // 첫 요청은 콜드스타트로 최대 60초 - 넉넉히 잡는다

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

// ---- 부분 구조 칠하기 (관능기 하나 붙거나 빠진 차이만 - RDKit.js(MinimalLib)는 rdFMCS 가 없다) ----

function molAtomBondCounts(mol) {
  const m = JSON.parse(mol.get_json()).molecules[0];
  return { atoms: m.atoms.length, bonds: m.bonds.length };
}
function complementIndices(total, matched) {
  const set = new Set(matched || []);
  const out = [];
  for (let i = 0; i < total; i++) if (!set.has(i)) out.push(i);
  return out;
}
function highlightDiff(refSmiles, userSmiles) {
  const refMol = RDKit.get_mol(refSmiles);
  const userMol = RDKit.get_mol(userSmiles);
  try {
    if (!refMol || !refMol.is_valid() || !userMol || !userMol.is_valid()) return null;
    const refInUser = JSON.parse(userMol.get_substruct_match(refMol));
    if (refInUser.atoms && refInUser.atoms.length) {
      const c = molAtomBondCounts(userMol);
      return { side: "user", atoms: complementIndices(c.atoms, refInUser.atoms), bonds: complementIndices(c.bonds, refInUser.bonds) };
    }
    const userInRef = JSON.parse(refMol.get_substruct_match(userMol));
    if (userInRef.atoms && userInRef.atoms.length) {
      const c = molAtomBondCounts(refMol);
      return { side: "ref", atoms: complementIndices(c.atoms, userInRef.atoms), bonds: complementIndices(c.bonds, userInRef.bonds) };
    }
    return null;
  } finally { refMol.delete(); userMol.delete(); }
}
function svgWithHighlight(smiles, highlight, w, h) {
  const mol = RDKit.get_mol(smiles);
  try {
    if (!mol || !mol.is_valid()) return "";
    if (!highlight || !highlight.atoms.length) return mol.get_svg(w, h);
    const details = JSON.stringify({ atoms: highlight.atoms, bonds: highlight.bonds, highlightColour: [0.94, 0.42, 0.42], width: w, height: h });
    return mol.get_svg_with_highlights(details);
  } finally { mol.delete(); }
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
    b.addEventListener("click", () => { $("nameInput").value = t; evaluate(); });
    pills.appendChild(b);
  }
  box.append(p, pills);
}
function hideSuggestions() { const box = $("suggestions"); box.hidden = true; box.innerHTML = ""; }

// ============================================================ 정본 카드 (복사 넷은 여기에만 - 이름으로 찾은 것에만)

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

// ============================================================ 판정 근거를 문자 그대로 보여준다 (추론 0, 문자열·원자수 비교)

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function markDiff(ref, read) {
  if (!read) return "";
  if (!ref) return escapeHtml(read);
  let out = "";
  for (let i = 0; i < read.length; i++) {
    const c = read[i];
    out += (i < ref.length && ref[i] === c) ? escapeHtml(c) : `<mark>${escapeHtml(c)}</mark>`;
  }
  return out;
}
const KO_ELEM = { C: "탄소", H: "수소", N: "질소", O: "산소", S: "황", P: "인", F: "불소", Cl: "염소", Br: "브롬", I: "아이오딘" };
function formulaDiffNote(refF, readF) {
  if (!refF || !readF) return null;
  const re = /([A-Z][a-z]?)(\d*)/g;
  const parseF = (f) => { const out = {}; let m; while ((m = re.exec(f))) out[m[1]] = (out[m[1]] || 0) + (m[2] ? parseInt(m[2], 10) : 1); re.lastIndex = 0; return out; };
  const a = parseF(refF), b = parseF(readF);
  const elems = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  const parts = [];
  for (const el of elems) {
    const d = (b[el] || 0) - (a[el] || 0);
    if (!d) continue;
    const ko = KO_ELEM[el] || el;
    const word = d > 0 ? (d === 1 ? "하나 많음" : `${d}개 많음`) : (d === -1 ? "하나 적음" : `${Math.abs(d)}개 적음`);
    parts.push(`${ko} ${word}`);
  }
  return parts.length ? parts.join(", ") : null;
}
function buildEvidenceBox(rows) {
  const box = document.createElement("div"); box.className = "evidence";
  for (const [k, html] of rows) {
    const row = document.createElement("div"); row.className = "row";
    const kd = document.createElement("div"); kd.className = "k"; kd.textContent = k;
    const vd = document.createElement("div"); vd.innerHTML = html;
    row.append(kd, vd); box.appendChild(row);
  }
  return box;
}
function appendReasons(card, reasons) {
  if (!reasons || !reasons.length) return;
  const ul = document.createElement("ul"); ul.className = "reasons";
  for (const r of reasons) { const li = document.createElement("li"); li.textContent = r; ul.appendChild(li); }
  card.appendChild(ul);
}
function compareKeys(a, b) { return a === b ? { cls: "match", text: "🟢 일치" } : { cls: "mismatch", text: "🔴 불일치" }; }
function skeletonNote(a, b) { return (a !== b && a.slice(0, 14) === b.slice(0, 14)) ? "앞 14자(골격)는 같고 입체·전하만 다르다." : null; }

function hideVerdict() { $("verdictSection").hidden = true; $("verdictCard").innerHTML = ""; }
function hideSolo() { $("soloCard").hidden = true; $("soloCard").innerHTML = ""; }

// 화면의 중심: 이름의 정본과 SMILES 입력을 InChIKey 로 대조해 빨강/초록을 낸다.
function renderComparison(ref, refDesc, user, nameRaw) {
  const diff = highlightDiff(refDesc.smiles, user.smiles);
  const refSvg = svgWithHighlight(refDesc.smiles, diff && diff.side === "ref" ? diff : null, 420, 360);
  renderCanonical({ svg: refSvg, title: ref.title || nameRaw, cid: ref.cid, source: ref.source, smiles: refDesc.smiles, inchikey: refDesc.inchikey, formula: refDesc.formula });

  hideSolo();
  $("verdictSection").hidden = false;
  const card = $("verdictCard"); card.innerHTML = "";

  const verdict = compareKeys(refDesc.inchikey, user.inchikey);
  const pill = document.createElement("p"); pill.className = "verdict-pill " + verdict.cls; pill.textContent = verdict.text;
  card.appendChild(pill);

  const userSvg = svgWithHighlight(user.smiles, diff && diff.side === "user" ? diff : null, 260, 220);
  const box = document.createElement("div"); box.className = "drawbox-inline";
  const label = document.createElement("p"); label.className = "drawlabel"; label.textContent = "입력한 SMILES (복사 없음)";
  const draw = document.createElement("div"); draw.className = "draw"; draw.innerHTML = userSvg || "";
  box.append(label, draw); card.appendChild(box);

  const rows = [
    [`이름 "${ref.title || nameRaw}" 의 정본`, escapeHtml(refDesc.inchikey)],
    ["입력한 SMILES", markDiff(refDesc.inchikey, user.inchikey)],
    ["정본 화학식", escapeHtml(refDesc.formula || "-")],
  ];
  if (user.formula) {
    const note = formulaDiffNote(refDesc.formula, user.formula);
    rows.push(["입력 SMILES 의 화학식", escapeHtml(user.formula) + (note ? `  ← ${escapeHtml(note)}` : "")]);
  }
  card.appendChild(buildEvidenceBox(rows));

  const reasons = [];
  const skel = skeletonNote(refDesc.inchikey, user.inchikey);
  if (skel) reasons.push(skel);
  if (verdict.cls === "mismatch" && !diff) reasons.push("두 구조가 부분 포함 관계가 아니라(고리 크기·위치·치환기 차이 등) 어디가 다른지는 못 칠했다.");
  appendReasons(card, reasons);
}

// SMILES 만 있고 이름이 없을 때 - 대조 상대가 없으니 판정 없이 정체만 보여준다(복사 없음).
async function renderSoloSmiles(user, smilesRaw) {
  hideVerdict();
  const box = $("soloCard"); box.hidden = false; box.innerHTML = "";
  const who = await identifyByKey(user.inchikey);
  const draw = document.createElement("div"); draw.className = "canon-draw"; draw.innerHTML = user.svg || "";
  const info = document.createElement("div");
  const title = document.createElement("p"); title.className = "canon-title";
  title.textContent = who ? `PubChem: ${who.title}` : "PubChem 에 없는 구조";
  const dl = document.createElement("dl"); dl.className = "facts";
  const rows = [["화학식", user.formula], ["SMILES", user.smiles], ["InChIKey", user.inchikey]];
  for (const [k, v] of rows) {
    if (!v) continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  }
  const note = document.createElement("p"); note.className = "field-note";
  note.textContent = "이름을 채우면 이 SMILES 를 정본과 대조해 판정을 낸다.";
  info.append(title, dl, note);
  box.append(draw, info);
}

// ============================================================ 대조 엔진 - 이름 칸 + SMILES 칸을 함께 본다

let debounceTimer = null;
function scheduleEvaluate() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(evaluate, 400);
}
async function evaluate() {
  const nameRaw = $("nameInput").value.trim();
  const smilesRaw = $("smilesInput").value.trim();
  hideSuggestions();
  clearErr();
  if (!nameRaw && !smilesRaw) { hideCanonical(); hideVerdict(); hideSolo(); return; }
  if (!RDKit) { status("RDKit 로딩 중… 준비되면 자동으로 확인한다."); return; }
  status("확인 중…");
  try {
    const ref = nameRaw ? await resolveName(nameRaw) : null;
    if (nameRaw && !ref) {
      hideCanonical(); hideVerdict(); hideSolo();
      showSuggestions(nameRaw);
      fail(`"${nameRaw}" - 내장 표 423개에도 PubChem 에도 없다.`);
      status(`RDKit ${RDKit.version()} 준비됨`);
      return;
    }
    const refDesc = ref ? describeFull(ref.smiles) : null;
    const user = smilesRaw ? describeFull(smilesRaw) : null;
    if (smilesRaw && !user) fail("SMILES 를 읽을 수 없다 - RDKit 이 파싱하지 못했다: " + smilesRaw);
    else clearErr();

    if (ref && user) {
      renderComparison(ref, refDesc, user, nameRaw);
    } else if (ref) {
      renderCanonical({ svg: refDesc.svg, title: ref.title || nameRaw, cid: ref.cid, source: ref.source, smiles: refDesc.smiles, inchikey: refDesc.inchikey, formula: refDesc.formula });
      hideVerdict(); hideSolo();
    } else if (user) {
      hideCanonical();
      await renderSoloSmiles(user, smilesRaw);
    } else {
      hideCanonical(); hideVerdict(); hideSolo();
    }
    status(`RDKit ${RDKit.version()} 준비됨`);
  } catch (e) {
    hideCanonical(); hideVerdict(); hideSolo();
    fail("실패: " + (e && e.message ? e.message : e) + " (PubChem 조회는 네트워크가 필요하다)");
    status(RDKit ? `RDKit ${RDKit.version()} 준비됨` : "");
  }
}

$("nameInput").addEventListener("input", scheduleEvaluate);
$("nameInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(debounceTimer); evaluate(); } });
$("smilesInput").addEventListener("input", scheduleEvaluate);
$("smilesInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(debounceTimer); evaluate(); } });

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
// 이미지는 이름칸만 채운다 - 그림 자체 검사(OCSR)는 3GB 인식기가 필요해 로컬 CLI 의 몫이다.

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

async function handleImage(blob) {
  showImagePreview(blob);
  imageNote("OCR 로 이름을 읽는 중…");

  let hints = null;
  try {
    const mod = await import("./ocr.js");
    if (mod && typeof mod.readLabels === "function") hints = await mod.readLabels(blob);
  } catch (e) { /* web/ocr.js 아직 없다 - 임계 경로가 아니므로 조용히 건너뛴다 */ }

  const name = hints && hints.names && hints.names[0];
  if (name) {
    imageNote(`OCR 이 이름 "${name}" 을 읽어 이름칸에 채웠다 - 확인하고 필요하면 고쳐라.`);
    $("nameInput").value = name;
    await evaluate();
  } else if ($("nameInput").value.trim()) {
    imageNote("이미지에서 이름을 읽지 못했지만 이름칸에 이미 값이 있다 - 그대로 서버 대조에 쓴다.");
  } else {
    imageNote("이미지에서 이름을 읽지 못했다 - OCR(web/ocr.js)이 아직 없거나 찾지 못했다. 위 이름칸에 직접 입력하면 서버 대조에도 쓰인다.");
  }

  await checkServerImage(blob, $("nameInput").value.trim());
}

// "야생에서 잡은 오류" 샘플 버튼 - 미리 적어둔 값 없음. 실제로 이 이미지를 불러와
// 이름칸을 채우고 살아 있는 서버를 그대로 부른다(handleImage 와 완전히 같은 경로).
// 새로고침하면 사라지고 다시 누르면 다시 서버가 계산한다 - 하드코딩이 아니다.
async function loadWildcatchSample() {
  $("nameInput").value = "Caffeine";
  await evaluate();
  $("canonCard").scrollIntoView({ behavior: "smooth", block: "start" });
  let blob;
  try {
    const res = await fetch("evidence/caffeine_gemini.jpeg");
    if (!res.ok) throw new Error(`샘플 이미지 응답 ${res.status}`);
    blob = await res.blob();
  } catch (e) {
    imageNote("샘플 이미지를 이 방식으로는 못 불러왔다(" + (e && e.message ? e.message : e) + ") - file:// 로 열었다면 GitHub Pages 배포판에서 해보거나, 이미지를 직접 붙여넣어라.");
    return;
  }
  await handleImage(blob);
}

// ============================================================ 이미지 자체 대조 - 서버(S). 정본은 이미 떠 있으니 이건 나중에 채운다.

function showImageVerdictLoading() {
  $("imageVerdictSection").hidden = false;
  const card = $("imageVerdictCard"); card.innerHTML = "";
  const p = document.createElement("p"); p.className = "result-placeholder";
  p.textContent = "서버가 그림을 읽는 중… 처음이면(콜드스타트) 최대 1분 걸릴 수 있다 - 따뜻하면 2~3초.";
  card.appendChild(p);
}
function showImageVerdictNote(msg) {
  $("imageVerdictSection").hidden = false;
  const card = $("imageVerdictCard"); card.innerHTML = "";
  const p = document.createElement("p"); p.className = "result-placeholder"; p.textContent = msg;
  card.appendChild(p);
}

async function checkServerImage(blob, name) {
  showImageVerdictLoading();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS);
  try {
    const fd = new FormData();
    fd.append("image", blob, "image.png");
    fd.append("name", name || "");
    const res = await fetch(CHECK_URL, { method: "POST", body: fd, signal: controller.signal });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    renderImageVerdict(await res.json());
  } catch (e) {
    const timedOut = e && e.name === "AbortError";
    showImageVerdictNote(
      (timedOut ? "서버가 시간 안에 응답하지 않았다(최대 1분 대기했다). " : "서버에 연결하지 못했다. ") +
      "그림 자체는 확인하지 못했다 - 위 정본과 나란히 놓고 비교해 보라."
    );
  } finally {
    clearTimeout(timer);
  }
}

function renderImageVerdict(json) {
  const card = $("imageVerdictCard"); card.innerHTML = "";

  if (json.verdict === "unreadable") {
    const pill = document.createElement("p"); pill.className = "verdict-pill unreadable"; pill.textContent = "⚪ 읽지 못함";
    card.appendChild(pill);
    const note = document.createElement("p"); note.className = "result-placeholder";
    note.textContent = "서버가 그림을 확실히 읽지 못했다. 요청하신 구조는 위 정본이다 - 나란히 놓고 보라.";
    card.appendChild(note);
    appendReasons(card, json.reasons);
    return;
  }

  const pill = document.createElement("p");
  pill.className = "verdict-pill " + (json.verdict === "match" ? "match" : "mismatch");
  pill.textContent = json.verdict === "match" ? "🟢 일치" : "🔴 불일치";
  if (json.grade) {
    const g = document.createElement("span"); g.className = "grade-badge";
    g.textContent = json.grade === "strong" ? "확신: 강함 (인식기 둘 합의)" : "확신: 약함 (인식기 하나)";
    pill.append(" ", g);
  }
  card.appendChild(pill);

  const ref = json.reference, read = json.read;
  const rows = [];
  if (ref) rows.push([`이름 "${ref.name || ""}" 의 정본`, escapeHtml(ref.inchikey || "-")]);
  if (read) rows.push(["그림에서 읽은 것", markDiff(ref && ref.inchikey, read.inchikey)]);
  if (ref && ref.formula) rows.push(["정본 화학식", escapeHtml(ref.formula)]);
  if (read && read.heavy_formula) {
    const note = formulaDiffNote(ref && ref.formula, read.heavy_formula);
    rows.push(["그림에서 센 원자", escapeHtml(read.heavy_formula) + (note ? `  ← ${escapeHtml(note)}` : "")]);
  }
  if (rows.length) card.appendChild(buildEvidenceBox(rows));

  appendReasons(card, json.reasons);

  if (read && read.engines && read.engines.length) {
    const box = document.createElement("div"); box.className = "evidence";
    const title = document.createElement("div"); title.className = "k"; title.textContent = "인식기별 결과";
    box.appendChild(title);
    for (const e of read.engines) {
      const row = document.createElement("div"); row.className = "row";
      const k = document.createElement("div"); k.className = "k"; k.textContent = e.engine || "-";
      const v = document.createElement("div");
      v.textContent = `${e.inchikey || "(파싱 실패)"}${typeof e.confidence === "number" ? "  신뢰도 " + e.confidence.toFixed(3) : ""}`;
      row.append(k, v); box.appendChild(row);
    }
    card.appendChild(box);
  }
}

// 콜드스타트가 55초(따뜻하면 2.3초) - 페이지가 열리자마자 조용히 한 번 깨워 둔다.
// 응답은 상태 표시에만 쓴다. 실패해도 무시한다 - 크레딧 안 쓰는 공짜 최적화다.
function warmupServer() {
  const el = $("serverStatus");
  fetch(HEALTH_URL).then((res) => {
    el.textContent = res.ok ? "그림 검사 준비됨" : "그림 검사는 준비 중입니다 (이름·SMILES 검사는 지금 됩니다)";
  }).catch(() => {
    el.textContent = "그림 검사는 준비 중입니다 (이름·SMILES 검사는 지금 됩니다)";
  });
}

// ============================================================ 시작

$("foot").textContent = `내장 표 ${Object.keys(TABLE).length}개 화합물 (web/build_table.py 가 chemcheck/data 에서 구움). ` +
  `이름·SMILES 대조와 InChIKey·화학식 계산은 이 브라우저 안에서 표·PubChem·RDKit(WASM) 만으로 끝난다 - 서버가 필요 없다. ` +
  `이미지는 web/crop.js·web/ocr.js 가 있으면 이름칸을 자동으로 채우고(오늘은 없을 수 있다), 그림 자체는 서버(${CHECK_URL})가 읽어 대조한다 - ` +
  `처음이면(콜드스타트) 최대 1분, 서버가 응답하지 않으면 정본만 보여준다.`;

setupImageInput();
warmupServer();
const wildcatchBtn = $("wildcatchSampleBtn");
if (wildcatchBtn) wildcatchBtn.addEventListener("click", loadWildcatchSample);

const q = new URLSearchParams(location.search);
if (q.get("name")) $("nameInput").value = q.get("name");
if (q.get("smiles")) $("smilesInput").value = q.get("smiles");

const t0 = performance.now();
window.initRDKitModule().then((m) => {
  RDKit = m;
  status(`RDKit ${m.version()} 준비됨 (${Math.round(performance.now() - t0)}ms)`);
  if ($("nameInput").value.trim() || $("smilesInput").value.trim()) evaluate();
}).catch((e) => { status(""); fail("RDKit 을 불러오지 못했다: " + e); });
