// web/crop.js — C 트랙 소유. 동결 인터페이스: mountCropper(container, { onCrop })
//
// 왜 크롭이 정상 흐름인가 (docs/WEB_CONTRACT.md 개정 1):
// 서버·OCSR 은 취소됐고 OCR 이 핵심 경로다. 슬라이드에는 제목·화학식·SMILES 같은
// 텍스트가 여기저기 흩어져 있어 전체를 그대로 먹이면 OCR 정확도가 떨어진다.
// 그래서 이 모듈의 목적은 "인식기에 깨끗한 구조"가 아니라 "OCR 에 깨끗한 텍스트"다 -
// 사용자가 제목/화학식/SMILES 가 적힌 영역을 원본 해상도 그대로 빠르게 오려낼 수 있어야 한다.

const MAX_DISPLAY = 800; // 화면 표시용 캔버스 최대 변 길이(px). 원본은 그대로 둔다.
const HANDLE = 11; // 리사이즈 핸들 히트 영역(px, 표시 좌표)

let styleInjected = false;
function injectStyle() {
  if (styleInjected) return;
  styleInjected = true;
  const s = document.createElement("style");
  s.textContent = `
.cropx { --cropx-ink:#1b1b1f; --cropx-mute:#6b6f76; --cropx-line:#d9dce1; --cropx-accent:#1b1b1f; font: 14px/1.4 system-ui, "Malgun Gothic", sans-serif; color: var(--cropx-ink); }
.cropx-drop { border: 2px dashed var(--cropx-line); border-radius: 10px; padding: 28px 16px; text-align: center; color: var(--cropx-mute); cursor: pointer; transition: border-color .15s, background .15s; }
.cropx-drop.cropx-over { border-color: var(--cropx-ink); background: #f4f4f5; }
.cropx-drop p { margin: 0 0 10px; }
.cropx-drop button { padding: 7px 14px; border:1px solid var(--cropx-ink); background:#fff; color:var(--cropx-ink); border-radius:6px; font:inherit; cursor:pointer; }
.cropx-stage { position: relative; display: inline-block; user-select: none; touch-action: none; }
.cropx-stage canvas { display: block; border-radius: 6px; border: 1px solid var(--cropx-line); background: #fff; max-width: 100%; height: auto; }
.cropx-rect { position: absolute; border: 2px solid #2f8fef; background: rgba(47,143,239,0.12); box-sizing: border-box; cursor: move; }
.cropx-handle { position: absolute; width: ${HANDLE}px; height: ${HANDLE}px; margin: -${(HANDLE / 2) | 0}px; background: #2f8fef; border: 2px solid #fff; border-radius: 50%; box-sizing: border-box; }
.cropx-handle.nw { top: 0; left: 0; cursor: nwse-resize; } .cropx-handle.ne { top: 0; left: 100%; cursor: nesw-resize; }
.cropx-handle.sw { top: 100%; left: 0; cursor: nesw-resize; } .cropx-handle.se { top: 100%; left: 100%; cursor: nwse-resize; }
.cropx-row { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
.cropx-row button { padding: 8px 14px; border:1px solid var(--cropx-ink); background:var(--cropx-ink); color:#fff; border-radius:6px; font:inherit; cursor:pointer; }
.cropx-row button.cropx-ghost { background:#fff; color:var(--cropx-ink); }
.cropx-row .cropx-hint { color: var(--cropx-mute); font-size: 12px; }
.cropx-preview { display:flex; align-items:center; gap: 14px; flex-wrap: wrap; }
.cropx-preview img { max-width: 220px; max-height: 220px; border: 1px solid var(--cropx-line); border-radius: 6px; background: #fff; }
.cropx-input-file { display:none; }
`;
  document.head.appendChild(s);
}

function el(tag, cls, parent) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (parent) parent.appendChild(e);
  return e;
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * container: HTMLElement
 * onCrop(blob, meta) — meta = { full: Blob }
 */
