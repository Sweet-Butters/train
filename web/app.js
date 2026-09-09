// web/app.js (U 소유) - try.html 의 도구 로직. index.html 은 정적 페이지라 이 파일을 쓰지 않는다.
// web/crop.js(C)·web/ocr.js(O)는 아직 없을 수 있다 - 있으면 쓰고 없으면 조용히 건너뛴다.
//
// 결과 영역은 하나다: 정본 카드가 0.5초 안에 뜨고(이름/SMILES, 서버 없이 완결),
// 이미지를 주면 같은 카드 안의 판정 슬롯이 서버 응답으로 나중에 채워진다.

const TABLE = window.CHEMCHECK_TABLE || {};
const ALIASES = window.CHEMCHECK_ALIASES || {};
const BY_KEY = {};
for (const [name, row] of Object.entries(TABLE)) if (row.inchikey) BY_KEY[row.inchikey] = { name, ...row };
const PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound";
// 서버 주소는 web/config.js 한 곳에만 있다. 경로(/api/check, /api/health)만 여기서 붙인다.
const SERVER = window.CHEMCHECK_SERVER || "";
const CHECK_URL = SERVER + "/api/check";
const HEALTH_URL = SERVER + "/api/health";
const CHECK_TIMEOUT_MS = 65000; // 첫 요청은 콜드스타트로 최대 60초 - 넉넉히 잡는다

const $ = (id) => document.getElementById(id);

// ── 결과 DOM 을 JS 가 만든다 ────────────────────────────────────────────────
// index.html 재설계에서 <article id="result"> 가 빈 껍데기가 되면서, app.js 가
// 기대하던 11 개 id(canon·molDraw·molTitle·molFacts·molSource·verdict·copy*)가
// 사라졌다. 모듈 최상단의 copyPng 리스너 등록이 null 에서 죽어 **그 아래 코드가
// 통째로 실행되지 않았다** - 예제 버튼도 판정 렌더도 그래서 아무 일이 없었다.
// HTML 에 있으면 그대로 쓰고, 없으면 여기서 만든다.
(function ensureResultDom() {
  const host = document.getElementById("result");
  if (!host || document.getElementById("verdict")) return;
  const mk = (tag, id, cls) => {
    const e = document.createElement(tag);
    if (id) e.id = id;
    if (cls) e.className = cls;
    return e;
  };
  const canon = mk("div", "canon", "canon");
  canon.hidden = true;
  canon.appendChild(mk("div", "molDraw", "mol-draw"));
  const side = mk("div", null, "mol-side");
  side.appendChild(mk("p", "molSource", "mol-source"));
  side.appendChild(mk("h2", "molTitle", "mol-name"));
  side.appendChild(mk("dl", "molFacts", "facts"));
  const row = mk("div", null, "copy-row");
  for (const [id, label] of [["copyPng", "PNG 복사"], ["copySvg", "SVG 다운로드"],
                             ["copySmiles", "SMILES 복사"], ["copyKey", "InChIKey 복사"]]) {
    const b = mk("button", id, "btn btn-sm");
    b.type = "button"; b.textContent = label;
    row.appendChild(b);
  }
  side.appendChild(row);
  side.appendChild(mk("p", "copyNote", "copy-note"));
  canon.appendChild(side);
  host.appendChild(canon);

  const v = mk("div", "verdict", "verdict");
  v.hidden = true;
  host.appendChild(v);
})();
let RDKit = null;
let currentMol = null;

function status(t) { $("rdkitStatus").textContent = t; }
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
  // Title(관용명)과 IUPACName 을 함께 받는다 - 새로 만난 분자는 관용명이 없고
  // IUPAC 명만 있는 경우가 많다. 둘 다 없으면 진짜 미등재다.
  const res = await fetch(`${PUBCHEM}/inchikey/${inchikey}/property/Title,IUPACName/JSON`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`PubChem 응답 ${res.status}`);
  const p = (await res.json()).PropertyTable.Properties[0];
  return { title: p.Title, iupac: p.IUPACName, cid: p.CID, source: "PubChem" };
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

