// web/ocr.js  (O 트랙 소유 — 동결 인터페이스: readLabels(blob))
//
// 브라우저에서 Tesseract.js(CDN, ESM 빌드)로 이미지 안 텍스트를 읽어
// 화합물 이름 후보를 뽑는다. web/compounds.js(423 개 표)와 편집거리로 대조해
// OCR 오독("Coffeine" 등)을 정본 이름으로 복구한다.
//
// 범위 (코디네이터 지시, 2026-09-09): SMILES/화학식은 신뢰할 근거가 없어 뽑지 않는다.
// formula/smiles 는 인터페이스 유지를 위해 필드만 남기고 항상 null 이다.
// 임계 경로가 아니다 — 실패하면 조용히 빈 결과를 돌려준다.

const TESSERACT_ESM_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.esm.min.js';
const COMPOUNDS_URL = new URL('./compounds.js', import.meta.url).href;

// 이름 후보와 정본 표를 대조할 때 이 거리 이상이면 버린다(문자열 길이에 비례).
const MAX_MATCH_RATIO = 0.22;
// 후보와 표 항목의 길이가 이보다 더 차이 나면(짧은 쪽/긴 쪽) 애초에 비교하지 않는다 -
// 무관한 짧은 잡음이 긴 이름과 우연히 편집거리로 가까워지는 오탐을 막는다.
const MIN_LENGTH_RATIO = 0.6;
const MAX_NAME_CANDIDATES = 8;

let tesseractModulePromise = null;
function loadTesseract() {
  if (!tesseractModulePromise) {
    tesseractModulePromise = import(/* @vite-ignore */ TESSERACT_ESM_URL).then((mod) => mod.default || mod);
  }
  return tesseractModulePromise;
}

let workerPromise = null;
function getWorker() {
  if (!workerPromise) {
    workerPromise = loadTesseract()
      .then((Tesseract) => Tesseract.createWorker('eng'))
      .catch((err) => {
        workerPromise = null;
        throw err;
      });
  }
  return workerPromise;
}

let compoundsPromise = null;
// window.CHEMCHECK_TABLE: { "정규화안된 소문자 이름": {cid, inchikey, smiles, title} }
function loadCompoundsTable() {
  if (compoundsPromise) return compoundsPromise;
  compoundsPromise = new Promise((resolve) => {
    if (typeof window === 'undefined') return resolve(null);
    if (window.CHEMCHECK_TABLE) return resolve(window.CHEMCHECK_TABLE);
    const script = document.createElement('script');
    script.src = COMPOUNDS_URL;
    script.onload = () => resolve(window.CHEMCHECK_TABLE || null);
    script.onerror = () => resolve(null);
    document.head.appendChild(script);
  }).catch(() => null);
  return compoundsPromise;
}

/**
 * @param {Blob} blob
 * @returns {Promise<{names: string[], formula: string|null, smiles: string|null}>}
 */
export async function readLabels(blob) {
  const empty = { names: [], formula: null, smiles: null };
  try {
    const [worker, table] = await Promise.all([getWorker(), loadCompoundsTable()]);
    const { data } = await worker.recognize(blob);
    const text = (data && data.text) || '';
    return {
      names: extractNames(text, table),
      formula: null,
      smiles: null,
    };
  } catch (err) {
    return empty;
  }
}

// ---------------------------------------------------------------------------
// 이름 후보 + 정본 표 대조
// ---------------------------------------------------------------------------

function normalizeForMatch(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9가-힣]/g, '');
}

function levenshtein(a, b) {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1);
  let curr = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n];
}

// OCR 원문에서 이름일 법한 후보 구절을 뽑는다: 줄 전체, 그리고 줄 안의 1~4 단어 조각.
function collectRawCandidates(rawText) {
  const lines = (rawText || '').split(/\r?\n/);
  const candidates = new Set();
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (line.length < 3 || line.length > 60) continue;
    candidates.add(line);

    // 쉼표/괄호로 나뉜 조각도 후보로 (예: "1,3,7-Trimethylxanthine (Caffeine)").
    for (const part of line.split(/[(),;]/)) {
      const p = part.trim();
      if (p.length >= 3 && p.length <= 60) candidates.add(p);
    }

    // 단어 n-그램(1~4 단어)도 후보로 — 제목 줄에 부가 텍스트가 섞였을 때 대비.
    const words = line.split(/\s+/).filter(Boolean);
    for (let n = 1; n <= 4 && n <= words.length; n++) {
      for (let i = 0; i + n <= words.length; i++) {
        const phrase = words.slice(i, i + n).join(' ');
        if (phrase.length >= 3 && phrase.length <= 60) candidates.add(phrase);
      }
    }
  }
  return Array.from(candidates);
}

// 표 대조 없이도 쓸 수 있는 "라틴 이름처럼 생긴" 후보인지 (표가 못 실렸을 때 최후 수단).
function looksLikeLatinName(s) {
  return /^[A-Za-z][A-Za-z0-9\-,'()\s]*$/.test(s) && /[A-Za-z]{3,}/.test(s);
}

export function extractNames(rawText, table) {
  const rawCandidates = collectRawCandidates(rawText);

  if (table && typeof table === 'object') {
    const entries = Object.keys(table).map((key) => ({
      norm: normalizeForMatch(key),
      title: table[key] && table[key].title,
    })).filter((e) => e.norm && e.title);

    const matches = []; // {title, dist, ratio}
    const seenTitles = new Set();
    for (const cand of rawCandidates) {
      const normCand = normalizeForMatch(cand);
      if (normCand.length < 3) continue;
      let best = null;
      for (const entry of entries) {
        // 길이가 너무 다르면(짧은/긴 쪽 어느 방향이든) 비교하지 않는다.
        const lenRatio = Math.min(entry.norm.length, normCand.length) / Math.max(entry.norm.length, normCand.length);
        if (lenRatio < MIN_LENGTH_RATIO) continue;
        const dist = levenshtein(normCand, entry.norm);
        const ratio = dist / Math.max(entry.norm.length, normCand.length);
        if (ratio > MAX_MATCH_RATIO) continue;
        if (!best || dist < best.dist) best = { title: entry.title, dist, ratio };
      }
      if (best && !seenTitles.has(best.title)) {
        seenTitles.add(best.title);
        matches.push(best);
      }
    }

    if (matches.length > 0) {
      matches.sort((a, b) => a.ratio - b.ratio);
      return matches.slice(0, MAX_NAME_CANDIDATES).map((m) => m.title);
    }

    // 표는 불러왔는데 근접 후보가 하나도 없다면 정직하게 빈 배열을 돌려준다 -
    // OCR 잡음을 이름인 것처럼 입력칸에 채우지 않는다.
    return [];
  }

  // 표 자체를 못 불러왔을 때만 최후 수단으로 라틴 문자 이름처럼 생긴 줄을 돌려준다.
  const fallback = [];
  const seen = new Set();
  for (const cand of rawCandidates) {
    if (!looksLikeLatinName(cand)) continue;
    const key = cand.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    fallback.push(cand);
    if (fallback.length >= MAX_NAME_CANDIDATES) break;
  }
  return fallback;
}