export function mountCropper(container, { onCrop, onLoad }) {
  injectStyle();
  container.innerHTML = "";
  const root = el("div", "cropx", container);

  // ---- state ----
  let img = null; // full-res Image element
  let fullBlob = null; // original bytes, unmodified
  let scale = 1; // displayWidth / naturalWidth
  let dispW = 0, dispH = 0;

  // ---- drop zone ----
  const drop = el("div", "cropx-drop", root);
  const p1 = el("p", null, drop);
  p1.textContent = "이미지를 붙여넣거나(Ctrl+V) 끌어오세요";
  // 카메라 - 폰에서는 뒷면 카메라가 바로 열린다(capture). 데스크탑에서는 파일 선택으로 떨어진다.
  const camBtn = el("button", null, drop);
  camBtn.type = "button";
  camBtn.textContent = "사진 찍기";
  const camInput = el("input", "cropx-input-file", drop);
  camInput.type = "file";
  camInput.accept = "image/*";
  camInput.setAttribute("capture", "environment");

  const fileBtn = el("button", null, drop);
  fileBtn.type = "button";
  fileBtn.textContent = "파일 선택";
  const fileInput = el("input", "cropx-input-file", drop);
  fileInput.type = "file";
  fileInput.accept = "image/*";

  camBtn.addEventListener("click", () => camInput.click());
  camInput.addEventListener("change", () => {
    const f = camInput.files && camInput.files[0];
    if (f) loadBlob(f);
  });

  fileBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) loadBlob(f);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("cropx-over");
    })
  );
  ["dragleave", "dragend", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      if (ev !== "drop") e.preventDefault();
      drop.classList.remove("cropx-over");
    })
  );
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f && f.type.startsWith("image/")) loadBlob(f);
  });

  document.addEventListener("paste", (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) {
          e.preventDefault();
          loadBlob(f);
        }
        return;
      }
    }
  });

  // ---- working area (stage + controls), built lazily ----
  const work = el("div", null, root);
  work.hidden = true;
  const stage = el("div", "cropx-stage", work);
  const canvas = el("canvas", null, stage);
  const ctx = canvas.getContext("2d");
  const rectEl = el("div", "cropx-rect", stage);
  ["nw", "ne", "sw", "se"].forEach((c) => el("div", `cropx-handle ${c}`, rectEl));

  // OCR 이 핵심 경로가 된 뒤로는 텍스트(제목/화학식/SMILES)가 슬라이드 어디에나 있을 수 있다.
  // 자유 드래그로도 어디든 잡을 수 있지만, 자주 쓰는 영역은 한 번의 클릭으로.
  const presetRow = el("div", "cropx-row", work);
  el("span", "cropx-hint", presetRow).textContent = "빠른 선택:";
  const presets = [
    { key: "full", label: "전체", rect: () => ({ x: 0, y: 0, w: dispW, h: dispH }) },
    { key: "top", label: "위쪽 (제목)", rect: () => ({ x: 0, y: 0, w: dispW, h: dispH * 0.3 }) },
    {
      key: "middle",
      label: "가운데 (그림)",
      rect: () => ({ x: dispW * 0.2, y: dispH * 0.2, w: dispW * 0.6, h: dispH * 0.6 }),
    },
    {
      key: "bottom",
      label: "아래쪽 (화학식·SMILES)",
      rect: () => ({ x: 0, y: dispH * 0.7, w: dispW, h: dispH * 0.3 }),
    },
  ];
  for (const preset of presets) {
    const b = el("button", "cropx-ghost", presetRow);
    b.type = "button";
    b.textContent = preset.label;
    b.addEventListener("click", () => {
      rect = preset.rect();
      drawRect();
    });
  }

  const row = el("div", "cropx-row", work);
  const confirmBtn = el("button", null, row);
  confirmBtn.type = "button";
  confirmBtn.textContent = "이 영역으로 자르기";
  const skipBtn = el("button", "cropx-ghost", row);
  skipBtn.type = "button";
  skipBtn.textContent = "크롭 건너뛰기 (원본 그대로)";
  const newBtn = el("button", "cropx-ghost", row);
  newBtn.type = "button";
  newBtn.textContent = "다른 이미지";
  el("span", "cropx-hint", row).textContent = "모서리를 끌어 크기 조절, 안쪽을 끌어 이동해 직접 잡을 수도 있습니다";

  // ---- preview area, shown after a crop is confirmed ----
  const preview = el("div", "cropx-preview", root);
  preview.hidden = true;
  const previewImg = el("img", null, preview);
  const recropBtn = el("button", "cropx-ghost", preview);
  recropBtn.type = "button";
  recropBtn.textContent = "다시 자르기";
  const previewNewBtn = el("button", "cropx-ghost", preview);
  previewNewBtn.type = "button";
  previewNewBtn.textContent = "다른 이미지";

  newBtn.addEventListener("click", reset);
  previewNewBtn.addEventListener("click", reset);
  recropBtn.addEventListener("click", () => {
    preview.hidden = true;
    work.hidden = false;
  });
  skipBtn.addEventListener("click", () => {
    if (!fullBlob) return;
    finish(fullBlob);
  });
  confirmBtn.addEventListener("click", () => {
    if (!img) return;
    confirmBtn.disabled = true;
    const prev = confirmBtn.textContent;
    confirmBtn.textContent = "자르는 중…";
    cropAtFullRes()
      .then((b) => {
        // 잘라내기가 실패하면(캔버스 한계 등) 조용히 죽지 않고 원본을 보낸다.
        finish(b || fullBlob);
      })
      .catch(() => finish(fullBlob))
      .finally(() => { confirmBtn.disabled = false; confirmBtn.textContent = prev; });
  });

  function reset() {
    img = null;
    fullBlob = null;
    drop.hidden = false;
    work.hidden = true;
    preview.hidden = true;
  }

  // 판정이 "너무 크다(슬라이드 통째)" 로 걸렸을 때 부르는 훅. 자르기 화면을 다시 연다.
  // mountCropper 의 반환값으로 나간다 - 기존 호출부는 반환값을 안 써도 그대로 동작한다.
  function recrop() {
    if (!img) return false;
    preview.hidden = true;
    drop.hidden = true;
    work.hidden = false;
    root.scrollIntoView({ behavior: "smooth", block: "center" });
    return true;
  }

  function finish(blob) {
    onCrop(blob, { full: fullBlob });
    const url = URL.createObjectURL(blob);
    previewImg.onload = () => URL.revokeObjectURL(url);
    previewImg.src = url;
    work.hidden = true;
    preview.hidden = false;
  }

  function loadBlob(blob) {
    // 폰 사진은 EXIF 로 회전 정보가 붙는다. createImageBitmap 이 있으면 그걸로
    // 방향을 바로잡아 캔버스와 화면이 어긋나지 않게 한다.
    if (typeof createImageBitmap === "function") {
      createImageBitmap(blob, { imageOrientation: "from-image" })
        .then((bmp) => {
          const c = document.createElement("canvas");
          c.width = bmp.width; c.height = bmp.height;
          c.getContext("2d").drawImage(bmp, 0, 0);
          bmp.close && bmp.close();
          c.toBlob((b) => loadBlobRaw(b || blob), "image/png");
        })
        .catch(() => loadBlobRaw(blob));
      return;
    }
    loadBlobRaw(blob);
  }

  function loadBlobRaw(blob) {
    // 페이지 쪽 미리보기도 같이 갱신한다 - 무엇을 넣었는지 즉시 보이게.
    try { if (typeof onLoad === "function") onLoad(blob); } catch (e) { /* 부수효과일 뿐 */ }
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      img = image;
      fullBlob = blob;
      URL.revokeObjectURL(url);
      drop.hidden = true;
      preview.hidden = true;
      work.hidden = false;
      setupStage();
    };
    image.onerror = () => URL.revokeObjectURL(url);
    image.src = url;
  }

  function setupStage() {
    const nw = img.naturalWidth, nh = img.naturalHeight;
    scale = Math.min(1, MAX_DISPLAY / Math.max(nw, nh));
    dispW = Math.max(1, Math.round(nw * scale));
    dispH = Math.max(1, Math.round(nh * scale));
    canvas.width = dispW;
    canvas.height = dispH;
    canvas.style.width = dispW + "px";
    canvas.style.height = dispH + "px";
    ctx.clearRect(0, 0, dispW, dispH);
    ctx.drawImage(img, 0, 0, dispW, dispH);

    // 기본 제안 영역: 전체. 텍스트(제목/화학식/SMILES)가 슬라이드 어디에나 있을 수 있어
    // 가운데로 좁혀 두면 오히려 놓친다 - 필요하면 위 빠른 선택이나 직접 드래그로 좁힌다.
    rect = { x: 0, y: 0, w: dispW, h: dispH };
    drawRect();
  }

  // ---- crop rectangle interaction (display coords) ----
  let rect = { x: 0, y: 0, w: 0, h: 0 };

  function drawRect() {
    rectEl.style.left = rect.x + "px";
    rectEl.style.top = rect.y + "px";
    rectEl.style.width = rect.w + "px";
    rectEl.style.height = rect.h + "px";
  }

  let drag = null; // { mode: 'move'|'nw'|'ne'|'sw'|'se', startX, startY, orig }

  function stagePoint(e) {
    const b = stage.getBoundingClientRect();
    const cx = (e.touches ? e.touches[0].clientX : e.clientX) - b.left;
    const cy = (e.touches ? e.touches[0].clientY : e.clientY) - b.top;
    // canvas may be CSS-scaled down further (max-width:100%); map to canvas pixel space
    const sx = dispW / stage.clientWidth;
    const sy = dispH / stage.clientHeight;
    return { x: cx * sx, y: cy * sy };
  }

  function startDrag(mode) {
    return (e) => {
      e.preventDefault();
      drag = { mode, orig: { ...rect }, start: stagePoint(e) };
      window.addEventListener("pointermove", onDrag);
      window.addEventListener("pointerup", endDrag, { once: true });
    };
  }

  rectEl.addEventListener("pointerdown", (e) => {
    if (e.target.classList.contains("cropx-handle")) return; // handles have their own listener
    startDrag("move")(e);
  });
  rectEl.querySelector(".nw").addEventListener("pointerdown", startDrag("nw"));
  rectEl.querySelector(".ne").addEventListener("pointerdown", startDrag("ne"));
  rectEl.querySelector(".sw").addEventListener("pointerdown", startDrag("sw"));
  rectEl.querySelector(".se").addEventListener("pointerdown", startDrag("se"));

  // 빈 캔버스 영역을 끌면 새 영역을 그린다
  canvas.addEventListener("pointerdown", (e) => {
    const pt = stagePoint(e);
    drag = { mode: "new", start: pt };
    rect = { x: pt.x, y: pt.y, w: 0, h: 0 };
    drawRect();
    window.addEventListener("pointermove", onDrag);
    window.addEventListener("pointerup", endDrag, { once: true });
  });

  function onDrag(e) {
    if (!drag) return;
    const pt = stagePoint(e);
    const dx = pt.x - drag.start.x, dy = pt.y - drag.start.y;
    if (drag.mode === "move") {
      rect.x = clamp(drag.orig.x + dx, 0, dispW - rect.w);
      rect.y = clamp(drag.orig.y + dy, 0, dispH - rect.h);
    } else if (drag.mode === "new") {
      const x0 = drag.start.x, y0 = drag.start.y;
      rect.x = clamp(Math.min(x0, pt.x), 0, dispW);
      rect.y = clamp(Math.min(y0, pt.y), 0, dispH);
      rect.w = clamp(Math.abs(pt.x - x0), 0, dispW - rect.x);
      rect.h = clamp(Math.abs(pt.y - y0), 0, dispH - rect.y);
    } else {
      const o = drag.orig;
      let x1 = o.x, y1 = o.y, x2 = o.x + o.w, y2 = o.y + o.h;
      if (drag.mode.includes("w")) x1 = clamp(o.x + dx, 0, x2 - 10);
      if (drag.mode.includes("e")) x2 = clamp(o.x + o.w + dx, x1 + 10, dispW);
      if (drag.mode.includes("n")) y1 = clamp(o.y + dy, 0, y2 - 10);
      if (drag.mode.includes("s")) y2 = clamp(o.y + o.h + dy, y1 + 10, dispH);
      rect = { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
    }
    drawRect();
  }

  function endDrag() {
    drag = null;
    window.removeEventListener("pointermove", onDrag);
    if (rect.w < 4 || rect.h < 4) {
      // 너무 작으면(잘못 클릭 등) 전체 영역으로 되돌린다
      rect = { x: 0, y: 0, w: dispW, h: dispH };
      drawRect();
    }
  }

  // 폰 사진은 4000x3000 이 흔하다. 원본 해상도로 캔버스를 만들면 iOS 의 캔버스
  // 면적 한계(약 16.7M px)에 걸려 toBlob 이 null 을 돌려주고, 그러면 아무 일도
  // 일어나지 않는다. 인식기는 1600px 이상을 필요로 하지 않으므로 긴 변을 제한한다.
  const MAX_OUT = 1600;

  async function cropAtFullRes() {
    const inv = 1 / scale;
    const sx = Math.round(rect.x * inv);
    const sy = Math.round(rect.y * inv);
    const sw = Math.max(1, Math.round(rect.w * inv));
    const sh = Math.max(1, Math.round(rect.h * inv));

    const k = Math.min(1, MAX_OUT / Math.max(sw, sh));
    const ow = Math.max(1, Math.round(sw * k));
    const oh = Math.max(1, Math.round(sh * k));

    const out = document.createElement("canvas");
    out.width = ow;
    out.height = oh;
    const c = out.getContext("2d");
    c.fillStyle = "#fff";               // 투명 배경을 흰색으로 - 인식기가 검게 읽지 않게
    c.fillRect(0, 0, ow, oh);
    c.drawImage(img, sx, sy, sw, sh, 0, 0, ow, oh);

    return new Promise((resolve) => {
      try {
        out.toBlob((b) => resolve(b || null), "image/png");
      } catch (e) { resolve(null); }
    });
  }

  return { recrop, reset, load: loadBlob };
}