// 판별 규칙: RDKit 이 파싱하면 SMILES, 못 하면 이름 - 추론이 아니라 규칙이다.
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

// ============================================================ 결과 카드 - 하나. 정본이 먼저, 판정은 같은 카드 안에서 나중에.

// 카드(#result)는 정본(#canon)이나 판정(#verdict) 중 하나라도 있을 때만 보인다.
// 이름 없이 이미지만 넣으면 정본 없이 판정 슬롯만 뜬다 - 그래도 카드는 하나다.
function syncResult() {
  const hasMol = !!currentMol, hasVerdict = !$("verdict").hidden;
  $("canon").hidden = !hasMol;
  $("result").hidden = !(hasMol || hasVerdict);
}
function renderMol(data) {
  currentMol = data;
  $("molDraw").innerHTML = data.svg || "";
  $("molSource").textContent = "정본 · " + (data.source || "");
  $("molTitle").textContent = data.title || "(PubChem 에 이름 없음)";
  const dl = $("molFacts"); dl.innerHTML = "";
  const cidLink = data.cid ? { href: `https://pubchem.ncbi.nlm.nih.gov/compound/${data.cid}`, text: `CID ${data.cid}` } : null;
  const rows = [["PubChem", cidLink], ["화학식", data.formula], ["SMILES", data.smiles], ["InChIKey", data.inchikey]];
  for (const [k, val] of rows) {
    if (val === undefined || val === null || val === "") continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd");
    if (val && val.href) { const a = document.createElement("a"); a.href = val.href; a.textContent = val.text; a.target = "_blank"; a.rel = "noopener"; dd.appendChild(a); }
    else dd.textContent = val;
    dl.append(dt, dd);
  }
  $("copyNote").textContent = "";
  syncResult();
}
function hideMol() { currentMol = null; syncResult(); }
function hideVerdict() { const v = $("verdict"); v.hidden = true; v.innerHTML = ""; v.className = "verdict"; syncResult(); }
// 판정 슬롯을 비우고 연다. state 는 match / mismatch / unreadable / "" (대기·안내).
function openVerdict(state) {
  const v = $("verdict"); v.hidden = false; v.innerHTML = ""; v.className = "verdict" + (state ? " " + state : "");
  syncResult();
  return v;
}

async function attemptResolve(raw) {
  raw = raw.trim();
  hideSuggestions();
  hideVerdict(); // 새 조회는 이전 판정과 무관하다
  if (!raw) { hideMol(); clearErr(); return; }
  if (!RDKit) { status("RDKit 로딩 중… 준비되면 자동으로 확인한다."); return; }
  status("확인 중…");
  clearErr();
  try {
    const res = await resolveSingle(raw);
    if (!res) {
      hideMol();
      showSuggestions(raw);
      fail(`"${raw}" - 내장 표 423개에도, PubChem 에도, 유효한 SMILES 로도 못 찾았다.`);
      status(`RDKit ${RDKit.version()} 준비됨`);
      return;
    }
    renderMol(res);
    status(`RDKit ${RDKit.version()} 준비됨`);
  } catch (e) {
    hideMol();
    fail("실패: " + (e && e.message ? e.message : e) + " (PubChem 조회는 네트워크가 필요하다)");
    status(RDKit ? `RDKit ${RDKit.version()} 준비됨` : "");
  }
}

let debounceTimer = null;
$("queryInput").addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => attemptResolve($("queryInput").value), 400);
});
$("queryInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { clearTimeout(debounceTimer); attemptResolve($("queryInput").value); }
});

// ============================================================ 정본 복사 (넷) - 이 카드에만 둔다. 서버가 읽은 그림 쪽엔 두지 않는다.

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
  if (!currentMol) return;
  let blob;
  try { blob = await svgToPngBlob(currentMol.svg, 420, 360); }
  catch (e) { copyNote("PNG 생성 실패: " + (e && e.message ? e.message : e)); return; }
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    copyNote("PNG 를 클립보드에 복사했다.");
  } catch (e) {
    downloadBlob(blob, safeFileName(currentMol) + ".png");
    copyNote("클립보드 복사가 막혀 파일로 내려받았다.");
  }
});
$("copySvg").addEventListener("click", () => {
  if (!currentMol) return;
  downloadBlob(new Blob([currentMol.svg], { type: "image/svg+xml" }), safeFileName(currentMol) + ".svg");
  copyNote("SVG 를 내려받았다.");
});
$("copySmiles").addEventListener("click", () => copyText(currentMol && currentMol.smiles, "SMILES"));
$("copyKey").addEventListener("click", () => copyText(currentMol && currentMol.inchikey, "InChIKey"));

// ============================================================ 이미지 입력 - crop.js(C)·ocr.js(O) 있으면 쓰고 없으면 대체. 임계 경로 아님.

let usingCropper = false; // crop.js 가 붙으면 미리보기·크롭 UI 는 crop.js 가 맡는다
// ── 예제 - 심사위원이 아무것도 준비하지 않고 바로 확인할 수 있게 ────────────
// evidence/crops 의 실제 그림을 사용자가 올린 것과 **같은 경로**로 통과시킨다.
// 미리 채우는 것은 입력(그림·이름)뿐이고 판정은 서버가 그때 낸다.
const EXAMPLE_GROUPS = [
  { label: "AI 가 그린 그림 - 이름과 대조합니다", items: [
      { file: "caffeine_gemini_crop.png", name: "Caffeine",  label: "Gemini 카페인" },
      { file: "caffeine_gpt_crop.png",    name: "Caffeine",  label: "GPT 카페인" },
      { file: "alanine_gemini_crop.png",  name: "L-alanine", label: "Gemini 알라닌" },
      { file: "alanine_gpt_crop.png",     name: "L-alanine", label: "GPT 알라닌" },
  ] },
  { label: "처음 보는 분자 - PubChem 에도 내장 표에도 없습니다. 그림에서만 읽습니다", items: [
      { file: "novel_a.png", name: "", label: "신규 A" },
      { file: "novel_b.png", name: "", label: "신규 B" },
      { file: "novel_c.png", name: "", label: "신규 C" },
  ] },
];

async function runExample(ex) {
  const btns = document.querySelectorAll("#examples button");
  btns.forEach((b) => { b.disabled = true; });
  try {
    const res = await fetch("evidence/crops/" + ex.file);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const blob = await res.blob();
    const q = $("queryInput");
    if (q) { q.value = ex.name || ""; }
    // 원본을 입력 칸에 실제로 띄운다 - 사용자가 붙여넣은 것과 같은 상태가 되게.
    if (CROPPER && typeof CROPPER.load === "function") CROPPER.load(blob);
    else showImagePreview(blob);
    await handleImage(blob);
  } catch (e) {
    const n = $("imageNote");
    if (n) n.textContent = "예제를 불러오지 못했다: " + (e && e.message ? e.message : e);
  } finally {
    btns.forEach((b) => { b.disabled = false; });
  }
}

function setupExamples() {
  const box = $("examples");
  if (!box) return;
  for (const g of EXAMPLE_GROUPS) {
    const row = document.createElement("div");
    row.className = "example-row";
    const lab = document.createElement("span");
    lab.className = "example-group";
    lab.textContent = g.label;
    row.appendChild(lab);
    for (const ex of g.items) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip";
      b.textContent = ex.label;
      b.addEventListener("click", () => runExample(ex));
      row.appendChild(b);
    }
    box.appendChild(row);
  }
}

// ============================================================ 요금제 - 눌린다.
// 월/연 토글, 선택 카드(테두리·배지), 요약 한 줄. role=radiogroup + 화살표 키.
// web-ui 브랜치(Sweet-Butters/web-ui)의 app.js 에서 그대로 포팅했다 - HTML·CSS
// (#plans·.plan·.plan-cta·.toggle-btn·.is-selected·.is-on)는 이미 있었다.
(function pricing() {
  const plans = [...document.querySelectorAll("#plans .plan")];
  if (!plans.length) return;
  let cycle = "monthly";
  const priceOf = (p) => {
    const amount = p.querySelector(".amount");
    const per = p.querySelector(".per");
    return (amount.dataset[cycle] || amount.textContent) +
      (per ? (per.dataset[cycle] || per.textContent).replace(" ", "") : "");
  };
  function select(p, focus) {
    for (const q of plans) {
      const on = q === p;
      q.setAttribute("aria-checked", on);
      q.tabIndex = on ? 0 : -1;
      q.classList.toggle("is-selected", on);
    }
    $("planSummary").textContent = p.dataset.summary.replace("{price}", priceOf(p));
    if (focus) p.focus();
  }
  function applyCycle() {
    for (const s of document.querySelectorAll("#plans [data-monthly]")) s.textContent = s.dataset[cycle];
    for (const b of document.querySelectorAll(".toggle-btn")) {
      const on = b.dataset.cycle === cycle;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on);
    }
    select(plans.find((p) => p.getAttribute("aria-checked") === "true") || plans[1]);
  }
  for (const p of plans) {
    p.addEventListener("click", (e) => { if (!e.target.closest(".plan-cta")) select(p); });
    p.addEventListener("keydown", (e) => {
      const i = plans.indexOf(p);
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault(); select(plans[(i + 1) % plans.length], true);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault(); select(plans[(i - 1 + plans.length) % plans.length], true);
      } else if ((e.key === " " || e.key === "Enter") && e.target === p) {
        e.preventDefault(); select(p);
      }
    });
  }
  for (const b of document.querySelectorAll(".toggle-btn")) {
    b.addEventListener("click", () => { cycle = b.dataset.cycle; applyCycle(); });
  }
  const startBtn = document.querySelector('[data-action="start"]');
  if (startBtn) {
    startBtn.addEventListener("click", (e) => {
      e.preventDefault();
      $("queryInput").scrollIntoView({ behavior: "smooth", block: "center" });
      $("queryInput").focus();
    });
  }
  applyCycle();
})();

let CROPPER = null;   // crop.js 가 준 { recrop, reset } - 크기 게이트에 걸리면 다시 연다

async function setupImageInput() {
  const dropzone = $("dropzone");
  try {
    const mod = await import("./crop.js?v=202609100444");
    if (mod && typeof mod.mountCropper === "function") {
      CROPPER = mod.mountCropper(dropzone, { onCrop: handleImage }) || null;
      usingCropper = true;
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
  if (!usingCropper) showImagePreview(blob);
  imageNote("OCR 로 이름을 읽는 중…");

  let hints = null;
  try {
    const mod = await import("./ocr.js?v=202609100444");
    if (mod && typeof mod.readLabels === "function") hints = await mod.readLabels(blob);
  } catch (e) { /* web/ocr.js 아직 없다 - 임계 경로가 아니므로 조용히 건너뛴다 */ }

  const name = hints && hints.names && hints.names[0];
  if (name) {
    imageNote(`OCR 이 이름 "${name}" 을 읽어 이름칸에 채웠다 - 확인하고 필요하면 고쳐라.`);
    $("queryInput").value = name;
    await attemptResolve(name);
  } else if ($("queryInput").value.trim()) {
    imageNote("이미지에서 이름을 읽지 못했지만 입력칸에 이미 값이 있다 - 그대로 서버 대조에 쓴다.");
  } else {
    imageNote("이미지에서 이름을 읽지 못했다 - OCR(web/ocr.js)이 아직 없거나 찾지 못했다. 위 칸에 직접 입력하면 서버 대조에도 쓰인다.");
  }

  await checkServerImage(blob, $("queryInput").value.trim());
}

// ============================================================ 이미지 자체 대조 - 서버(S). 정본 카드 안의 같은 자리에 채운다.

// 스피너로 덮지 않는다 - 정본은 그대로 두고, 판정 슬롯에 한 줄만 적는다.
function showVerdictLoading() {
  showVerdictNote("서버가 그림을 읽는 중… 처음이면(콜드스타트) 최대 1분 걸릴 수 있다. 따뜻하면 2~3초.");
}
function showVerdictNote(msg) {
  const card = openVerdict("");
  const p = document.createElement("p"); p.className = "verdict-note"; p.textContent = msg;
  card.appendChild(p);
}

async function checkServerImage(blob, name) {
  showVerdictLoading();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS);
  try {
    const fd = new FormData();
    fd.append("image", blob, "image.png");
    fd.append("name", name || "");
    const res = await fetch(CHECK_URL, { method: "POST", body: fd, signal: controller.signal });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    renderVerdict(await res.json());
  } catch (e) {
    const timedOut = e && e.name === "AbortError";
    showVerdictNote(
      (timedOut ? "서버가 시간 안에 응답하지 않았다(최대 1분 대기했다). " : "서버에 연결하지 못했다. ") +
      "그림 자체는 확인하지 못했다 - 위 정본과 나란히 놓고 비교해 보라."
    );
  } finally {
    clearTimeout(timer);
  }
}

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
    if (el === "H") continue; // heavy_formula 는 중원자만 센다 - 수소 차이는 비교 대상이 아니다
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
    const vd = document.createElement("div"); vd.className = "v"; vd.innerHTML = html;
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

function verdictLabel(state, text) {
  const h = document.createElement("p"); h.className = "verdict-label";
  const dot = document.createElement("span"); dot.className = "dot " + state;
  h.append(dot, text);
  return h;
}

// ── 인식기가 갈렸을 때: 문자열 대신 **구조를 그려서** 보여준다 ──────────────
// InChIKey 는 사람이 못 읽는다. 무엇을 읽었는지 알려면 그림이어야 한다.
function keyName(inchikey) {
  if (!inchikey) return null;
  const row = BY_KEY[inchikey] || BY_KEY[(inchikey || "").slice(0, 14)];
  return row ? (row.title || row.name) : null;
}

function smallSvg(smiles, w, h) {
  if (!RDKit || !smiles) return "";
  let mol = null;
  try {
    mol = RDKit.get_mol(smiles);
    if (!mol || !mol.is_valid()) return "";
    return mol.get_svg(w, h);
  } catch (e) { return ""; }
  finally { if (mol) mol.delete(); }
}

function candidateTile(title, smiles, inchikey, verdictText, tone) {
  const box = document.createElement("div");
  box.style.cssText = "flex:1 1 190px;min-width:180px;border:1px solid " +
    (tone === "ok" ? "#7bb37f" : tone === "no" ? "#dd9999" : "#dddddd") +
    ";border-radius:8px;padding:.5rem;background:#fff";
  const h = document.createElement("div");
  h.style.cssText = "font-size:.78rem;opacity:.7;margin-bottom:.25rem";
  h.textContent = title;
  box.appendChild(h);

  const svg = smallSvg(smiles, 190, 150);
  const art = document.createElement("div");
  art.style.cssText = "min-height:150px;display:flex;align-items:center;justify-content:center";
  if (svg) { art.innerHTML = svg; }
  else { art.textContent = "그리지 못함"; art.style.fontSize = ".8rem"; art.style.opacity = ".6"; }
  box.appendChild(art);

  const nm = keyName(inchikey);
  const label = document.createElement("div");
  label.style.cssText = "font-size:.82rem;margin-top:.3rem;font-weight:600";
  label.textContent = nm || (inchikey ? "이름 없음 (표·PubChem 미등재)" : "읽지 못함");
  box.appendChild(label);

  if (inchikey) {
    const k = document.createElement("div");
    k.style.cssText = "font-size:.7rem;opacity:.55;word-break:break-all";
    k.textContent = inchikey;
    box.appendChild(k);
  }
  if (verdictText) {
    const v = document.createElement("div");
    v.style.cssText = "font-size:.78rem;margin-top:.25rem;color:" +
      (tone === "ok" ? "#1f7a3d" : tone === "no" ? "#b3261e" : "#666666");
    v.textContent = verdictText;
    box.appendChild(v);
  }
  return box;
}

function appendCandidates(card, json) {
  const read = json.read || {};
  const engines = read.engines || [];
  if (!engines.length) return false;

  const ref = json.reference;
  const refSkel = ref && ref.inchikey ? ref.inchikey.slice(0, 14) : null;

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:.6rem;flex-wrap:wrap;margin:.6rem 0";

  if (ref && ref.smiles) {
    row.appendChild(candidateTile('정본 "' + (ref.name || "") + '"', ref.smiles, ref.inchikey, "요청하신 구조", ""));
  }
  let anyMatch = false;
  for (const e of engines) {
    const skel = e.inchikey ? e.inchikey.slice(0, 14) : null;
    let tone = "", txt = "";
    if (refSkel && skel) {
      if (skel === refSkel) { tone = "ok"; txt = "정본과 같음"; anyMatch = true; }
      else { tone = "no"; txt = "정본과 다름"; }
    } else if (!e.inchikey) {
      tone = "no"; txt = "RDKit 이 분자로 못 받음";
    }
    row.appendChild(candidateTile(e.engine + " 가 읽음", e.smiles, e.inchikey, txt, tone));
  }
  card.appendChild(row);

  const tip = document.createElement("p");
  tip.className = "verdict-note";
  tip.textContent = anyMatch
    ? "인식기 하나는 정본과 같게 읽었습니다. 다른 하나가 갈렸으므로 판정하지 않습니다 - 사진이 흐리거나 기울면 이런 일이 생깁니다. 구조 영역만 잘라 다시 넣어 보세요."
    : "두 인식기가 서로 다르게 읽었습니다. 어느 쪽이 맞는지 우리가 모르므로 판정하지 않습니다 - 구조 영역만 잘라 다시 넣어 보세요.";
  card.appendChild(tip);
  return true;
}

// ── 이름 없이 그림만 넣은 경우: 판정이 아니라 **무엇으로 읽혔는가** 를 보여준다 ──
// 대조할 이름이 없으므로 "일치/다름" 은 성립하지 않는다. 대신 읽은 구조를 그리고,
// InChIKey 로 이름을 역조회하고, SMILES 를 복사할 수 있게 준다.
function appendIdentification(card, json) {
  const read = json.read || {};
  if (!read.smiles) return false;

  const h = document.createElement("p");
  h.className = "verdict-grade";
  h.textContent = json.grade === "strong"
    ? "인식기 둘이 서로 다른 구조로 학습됐는데도 같게 읽었습니다"
    : "인식기 하나가 읽은 것입니다 - 대조할 이름이 없어 확인되지 않았습니다";
  card.appendChild(h);

  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;gap:.8rem;flex-wrap:wrap;align-items:flex-start;margin:.5rem 0";

  const svg = smallSvg(read.smiles, 220, 180);
  if (svg) {
    const art = document.createElement("div");
    art.style.cssText = "border:1px solid #e0e0e0;border-radius:8px;padding:.3rem;background:#fff";
    art.innerHTML = svg;
    wrap.appendChild(art);
  }

  const info = document.createElement("div");
  info.style.cssText = "flex:1 1 260px;min-width:240px";

  const nameEl = document.createElement("p");
  nameEl.style.cssText = "font-size:1.05rem;font-weight:700;margin:.1rem 0 .35rem";
  const local = keyName(read.inchikey);
  nameEl.textContent = local || "이름을 찾는 중…";
  info.appendChild(nameEl);

  const iupacEl = document.createElement("p");
  iupacEl.style.cssText = "font-size:.85rem;opacity:.8;margin:.1rem 0 .4rem;word-break:break-word";
  info.appendChild(iupacEl);

  const rows = [];
  if (read.smiles) rows.push(["SMILES", escapeHtml(read.smiles)]);
  if (read.inchikey) rows.push(["InChIKey", escapeHtml(read.inchikey)]);
  if (read.heavy_formula) rows.push(["중원자 조성", escapeHtml(read.heavy_formula)]);
  info.appendChild(buildEvidenceBox(rows));

  const bar = document.createElement("div");
  bar.style.cssText = "margin:.45rem 0";
  const mk = (label, text) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn btn-sm";
    b.textContent = label;
    b.style.cssText = "margin:.15rem .3rem .15rem 0";
    b.addEventListener("click", () => copyText(text, label.replace("복사", "").trim()));
    return b;
  };
  bar.appendChild(mk("SMILES 복사", read.smiles));
  if (read.inchikey) bar.appendChild(mk("InChIKey 복사", read.inchikey));
  info.appendChild(bar);

  const tip = document.createElement("p");
  tip.className = "verdict-note";
  tip.textContent = "이름을 위 칸에 넣으면 정본과 대조해 맞는지까지 판정합니다.";
  info.appendChild(tip);

  wrap.appendChild(info);
  card.appendChild(wrap);

  // 내장 표에 없으면 PubChem 으로 역조회한다. 실패해도 화면은 이미 서 있다.
  if (!local && read.inchikey) {
    identifyByKey(read.inchikey)
      .then((hit) => {
        if (!hit) {
          nameEl.textContent = "이름 없음 - PubChem 에 등재되지 않은 분자입니다";
          iupacEl.textContent = "새 분자이거나, 아직 이름이 붙지 않은 구조입니다. SMILES 로 쓰십시오.";
          return;
        }
        const common = hit.title || hit.name;
        const iupac = hit.iupac;
        nameEl.textContent = common || iupac || "이름 없음";
        if (iupac && iupac !== common) iupacEl.textContent = "IUPAC · " + iupac;
        else if (hit.source) iupacEl.textContent = "출처 · " + hit.source;
      })
      .catch(() => { nameEl.textContent = "이름 조회 실패 (네트워크)"; });
  }
  return true;
}

function renderVerdict(json) {
  const state = json.verdict === "match" ? "match" : json.verdict === "mismatch" ? "mismatch" : "unreadable";
  const card = openVerdict(state);

  if (state === "unreadable") {
    card.appendChild(verdictLabel(state, "판정 불가"));
    // 이름 없이 그림만 넣었고 읽기에는 성공했다 - 판정이 아니라 식별을 보여준다.
    const noName = !json.reference;
    if (noName && (json.read || {}).smiles) {
      card.appendChild(verdictLabel("unreadable", "이 그림은 이렇게 읽혔습니다"));
      appendIdentification(card, json);
      return;
    }

    // 슬라이드를 통째로 넣어 "너무 크다" 로 걸린 경우 - 막다른 길이 아니라 한 동작이 되게.
    const tooBig = (json.reasons || []).some((x) => x.indexOf("지나치게 큽니다") >= 0);
    if (tooBig) {
      const note = document.createElement("p"); note.className = "verdict-note";
      note.textContent = "슬라이드 전체를 넣으신 것 같습니다. 인식기는 구조 그림만 읽습니다 - 구조 부분만 잘라 주세요.";
      card.appendChild(note);
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn btn-primary";
      b.textContent = "구조 부분만 잘라서 다시 시도 →";
      b.style.cssText = "margin:.4rem 0";
      b.addEventListener("click", () => {
        if (!(CROPPER && CROPPER.recrop && CROPPER.recrop())) {
          const dz = $("dropzone");
          if (dz && dz.scrollIntoView) dz.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
      card.appendChild(b);
      appendReasons(card, json.reasons);
      return;
    }

    const shown = appendCandidates(card, json);
    if (!shown) {
      const note = document.createElement("p"); note.className = "verdict-note";
      note.textContent = "그림에서 구조를 읽지 못했습니다. 요청하신 구조는 위 정본입니다 - 나란히 놓고 보세요.";
      card.appendChild(note);
    }
    appendReasons(card, json.reasons);
    return;
  }

  card.appendChild(verdictLabel(state, state === "match" ? "일치" : "다름"));
  if (json.grade) {
    // 확신 등급은 색이 아니라 작은 글씨 한 줄로.
    const g = document.createElement("p"); g.className = "verdict-grade";
    g.textContent = json.grade === "strong" ? "확신 강함 · 인식기 둘이 같은 골격을 읽었다" : "확신 약함 · 인식기 하나의 답이다";
    card.appendChild(g);
  }

  const ref = json.reference, read = json.read;
  const rows = [];
  if (ref) rows.push([`"${ref.name || ""}" 의 정본`, escapeHtml(ref.inchikey || "-")]);
  if (read) rows.push(["그림에서 읽은 것", markDiff(ref && ref.inchikey, read.inchikey)]);
  if (ref && ref.formula) rows.push(["정본 화학식", escapeHtml(ref.formula)]);
  if (read && read.heavy_formula) {
    const note = formulaDiffNote(ref && ref.formula, read.heavy_formula);
    rows.push(["그림에서 센 원자", escapeHtml(read.heavy_formula) + (note ? ` <span class="note">${escapeHtml(note)}</span>` : "")]);
  }
  if (rows.length) card.appendChild(buildEvidenceBox(rows));

  appendReasons(card, json.reasons);

  if (read && read.engines && read.engines.length) {
    const title = document.createElement("p"); title.className = "evidence-title"; title.textContent = "인식기별 결과";
    card.appendChild(title);
    card.appendChild(buildEvidenceBox(read.engines.map((e) => [
      e.engine || "-",
      escapeHtml(e.inchikey || "(파싱 실패)") + (typeof e.confidence === "number" ? ` <span class="note">신뢰도 ${e.confidence.toFixed(3)}</span>` : ""),
    ])));
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

// index.html 의 "야생에서 잡은 오류" 이야기에서 "직접 해보기" 로 넘어오면(?sample=caffeine_gemini)
// 이름칸을 채우고 그 이미지를 실제로 불러와 이 페이지의 정상 경로(handleImage)를 그대로 태운다.
// 미리 적어둔 값은 없다 - 매번 서버가 새로 계산한다.
async function loadSample(key) {
  const samples = { caffeine_gemini: { name: "Caffeine", path: "evidence/caffeine_gemini_crop.png" } };
  // 통째 슬라이드가 아니라 **구조 영역만 오려낸 것**을 보낸다. 이야기에 적힌 CSXLFNRQOLIQAN
  // 이 그 기준이고, 통째로 보내면 서버가 "판정 불가 - 구조 부분만 잘라서" 로 물러난다.
  const s = samples[key];
  if (!s) return;
  $("queryInput").value = s.name;
  await attemptResolve(s.name);
  let blob;
  try {
    const res = await fetch(s.path);
    if (!res.ok) throw new Error(`샘플 이미지 응답 ${res.status}`);
    blob = await res.blob();
  } catch (e) {
    imageNote("샘플 이미지를 이 방식으로는 못 불러왔다(" + (e && e.message ? e.message : e) + ") - file:// 로 열었다면 GitHub Pages 배포판에서 해보거나, 이미지를 직접 붙여넣어라.");
    return;
  }
  await handleImage(blob);
}

// ============================================================ 시작

setupImageInput();
setupExamples();
warmupServer();

const q = new URLSearchParams(location.search);
if (q.get("name")) $("queryInput").value = q.get("name");

const t0 = performance.now();
window.initRDKitModule().then((m) => {
  RDKit = m;
  status(`RDKit ${m.version()} 준비됨 (${Math.round(performance.now() - t0)}ms)`);
  const sample = q.get("sample");
  if (sample) loadSample(sample);
  else if ($("queryInput").value.trim()) attemptResolve($("queryInput").value);
}).catch((e) => { status(""); fail("RDKit 을 불러오지 못했다: " + e); });
