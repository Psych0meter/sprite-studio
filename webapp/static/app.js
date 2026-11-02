// Sprite Studio front end: one page, plain JavaScript, no build step.
"use strict";

// ---------------------------------------------------------------- helpers

// append() that skips null / false and flattens arrays, like h() does for its children
const nativeAppend = Element.prototype.append;
Element.prototype.append = function (...kids) {
  nativeAppend.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
};

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch("/api" + path, init);
  const text = await r.text();
  let data = text;
  try { data = JSON.parse(text); } catch (_) { /* plain text */ }
  if (!r.ok) {
    const msg = (data && data.error) || (data && data.detail) || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 9000 : 4500);
}

async function attempt(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";
const fileUrl = (p) => !p ? BLANK : "/api/file?path=" + encodeURIComponent(p) + (p.includes("/output/") ? "&t=" + Math.floor(Date.now() / 60000) : "");
const base = (p) => p.split("/").pop().replace(/\.png$/, "");
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
function fmtDur(s) {
  if (s == null) return "";
  s = Math.round(s);
  if (s < 60) return s + " s";
  if (s < 3600) return Math.floor(s / 60) + " min " + String(s % 60).padStart(2, "0") + " s";
  return Math.floor(s / 3600) + " h " + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + " min";
}
const fmtMB = (b) => b ? (b / 1048576).toFixed(b > 1e9 ? 0 : 1) + " MB" : "";
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return fmtTime(t);
}

// The LoRA a render of model m will use (chosen: a file name, or "" for the model's default):
// {name, ok, info}. ok is false only when ComfyUI's folder is visible and the file isn't in it.
function loraFor(m, chosen) {
  const L = m.lora || { default: {}, extra: [], xl: {} };
  const all = [L.default, L.xl, ...(L.extra || [])].filter((x) => x && x.name);
  const name = chosen || (L.default && L.default.name) || m.trigger + ".safetensors";
  const info = all.find((x) => x.name === name) || (S.catalog.loras || []).find((x) => x.name === name) || { name };
  const installed = info.installed !== undefined ? info.installed : (S.catalog.loras || []).some((x) => x.name === name);
  return { name, info, ok: installed || L.dir_ok === false };
}

// One line saying which LoRA a render uses and where it came from, or why nothing can render.
function loraNote(m, chosen, onTrain) {
  const { name, info, ok } = loraFor(m, chosen);
  if (!ok && onTrain) return h("div", { class: "note warn lora-note" }, `Nothing installed yet: click Use on the epoch you prefer, it becomes ${name}, the LoRA generation uses.`);
  if (!ok) {
    return h("div", { class: "note bad lora-note" }, h("b", {}, `No LoRA installed for ${m.label}. `),
      `${name} isn't in ComfyUI's models/loras, so nothing can be rendered with it. Train the model, then pick an epoch and click Use on `,
      h("a", { href: "#/train/" + m.id }, "the Train page"), ".");
  }
  const where = info.checkpoint
    ? [info.final ? "the final epoch" : `epoch ${info.epoch}`, ` of the training of ${fmtTime(info.trained)}`, `, installed ${ago(info.installed_at)}`]
    : info.mtime ? [`installed ${ago(info.mtime)}`, h("span", { class: "muted" }, " (copied outside the app: epoch unknown)")] : [];
  return h("div", { class: "note ok lora-note" }, onTrain ? "In use: " : "LoRA ", h("b", { class: "mono" }, name), info.xl ? " (SDXL, 1024 px)" : "", where.length ? ": " : "", where,
    onTrain ? null : [" ", h("a", { href: "#/train/" + m.id, class: "small" }, "Change")]);
}

// The model's installed LoRAs, for a "LoRA" select.
function loraOptions(m, chosen) {
  const L = m.lora || { extra: [] };
  const names = [L.xl, ...(L.extra || [])].filter((x) => x && x.installed).map((x) => x.name);
  const others = (S.catalog.loras || []).map((x) => x.name).filter((n) => !names.includes(n) && n !== (L.default && L.default.name));
  const def = (L.default && L.default.name) || m.trigger + ".safetensors";
  return [h("option", { value: "" }, `Default: ${def}` + (L.default && L.default.installed === false ? " (not installed)" : "")),
    names.length ? h("optgroup", { label: "This model" }, names.map((n) => h("option", { value: n, selected: n === chosen }, n + (n.includes("_xl") ? " (SDXL)" : "")))) : null,
    others.length ? h("optgroup", { label: "Other LoRAs" }, others.map((n) => h("option", { value: n, selected: n === chosen }, n))) : null];
}

const store = {
  get(k, d) { try { const v = localStorage.getItem("ss." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem("ss." + k, JSON.stringify(v)); } catch (_) { /* private mode */ } },
};

const ICONS = {
  star: '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M8 1.5l1.9 4 4.4.5-3.3 3 .9 4.3L8 11.2 4.1 13.3 5 9 1.7 6l4.4-.5z"/></svg>',
  starOff: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M8 1.5l1.9 4 4.4.5-3.3 3 .9 4.3L8 11.2 4.1 13.3 5 9 1.7 6l4.4-.5z"/></svg>',
  compare: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M2 3h5v10H2zM9 3h5v10H9z"/></svg>',
  palette: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M8 1.8C4.5 1.8 1.8 4.4 1.8 7.7c0 3.3 2.6 6.4 5.6 6.4 1.2 0 1.3-1 .8-1.7-.5-.8 0-1.8 1-1.8h1.7c1.9 0 3.3-1.5 3.3-3.3 0-3-2.8-5.5-6.2-5.5z"/><circle cx="5" cy="7" r="1" fill="currentColor"/><circle cx="7.5" cy="4.6" r="1" fill="currentColor"/><circle cx="10.6" cy="5.6" r="1" fill="currentColor"/></svg>',
  brush: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.4" d="M13.5 2.5 7 9M7 9c-1.5-1-3.5 0-3.5 2 0 1-.5 2-1.5 2.5 2.5.5 5 0 5-2.5"/></svg>',
  redo: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.4" d="M12.5 6.5A5 5 0 1 0 13 10M12.8 2.5v4h-4"/></svg>',
  download: '<svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.4" d="M8 2v8M4.5 7 8 10.5 11.5 7M3 13.5h10"/></svg>',
  x: '<svg viewBox="0 0 16 16"><path stroke="currentColor" stroke-width="1.5" d="M4 4l8 8M12 4l-8 8"/></svg>',
};
const iconBtn = (name, title, onclick, on = false) =>
  h("button", { class: "icon-btn" + (on ? " on" : ""), title, "aria-label": title, type: "button", onclick, html: ICONS[name] });

// ---------------------------------------------------------------- shared state

const S = {
  catalog: null,
  status: null,
  picks: {},
  tray: store.get("tray", []),
  view: Object.assign({ zoom: "fit", bg: "check", grid: false, raw: false }, store.get("view", {})),
};

function saveView() { store.set("view", S.view); }
function setTray(t) { S.tray = t; store.set("tray", t); $("#tray-count").textContent = t.length || ""; }

async function loadCatalog() { S.catalog = await api("/catalog"); drawProjectPicker(); return S.catalog; }

/** The project (game) shown: every page lists only its models. */
S.project = store.get("project", "");
const projectsOf = () => [...new Set([...(S.catalog.projects || []), ...S.catalog.models.map((m) => m.project || "No project")])]
  .sort((a, b) => (a === "No project") - (b === "No project") || a.localeCompare(b));

/** A select of the projects, plus "No project" and "New project…" (asks for the name). */
function projectSelect(value, onchange, opts = {}) {
  const names = (S.catalog.projects || []).slice();
  if (value && !value.startsWith("__") && !names.includes(value)) names.push(value);
  const sel = h("select", { id: opts.id, onchange: async (e) => {
    if (e.target.value === "__new__") {
      const name = await askText("New project", { input: "Name (a game)", placeholder: "My game", ok: "Create" });
      if (!name) { e.target.value = value; return; }
      if (!(S.catalog.projects || []).includes(name)) await attempt(() => api("/projects", { body: { name } }));
      await loadCatalog();
      value = name;
      sel.replaceWith(sel2 = projectSelect(name, onchange, opts));
      onchange(name);
      return;
    }
    value = e.target.value;
    onchange(value);
  } },
  opts.keep ? h("option", { value: "__keep__", selected: value === "__keep__" }, opts.keep) : null,
  names.sort((a, b) => a.localeCompare(b)).map((n) => h("option", { value: n, selected: n === value }, n)),
  h("option", { value: "", selected: value === "" }, "No project"),
  h("option", { value: "__new__" }, "New project…"));
  let sel2 = sel;
  return sel;
}
function visibleModels() {
  const all = S.catalog.models;
  const shown = S.project ? all.filter((m) => (m.project || "No project") === S.project) : all;
  return shown.length ? shown : all;
}
function drawProjectPicker() {
  const box = $("#project-picker");
  if (!box || !S.catalog) return;
  const ps = projectsOf();
  if (S.project && !ps.includes(S.project)) S.project = "";
  box.innerHTML = "";
  if (ps.length < 2 && !S.project) return;
  box.append(h("select", { "aria-label": "Project", onchange: (e) => { S.project = e.target.value; store.set("project", S.project); route(); } },
    h("option", { value: "" }, "All projects"), ps.map((p) => h("option", { value: p, selected: p === S.project },
      p + (S.catalog.models.some((m) => (m.project || "No project") === p) ? "" : " (no model yet)")))));
}
async function loadPicks() { S.picks = await api("/picks"); return S.picks; }

// ---------------------------------------------------------------- status bar

let statusTimer = null;
async function pollStatus() {
  try {
    S.status = await api("/status");
    renderStatus();
  } catch (e) {
    $("#gpu-line").innerHTML = "";
    $("#gpu-line").append(h("span", {}, h("i", { class: "dot down" }), "The app is not answering"));
  }
  clearTimeout(statusTimer);
  statusTimer = setTimeout(pollStatus, S.status && S.status.running.length ? 2500 : 5000);
}

function renderStatus() {
  const st = S.status;
  const c = st.comfy;
  const line = $("#gpu-line");
  line.innerHTML = "";
  line.append(h("span", {}, h("i", { class: "dot " + (c.up ? "up" : "down") }), c.up ? "ComfyUI running" : "ComfyUI stopped"));
  if (st.gpu) {
    line.append(h("span", {}, `${st.gpu.name.replace("NVIDIA GeForce ", "")}  ${(st.gpu.mem_used / 1024).toFixed(1)} of ${(st.gpu.mem_total / 1024).toFixed(0)} GB, ${st.gpu.util} %, ${st.gpu.temp} °C`));
  } else if (c.up && c.vram_total) {
    line.append(h("span", {}, `${(c.device || "GPU").replace("NVIDIA GeForce ", "")}  ${((c.vram_total - c.vram_free) / 1073741824).toFixed(1)} of ${(c.vram_total / 1073741824).toFixed(0)} GB in use`));
  }
  const aj = $("#active-job");
  aj.innerHTML = "";
  const run = st.running.find((j) => j.lane === "gpu") || st.running[0];
  if (run) {
    const p = run.progress || {};
    aj.append(
      h("i", { class: "dot run" }),
      h("a", { href: "#/jobs/" + run.id, title: run.title }, run.title),
      h("div", { class: "bar" }, h("i", { style: { width: Math.round((p.fraction || 0) * 100) + "%" } })),
      h("span", { class: "muted small nowrap" }, p.text || ""),
    );
  } else if (st.queued.length) {
    aj.append(h("span", { class: "muted" }, st.queued.length + " waiting"));
  }
  $("#queue-count").textContent = (st.running.length + st.queued.length) || "";
  document.dispatchEvent(new CustomEvent("status", { detail: st }));
}

// ---------------------------------------------------------------- sprites

function ltClass() { return "lt-" + S.view.bg; }

const FIT = 224; // "Fit": the largest whole zoom that keeps a sprite within this many pixels
function fitFactor(W, H, box = FIT) {
  const m = Math.max(W, H) || 1;
  return m <= box ? Math.max(1, Math.floor(box / m)) : box / m;
}

/** A sprite drawn at the current zoom, pixel-exact ("fit": per sprite). */
function spriteImg(path, w, ht, zoom = S.view.zoom) {
  const img = h("img", { class: "px", src: fileUrl(path), alt: base(path), loading: "lazy" });
  const size = (W, H) => {
    const z = zoom === "fit" ? fitFactor(W, H) : zoom;
    img.style.width = W * z + "px"; img.style.height = H * z + "px";
  };
  if (w && ht) size(w, ht);
  else img.addEventListener("load", () => size(img.naturalWidth, img.naturalHeight), { once: true });
  return img;
}

function openSprite(path, title) {
  const m = $("#modal");
  let zoom = "fit2";
  let grid = S.view.grid;
  const stage = h("div", { class: "modal-stage " + ltClass() });
  const info = h("span", { class: "muted small" });
  const draw = () => {
    stage.innerHTML = "";
    const img = spriteImg(path, 0, 0, 1);
    const wrap = h("div", { class: grid ? "px-grid" : "" }, img);
    img.addEventListener("load", () => {
      const W = img.naturalWidth, H = img.naturalHeight;
      const z = zoom === "fit2" ? fitFactor(W, H, 640) : zoom;
      img.style.width = W * z + "px"; img.style.height = H * z + "px";
      wrap.style.setProperty("--cell", z + "px");
    }, { once: true });
    stage.append(wrap);
  };
  const zoomSeg = seg([{ id: "fit2", label: "Fit" }, ...[1, 2, 4, 8, 12].map((z) => ({ id: z, label: z + "×" }))], zoom, (z) => { zoom = z; draw(); });
  const box = h("div", { class: "modal-box" },
    h("div", { class: "row between", style: { marginBottom: "12px" } },
      h("strong", { class: "mono" }, title || base(path)),
      h("div", { class: "row" }, zoomSeg,
        h("label", { class: "check small" }, h("input", { type: "checkbox", checked: grid, onchange: (e) => { grid = e.target.checked; draw(); } }), "Pixel grid"),
        h("a", { class: "btn sm", href: fileUrl(path), download: base(path) + ".png", html: ICONS.download + "PNG" }),
        h("button", { class: "btn sm ghost", onclick: closeModal }, "Close"))),
    stage, h("div", { class: "row", style: { marginTop: "10px" } }, info, h("span", { class: "faint small mono" }, path)));
  m.innerHTML = "";
  m.append(box);
  m.hidden = false;
  draw();
  api("/info?path=" + encodeURIComponent(path)).then((i) => {
    info.textContent = `${i.w}×${i.h} px, ${i.colours} colours, ${i.parts} part${i.parts === 1 ? "" : "s"}` + (i.standard ? "" : " (outside the sprite standard)");
  }).catch(() => {});
}
/** Touch-up editor: paint the pixels to redraw, the rest of the sprite stays. */
function touchUp(v, set) {
  const p = (set && set.params) || {};
  const img = new Image();
  img.onload = () => {
    const W = img.naturalWidth, H = img.naturalHeight;
    const z = Math.max(1, Math.floor(512 / Math.max(W, H)));
    const mask = new Uint8Array(W * H);
    let brush = 2, erase = false, denoise = 0.75, count = 4, extra = "";
    const mid = p.model || (visibleModels()[0] || {}).id;
    const group = p.group || "";
    const kind = p.kind && p.kind !== "none" ? p.kind : "";
    const cv = h("canvas", { width: W, height: H, class: "touch-mask", style: { width: W * z + "px", height: H * z + "px" } });
    const ctx = cv.getContext("2d");
    const paintAll = () => {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "rgba(240, 177, 60, 0.6)";
      for (let i = 0; i < mask.length; i++) if (mask[i]) ctx.fillRect(i % W, Math.floor(i / W), 1, 1);
      countLbl.textContent = mask.reduce((a, b) => a + b, 0) + " pixels marked";
    };
    const dab = (e) => {
      const r = cv.getBoundingClientRect();
      const x = Math.floor((e.clientX - r.left) / z), y = Math.floor((e.clientY - r.top) / z);
      const off = Math.floor((brush - 1) / 2);
      for (let dy = 0; dy < brush; dy++) for (let dx = 0; dx < brush; dx++) {
        const X = x - off + dx, Y = y - off + dy;
        if (X >= 0 && Y >= 0 && X < W && Y < H) mask[Y * W + X] = (erase || e.buttons === 2 || e.shiftKey) ? 0 : 1;
      }
      paintAll();
    };
    cv.addEventListener("pointerdown", (e) => { cv.setPointerCapture(e.pointerId); dab(e); });
    cv.addEventListener("pointermove", (e) => { if (e.buttons) dab(e); });
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    const countLbl = h("span", { class: "muted small" });
    const stage = h("div", { class: "modal-stage " + ltClass() },
      h("div", { class: "touch-stage", style: { width: W * z + "px", height: H * z + "px" } },
        h("img", { class: "px", src: img.src, alt: "", style: { width: W * z + "px", height: H * z + "px" } }), cv));
    const out = h("output", {}, denoise.toFixed(2));
    const send = async () => {
      if (!mask.some((x) => x)) return toast("Paint over the part to redraw first.", true);
      if (!group) return toast("This run does not say which group it belongs to: use Generate with this sprite instead.", true);
      const mc = document.createElement("canvas");
      mc.width = W; mc.height = H;
      const mctx = mc.getContext("2d");
      mctx.fillStyle = "#000"; mctx.fillRect(0, 0, W, H);
      mctx.fillStyle = "#fff";
      for (let i = 0; i < mask.length; i++) if (mask[i]) mctx.fillRect(i % W, Math.floor(i / W), 1, 1);
      const up = await attempt(() => api("/upload", { body: { name: base(v.path) + "_mask.png", data: mc.toDataURL("image/png") } }));
      if (!up) return;
      const body = { start: "sprite", init: v.path, mask: up.path, denoise, count, name: ((set && set.target) || base(v.path)) + "_touchup",
        lora: p.lora || "", size: Math.max(W, H) % 8 === 0 ? Math.max(W, H) : "" };
      Object.assign(body, { model: mid, group, kind, extra: [(set && set.what) || "", extra].filter(Boolean).join(", ") });
      const j = await attempt(() => api("/generate", { body }), "Touch-up queued");
      if (j) { closeModal(); location.hash = "#/jobs/" + j.id; }
    };
    const box = h("div", { class: "modal-box", style: { maxWidth: "980px" } },
      h("div", { class: "row between", style: { marginBottom: "12px" } },
        h("div", {}, h("h2", { style: { margin: 0 } }, "Touch up " + base(v.path)),
          h("div", { class: "small muted" }, "Paint the pixels to redraw (right click or Shift to erase). Everything else stays as it is.")),
        h("button", { class: "btn sm ghost", onclick: closeModal }, "Close")),
      h("div", { class: "row", style: { alignItems: "flex-start", gap: "18px" } },
        stage,
        h("div", { style: { width: "280px" } },
          h("div", { class: "field" }, h("span", { class: "label" }, "Brush"),
            seg([1, 2, 3, 5].map((b) => ({ id: b, label: b + " px" })), brush, (b) => { brush = b; })),
          h("div", { class: "field" }, h("span", { class: "label" }, "Mode"),
            seg([{ id: false, label: "Paint" }, { id: true, label: "Erase" }], erase, (m) => { erase = m; })),
          h("div", { class: "row", style: { marginTop: "10px" } }, countLbl,
            h("button", { class: "btn sm ghost", onclick: () => { mask.fill(0); paintAll(); } }, "Clear")),
          h("div", { class: "field" }, h("label", {}, "What to draw there (optional)"),
            h("input", { type: "text", placeholder: "twin cannons, glowing core", oninput: (e) => { extra = e.target.value; } })),
          h("div", { class: "field" }, h("label", {}, "How much it changes"),
            h("div", { class: "range-row" }, h("input", { type: "range", min: 0.4, max: 1, step: 0.05, value: denoise,
              oninput: (e) => { denoise = Number(e.target.value); out.textContent = denoise.toFixed(2); } }), out),
            h("div", { class: "range-ends" }, h("span", {}, "Clean it up"), h("span", {}, "Redraw it"))),
          h("div", { class: "field" }, h("label", {}, "Variants"),
            h("input", { type: "number", min: 1, max: 12, value: count, oninput: (e) => { count = Number(e.target.value) || 4; } })),
          h("button", { class: "btn primary block", style: { marginTop: "16px" }, onclick: send }, "Redraw the marked pixels"))));
    const m = $("#modal");
    m.innerHTML = "";
    m.append(box);
    m.hidden = false;
    paintAll();
  };
  img.src = fileUrl(v.path);
}
// In-app dialogs instead of the browser's prompt() and confirm(): a layer of
// its own, so they also work above an open modal. Enter confirms, Escape cancels.
function dialog({ title, text, input, value = "", placeholder = "", hint = "", ok = "OK", cancel = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const layer = h("div", { class: "modal dialog-layer" });
    const field = input ? h("input", { type: "text", value, placeholder, class: "dialog-input" }) : null;
    const done = (v) => { layer.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const submit = () => done(input ? field.value.trim() || null : true);
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); done(input ? null : false); }
      else if (e.key === "Enter" && (input ? document.activeElement === field : true)) { e.preventDefault(); submit(); }
    };
    layer.append(h("div", { class: "modal-box dialog-box", role: "dialog", "aria-modal": "true" },
      title ? h("h2", {}, title) : null,
      text ? h("p", { class: "dialog-text" }, text) : null,
      input ? h("div", { class: "field" }, input === true ? null : h("label", {}, input), field, hint ? h("div", { class: "hint" }, hint) : null) : null,
      h("div", { class: "row", style: { marginTop: "18px", justifyContent: "flex-end" } },
        h("button", { class: "btn ghost", type: "button", onclick: () => done(input ? null : false) }, cancel),
        h("button", { class: "btn " + (danger ? "danger-solid" : "primary"), type: "button", onclick: submit }, ok))));
    layer.addEventListener("click", (e) => { if (e.target === layer) done(input ? null : false); });
    document.addEventListener("keydown", onKey, true);
    document.body.append(layer);
    setTimeout(() => (field || layer.querySelector(".btn.primary, .btn.danger-solid")).focus(), 0);
    if (field) setTimeout(() => field.select(), 0);
  });
}
const askText = (title, opts = {}) => dialog(Object.assign({ title, input: true }, opts));
const askConfirm = (text, opts = {}) => dialog(Object.assign({ title: opts.title || "Are you sure?", text }, opts));

function closeModal() { $("#modal").hidden = true; $("#modal").innerHTML = ""; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

function seg(options, value, onchange) {
  const el = h("div", { class: "seg", role: "group" });
  const draw = () => {
    el.innerHTML = "";
    for (const o of options) {
      el.append(h("button", { type: "button", class: o.id === value ? "on" : "", "aria-pressed": String(o.id === value),
        onclick: () => { value = o.id; draw(); onchange(o.id); } }, o.label));
    }
  };
  draw();
  return el;
}

/** The zoom / backdrop / grid bar shared by every sprite view. */
function viewBar(redraw, extra = []) {
  return h("div", { class: "viewbar" },
    h("span", { class: "label" }, "Zoom"),
    seg([{ id: "fit", label: "Fit" }, ...[1, 2, 3, 4, 6, 8].map((z) => ({ id: z, label: z + "×" }))], S.view.zoom, (z) => { S.view.zoom = z; saveView(); redraw(); }),
    h("span", { class: "label" }, "Backdrop"),
    seg([{ id: "check", label: "Checker" }, { id: "dark", label: "Dark" }, { id: "floor", label: "Floor" }, { id: "light", label: "Light" }],
      S.view.bg, (b) => { S.view.bg = b; saveView(); redraw(); }),
    extra);
}

/** One sprite card: picture, name, pick / compare / remaster tools. */
function tile(v, opts = {}) {
  const picked = !!S.picks[v.path];
  const inTray = S.tray.some((t) => t.path === v.path);
  const el = h("div", { class: "tile" + (opts.orig ? " orig" : "") + (picked ? " picked" : "") });
  const stage = h("div", { class: "stage " + ltClass(), title: "Open", onclick: () => openSprite(v.path, v.label || v.name) },
    spriteImg(S.view.raw && v.raw ? v.raw : v.path, S.view.raw && v.raw ? 0 : v.w, S.view.raw && v.raw ? 0 : v.h,
      S.view.raw && v.raw ? (S.view.zoom === "fit" ? "fit" : Math.max(0.25, S.view.zoom / 4)) : S.view.zoom));
  const tools = h("div", { class: "tools" });
  if (!opts.orig && opts.pickAs !== undefined) {
    tools.append(iconBtn(picked ? "star" : "starOff", picked ? "Picked: click to unpick" : "Pick this one", async () => {
      await attempt(async () => { S.picks = await api("/picks", { body: { path: v.path, picked: !picked, as: opts.pickAs } }); });
      opts.refresh && opts.refresh();
    }, picked));
  }
  tools.append(iconBtn("compare", inTray ? "In the comparison: click to remove" : "Add to the comparison", () => {
    setTray(inTray ? S.tray.filter((t) => t.path !== v.path) : [...S.tray, { path: v.path, w: v.w, h: v.h, label: v.label || v.name }]);
    opts.refresh && opts.refresh();
  }, inTray));
  if (opts.touchup) tools.append(iconBtn("brush", "Touch up: repaint only the parts you mark", () => opts.touchup(v)));
  if (opts.recolour) tools.append(iconBtn("palette", "Recolour with a palette", () => opts.recolour(v)));
  if (opts.remaster) tools.append(iconBtn("redo", "Generate again from this one", () => opts.remaster(v)));
  el.append(stage, h("div", { class: "cap" }, h("span", { class: "name", title: v.label || v.name }, v.label || v.name), tools));
  return el;
}

// ---------------------------------------------------------------- palettes (UI)
//
// The palette arithmetic (tunePixel, relight, applyPalette...) is in palette.js.

/** A canvas showing the sprite at `path` with the palette on it (null: as it is), `zoom` px per pixel. */
function palettePreview(path, pal, zoom = 3) {
  const cv = h("canvas", { class: "px pal-canvas" });
  const img = new Image();
  img.onload = () => {
    cv.width = img.naturalWidth; cv.height = img.naturalHeight;
    cv.style.width = img.naturalWidth * zoom + "px"; cv.style.height = img.naturalHeight * zoom + "px";
    const ctx = cv.getContext("2d");
    ctx.drawImage(img, 0, 0);
    if (pal) {
      const d = ctx.getImageData(0, 0, cv.width, cv.height);
      applyPalette(d.data, pal, cv.width);
      ctx.putImageData(d, 0, 0);
    }
  };
  img.src = fileUrl(path);
  return cv;
}

const swatchStrip = (cols, t, big) => h("div", { class: "swatches" + (big ? " big" : "") },
  tuneColours(cols || [], t || {}).map((c) => h("span", { style: { background: rgbHex(c) }, title: rgbHex(c) })));

async function loadPalettes() { S.palettes = await api("/palettes"); return S.palettes; }

/** Options of a palette select: "" = the group's own (named), "none", then every saved palette. */
function paletteOptions(chosen, groupPalette) {
  const gp = (S.palettes || []).find((p) => p.id === groupPalette);
  return [h("option", { value: "", selected: !chosen }, groupPalette ? `The group's: ${gp ? gp.label : groupPalette}` : "The group's (none)"),
    h("option", { value: "none", selected: chosen === "none" }, "No palette"),
    (S.palettes || []).map((p) => h("option", { value: p.id, selected: p.id === chosen }, `${p.label} (${p.colours.length ? p.colours.length + " colours, " : ""}${p.mode})`))];
}

/** Recolour one sprite: pick a palette, see before and after, keep it as a new variant. */
async function recolourDialog(v, done) {
  await loadPalettes();
  const m = $("#modal");
  let pid = (S.palettes[0] || {}).id || "";
  const stage = h("div", { class: "row", style: { gap: "24px", alignItems: "flex-start" } });
  const draw = () => {
    stage.innerHTML = "";
    const pal = S.palettes.find((p) => p.id === pid);
    const z = Math.max(1, Math.floor(400 / Math.max(v.w || 128, v.h || 128)));
    stage.append(h("div", {}, h("div", { class: "label" }, "Now"), h("div", { class: "pal-stage " + ltClass() }, palettePreview(v.path, null, z))),
      h("div", {}, h("div", { class: "label" }, "With the palette"), h("div", { class: "pal-stage " + ltClass() }, pal ? palettePreview(v.path, pal, z) : null)));
  };
  const box = h("div", { class: "modal-box", style: { maxWidth: "980px" } },
    h("div", { class: "row between", style: { marginBottom: "12px" } }, h("strong", {}, "Recolour " + (v.label || v.name)),
      h("button", { class: "btn sm ghost", onclick: closeModal }, "Close")),
    S.palettes.length ? h("div", { class: "row", style: { marginBottom: "14px" } },
      h("select", { style: { width: "auto" }, onchange: (e) => { pid = e.target.value; draw(); } }, S.palettes.map((p) => h("option", { value: p.id }, p.label))),
      h("a", { href: "#/palettes", class: "small", onclick: closeModal }, "Edit the palettes"))
      : h("div", { class: "note warn" }, "No palette yet. ", h("a", { href: "#/palettes", onclick: closeModal }, "Make one on the Palettes page"), "."),
    stage,
    h("div", { class: "row", style: { marginTop: "16px" } },
      h("button", { class: "btn primary", disabled: !S.palettes.length, onclick: async () => {
        const r = await attempt(() => api("/palettes/apply", { body: { paths: [v.path], id: pid } }));
        if (r) { toast("Saved as " + base(r.files[0])); closeModal(); done && done(); }
      } }, "Keep it as a new variant"),
      h("span", { class: "muted small" }, "Saved next to it as <name>_pal-<palette>.png; the original stays.")));
  m.innerHTML = "";
  m.append(box);
  m.hidden = false;
  draw();
}

// ---------------------------------------------------------------- jobs UI

function statusLabel(j) {
  const words = { queued: j.position ? `waiting (${j.position} in line)` : "waiting", running: "running", done: "finished",
    failed: "failed", cancelled: "cancelled", interrupted: "interrupted" };
  return h("span", { class: "status " + j.status }, words[j.status] || j.status);
}

function progressBar(j) {
  const f = j.status === "done" ? 1 : (j.progress && j.progress.fraction) || 0;
  return h("div", { class: "bar " + (j.status === "done" ? "done" : j.status === "failed" ? "failed" : "") },
    h("i", { style: { width: Math.round(f * 100) + "%" } }));
}

/** A live job panel: status, progress, log; calls onDone(job) once.
 *  opts.detail: on the job's own page (no Details link, the page shows the log). */
function jobPanel(jobId, onDone, opts = {}) {
  const el = h("div", { class: "panel" });
  let timer = null, finished = false, showLog = false;
  const logBox = h("pre", { class: "log" });
  const draw = async () => {
    let j;
    try { j = await api("/jobs/" + jobId); } catch (e) { el.textContent = e.message; return; }
    const ended = ["done", "failed", "cancelled", "interrupted"].includes(j.status);
    el.innerHTML = "";
    el.append(
      h("div", { class: "panel-head" },
        h("div", {}, h("h2", {}, j.title), h("div", { class: "small" }, statusLabel(j),
          h("span", { class: "muted" }, "  ", j.started ? (ended ? fmtDur(j.ended - j.started) : fmtDur(Date.now() / 1000 - j.started)) : ""))),
        h("div", { class: "row" },
          !ended ? h("button", { class: "btn sm danger", onclick: () => attempt(() => api(`/jobs/${j.id}/cancel`, { body: {} }), "Cancelled") }, "Cancel") : null,
          opts.detail ? null : h("button", { class: "btn sm ghost", onclick: () => { showLog = !showLog; draw(); } }, showLog ? "Hide log" : "Show log"),
          opts.detail ? null : h("a", { class: "btn sm ghost", href: "#/jobs/" + j.id }, "Full log and details"))),
      progressBar(j),
      h("p", { class: "small muted", style: { marginTop: "6px" } }, (j.progress && j.progress.text) || ""),
      j.error ? h("div", { class: "note bad" }, j.error) : null,
    );
    if (!opts.detail && showLog) {
      logBox.textContent = await api(`/jobs/${j.id}/log?tail=20000`).catch(() => "");
      el.append(logBox);
      logBox.scrollTop = logBox.scrollHeight;
    }
    if (ended && !finished) { finished = true; onDone && onDone(j); }
    if (!ended && el.isConnected !== false) timer = setTimeout(draw, 2000);
  };
  draw();
  el.stop = () => clearTimeout(timer);
  return el;
}

// ---------------------------------------------------------------- router

const routes = {};
let cleanup = [];
function onLeave(fn) { cleanup.push(fn); }

async function route() {
  cleanup.forEach((f) => { try { f(); } catch (_) { /* ignore */ } });
  cleanup = [];
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query] = hash.split("?");
  const parts = path.split("/").filter(Boolean);
  const name = parts[0] || "";
  document.querySelectorAll(".rail a[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  const main = $("#main");
  main.innerHTML = "";
  const params = new URLSearchParams(query || "");
  try {
    if (!S.catalog) await loadCatalog();
    if (NEEDS_MODEL.includes(name) && !S.catalog.models.length) {
      main.append(head(name[0].toUpperCase() + name.slice(1), ""),
        h("div", { class: "empty" }, "No model yet. A model is what learns your sprites: create one first.", h("br"),
          h("a", { class: "btn primary", href: "#/models", style: { marginTop: "12px" } }, "Create a model")));
      return;
    }
    await (routes[name] || routes[""])(main, parts.slice(1), params);
  } catch (e) {
    main.append(h("div", { class: "note bad" }, e.message));
  }
  main.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);
// Pages that work on a model: an empty state instead when there is none yet.
const NEEDS_MODEL = ["generate", "train", "batch", "loras", "palettes"];

function head(title, text, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), text ? h("p", {}, text) : null), h("div", { class: "row" }, actions));
}

// ---------------------------------------------------------------- Overview

routes[""] = async (main) => {
  main.append(head("Overview", "What the GPU is doing, and the latest sprites."));
  if (!S.catalog.models.length) {
    main.append(h("div", { class: "panel", style: { marginBottom: "16px" } }, h("h2", {}, "Getting started"),
      h("ol", { class: "small", style: { lineHeight: 1.8, margin: 0 } },
        h("li", {}, h("a", { href: "#/models" }, "Models"), ": create a model (empty, or from the Generic template set) and add its sprites: a folder, a zip or a few PNG files."),
        h("li", {}, "Give it groups (families, factions...) and kinds (sizes), with the words that describe them."),
        h("li", {}, h("a", { href: "#/train" }, "Train"), ": build the training set, train the LoRA, then pick the best epoch and click Use."),
        h("li", {}, h("a", { href: "#/generate" }, "Generate"), " or ", h("a", { href: "#/batch" }, "Batch"), ": remaster your sprites or invent new ones; tune the colours with ",
          h("a", { href: "#/palettes" }, "Palettes"), "."))));
  }
  const cards = h("div", { class: "cards" });
  const recent = h("div", { class: "panel" });
  main.append(cards, h("div", { style: { height: "16px" } }), recent);

  const drawCards = () => {
    const st = S.status;
    if (!st) return;
    cards.innerHTML = "";
    const c = st.comfy;
    cards.append(h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, "ComfyUI"), h("span", { class: "status " + (c.up ? "done" : "failed") }, c.up ? "running" : "stopped")),
      h("p", { class: "muted small" }, c.up ? `${c.device || "GPU"}, ComfyUI ${c.version || ""}` : "Generation needs it running. Training stops it on its own and starts it again afterwards."),
      h("div", { class: "row" },
        c.up ? h("button", { class: "btn sm", onclick: () => attempt(() => api("/comfy/restart", { body: {} }), "Restarting ComfyUI") }, "Restart")
          : h("button", { class: "btn sm primary", onclick: () => attempt(() => api("/comfy/start", { body: {} }), "Starting ComfyUI") }, "Start"),
        c.up ? h("button", { class: "btn sm ghost", onclick: () => attempt(() => api("/comfy/stop", { body: {} }), "Stopping ComfyUI") }, "Stop") : null)));
    const run = st.running;
    cards.append(h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, "Working on"), h("a", { href: "#/jobs", class: "small" }, "All jobs")),
      run.length ? run.map((j) => h("div", { style: { marginBottom: "10px" } },
        h("div", { class: "row between" }, h("a", { href: "#/jobs/" + j.id }, j.title), h("span", { class: "muted small" }, fmtDur(Date.now() / 1000 - j.started))),
        progressBar(j), h("div", { class: "small muted", style: { marginTop: "4px" } }, (j.progress && j.progress.text) || "")))
        : h("p", { class: "muted" }, "Nothing running."),
      st.queued.length ? h("div", { class: "small" }, h("div", { class: "label" }, "Waiting"), st.queued.map((j) => h("div", {}, h("a", { href: "#/jobs/" + j.id }, j.title)))) : null));
    const loras = S.catalog.loras;
    cards.append(h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, "LoRAs"), h("a", { href: "#/train", class: "small" }, "Train")),
      loras.length ? h("table", {}, h("tbody", {}, loras.map((l) => h("tr", {}, h("td", { class: "mono" }, l.name), h("td", { class: "muted small nowrap" }, ago(l.mtime))))))
        : h("p", { class: "muted" }, "No LoRA installed yet. Train one, or copy your .safetensors files to ComfyUI's models/loras.")));
  };
  document.addEventListener("status", drawCards);
  onLeave(() => document.removeEventListener("status", drawCards));
  drawCards();

  const results = await api("/results");
  recent.append(h("div", { class: "panel-head" }, h("h2", {}, "Latest results"), h("a", { href: "#/results", class: "small" }, "All results")));
  if (!results.length) {
    recent.append(h("div", { class: "empty" }, "No results yet.", h("br"), h("a", { class: "btn primary", href: "#/generate" }, "Generate sprites")));
    return;
  }
  recent.append(h("div", { class: "strip" }, results.slice(0, 14).map((r) => h("a", { href: "#/results?path=" + encodeURIComponent(r.path), title: r.title },
    h("div", { class: "thumb " + ltClass() }, r.cover ? h("img", { class: "px", src: fileUrl(r.cover), alt: "" }) : h("span", { class: "faint small" }, "empty")),
    h("div", { class: "lbl" }, r.title)))));
};

// ---------------------------------------------------------------- Generate

/** A model of the shown project (the first one when id is not among them). */
const modelById = (id) => visibleModels().find((m) => m.id === id) || visibleModels()[0];

routes.generate = async (main, _parts, params) => {
  const saved = store.get("gen2", {});
  const f = Object.assign({
    model: "", group: "", kind: "", refs_kind: "", extra: "", colours: "", start: "none", init: "", sketch: "",
    denoise: "", count: 4, seed: "", size: "", mirror: "auto", lora: "", strength: 1.0, name: "", keep_green: "auto", no_tidy: false, palette: "", fill: "",
  }, saved, { init: "", sketch: "" });
  for (const k of ["model", "group", "kind"]) if (params.get(k)) f[k] = params.get(k);
  const asked = S.catalog.models.find((x) => x.id === params.get("model"));
  if (asked && !visibleModels().includes(asked)) { S.project = asked.project || "No project"; store.set("project", S.project); drawProjectPicker(); }
  if (params.get("init")) { f.start = "sprite"; f.init = params.get("init"); }
  const remember = () => store.set("gen2", Object.assign({}, f, { init: "", sketch: "" }));
  const fixChoices = () => {
    const m = modelById(f.model);
    f.model = m.id;
    if (!m.groups.some((g) => g.id === f.group)) f.group = (m.groups.find((g) => g.enabled) || m.groups[0] || {}).id || "";
    if (m.kinds.length && !m.kinds.some((k) => k.id === f.kind)) f.kind = m.kinds[0].id;
    if (!m.kinds.length) f.kind = "";
  };
  fixChoices();
  await loadPalettes();
  if (f.palette && f.palette !== "none" && !S.palettes.some((p) => p.id === f.palette)) f.palette = "";

  main.append(head("Generate", "Remaster a sprite, or draw a new one from a description or a sketch. The model's words for the group and kind are added to yours; each run renders a few variants."));
  const layout = h("div", { class: "grid-form" });
  const form = h("form", { class: "panel", onsubmit: (e) => { e.preventDefault(); submit(); } });
  const side = h("div", { class: "stack" });
  layout.append(form, side);
  main.append(layout);

  let sprites = [];
  const loadSprites = async () => {
    const d = await api("/models/" + f.model).catch(() => null);
    sprites = d ? d.sprites : [];
  };

  const draw = () => {
    form.innerHTML = "";
    const m = modelById(f.model);
    const g = m.groups.find((x) => x.id === f.group);
    const k = m.kinds.find((x) => x.id === f.kind);
    const loraMissing = !loraFor(m, f.lora).ok;

    form.append(h("div", { class: "field" }, h("span", { class: "label" }, "Model"),
      h("div", { class: "chips" }, visibleModels().map((x) => h("button", { type: "button", class: "chip" + (x.id === f.model ? " on" : ""),
        onclick: async () => { f.model = x.id; f.init = ""; f.lora = ""; fixChoices(); remember(); await loadSprites(); draw(); drawSide(); } }, x.label)))),
      loraNote(m, f.lora));
    form.append(h("div", { class: "inline-fields", style: { marginTop: "14px" } },
      h("div", { class: "field" }, h("label", { for: "f-group" }, "Group"),
        h("select", { id: "f-group", onchange: (e) => { f.group = e.target.value; remember(); draw(); drawSide(); } },
          m.groups.map((x) => h("option", { value: x.id, selected: x.id === f.group }, x.label + (x.enabled ? "" : " (not trained)"))))),
      m.kinds.length ? h("div", { class: "field" }, h("label", { for: "f-kind" }, "Kind"),
        h("select", { id: "f-kind", onchange: (e) => { f.kind = e.target.value; remember(); draw(); } },
          m.kinds.map((x) => h("option", { value: x.id, selected: x.id === f.kind }, `${x.label} (${x.size} px)`)))) : null));
    form.append(h("p", { class: "hint", style: { marginTop: "6px" } }, "Prompt starts with: ",
      h("span", { class: "mono" }, [m.trigger, g && g.words, k && k.words].filter(Boolean).join(", "))));

    form.append(h("div", { class: "field" }, h("label", { for: "f-extra" }, "Description"),
      h("textarea", { id: "f-extra", rows: 2, placeholder: "what this one is or looks like",
        oninput: (e) => { f.extra = e.target.value; remember(); } }, f.extra),
      h("div", { class: "hint" }, "What this one is or looks like. Optional when starting from a sprite.")));

    form.append(h("div", { class: "field" }, h("span", { class: "label" }, "Start from"),
      seg([{ id: "none", label: "The description" }, { id: "sprite", label: "A sprite" }, { id: "sketch", label: "My sketch" }], f.start,
        (v) => { f.start = v; remember(); draw(); drawSide(); })));
    if (f.start === "sprite") {
      form.append(h("div", { class: "note " + (f.init ? "" : "warn") }, f.init ? ["Starting from ", h("strong", { class: "mono" }, base(f.init))] : "Pick the sprite on the right."));
    }
    if (f.start === "sketch") {
      form.append(h("div", { class: "field" },
        h("input", { type: "file", accept: "image/png", onchange: async (e) => {
          const file = e.target.files[0];
          if (!file) return;
          const data = await readFile(file);
          const up = await attempt(() => api("/upload", { body: { name: file.name, data } }), "Sketch uploaded");
          if (up) { f.sketch = up.path; draw(); }
        } }),
        h("div", { class: "hint" }, f.sketch ? "Using " + base(f.sketch) : "A rough silhouette in flat colours on a transparent or green background, nose up.")));
    }
    if (f.start !== "none") {
      const def = f.start === "sketch" ? 0.85 : (k && k.denoise) || 0.7;
      const val = f.denoise === "" ? def : Number(f.denoise);
      const out = h("output", {}, val.toFixed(2));
      form.append(h("div", { class: "field" }, h("label", { for: "f-denoise" }, "How far from it"),
        h("div", { class: "range-row" }, h("input", { id: "f-denoise", type: "range", min: 0.3, max: 0.95, step: 0.05, value: val,
          oninput: (e) => { f.denoise = e.target.value; out.textContent = Number(e.target.value).toFixed(2); remember(); } }), out),
        h("div", { class: "range-ends" }, h("span", {}, "Close to it"), h("span", {}, "A new design"))));
    }
    form.append(h("div", { class: "field" }, h("label", { for: "f-colours" }, "Colours"),
      h("input", { id: "f-colours", type: "text", value: f.colours, placeholder: m.colours_from === "self" ? "read from the sprite you start from" : "read from the group's sprites",
        oninput: (e) => { f.colours = e.target.value; remember(); } }),
      h("div", { class: "hint" }, "Empty: read from the sprites. Or your own words: \"dark grey body, bright glowing green lights\".")));

    form.append(h("div", { class: "inline-fields", style: { marginTop: "14px" } },
      h("div", { class: "field" }, h("label", { for: "f-count" }, "Variants"), h("input", { id: "f-count", type: "number", min: 1, max: 32, value: f.count, oninput: (e) => { f.count = e.target.value; remember(); } })),
      h("div", { class: "field" }, h("label", { for: "f-size" }, "Size (px)"), h("input", { id: "f-size", type: "number", min: 8, max: 1024, step: 8, value: f.size, placeholder: (k && k.size) || m.size, oninput: (e) => { f.size = e.target.value; remember(); } })),
      h("div", { class: "field" }, h("label", { for: "f-seed" }, "Seed"), h("input", { id: "f-seed", type: "number", value: f.seed, placeholder: "random", oninput: (e) => { f.seed = e.target.value; } }))));

    const adv = h("details", { style: { marginTop: "16px" } }, h("summary", { class: "label", style: { cursor: "pointer" } }, "More settings"));
    adv.append(h("div", { class: "inline-fields", style: { marginTop: "12px" } },
      h("div", { class: "field" }, h("label", { for: "f-lora" }, "LoRA"),
        h("select", { id: "f-lora", onchange: (e) => { f.lora = e.target.value; remember(); draw(); } }, loraOptions(m, f.lora))),
      h("div", { class: "field" }, h("label", { for: "f-strength" }, "Strength"),
        h("input", { id: "f-strength", type: "number", min: 0, max: 1.6, step: 0.05, value: f.strength, oninput: (e) => { f.strength = e.target.value; remember(); } }))));
    adv.append(h("div", { class: "inline-fields", style: { marginTop: "14px" } },
      m.kinds.length ? h("div", { class: "field" }, h("label", { for: "f-refs" }, "Colours from the kind"),
        h("select", { id: "f-refs", onchange: (e) => { f.refs_kind = e.target.value; remember(); } },
          h("option", { value: "" }, "The same kind"), m.kinds.map((x) => h("option", { value: x.id, selected: x.id === f.refs_kind }, x.label)))) : null,
      h("div", { class: "field" }, h("label", { for: "f-mirror" }, "Symmetry"),
        h("select", { id: "f-mirror", onchange: (e) => { f.mirror = e.target.value; remember(); } },
          [["auto", "Like the original"], ["none", "None"], ["left", "Left half mirrored"], ["right", "Right half mirrored"]]
            .map(([v, l]) => h("option", { value: v, selected: v === f.mirror }, l)))),
      h("div", { class: "field" }, h("label", { for: "f-green" }, "Green parts"),
        h("select", { id: "f-green", onchange: (e) => { f.keep_green = e.target.value; remember(); } },
          [["auto", "Detect"], ["on", "Keep them"]].map(([v, l]) => h("option", { value: v, selected: v === f.keep_green }, l))))));
    const kfill = (k && Number(k.fill)) || 0;
    adv.append(h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "f-fill" }, "Fill (%)"),
      h("input", { id: "f-fill", type: "number", min: 0, max: 100, step: 5, value: f.fill, placeholder: f.start === "sprite" ? "like the original" : kfill ? `${kfill} (the kind's)` : "as drawn",
        oninput: (e) => { f.fill = e.target.value; remember(); } }),
      h("div", { class: "hint" }, "How much of the canvas the sprite's longest side takes: 100 edge to edge, 90 leaves a small margin. Empty: the kind's setting for new sprites; a remaster keeps the original's size.")));
    adv.append(h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "f-pal" }, "Palette"),
      h("select", { id: "f-pal", onchange: (e) => { f.palette = e.target.value; remember(); draw(); } }, paletteOptions(f.palette, g && g.palette)),
      h("div", { class: "hint" }, "Colours and tuning put on the results. ", h("a", { href: "#/palettes" }, "Palettes"))));
    adv.append(h("label", { class: "check small", style: { marginTop: "12px" } }, h("input", { type: "checkbox", checked: f.no_tidy, onchange: (e) => { f.no_tidy = e.target.checked; remember(); } }), "No dark outline"));
    adv.append(h("div", { class: "field" }, h("label", { for: "f-name" }, "Name of the results"),
      h("input", { id: "f-name", type: "text", value: f.name, placeholder: `${f.kind || f.model}_${f.group}`, oninput: (e) => { f.name = e.target.value; remember(); } })));
    form.append(adv);
    const activePal = f.palette === "none" ? null : S.palettes.find((p) => p.id === (f.palette || (g && g.palette)));
    if (activePal) form.append(h("div", { class: "note", style: { marginTop: "14px" } }, "Palette ", h("b", {}, activePal.label), f.palette ? "" : " (the group's)",
      ` is put on the results (${({ tune: "tuning only", snap: "snapped to its colours", match: "light matched, then snapped" })[activePal.mode]}). `,
      swatchStrip(activePal.colours.length ? activePal.colours : [], activePal.tune)));

    if (S.status && !S.status.comfy.up) form.append(h("div", { class: "note warn", style: { marginTop: "14px" } }, "ComfyUI is stopped. Start it on the Overview."));
    form.append(h("div", { style: { marginTop: "18px" } }, h("button", { class: "btn primary block", type: "submit", disabled: loraMissing,
      title: loraMissing ? "No LoRA installed for this model: see the note at the top" : "" }, `Generate ${f.count} variant${Number(f.count) === 1 ? "" : "s"}`)));
  };

  const pickerPanel = h("div", { class: "panel" });
  const jobBox = h("div", {});
  side.append(jobBox, pickerPanel);
  let filter = "";
  let allGroups = false;
  const drawSide = () => {
    pickerPanel.innerHTML = "";
    const m = modelById(f.model);
    const list = sprites.filter((s) => (allGroups || s.group === f.group) && s.name.includes(filter));
    pickerPanel.append(h("div", { class: "panel-head" }, h("h2", {}, f.start === "sprite" ? "Sprite to start from" : "The group's sprites"),
      h("div", { class: "row" },
        h("label", { class: "check small" }, h("input", { type: "checkbox", checked: allGroups, onchange: (e) => { allGroups = e.target.checked; drawSide(); } }), "All groups"),
        h("input", { type: "text", placeholder: "Filter", value: filter, style: { width: "140px" }, oninput: (e) => { filter = e.target.value; drawSide(); } }))));
    if (!sprites.length) pickerPanel.append(h("div", { class: "note warn" }, "This model has no sprites yet. ", h("a", { href: "#/models/" + m.id }, "Add some in Models"), "."));
    else if (!list.length) pickerPanel.append(h("p", { class: "muted small" }, "No sprite in this group."));
    const choosable = f.start === "sprite";
    pickerPanel.append(h("div", { class: "picker" }, list.map((s) => h("div", {
      class: "pick" + (f.init === s.path ? " on" : ""), title: `${s.name} (${s.w}×${s.h})`, tabindex: choosable ? 0 : -1,
      onclick: () => { if (choosable) { f.init = s.path; if (s.kind) f.kind = s.kind; if (s.group) f.group = s.group; draw(); drawSide(); } else openSprite(s.path); },
    }, h("img", { class: "px", src: fileUrl(s.path), alt: "", loading: "lazy" }), h("span", {}, s.name.replace(/\.png$/, ""))))));
  };

  let panel = null;
  const submit = async () => {
    const body = Object.assign({}, f, { keep_green: f.keep_green === "on" });
    if (f.start === "sprite" && !f.init) return toast("Pick the sprite to start from.", true);
    if (f.start === "sketch" && !f.sketch) return toast("Upload your sketch first.", true);
    const job = await attempt(() => api("/generate", { body }), "Queued");
    if (!job) return;
    if (panel) panel.stop();
    jobBox.innerHTML = "";
    panel = jobPanel(job.id, async () => {
      const r = await api("/jobs/" + job.id);
      if (r.result) showResult(r.result);
    });
    jobBox.append(panel);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const showResult = (r) => {
    const box = h("div", { class: "panel" });
    const drawR = () => {
      box.innerHTML = "";
      box.append(h("div", { class: "panel-head" }, h("h2", {}, "Result"), h("a", { class: "btn sm", href: "#/results?path=" + encodeURIComponent(r.path) }, "Open in Results")));
      box.append(h("div", { class: "sprite-row" },
        r.orig ? tile({ path: r.orig, name: "original", w: r.orig_size && r.orig_size[0], h: r.orig_size && r.orig_size[1] }, { orig: true, refresh: drawR }) : null,
        r.variants.map((v) => tile(v, { pickAs: r.target, refresh: drawR, remaster: remasterFrom, touchup: (x) => touchUp(x, r) }))));
      if (!r.variants.length) box.append(h("div", { class: "note bad" }, "No sprite came out of this run: see the log."));
    };
    drawR();
    jobBox.append(box);
  };
  const remasterFrom = (v) => { f.start = "sprite"; f.init = v.path; draw(); drawSide(); window.scrollTo({ top: 0, behavior: "smooth" }); };

  onLeave(() => panel && panel.stop());
  await loadPicks();
  await loadSprites();
  draw();
  drawSide();
};

function readFile(file) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
}

// ---------------------------------------------------------------- Results

routes.results = async (main, _parts, params) => {
  main.append(head("Results", "Every generation, batch and LoRA comparison in the output folder. Star the variant to keep, then export the picks.",
    h("button", { class: "btn", onclick: exportPicks }, "Export picks")));
  const layout = h("div", { class: "split" });
  const listPanel = h("div", { class: "panel" });
  const view = h("div", {});
  layout.append(listPanel, view);
  main.append(layout);
  await loadPicks();
  let items = await api("/results");
  let bin = await api("/results/bin");
  let current = params.get("path") || (items.find((i) => !i.empty) || items[0] || {}).path;
  let filter = "";
  let typeFilter = "all";

  // Deleting moves the folders to the output folder's .bin: they can be put back until the bin is emptied.
  const removeResults = async (body, what) => {
    if (!(await askConfirm(`Delete ${what}? It goes to the bin (output folder/.bin) and can be put back until you empty the bin.`, { danger: true, ok: "Delete", title: "Delete" }))) return;
    const r = await attempt(() => api("/results/delete", { body }));
    if (!r) return;
    toast(`${r.deleted.length} result${r.deleted.length === 1 ? "" : "s"} moved to the bin`);
    bin = r.bin;
    items = await api("/results");
    if (r.deleted.some((x) => current && (current.endsWith("/" + x) || current.includes("/" + x + "/")))) {
      current = (items.find((i) => !i.empty) || items[0] || {}).path;
      history.replaceState(null, "", current ? "#/results?path=" + encodeURIComponent(current) : "#/results");
    }
    drawList();
    drawView();
  };

  const binBar = () => {
    const nEmpty = items.filter((i) => i.empty).length;
    return h("div", { class: "results-tools" },
      nEmpty ? h("button", { class: "btn sm", title: "Results with nothing rendered in them (every sprite failed, or a stopped job)",
        onclick: () => removeResults({ empty: true }, `the ${nEmpty} empty result${nEmpty === 1 ? "" : "s"}`) }, `Delete empty (${nEmpty})`) : null,
      bin.count ? h("span", { class: "muted small" }, `In the bin: ${bin.count} (${(bin.bytes / 1048576).toFixed(1)} MB)`) : null,
      bin.count ? h("button", { class: "btn sm ghost", onclick: async () => {
        const r = await attempt(() => api("/results/bin/restore", { body: { count: 1 } }));
        if (!r) return;
        toast(r.restored.length ? "Put back: " + r.restored.join(", ") : "Nothing to put back (a folder with that name exists again)", !r.restored.length);
        bin = r.bin; items = await api("/results"); drawList(); drawView();
      } }, "Undo last delete") : null,
      bin.count ? h("button", { class: "btn sm ghost danger", onclick: async () => {
        if (!(await askConfirm(`Erase the ${bin.count} deleted result(s) for good?`, { danger: true, ok: "Erase", title: "Empty the bin" }))) return;
        const r = await attempt(() => api("/results/bin/empty", { body: {} }));
        if (r) { bin = r; toast("Bin emptied"); drawList(); }
      } }, "Empty bin") : null);
  };

  const drawList = () => {
    listPanel.innerHTML = "";
    listPanel.append(binBar(), h("input", { type: "text", placeholder: "Filter by name", value: filter, oninput: (e) => { filter = e.target.value; drawItems(); } }),
      h("div", { style: { margin: "10px 0" } }, seg([{ id: "all", label: "All" }, { id: "single", label: "Runs" }, { id: "batch", label: "Batches" }, { id: "matrix", label: "LoRA tests" }],
        typeFilter, (v) => { typeFilter = v; drawItems(); })));
    const box = h("div", { class: "list" });
    listPanel.append(box);
    const drawItems = () => {
      box.innerHTML = "";
      const shown = items.filter((i) => (typeFilter === "all" || i.type === typeFilter) && i.title.toLowerCase().includes(filter.toLowerCase()));
      if (!shown.length) box.append(h("p", { class: "muted small" }, "Nothing here."));
      for (const i of shown) {
        box.append(h("div", { class: "list-item" + (i.path === current ? " on" : ""), tabindex: 0,
          onclick: () => { current = i.path; history.replaceState(null, "", "#/results?path=" + encodeURIComponent(i.path)); drawItems(); drawView(); },
          onkeydown: (e) => { if (e.key === "Enter") e.target.click(); } },
          h("div", { class: "thumb " + ltClass() }, i.cover ? h("img", { class: "px", src: fileUrl(i.cover), alt: "" }) : null),
          h("div", { style: { minWidth: 0 } }, h("div", { class: "t", title: i.title }, i.title),
            h("div", { class: "s" }, i.type === "batch" ? h("span", { class: "tag amber" }, "batch") : i.type === "matrix" ? h("span", { class: "tag" }, "LoRA test") : null,
              i.empty ? [" ", h("span", { class: "tag bad", title: "Nothing was rendered: see the job's log on the Jobs page" }, "empty")] : null,
              i.busy ? [" ", h("span", { class: "tag" }, "running")] : null,
              " ", i.type === "matrix" ? `${i.count} subjects` : i.type === "batch" ? `${i.count} of ${i.tried} sprites` : `${i.count} variants`, ", ", ago(i.mtime)))));
      }
    };
    drawItems();
  };

  const drawView = async () => {
    view.innerHTML = "";
    if (!current) {
      view.append(h("div", { class: "empty" }, "No results yet.", h("br"), h("a", { class: "btn primary", href: "#/generate" }, "Generate sprites")));
      return;
    }
    let d;
    try { d = await api("/results/detail?path=" + encodeURIComponent(current)); } catch (e) { view.append(h("div", { class: "note bad" }, e.message)); return; }
    const body = h("div", {});
    const item = items.find((i) => i.path === d.path) || {};
    const delBtn = (path, label, small) => item.busy ? null : h("button", { class: "btn sm ghost danger", title: "Move to the bin",
      onclick: () => removeResults({ paths: [path] }, label) }, small ? "Delete" : "Delete " + (d.type === "batch" ? "batch" : d.type === "matrix" ? "test" : "result"));
    const redraw = () => { body.innerHTML = ""; drawBody(); bar.replaceWith(bar = makeBar()); };
    const makeBar = () => viewBar(redraw, [h("label", { class: "check small" }, h("input", { type: "checkbox", checked: S.view.raw, onchange: (e) => { S.view.raw = e.target.checked; saveView(); redraw(); } }), "Raw renders")]);
    let bar = makeBar();
    const remaster = (set) => (v) => {
      const q = new URLSearchParams({ init: v.path });
      const p = set.params || {};
      if (p.model) q.set("model", p.model);
      if (p.group) q.set("group", p.group);
      if (p.kind && p.kind !== "none") q.set("kind", p.kind);
      location.hash = "#/generate?" + q;
    };
    const setRow = (s, pickAs, title, deletable) => h("div", { class: "set" },
      h("div", { class: "set-head" }, h("h3", {}, title || s.name), s.done ? null : item.busy ? h("span", { class: "tag amber" }, "rendering…")
          : h("span", { class: s.variants.length ? "tag" : "tag bad" }, s.variants.length ? "unfinished" : "failed"),
        h("span", { class: "muted small", title: s.prompt }, [s.params.denoise && s.params.denoise !== "n/a" ? `denoise ${s.params.denoise}` : "", s.params.lora || "",
          s.params.palette && s.params.palette !== "none" ? "palette " + base(s.params.palette).replace(/\.json$/, "") : ""].filter(Boolean).join(", ")),
        deletable ? h("span", { style: { marginLeft: "auto" } }, delBtn(s.path, `the sprite ${s.name} of this batch`, true)) : null),
      h("div", { class: "sprite-row" },
        s.orig ? tile({ path: s.orig, name: "original", w: s.orig_size && s.orig_size[0], h: s.orig_size && s.orig_size[1] }, { orig: true, refresh: redraw }) : null,
        s.variants.map((v) => tile(v, { pickAs, refresh: redraw, remaster: remaster(s), touchup: (x) => touchUp(x, s),
          recolour: (x) => recolourDialog(x, async () => { items = await api("/results"); drawList(); drawView(); }) })),
        !s.variants.length ? h("span", { class: "muted small" }, item.busy && !s.done ? "being rendered: they appear here when the job finishes this sprite" : "no variants") : null));
    const drawBody = () => {
      if (d.type === "single") {
        body.append(h("div", { class: "panel" },
          h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, d.name), h("div", { class: "small muted" }, fmtTime(d.mtime))),
            h("div", { class: "row" }, h("button", { class: "btn sm ghost", title: d.path, onclick: () => { navigator.clipboard && navigator.clipboard.writeText(d.path); toast("Folder path copied"); } }, "Copy folder path"),
              delBtn(d.path, `the result ${d.name}`))),
          d.prompt ? h("p", { class: "small muted" }, d.prompt) : null,
          setRow(d, d.target, " ")));
      } else if (d.type === "batch") {
        const classes = [...new Set(d.sets.map((s) => s.class))];
        const pickedCount = d.sets.filter((s) => s.variants.some((v) => S.picks[v.path])).length;
        const failedSets = d.sets.filter((s) => !s.variants.length && !item.busy);
        body.append(h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Batch " + d.title),
          h("div", { class: "row" }, h("span", { class: "muted small" }, `${d.sets.length - failedSets.length} sprites made, ${pickedCount} picked` + (failedSets.length ? `, ${failedSets.length} failed` : "")),
            failedSets.length && failedSets.length < d.sets.length ? h("button", { class: "btn sm ghost", onclick: () => removeResults({ paths: failedSets.map((s) => s.path) }, `the ${failedSets.length} failed sprite(s) of this batch`) }, "Delete failed") : null,
            delBtn(d.path, `the whole batch ${d.title}`))),
          d.sheets.length ? h("div", { class: "row small" }, "Sheets: ", d.sheets.map((sh) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); openSprite(sh); } }, base(sh)))) : null));
        for (const cls of classes) {
          const p = h("div", { class: "panel" }, h("h2", {}, cls.replace("_", " ")));
          for (const s of d.sets.filter((x) => x.class === cls)) p.append(setRow(s, s.target, null, true));
          body.append(p);
        }
      } else {
        for (const subj of d.subjects) {
          const p = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, subj.name),
            h("div", { class: "row" }, subj.sheet ? h("button", { class: "btn sm", onclick: () => openSprite(subj.sheet) }, "Sheet") : null,
              d.subjects[0] === subj ? delBtn(d.path, `the LoRA test ${d.title}`) : null)));
          for (const c of subj.cells) p.append(setRow(c, c.target, c.name.replace(subj.name + "_", "")));
          body.append(p);
        }
      }
    };
    drawBody();
    view.append(bar, body);
    // A job is still writing here: refresh every 15 s, so new sprites show and the tags settle.
    clearTimeout(liveTimer);
    if (item.busy) {
      const shown = current;
      liveTimer = setTimeout(async () => {
        if (current !== shown || !document.body.contains(view)) return;
        const y = window.scrollY;
        items = await api("/results");
        drawList();
        await drawView();
        window.scrollTo(0, y);
      }, 15000);
    }
  };
  let liveTimer = null;
  onLeave(() => clearTimeout(liveTimer));

  drawList();
  drawView();
};

async function exportPicks() {
  const r = await attempt(() => api("/picks/export", { body: {} }));
  if (r) toast(r.files.length ? `${r.files.length} sprite${r.files.length === 1 ? "" : "s"} copied to ${r.folder}` : "Nothing picked yet: star the variants you want first.", !r.files.length);
}

// ---------------------------------------------------------------- Compare

routes.compare = async (main) => {
  main.append(head("Compare", "Sprites you added with the compare button, side by side at the same scale.",
    h("button", { class: "btn ghost", onclick: () => { setTray([]); route(); } }, "Clear")));
  const body = h("div", {});
  const redraw = () => { body.innerHTML = ""; draw(); };
  main.append(viewBar(() => { main.innerHTML = ""; routes.compare(main); }), body);
  const draw = () => {
    if (!S.tray.length) {
      body.append(h("div", { class: "empty" }, "Nothing to compare yet. Use the compare button on any sprite in Results or Generate."));
      return;
    }
    body.append(h("div", { class: "panel" }, h("div", { class: "sprite-row" }, S.tray.map((t, i) => {
      const el = tile({ path: t.path, w: t.w, h: t.h, name: t.label }, { refresh: redraw });
      const tools = el.querySelector(".tools");
      tools.innerHTML = "";
      if (i > 0) tools.append(h("button", { class: "icon-btn", title: "Move left", onclick: () => { const a = [...S.tray]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; setTray(a); redraw(); } }, "‹"));
      tools.append(iconBtn("x", "Remove", () => { setTray(S.tray.filter((_, k) => k !== i)); redraw(); }));
      return el;
    }))));
  };
  await loadPicks();
  draw();
};

// ---------------------------------------------------------------- Train

routes.train = async (main, parts) => {
  await loadCatalog();
  const models = visibleModels();
  const want = parts[0] || store.get("trainCat", "");
  const cat = models.some((m) => m.id === want) ? want : models[0].id;
  store.set("trainCat", cat);
  main.append(head("Train", "Teach a model's LoRA the look of its sprites. Build the training set, train, then choose the best checkpoint from its previews. What a model learns (its groups, kinds, words and sprites) is set in Models."));
  main.append(h("div", { class: "chips", style: { marginBottom: "16px" } }, models.map((m) => h("button", { type: "button", class: "chip" + (m.id === cat ? " on" : ""),
    onclick: () => { location.hash = "#/train/" + m.id; } }, m.label))));
  const box = h("div", { class: "stack" });
  main.append(box);
  let timer = null;
  onLeave(() => clearTimeout(timer));
  const opts = Object.assign({ model: "sd15", epochs: "", dim: "", lr: "0.0001", precision: "auto", repeats: "", src: "" }, store.get("train." + cat, {}));
  if (opts.precision === "no" && !store.get("precisionChosen", false)) opts.precision = "auto"; // the old default, not a choice
  if (opts.epochs === 12) opts.epochs = "";
  if (opts.dim === 32) opts.dim = "";
  const keep = () => store.set("train." + cat, opts);
  let captionFilter = "";
  let showCaptions = false;

  const draw = async () => {
    const info = await api(`/train/${cat}?model=${opts.model}`);
    const c = models.find((m) => m.id === cat);
    const xl = opts.model === "sdxl";
    const running = info.jobs.find((j) => j.kind !== "setup" && ["running", "queued"].includes(j.status));
    const downloading = info.jobs.find((j) => j.kind === "setup" && ["running", "queued"].includes(j.status));
    const trainJob = info.jobs.find((j) => j.kind === "train");
    box.innerHTML = "";

    // 0. which base model
    const s0 = h("div", { class: "panel" },
      h("div", { class: "row between" },
        h("div", {}, h("h2", { style: { marginBottom: "4px" } }, "Base model"),
          h("p", { class: "small muted", style: { margin: 0, maxWidth: "80ch" } }, xl
            ? "SDXL renders at 1024 px: twice as many render pixels per sprite pixel as SD 1.5, so finer detail and cleaner pixels, but several times slower to train and to render. Its LoRA is " + info.name + "."
            : "SD 1.5 renders at 512 px: quick to train and to render. Its LoRA is " + info.name + ".")),
        seg([{ id: "sd15", label: "SD 1.5" }, { id: "sdxl", label: "SDXL" }], opts.model, (v) => { opts.model = v; keep(); draw(); })),
      xl && S.catalog.gpu_mb && S.catalog.gpu_mb < 12000 ? h("div", { class: "note warn", style: { marginTop: "12px" } },
        `This card has ${(S.catalog.gpu_mb / 1024).toFixed(0)} GB: SDXL only fits with its weights kept in half precision (done automatically), with almost nothing to spare, `
        + "so close anything else using the GPU first. On GTX 10 cards (no fast half precision) expect many hours; SD 1.5 is the practical choice on such a card.") : null,
      xl && !info.sdxl_ready ? (() => {
        const dl = info.jobs.find((j) => j.kind === "setup");
        if (dl && ["running", "queued"].includes(dl.status)) {
          return h("div", { class: "note", style: { marginTop: "12px" } }, "Downloading the SDXL model (about 7 GB, a few minutes to an hour depending on the connection). ",
            h("a", { href: "#/jobs/" + dl.id }, "Follow it"), ". Training SDXL is possible once it is done.");
        }
        return h("div", { class: "note warn", style: { marginTop: "12px" } },
          dl && dl.status === "failed" ? ["The SDXL download failed: ", h("a", { href: "#/jobs/" + dl.id }, "see why"), ". "] : "The SDXL model is not downloaded yet (about 7 GB). ",
          h("button", { class: "btn sm", onclick: async () => { if (await attempt(() => api("/train/setup-sdxl", { body: {} }), "Downloading SDXL")) schedule(1500); } }, "Download it"));
      })() : null);
    box.append(s0);

    // 1. training set
    const ds = info.dataset;
    const s1 = h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("div", { class: "step-head" }, h("span", { class: "n" + (ds.exists ? " done" : "") }, "1"), h("h2", {}, "Training set")),
        ds.exists ? h("span", { class: "muted small" }, `${ds.count} images × ${ds.repeats} repeats = ${ds.count * ds.repeats} steps per epoch, built ${ago(ds.mtime)}`) : null),
      h("p", { class: "muted small" }, `The model's ${c.sprites} sprites that are in an enabled group, each scaled onto the green background with a caption made from its group, kind, name and colours. `,
        h("a", { href: "#/models/" + cat }, `Groups: ${c.groups.filter((g) => g.enabled).map((g) => g.label).join(", ") || "none"}.`), " Change them in Models, then build again."),
      h("div", { class: "row" },
        h("div", { class: "field", style: { width: "130px" } }, h("label", {}, "Repeats"),
          h("input", { type: "number", min: 1, max: 30, value: opts.repeats, placeholder: String(c.repeats || ""), oninput: (e) => { opts.repeats = e.target.value; keep(); } })),
        h("button", { class: "btn" + (ds.exists ? "" : " primary"), style: { alignSelf: "flex-end" }, disabled: !!running,
          onclick: async () => { const j = await attempt(() => api(`/train/${cat}/dataset`, { body: opts }), "Building the training set"); if (j) schedule(800); } },
          ds.exists ? "Build again" : "Build the training set")),
      h("div", { class: "hint", style: { marginTop: "6px" } }, "Repeats: how often each image is seen per epoch. Aim for about 300 steps per epoch."));
    if (ds.exists) {
      s1.append(h("div", { style: { marginTop: "14px" } }, h("img", { src: fileUrl(ds.sheet), class: "px", alt: "every training image", title: "Open full size",
        style: { maxWidth: "100%", maxHeight: "220px", borderRadius: "6px", cursor: "zoom-in" }, onclick: () => openSprite(ds.sheet, "Training set") })));
      s1.append(h("button", { class: "btn sm ghost", style: { marginTop: "8px" }, onclick: () => { showCaptions = !showCaptions; draw(); } }, showCaptions ? "Hide the captions" : `Review the ${ds.count} captions`));
      if (showCaptions) {
        const tbody = h("tbody");
        const drawRows = () => {
          tbody.innerHTML = "";
          for (const r of ds.rows.filter((x) => (x.id + x.caption).toLowerCase().includes(captionFilter.toLowerCase()))) {
            const ta = h("textarea", { rows: 2 }, r.caption);
            const saveBtn = h("button", { class: "btn sm", disabled: true, onclick: async () => {
              if (await attempt(() => api(`/train/${cat}/caption`, { method: "PUT", body: { id: r.id, caption: ta.value, model: opts.model } }), "Caption saved")) { r.caption = ta.value; saveBtn.disabled = true; }
            } }, "Save");
            ta.addEventListener("input", () => { saveBtn.disabled = ta.value === r.caption; });
            tbody.append(h("tr", {}, h("td", { style: { width: "84px" } }, h("img", { class: "px", src: fileUrl(r.image), alt: r.id, style: { width: "72px", height: "72px", borderRadius: "4px" } })),
              h("td", { class: "caption-cell" }, h("div", { class: "mono small", style: { marginBottom: "4px" } }, r.id), ta), h("td", { style: { width: "70px" } }, saveBtn)));
          }
        };
        s1.append(h("div", { style: { marginTop: "10px" } },
          h("input", { type: "text", placeholder: "Filter captions", value: captionFilter, oninput: (e) => { captionFilter = e.target.value; drawRows(); }, style: { maxWidth: "300px", marginBottom: "8px" } }),
          h("p", { class: "hint" }, "Edits are lost when the training set is built again. Every caption starts with the trigger word " + c.trigger + "."),
          h("table", {}, tbody)));
        drawRows();
      }
    }
    box.append(s1);

    // 2. training
    const s2 = h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("div", { class: "step-head" }, h("span", { class: "n" + (info.checkpoints.length ? " done" : "") }, "2"), h("h2", {}, "Train")),
        h("span", { class: "muted small" }, trainEstimate(xl))));
    if (trainJob && ["running", "queued"].includes(trainJob.status)) {
      s2.append(trainProgress(trainJob));
    } else {
      if (trainJob && trainJob.status === "failed" && trainJob.error) s2.append(h("div", { class: "note bad", style: { marginBottom: "12px" } }, trainJob.error));
      if (trainJob) s2.append(h("p", { class: "small" }, "Last run: ", statusLabel(trainJob), h("span", { class: "muted" }, " ", fmtTime(trainJob.ended), trainJob.started && trainJob.ended ? `, ${fmtDur(trainJob.ended - trainJob.started)}` : ""),
        " ", h("a", { href: "#/jobs/" + trainJob.id }, "log")));
      s2.append(h("div", { class: "inline-fields" },
        numField("Epochs", "epochs", 1, 60, 1, "passes over the set; a checkpoint every 2", xl ? "8" : "12"),
        numField("Rank", "dim", 4, 128, 4, xl ? "LoRA size; 16 keeps SDXL in 8 GB" : "LoRA size; 32 is plenty", xl ? "16" : "32"),
        numField("Learning rate", "lr", 0.00001, 0.001, 0.00001, "1e-4: slower is safer"),
        xl ? null : h("div", { class: "field" }, h("label", {}, "Precision"),
          h("select", { onchange: (e) => { opts.precision = e.target.value; store.set("precisionChosen", true); keep(); } },
            [["auto", "Automatic"], ["no", "Full (fp32)"], ["fp16", "Half (fp16)"], ["bf16", "Half (bf16, RTX 30 and newer)"]]
              .map(([v, l]) => h("option", { value: v, selected: (opts.precision || "auto") === v }, l))),
          h("div", { class: "hint" }, "Automatic: bf16 on RTX 30 and newer, fp16 on RTX 20, full on older cards"))));
      s2.append(h("div", { class: "note", style: { marginTop: "14px" } }, (S.catalog.gpu_mb && S.catalog.gpu_mb >= 15000)
        ? "This card has room for both: ComfyUI keeps running during training (Settings can change it). Jobs already in the queue run first."
        : "Training needs the whole GPU: the app stops ComfyUI for the run and starts it again at the end. Jobs already in the queue run first."));
      s2.append(h("div", { style: { marginTop: "14px" } }, h("button", { class: "btn primary", disabled: !ds.exists || !!running || (xl && !info.sdxl_ready),
        onclick: async () => { const j = await attempt(() => api(`/train/${cat}/start`, { body: opts }), "Training queued"); if (j) schedule(1500); } },
        ds.exists ? `Train ${info.name}` : "Build the training set first")));
    }
    box.append(s2);

    // 3. checkpoints and previews
    const s3 = h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("div", { class: "step-head" }, h("span", { class: "n" + (info.installed.length ? " done" : "") }, "3"), h("h2", {}, "Choose a checkpoint")),
        info.installed.length > 1 ? h("span", { class: "muted small" }, "Also installed: ", info.installed.filter((i) => i.name !== info.name + ".safetensors").map((i) => h("span", { class: "mono" }, i.name + " ")))
          : null),
      loraNote(modelById(cat), info.name + ".safetensors", true));
    const used = loraFor(modelById(cat), info.name + ".safetensors").info.checkpoint;
    if (!info.checkpoints.length) {
      s3.append(h("p", { class: "muted" }, "Checkpoints and their previews appear here during training, every 2 epochs. SDXL on a card under 12 GB saves the checkpoints without previews: try them on Compare LoRAs."));
    } else {
      s3.append(h("p", { class: "small muted" }, "Each column is one checkpoint, each row one test prompt (things the game does not have, to see the style rather than copies). Pick the earliest column that looks right: later ones tend to copy the training set."));
      const grid = info.samples.grid;
      const epochs = [...new Set(Object.values(grid).flatMap((row) => Object.keys(row)))].map(Number).filter((e) => e > 0).sort((a, b) => a - b);
      const ckptFor = (e) => info.checkpoints.find((k) => k.epoch === e) || (e === Math.max(...epochs) ? info.checkpoints.find((k) => k.epoch === null) : null);
      const table = h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}), epochs.map((e) => {
          const k = ckptFor(e);
          return h("th", {}, h("div", {}, "Epoch " + e), k ? h("div", { class: "row", style: { justifyContent: "center", marginTop: "4px", gap: "4px" } },
            used === k.name ? h("span", { class: "tag ok", title: `Installed as ${info.name}.safetensors` }, "in use")
              : h("button", { class: "btn sm primary", title: `Install as ${info.name}.safetensors, the one the generator uses`, onclick: () => install(k.path, info.name) }, "Use"),
            h("button", { class: "btn sm", title: "Install under its own name, to compare", onclick: () => install(k.path, `${c.trigger}_e${e}${xl ? "_xl" : ""}`) }, "Add")) : null);
        }))),
        h("tbody", {}, Object.keys(grid).sort().map((pi) => h("tr", {},
          h("td", { class: "prompt-cell" }, (info.samples.prompts[Number(pi)] || "").replace(c.trigger + ", ", "").split(", pixel art")[0]),
          epochs.map((e) => h("td", {}, grid[pi][String(e)] ? h("img", { class: "px", src: fileUrl(grid[pi][String(e)]), alt: `epoch ${e}`, onclick: () => openSprite(grid[pi][String(e)], `Prompt ${Number(pi) + 1}, epoch ${e}`) }) : null))))));
      s3.append(h("div", { class: "preview-grid" }, table));
      if (!epochs.length) s3.append(h("p", { class: "muted small" }, "No previews yet."));
      s3.append(h("details", { style: { marginTop: "12px" } }, h("summary", { class: "label", style: { cursor: "pointer" } }, `All ${info.checkpoints.length} checkpoint files`),
        h("table", {}, h("tbody", {}, info.checkpoints.map((k) => h("tr", {}, h("td", { class: "mono small" }, k.name), h("td", { class: "muted small" }, fmtMB(k.size)), h("td", { class: "muted small" }, fmtTime(k.mtime)),
          h("td", {}, used === k.name ? h("span", { class: "tag ok" }, "in use") : h("button", { class: "btn sm", onclick: () => install(k.path, info.name) }, "Use"))))))));
    }
    box.append(s3);
    if (running) schedule(running.kind === "train" ? 5000 : 1500);
    else if (downloading) schedule(5000);
  };
  const numField = (label, key, min, max, step, hint, placeholder) => h("div", { class: "field" }, h("label", {}, label),
    h("input", { type: "number", min, max, step, value: opts[key], placeholder, oninput: (e) => { opts[key] = e.target.value; keep(); } }), h("div", { class: "hint" }, hint));
  const install = async (path, as) => {
    const r = await attempt(() => api(`/train/${cat}/install`, { body: { checkpoint: path, as } }));
    if (r) { toast(`Installed as ${r.installed}`); await loadCatalog(); draw(); }
  };
  const schedule = (ms) => { clearTimeout(timer); timer = setTimeout(draw, ms); };
  await draw();
};

/** How long a training run takes here: the last run's time when there is one. */
function trainEstimate(xl) {
  const t = S.catalog && S.catalog.speed && S.catalog.speed.train_minutes[xl ? "sdxl" : "sd15"];
  if (t) return `about ${fmtDur(t * 60)} (your last run)`;
  return xl ? "several hours on an 8 GB card, less on newer ones" : "about 1 to 2 hours on an 8 GB card, less on newer ones";
}

function trainProgress(j) {
  const p = j.progress || {};
  const el = h("div", {});
  el.append(h("div", { class: "row between", style: { marginBottom: "8px" } }, h("span", {}, statusLabel(j), h("span", { class: "muted small" }, "  ", p.text || "")),
    h("div", { class: "row" }, h("a", { class: "btn sm ghost", href: "#/jobs/" + j.id }, "Log"),
      h("button", { class: "btn sm danger", onclick: () => attempt(() => api(`/jobs/${j.id}/cancel`, { body: {} }), "Training cancelled") }, "Cancel"))));
  el.append(progressBar(j));
  if (p.loss && p.loss.length > 2) {
    const last = p.loss[p.loss.length - 1][1];
    el.append(h("div", { class: "stats", style: { marginTop: "14px" } },
      h("div", { class: "stat" }, h("b", {}, last.toFixed(4)), h("span", {}, "average loss")),
      h("div", { class: "stat" }, h("b", {}, p.elapsed || ""), h("span", {}, "elapsed"))));
    el.append(lossChart(p.loss));
    el.append(h("p", { class: "hint" }, "The loss goes down quickly, then wobbles: that is normal. The previews below matter more than the number."));
  }
  return el;
}

function lossChart(points) {
  const W = 600, H = 120, pad = 4;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x) => pad + (x - x0) / ((x1 - x0) || 1) * (W - 2 * pad);
  const sy = (y) => H - pad - (y - y0) / ((y1 - y0) || 1) * (H - 2 * pad);
  const d = points.map((p, i) => (i ? "L" : "M") + sx(p[0]).toFixed(1) + " " + sy(p[1]).toFixed(1)).join(" ");
  const svg = `<svg class="loss" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Loss over the training steps">
    <path d="${d}" fill="none" stroke="#62c3da" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
  return h("div", { html: svg, style: { background: "#1b2131", borderRadius: "6px", border: "1px solid var(--line-soft)" } });
}

// ---------------------------------------------------------------- Batch

routes.batch = async (main) => {
  await loadCatalog();
  const b = Object.assign({ model: "", groups: [], kinds: [], count: 2, tag: "", denoise: "", lora: "", palette: "", mode: "remaster", invent: 4, ideas: "" }, store.get("batch2", {}));
  await loadPalettes();
  if (b.palette && b.palette !== "none" && !S.palettes.some((p) => p.id === b.palette)) b.palette = "";
  const keep = () => store.set("batch2", b);
  main.append(head("Batch", "Remaster every sprite of a model in one go, or invent new ones for each group and kind, a few variants each. Finished sprites are skipped, so a stopped batch picks up where it was when started again with the same name."));
  const layout = h("div", { class: "grid-form" });
  const form = h("div", { class: "panel" });
  const side = h("div", { class: "stack" });
  layout.append(form, side);
  main.append(layout);
  const planBox = h("div", { class: "panel" });
  const jobs = (await api("/jobs")).filter((j) => j.kind === "batch");
  const live = jobs.find((j) => ["running", "queued"].includes(j.status));
  if (live) side.append(jobPanel(live.id, () => {}));
  side.append(planBox);

  const chipSet = (items, key) => h("div", { class: "chips" }, items.map((x) => {
    const btn = h("button", { type: "button", class: "chip" + (b[key].includes(x.id) ? " on" : ""), onclick: () => {
      b[key] = b[key].includes(x.id) ? b[key].filter((y) => y !== x.id) : [...b[key], x.id]; btn.classList.toggle("on"); keep();
    } }, x.label);
    return btn;
  }));
  const draw = () => {
    form.innerHTML = "";
    const m = modelById(b.model);
    b.model = m.id;
    form.append(
      h("div", { class: "field" }, h("span", { class: "label" }, "Model"),
        h("div", { class: "chips" }, visibleModels().map((x) => h("button", { type: "button", class: "chip" + (x.id === b.model ? " on" : ""),
          onclick: () => { b.model = x.id; b.groups = []; b.kinds = []; b.lora = ""; keep(); draw(); } }, x.label)))),
      loraNote(m, b.lora),
      h("div", { class: "field", style: { marginTop: "14px" } }, h("span", { class: "label" }, "What to make"),
        seg([{ id: "remaster", label: "Remaster every sprite" }, { id: "invent", label: "Invent new sprites" }], b.mode, (v) => { b.mode = v; keep(); draw(); }),
        h("div", { class: "hint" }, b.mode === "invent"
          ? "New designs from the description alone (no starting sprite), for each selected group and each kind it has sprites of (or the kinds selected). Expect more misses than remasters: render a few more and keep the best."
          : "Each sprite redone from itself: same shape, the LoRA's detail and the group's colours.")),
      b.mode === "invent" ? h("div", { class: "inline-fields", style: { marginTop: "14px" } },
        h("div", { class: "field" }, h("label", { for: "b-inv" }, "New sprites per group and kind"),
          h("input", { id: "b-inv", type: "number", min: 1, max: 50, value: b.invent, oninput: (e) => { b.invent = e.target.value; keep(); } }))) : null,
      b.mode === "invent" ? h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "b-ideas" }, "Ideas (optional, one per line)"),
        h("textarea", { id: "b-ideas", rows: 4, placeholder: "wide crescent hull\nlong lance, twin engine pods\nround carapace, six legs", oninput: (e) => { b.ideas = e.target.value; keep(); } }, b.ideas),
        h("div", { class: "hint" }, "What each new sprite is or looks like, used in turn (new sprite 1 takes line 1...). Empty: the group's and kind's words only, a random design in their style.")) : null,
      h("div", { class: "field", style: { marginTop: "14px" } }, h("span", { class: "label" }, "Groups"), chipSet(m.groups, "groups"),
        h("div", { class: "hint" }, "None selected: all of them.")),
      m.kinds.length ? h("div", { class: "field" }, h("span", { class: "label" }, "Kinds"), chipSet(m.kinds, "kinds"),
        h("div", { class: "hint" }, "None selected: all of them.")) : null,
      h("div", { class: "field" }, h("label", { for: "b-tag" }, "Batch name"),
        h("input", { id: "b-tag", type: "text", value: b.tag, placeholder: "today's date", oninput: (e) => { b.tag = e.target.value; keep(); } }),
        h("div", { class: "hint" }, "Same name as an earlier batch: it continues that one.")),
      h("div", { class: "inline-fields", style: { marginTop: "14px" } },
        h("div", { class: "field" }, h("label", { for: "b-n" }, "Variants each"), h("input", { id: "b-n", type: "number", min: 1, max: 8, value: b.count, oninput: (e) => { b.count = e.target.value; keep(); } })),
        b.mode === "invent" ? null : h("div", { class: "field" }, h("label", { for: "b-d" }, "Distance"), h("input", { id: "b-d", type: "number", min: 0.3, max: 0.95, step: 0.05, value: b.denoise, placeholder: "per kind", oninput: (e) => { b.denoise = e.target.value; keep(); } }))),
      h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "b-fill" }, "Fill (%)"),
        h("input", { id: "b-fill", type: "number", min: 0, max: 100, step: 5, value: b.fill || "", placeholder: b.mode === "invent" ? "each kind's setting" : "like the originals",
          oninput: (e) => { b.fill = e.target.value; keep(); } }),
        h("div", { class: "hint" }, b.mode === "invent" ? "How much of the canvas each new sprite's longest side takes (50 to 100). Empty: each kind's Fill (Models)."
          : "Empty: every remaster keeps its original's size. A value refits every remaster to it (their size in the game changes).")),
      h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "b-pal" }, "Palette"),
        h("select", { id: "b-pal", onchange: (e) => { b.palette = e.target.value; keep(); } },
          h("option", { value: "", selected: !b.palette }, "Each group's own" + (m.groups.some((x) => x.palette) ? ` (${m.groups.filter((x) => x.palette).map((x) => x.label + ": " + x.palette).join(", ")})` : " (none set)")),
          h("option", { value: "none", selected: b.palette === "none" }, "No palette"),
          (S.palettes || []).map((p) => h("option", { value: p.id, selected: p.id === b.palette }, p.label + " for every sprite"))),
        h("div", { class: "hint" }, "Set a group's own palette in Models or on the Palettes page.")),
      h("div", { class: "field", style: { marginTop: "14px" } }, h("label", { for: "b-lora" }, "LoRA"), h("select", { id: "b-lora", onchange: (e) => { b.lora = e.target.value; keep(); draw(); } }, loraOptions(m, b.lora)),
        h("div", { class: "hint" }, "Default: the epoch chosen with Use on the Train page. The ones added with Add are listed too, to batch with another epoch.")),
      h("div", { class: "row", style: { marginTop: "18px" } },
        h("button", { class: "btn", onclick: plan }, "Preview the list"),
        h("button", { class: "btn primary", onclick: start, disabled: !loraFor(m, b.lora).ok,
          title: loraFor(m, b.lora).ok ? "" : "No LoRA installed for this model: see the note at the top" }, "Start the batch")));
  };
  async function plan() {
    planBox.innerHTML = "";
    planBox.append(h("p", { class: "muted" }, "Listing…"));
    const p = await attempt(() => api("/batch/plan", { body: b }));
    planBox.innerHTML = "";
    if (!p) return;
    const per = (S.catalog.speed && S.catalog.speed.sec_per_image) || 15;
    planBox.append(h("div", { class: "panel-head" }, h("h2", {}, `${p.todo.length} sprites to render`),
      h("span", { class: "muted small" }, p.done ? `${p.done} already done` : "", p.todo.length ? `, about ${fmtDur(p.todo.length * Number(b.count || 2) * per)} with SD 1.5` : "")),
      p.error ? h("div", { class: "note bad" }, p.error) : null,
      h("div", { class: "chips" }, p.todo.map((t) => h("span", { class: "tag mono" }, t))));
  }
  async function start() {
    const j = await attempt(() => api("/batch", { body: b }), "Batch queued");
    if (j) { side.innerHTML = ""; side.append(jobPanel(j.id, () => toast("Batch finished: see Results"))); }
  }
  draw();
};

// ---------------------------------------------------------------- Compare LoRAs

routes.loras = async (main) => {
  await loadCatalog();
  const m = Object.assign({ model: "", loras: [], strengths: "0.8 1.0", seed: 1000, tag: "" }, store.get("matrix", {}));
  const keep = () => store.set("matrix", m);
  const model = modelById(m.model);
  m.model = model.id;
  main.append(head("Compare LoRAs", "Render the same test subjects with several LoRA files and strengths, on the same seeds, to choose between them: per group of the model, a new design and a remaster of one of its sprites. One sheet per subject."));
  const layout = h("div", { class: "grid-form" });
  const form = h("div", { class: "panel" });
  const side = h("div", { class: "stack" });
  layout.append(form, side);
  main.append(layout);
  const loras = S.catalog.loras.filter((l) => l.name.startsWith(model.trigger));
  form.append(
    h("div", { class: "field" }, h("span", { class: "label" }, "Model"),
      h("div", { class: "chips" }, visibleModels().map((x) => h("button", { type: "button", class: "chip" + (x.id === m.model ? " on" : ""),
        onclick: () => { m.model = x.id; m.loras = []; keep(); route(); } }, x.label)))),
    h("div", { class: "field" }, h("span", { class: "label" }, `Its LoRA files (${model.trigger}…)`),
      loras.length ? loras.map((l) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: m.loras.includes(l.name),
        onchange: (e) => { m.loras = e.target.checked ? [...m.loras, l.name] : m.loras.filter((x) => x !== l.name); keep(); } }), h("span", { class: "mono" }, l.name)))
        : h("p", { class: "muted small" }, `No ${model.trigger} LoRA installed: train the model first.`),
      h("div", { class: "hint" }, "Add checkpoints from Train with the Add button to compare epochs.")),
    h("div", { class: "field" }, h("label", { for: "m-s" }, "Strengths"), h("input", { id: "m-s", type: "text", value: m.strengths, oninput: (e) => { m.strengths = e.target.value; keep(); } }),
      h("div", { class: "hint" }, "Separated by spaces, between 0.6 and 1.2.")),
    h("div", { class: "inline-fields", style: { marginTop: "14px" } },
      h("div", { class: "field" }, h("label", { for: "m-seed" }, "Seed"), h("input", { id: "m-seed", type: "number", value: m.seed, oninput: (e) => { m.seed = e.target.value; keep(); } })),
      h("div", { class: "field" }, h("label", { for: "m-tag" }, "Name"), h("input", { id: "m-tag", type: "text", value: m.tag, placeholder: "date and time", oninput: (e) => { m.tag = e.target.value; keep(); } }))),
    h("div", { style: { marginTop: "18px" } }, h("button", { class: "btn primary block", onclick: async () => {
      const n = m.loras.length * m.strengths.split(/[ ,]+/).filter(Boolean).length * 2 * model.groups.filter((g) => g.enabled).slice(0, 6).length * 2;
      const j = await attempt(() => api("/matrix", { body: m }), `Queued: ${n} images, about ${fmtDur(n * ((S.catalog.speed && S.catalog.speed.sec_per_image) || 15))}`);
      if (j) { side.innerHTML = ""; side.append(jobPanel(j.id, () => { location.hash = "#/results?path=" + encodeURIComponent(j.out); })); }
    } }, "Start the comparison")));
  const jobs = (await api("/jobs")).filter((j) => j.kind === "matrix");
  const live = jobs.find((j) => ["running", "queued"].includes(j.status));
  if (live) side.append(jobPanel(live.id, () => {}));
  const past = (await api("/results")).filter((r) => r.type === "matrix");
  side.append(h("div", { class: "panel" }, h("h2", {}, "Earlier comparisons"),
    past.length ? h("table", {}, h("tbody", {}, past.map((r) => h("tr", { class: "clickable", onclick: () => { location.hash = "#/results?path=" + encodeURIComponent(r.path); } },
      h("td", {}, r.title), h("td", { class: "muted small" }, `${r.count} sheets`), h("td", { class: "muted small" }, ago(r.mtime))))))
      : h("p", { class: "muted" }, "None yet.")));
};

// ---------------------------------------------------------------- Models

routes.models = async (main, parts) => {
  await loadPalettes();
  await loadCatalog();
  if (parts[0]) return modelEditor(main, parts[0], parts[1] || "sprites");
  main.append(head("Models", "A model is one LoRA: its trigger word, its groups (factions, families...), its kinds (size classes), the words of each, and the sprites it learns from. Models belong to a project, one per game; the menu's project list shows one project at a time."));
  const templates = await api("/templates");
  const byProject = {};
  for (const m of visibleModels()) (byProject[m.project || "No project"] ||= []).push(m);
  if (!S.catalog.models.length) {
    main.append(h("div", { class: "empty" }, "No model yet. Start from a template set below, or create one."));
  }
  for (const [proj, ms] of Object.entries(byProject).sort()) {
    main.append(h("div", { class: "proj-head" }, proj));
    main.append(h("div", { class: "cards" }, ms.map((m) => h("a", { class: "panel model-card", href: "#/models/" + m.id },
      h("div", { class: "row between" }, h("h2", { style: { margin: 0 } }, m.label), h("span", { class: "tag mono" }, m.trigger)),
      h("p", { class: "small muted", style: { margin: "8px 0 0" } }, `${m.sprites} sprites, ${m.groups.length} group${m.groups.length === 1 ? "" : "s"}`,
        m.kinds.length ? `, ${m.kinds.length} kinds` : ""),
      h("p", { class: "small", style: { margin: "6px 0 0", color: "var(--muted)" } }, m.groups.map((g) => g.label).join(", "))))));
  }

  // new model
  const projects = projectsOf();
  const form = { id: "", label: "", trigger: "", from: "", groups: [], project: (S.project && S.project !== "No project" ? S.project : "") || (S.catalog.projects || [])[0] || "" };
  const groupChips = h("div", { class: "chips", style: { marginTop: "8px" } });
  const drawGroupChips = () => {
    groupChips.innerHTML = "";
    if (!form.from.startsWith("copy:")) return;
    const src = S.catalog.models.find((x) => x.id === form.from.slice(5));
    if (!src) return;
    groupChips.append(h("span", { class: "label", style: { width: "100%" } }, "Keep only these groups (none: all)"),
      src.groups.map((g) => h("button", { type: "button", class: "chip" + (form.groups.includes(g.id) ? " on" : ""), onclick: () => {
        form.groups = form.groups.includes(g.id) ? form.groups.filter((x) => x !== g.id) : [...form.groups, g.id]; drawGroupChips();
      } }, g.label)));
  };
  const newPanel = h("div", { class: "panel" }, h("h2", {}, "New model"),
    h("div", { class: "field" }, h("label", { for: "nm-label" }, "Name"), h("input", { id: "nm-label", type: "text", placeholder: "Player ships",
      oninput: (e) => {
        form.label = e.target.value;
        const id = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40);
        $("#nm-id").placeholder = id || "player_ships"; $("#nm-trigger").placeholder = "px" + id.replace(/_/g, "").slice(0, 12);
      } })),
    h("div", { class: "inline-fields", style: { marginTop: "14px" } },
      h("div", { class: "field" }, h("label", { for: "nm-id" }, "Id (folder)"), h("input", { id: "nm-id", type: "text", placeholder: "player_ships", oninput: (e) => { form.id = e.target.value; } })),
      h("div", { class: "field" }, h("label", { for: "nm-trigger" }, "Trigger word"), h("input", { id: "nm-trigger", type: "text", placeholder: "pxplayer", oninput: (e) => { form.trigger = e.target.value; } }))),
    h("div", { class: "field" }, h("label", { for: "nm-project" }, "Project (game)"),
      projectSelect(form.project, (v) => { form.project = v; }, { id: "nm-project" })),
    h("div", { class: "field" }, h("label", { for: "nm-from" }, "Start from"),
      h("select", { id: "nm-from", onchange: (e) => { form.from = e.target.value; form.groups = []; drawGroupChips(); } },
        h("option", { value: "" }, "An empty model"),
        h("optgroup", { label: "A copy of a model's settings (no sprites)" }, S.catalog.models.map((m) => h("option", { value: "copy:" + m.id }, `${m.label} (${m.project || "no project"})`))),
        templates.map((t) => h("optgroup", { label: "Template: " + t.label }, t.models.map((m) => h("option", { value: `tpl:${t.id}/${m.id}` }, m.label)))))),
    groupChips,
    h("button", { class: "btn primary", style: { marginTop: "14px" }, onclick: async () => {
      const id = form.id || $("#nm-id").placeholder;
      const body = { id, label: form.label || id, trigger: form.trigger || $("#nm-trigger").placeholder, project: form.project };
      if (form.from.startsWith("copy:")) Object.assign(body, { copy: form.from.slice(5), groups: form.groups });
      if (form.from.startsWith("tpl:")) { body.template = form.from.slice(4); if (!form.trigger) delete body.trigger; }
      const r = await attempt(() => api("/models", { body }), "Model created");
      if (r) { await loadCatalog(); location.hash = "#/models/" + id; }
    } }, "Create the model"));

  // template sets
  const tplPanel = h("div", { class: "panel" }, h("h2", {}, "Template sets"),
    h("p", { class: "small muted" }, "Ready-made models to start a project from. Adding a set creates its models that do not exist yet; then add sprites to each."),
    templates.map((t) => {
      const missing = t.models.filter((m) => !m.exists);
      return h("div", { class: "set" }, h("div", { class: "row between" }, h("strong", {}, t.label),
        missing.length ? h("button", { class: "btn sm", onclick: async () => {
          const project = await askText(`Add ${t.label}`, { input: "Project (game) for these models", value: S.project && S.project !== "No project" ? S.project : "My game", ok: "Add the models" });
          if (project === null) return;
          const r = await attempt(() => api(`/templates/${t.id}/add`, { body: { project } }));
          if (r) { toast(`${r.created.length} models added`); await loadCatalog(); route(); }
        } }, `Add ${missing.length} model${missing.length === 1 ? "" : "s"}`) : h("span", { class: "muted small" }, "all added")),
        h("p", { class: "small muted", style: { margin: "4px 0 0" } }, t.description),
        h("p", { class: "small", style: { margin: "4px 0 0" } }, t.models.map((m) => m.label + (m.exists ? " ✓" : "")).join(", ")));
    }));

  // import a model file
  const imp = { project: S.project || "", asTemplate: false };
  const fileIn = h("input", { type: "file", accept: ".json,application/json", onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    let data;
    try { data = JSON.parse(await f.text()); } catch (_) { return toast("That file is not JSON.", true); }
    const r = await attempt(() => api("/models/import", { body: { data, project: imp.project, save_template: imp.asTemplate, template_name: f.name.replace(/\.json$/, "") } }));
    if (r) { toast(`Imported: ${r.created.join(", ")}`); await loadCatalog(); route(); }
  } });
  const impPanel = h("div", { class: "panel" }, h("h2", {}, "Import a model file"),
    h("p", { class: "small muted" }, "A model exported from Sprite Studio (Settings tab → Export), here or on another machine: its groups, kinds, words and settings, without sprites."),
    h("div", { class: "field" }, h("label", {}, "Into the project"), projectSelect("__keep__", (v) => { imp.project = v === "__keep__" ? "" : v; }, { keep: "The file's own" })),
    h("label", { class: "check small", style: { marginTop: "10px" } }, h("input", { type: "checkbox", onchange: (e) => { imp.asTemplate = e.target.checked; } }), "Also keep it as a template"),
    h("div", { style: { marginTop: "12px" } }, fileIn));

  main.append(h("div", { class: "proj-head" }, "Add models"), h("div", { class: "cards" }, newPanel, tplPanel, impPanel));

  // projects
  const plist = await api("/projects");
  const projPanel = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Projects"),
    h("button", { class: "btn sm", onclick: async () => {
      const name = await askText("New project", { input: "Name (a game)", placeholder: "My game", ok: "Create" });
      if (name && await attempt(() => api("/projects", { body: { name } }), "Project created")) { await loadCatalog(); route(); }
    } }, "New project")),
    h("p", { class: "small muted" }, "A project groups the models of one game; the list at the top of the menu shows one at a time. Renaming moves its models with it; deleting one moves its models to another project (they are never deleted)."),
    h("table", {}, h("thead", {}, h("tr", {}, ["Project", "Models", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, plist.map((p) => {
        let dest = "";
        const others = plist.filter((x) => x.name !== p.name);
        return h("tr", {},
          h("td", {}, p.name ? h("b", {}, p.name) : h("span", { class: "muted" }, "No project")),
          h("td", { class: "small" }, p.models.length ? p.models.map((id) => (S.catalog.models.find((m) => m.id === id) || { label: id }).label).join(", ") : h("span", { class: "muted" }, "none yet")),
          h("td", { class: "nowrap" }, p.name ? h("div", { class: "row", style: { flexWrap: "nowrap" } },
            h("button", { class: "btn sm ghost", onclick: async () => {
              const name = await askText(`Rename ${p.name}`, { input: "New name", value: p.name, hint: "Its models follow. An existing project's name merges the two.", ok: "Rename" });
              if (!name || name === p.name) return;
              if (await attempt(() => api("/projects/rename", { body: { old: p.name, new: name } }), "Project renamed")) {
                if (S.project === p.name) { S.project = name; store.set("project", name); }
                await loadCatalog(); route();
              }
            } }, "Rename"),
            p.models.length ? h("select", { title: "Where its models go", onchange: (e) => { dest = e.target.value; } },
              others.map((o) => h("option", { value: o.name }, "move models to " + (o.name || "No project")))) : null,
            h("button", { class: "btn sm ghost danger", onclick: async () => {
              if (p.models.length && !dest) dest = (others[0] || { name: "" }).name;
              if (!(await askConfirm(`Delete the project ${p.name}?` + (p.models.length ? ` Its ${p.models.length} model(s) move to ${dest || "No project"}.` : ""), { danger: true, ok: "Delete", title: "Delete the project" }))) return;
              if (await attempt(() => api("/projects/delete", { body: { name: p.name, move_to: dest } }), "Project deleted")) {
                if (S.project === p.name) { S.project = ""; store.set("project", ""); }
                await loadCatalog(); route();
              }
            } }, "Delete")) : null));
      }))));
  main.append(h("div", { class: "proj-head" }, "Projects"), projPanel);
};

async function modelEditor(main, mid, tab) {
  let d = await api("/models/" + mid);
  let m = JSON.parse(JSON.stringify(d.model));
  let dirty = false;
  const tabs = [["sprites", "Sprites"], ["groups", "Groups and kinds"], ["previews", "Preview prompts"], ["settings", "Settings"]];
  const headEl = h("div", {});
  const body = h("div", {});
  main.append(headEl, body);
  const drawHead = () => {
    headEl.innerHTML = "";
    headEl.append(head(m.label, null, h("a", { class: "btn ghost", href: "#/models" }, "All models"),
      h("a", { class: "btn", href: "#/train/" + mid }, "Train it"), h("a", { class: "btn", href: "#/generate?model=" + mid }, "Generate")));
    headEl.append(h("div", { class: "row", style: { marginBottom: "16px" } },
      seg(tabs.map(([id, label]) => ({ id, label })), tab, (t) => { history.replaceState(null, "", `#/models/${mid}/${t}`); tab = t; draw(); }),
      d.saved ? null : h("span", { class: "tag amber" }, "built-in defaults: saved on the first change")));
  };
  const reload = async () => { d = await api("/models/" + mid); m = JSON.parse(JSON.stringify(d.model)); dirty = false; await loadCatalog(); };
  const save = async (msg = "Model saved") => {
    const r = await attempt(() => api("/models/" + mid, { method: "PUT", body: m }), msg);
    if (r) { await reload(); draw(); }
    return r;
  };
  const saveBar = () => h("div", { class: "row", style: { marginTop: "16px" } },
    h("button", { class: "btn primary", onclick: () => save() }, "Save"),
    h("button", { class: "btn ghost", onclick: async () => { await reload(); draw(); } }, "Discard changes"),
    h("span", { class: "hint" }, "Saved changes apply to the next training set you build, and to generation at once."));

  // ---- sprites
  let filter = "all", search = "", selected = new Set();
  const uploadInto = { group: "", kind: "", folders: false };
  const drawSprites = () => {
    const ss = d.sprites;
    const count = (pred) => ss.filter(pred).length;
    const groupLabel = (id) => (m.groups.find((g) => g.id === id) || {}).label || id;
    const kindLabel = (id) => (m.kinds.find((k) => k.id === id) || {}).label || id;

    // adding sprites
    const fileIn = h("input", { type: "file", accept: "image/png,.zip,application/zip", multiple: true, hidden: true, onchange: (e) => upload([...e.target.files]) });
    const intoSel = h("select", { style: { width: "auto" }, onchange: (e) => { uploadInto.group = e.target.value; } },
      h("option", { value: "" }, "By their names"), m.groups.map((g) => h("option", { value: g.id, selected: g.id === uploadInto.group }, g.label)));
    const intoKind = m.kinds.length ? h("select", { style: { width: "auto" }, onchange: (e) => { uploadInto.kind = e.target.value; } },
      h("option", { value: "" }, "By their names"), m.kinds.map((k) => h("option", { value: k.id, selected: k.id === uploadInto.kind }, k.label))) : null;
    const drop = h("div", { class: "dropzone", tabindex: 0, onclick: () => fileIn.click(), onkeydown: (e) => { if (e.key === "Enter") fileIn.click(); } },
      h("strong", {}, "Drop PNG sprites or a zip of them here"), h("span", { class: "muted small" }, " or click to choose them. They are put in groups and kinds by their names (the patterns of Groups and kinds); change it below."));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files].filter((f) => /\.(png|zip)$/i.test(f.name))); });
    const imp = { root: "game", glob: "", prefix: "", group: "" };
    const add = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Add sprites"),
      m.imports.length ? h("button", { class: "btn", onclick: async () => {
        const r = await attempt(() => api(`/models/${mid}/import`, { body: {} }));
        if (r) { toast(r.imported.length ? `${r.imported.length} sprites imported` : "Nothing new to import"); await reload(); draw(); }
      } }, "Import from the game folders") : null),
      fileIn, h("div", { class: "row", style: { marginBottom: "10px" } }, h("span", { class: "label" }, "New sprites go into the group"), intoSel,
        intoKind ? [h("span", { class: "label" }, "kind"), intoKind] : null), drop,
      h("label", { class: "check small", style: { marginTop: "8px" } }, h("input", { type: "checkbox", checked: uploadInto.folders, onchange: (e) => { uploadInto.folders = e.target.checked; } }),
        "Zip files: put each sub-folder's name in front of its sprites' names (icons/map/x.png becomes map_x.png)"),
      h("details", { style: { marginTop: "12px" } }, h("summary", { class: "label", style: { cursor: "pointer" } }, "Import from a source folder of the server"),
        h("div", { class: "inline-fields", style: { marginTop: "10px" } },
          h("div", { class: "field" }, h("label", {}, "Source folder"), h("select", { onchange: (e) => { imp.root = e.target.value; } },
            Object.entries(S.catalog.sources || {}).map(([k, v]) => h("option", { value: k, selected: k === imp.root, title: v }, k)))),
          h("div", { class: "field" }, h("label", {}, "Files"), h("input", { type: "text", placeholder: "ships/*_128.png", oninput: (e) => { imp.glob = e.target.value; } })),
          h("div", { class: "field" }, h("label", {}, "Name prefix"), h("input", { type: "text", placeholder: "none", oninput: (e) => { imp.prefix = e.target.value; } })),
          h("div", { class: "field" }, h("label", {}, "Into group"), h("select", { onchange: (e) => { imp.group = e.target.value; } },
            h("option", { value: "" }, "By name"), m.groups.map((g) => h("option", { value: g.id }, g.label))))),
        h("button", { class: "btn sm", style: { marginTop: "10px" }, onclick: async () => {
          if (!imp.glob) return toast("Give the files to import, e.g. ships/*_128.png", true);
          const r = await attempt(() => api(`/models/${mid}/import`, { body: imp }));
          if (r) { toast(`${r.imported.length} sprites imported`); await reload(); draw(); }
        } }, "Import")));

    // filter and grid
    const shown = ss.filter((s) => (filter === "all" || (filter === "none" && !s.group) || (filter === "excluded" && s.exclude) || s.group === filter)
      && s.name.toLowerCase().includes(search.toLowerCase()));
    const chips = [["all", `All ${ss.length}`], ...m.groups.map((g) => [g.id, `${g.label} ${count((s) => s.group === g.id)}`]),
      ["none", `No group ${count((s) => !s.group)}`], ["excluded", `Left out ${count((s) => s.exclude)}`]];
    const gridEl = h("div", { class: "sprite-grid" }, shown.map((s) => {
      const on = selected.has(s.name);
      return h("div", { class: "scard" + (on ? " on" : "") + (s.trained ? "" : " off"), tabindex: 0, title: s.caption || "not learned: no group, group off, or left out",
        onclick: () => { on ? selected.delete(s.name) : selected.add(s.name); drawSprites(); },
        onkeydown: (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); e.target.click(); } } },
        h("div", { class: "simg " + ltClass() }, h("img", { class: "px", src: fileUrl(s.path), alt: "", loading: "lazy" })),
        h("div", { class: "sname mono" }, s.name.replace(/\.png$/, "")),
        h("div", { class: "sbadges" },
          s.group ? h("span", { class: "tag" + (s.auto.group ? "" : " amber"), title: s.auto.group ? "by name" : "set by hand" }, groupLabel(s.group)) : h("span", { class: "tag err-text" }, "no group"),
          s.kind ? h("span", { class: "tag" + (s.auto.kind ? "" : " amber") }, kindLabel(s.kind)) : null,
          s.exclude ? h("span", { class: "tag err-text" }, "left out") : null),
        s.words ? h("div", { class: "swords" }, s.words) : null);
    }));
    const sel = [...selected];
    const one = sel.length === 1 ? ss.find((s) => s.name === sel[0]) : null;
    const act = (bodyExtra, msg) => attempt(() => api(`/models/${mid}/sprites`, { body: Object.assign({ names: sel }, bodyExtra) }), msg).then(async (r) => { if (r) { await reload(); draw(); } });
    let wordsVal = one ? one.words : "";
    const bar = sel.length ? h("div", { class: "selbar" },
      h("strong", {}, `${sel.length} selected`),
      h("select", { onchange: (e) => act({ group: e.target.value === "__auto" ? null : e.target.value }, "Group set") },
        h("option", { value: "" }, "Group…"), h("option", { value: "__auto" }, "By name"), m.groups.map((g) => h("option", { value: g.id }, g.label))),
      m.kinds.length ? h("select", { onchange: (e) => act({ kind: e.target.value === "__auto" ? null : e.target.value }, "Kind set") },
        h("option", { value: "" }, "Kind…"), h("option", { value: "__auto" }, "By name"), m.kinds.map((k) => h("option", { value: k.id }, k.label))) : null,
      h("input", { type: "text", placeholder: "own words (empty: from the name)", value: wordsVal, style: { width: "220px" }, oninput: (e) => { wordsVal = e.target.value; } }),
      h("button", { class: "btn sm", onclick: () => act({ words: wordsVal.trim() ? wordsVal.trim() : null }, "Words set") }, "Set words"),
      h("button", { class: "btn sm", onclick: () => act({ exclude: true }, "Left out of training") }, "Leave out"),
      h("button", { class: "btn sm", onclick: () => act({ exclude: null }, "Back in training") }, "Put back"),
      h("button", { class: "btn sm danger", onclick: async () => {
        if (!(await askConfirm(`Remove ${sel.length} sprite(s) from the model? (They go to sprites/.trash.)`, { danger: true, ok: "Remove", title: "Remove sprites" }))) return;
        if (await attempt(() => api(`/models/${mid}/sprites`, { method: "DELETE", body: { names: sel } }), "Removed")) { selected.clear(); await reload(); draw(); }
      } }, "Remove"),
      h("button", { class: "btn sm ghost", onclick: () => { selected.clear(); drawSprites(); } }, "Clear")) : null;
    body.innerHTML = "";
    body.append(add, h("div", { class: "panel" },
      h("div", { class: "row between", style: { marginBottom: "12px" } },
        h("div", { class: "chips" }, chips.map(([id, label]) => h("button", { type: "button", class: "chip" + (id === filter ? " on" : ""), onclick: () => { filter = id; drawSprites(); } }, label))),
        h("div", { class: "row" },
          h("input", { type: "text", placeholder: "Filter by name", value: search, style: { width: "180px" }, oninput: (e) => { search = e.target.value; drawSprites(); setTimeout(() => { const i = body.querySelector("input[placeholder='Filter by name']"); if (i) { i.focus(); i.setSelectionRange(search.length, search.length); } }); } }),
          h("button", { class: "btn sm ghost", onclick: () => { shown.forEach((s) => selected.add(s.name)); drawSprites(); } }, "Select all shown"),
          h("button", { class: "btn sm ghost", title: "Selects the sprites whose shading is brighter at the bottom than at the top", onclick: async () => {
            const r = await attempt(() => api(`/models/${mid}/lighting`));
            if (!r) return;
            const below = r.filter((x) => x.top < -0.3).map((x) => x.name);
            selected = new Set(below);
            toast(below.length ? `${below.length} sprites look lit from below: selected` : "No sprite looks lit from below");
            drawSprites();
          } }, "Find sprites lit from below"))),
      bar,
      one ? h("p", { class: "small mono muted", style: { margin: "8px 0" } }, one.caption || "Not learned (no group, its group is off, or left out).") : null,
      ss.length ? gridEl : h("div", { class: "empty" }, "No sprites yet: drop some above", m.imports.length ? ", or import them from the game folders." : ".")));
  };
  const upload = async (files) => {
    if (!files.length) return;
    let saved = 0, skipped = [];
    for (const z of files.filter((f) => /\.zip$/i.test(f.name))) {
      const r = await attempt(() => readFile(z).then((data) => api(`/models/${mid}/upload-zip`, { body: { data, folders: uploadInto.folders, group: uploadInto.group } })));
      if (r) { saved += r.saved.length; skipped = skipped.concat(r.skipped); }
    }
    files = files.filter((f) => !/\.zip$/i.test(f.name));
    for (let i = 0; i < files.length; i += 20) {
      const batch = await Promise.all(files.slice(i, i + 20).map(async (f) => ({ name: f.name, data: await readFile(f) })));
      const r = await attempt(() => api(`/models/${mid}/upload`, { body: { files: batch, group: uploadInto.group, kind: uploadInto.kind } }));
      if (r) { saved += r.saved.length; skipped = skipped.concat(r.skipped); }
    }
    toast(`${saved} sprite${saved === 1 ? "" : "s"} added` + (skipped.length ? `, ${skipped.length} skipped (${skipped.slice(0, 3).join(", ")}${skipped.length > 3 ? "…" : ""})` : ""), !saved);
    await reload();
    draw();
  };

  // ---- groups and kinds
  const cell = (obj, key, opts = {}) => h("input", { type: opts.type || "text", value: obj[key] ?? "", placeholder: opts.ph || "", class: opts.mono ? "mono" : "",
    style: { minWidth: opts.w || "80px" }, oninput: (e) => { obj[key] = opts.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value; dirty = true; } });
  const drawGroups = () => {
    const counts = {};
    d.sprites.forEach((s) => { counts[s.group] = (counts[s.group] || 0) + 1; });
    body.innerHTML = "";
    const gt = h("table", {}, h("thead", {}, h("tr", {}, ["On", "Id", "Label", "Words in every caption", "Keywords", "File name pattern", "Own words from", "Format", "Palette", "Sprites", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, m.groups.map((g, i) => h("tr", {},
        h("td", {}, h("input", { type: "checkbox", checked: g.enabled, title: "Learned, and offered by Generate", onchange: (e) => { g.enabled = e.target.checked; dirty = true; } })),
        h("td", {}, cell(g, "id", { mono: true, w: "110px" })), h("td", {}, cell(g, "label", { w: "120px" })),
        h("td", {}, cell(g, "words", { w: "170px", ph: "red faction" })), h("td", {}, cell(g, "keywords", { w: "170px", ph: "optional" })),
        h("td", {}, cell(g, "pattern", { mono: true, w: "150px", ph: "_red_" })), h("td", {}, cell(g, "name_pattern", { mono: true, w: "150px", ph: "^(.+)$" })),
        h("td", {}, cell(g, "name_format", { w: "80px", ph: "{}" })),
        h("td", {}, h("select", { title: "Put on everything generated for this group (Palettes page)", onchange: (e) => { g.palette = e.target.value; dirty = true; } },
          h("option", { value: "" }, "None"), (S.palettes || []).map((p) => h("option", { value: p.id, selected: p.id === g.palette }, p.label)),
          g.palette && !(S.palettes || []).some((p) => p.id === g.palette) ? h("option", { value: g.palette, selected: true }, g.palette + " (missing)") : null)),
        h("td", { class: "muted small" }, String(counts[g.id] || 0)),
        h("td", {}, iconBtn("x", "Remove this group", () => { m.groups.splice(i, 1); dirty = true; drawGroups(); }))))));
    const kt = h("table", {}, h("thead", {}, h("tr", {}, ["Id", "Label", "Words in every caption", "Keywords", "Words to avoid", "Final size (px)", "Fill (%)", "File name pattern", "Remaster distance", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, m.kinds.map((k, i) => h("tr", {},
        h("td", {}, cell(k, "id", { mono: true, w: "110px" })), h("td", {}, cell(k, "label", { w: "120px" })),
        h("td", {}, cell(k, "words", { w: "170px", ph: "small fighter" })), h("td", {}, cell(k, "keywords", { w: "170px", ph: "optional" })),
        h("td", {}, cell(k, "negative", { w: "150px", ph: "optional" })), h("td", {}, cell(k, "size", { type: "number", w: "70px" })),
        h("td", { title: "New sprites: how much of the canvas the longest side takes, 50 to 100 (0: the model's setting)" }, cell(k, "fill", { type: "number", w: "70px", ph: "0" })),
        h("td", {}, cell(k, "pattern", { mono: true, w: "150px", ph: "^small_" })), h("td", {}, cell(k, "denoise", { type: "number", w: "80px" })),
        h("td", {}, iconBtn("x", "Remove this kind", () => { m.kinds.splice(i, 1); dirty = true; drawGroups(); }))))));
    body.append(
      h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Groups"),
        h("button", { class: "btn sm", onclick: () => { m.groups.push({ id: "group" + (m.groups.length + 1), label: "New group", words: "", keywords: "", pattern: "", name_pattern: "", name_format: "{}", enabled: true }); dirty = true; drawGroups(); } }, "Add a group")),
        h("p", { class: "small muted" }, "The main axis: a faction, a family of items. Its words and keywords go into every caption of its sprites, and into the prompt when you generate for it. A sprite joins the first group whose pattern (a regular expression on the file name) matches, unless you set it by hand in Sprites. \"Own words from\": a pattern whose first (bracket) part becomes the sprite's own words, e.g. the item's name."),
        h("div", { class: "table-wrap" }, gt)),
      h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Kinds"),
        h("button", { class: "btn sm", onclick: () => { m.kinds.push({ id: "kind" + (m.kinds.length + 1), label: "New kind", words: "", keywords: "", negative: "", size: m.size, pattern: "", denoise: 0.7 }); dirty = true; drawGroups(); } }, "Add a kind")),
        h("p", { class: "small muted" }, "Optional second axis: a size class. Its words go into the captions too; its size sets the pixel grid of training and the size of what is generated. A model without kinds uses each sprite's own size."),
        m.kinds.length ? h("div", { class: "table-wrap" }, kt) : h("p", { class: "muted" }, "No kinds.")),
      saveBar());
  };

  // ---- previews
  const drawPreviews = () => {
    body.innerHTML = "";
    const ta = h("textarea", { rows: 8, class: "mono", placeholder: "One prompt per line. Empty: the automatic ones on the left.", oninput: (e) => { m.previews = e.target.value.split("\n"); dirty = true; } }, (m.previews || []).join("\n"));
    body.append(h("div", { class: "grid-2" },
      h("div", { class: "panel" }, h("h2", {}, "Automatic"), h("p", { class: "small muted" }, "One per enabled group (up to 8), each with a kind that group has, and no sprite's own words: new designs, to see whether the LoRA learned the style."),
        d.auto_previews.map((p) => h("p", { class: "small mono" }, p.prompt)),
        h("button", { class: "btn sm", onclick: () => { ta.value = d.auto_previews.map((p) => p.prompt).join("\n"); m.previews = ta.value.split("\n"); dirty = true; } }, "Copy them to edit")),
      h("div", { class: "panel" }, h("h2", {}, "Your own"), h("p", { class: "small muted" }, "Rendered every 2 epochs during training, one row each in the checkpoint grid. Each should start with the trigger word " + m.trigger + ". A line can end with its own words to avoid: \" --n organic, creature\"."), ta, saveBar())));
  };

  // ---- settings
  const drawSettings = () => {
    body.innerHTML = "";
    const field = (label, key, hint, opts = {}) => h("div", { class: "field" }, h("label", {}, label), cell(m, key, opts), hint ? h("div", { class: "hint" }, hint) : null);
    const impTa = h("textarea", { rows: 5, class: "mono", oninput: (e) => { try { m.imports = JSON.parse(e.target.value); impTa.style.borderColor = ""; dirty = true; } catch (_) { impTa.style.borderColor = "var(--err)"; } } },
      JSON.stringify(m.imports, null, 1));
    body.append(h("div", { class: "grid-2" },
      h("div", { class: "panel" }, h("h2", {}, "Model"),
        field("Name", "label"),
        h("div", { class: "field" }, h("label", {}, "Project (game)"),
          projectSelect(m.project || "", (v) => { m.project = v; dirty = true; }),
          h("div", { class: "hint" }, "Projects are managed at the bottom of the Models page (new, rename, delete).")),
        field("Trigger word", "trigger", "The LoRA's word, first in every caption and prompt; also its file name (" + m.trigger + ".safetensors). Changing it means training again.", { mono: true }),
        field("Words at the end of every caption", "tail", "e.g. top-down view, facing up"),
        h("div", { class: "inline-fields", style: { marginTop: "14px" } },
          h("div", { class: "field" }, h("label", {}, "Colour words"), h("select", { onchange: (e) => { m.colour_words = e.target.value; dirty = true; } },
            h("option", { value: "hard", selected: m.colour_words !== "neutral" }, "Hard surface (metal hull, plating)"), h("option", { value: "neutral", selected: m.colour_words === "neutral" }, "Neutral (body, highlights)"))),
          h("div", { class: "field" }, h("label", {}, "Remaster colours from"), h("select", { onchange: (e) => { m.colours_from = e.target.value; dirty = true; } },
            h("option", { value: "group", selected: m.colours_from === "group" }, "The group (one look per group)"), h("option", { value: "self", selected: m.colours_from === "self" }, "The sprite itself")))),
        h("div", { class: "inline-fields", style: { marginTop: "14px" } },
          field("Size without a kind (px)", "size", null, { type: "number" }), field("Repeats", "repeats", "0: automatic (about 300 steps per epoch)", { type: "number" })),
        field("Words to avoid", "negative", "added to the generator's negative prompt and to the training previews, e.g. lit from below, shadows on top"),
        field("Fill for new sprites (%)", "fill", "50 to 100: how much of the canvas a new sprite's longest side takes (100: edge to edge; 90 leaves 3 px each side on 64 px). 0: as the LoRA draws it. A kind's own Fill overrides it; remasters keep the original's size", { type: "number" }),
        field("Relight for training (0 to 100)", "relight", "sprites whose shading is lit from below get their edges relit from the top in the training set (50 to 70 works well); build the training set again and retrain", { type: "number" }),
        field("Batches leave out", "batch_skip", "a pattern of file names, e.g. two-state art: _(armored|vulnerable)$", { mono: true }),
        saveBar()),
      h("div", { class: "stack" },
        h("div", { class: "panel" }, h("h2", {}, "Import rules"),
          h("p", { class: "small muted" }, "What \"Import from the game folders\" copies: root (game or input), glob, optional prefix, group and skip (a pattern)."), impTa),
        d.template ? h("div", { class: "panel" }, h("h2", {}, "Template settings"),
          h("p", { class: "small muted" }, "Puts the groups, kinds, words, previews and other settings back to the ones of the template this model comes from. The sprites and what you set on them by hand stay."),
          h("button", { class: "btn", onclick: async () => {
            if (!(await askConfirm("Replace this model's groups, kinds, words and settings with the template's ones? Its sprites stay.", { title: "Use the template's settings", ok: "Replace" }))) return;
            if (await attempt(() => api(`/models/${mid}/reset`, { body: {} }), "Built-in settings restored")) { await reload(); draw(); }
          } }, "Use the template's settings")) : null,
        h("div", { class: "panel" }, h("h2", {}, "Share"),
          h("p", { class: "small muted" }, "A file with this model's groups, kinds, words and settings (no sprites), to import on another machine or as a template for another game (Models → Import a model file)."),
          h("a", { class: "btn", href: `/api/models/${mid}/export`, download: `${mid}.json` }, "Export the model")),
        h("div", { class: "panel" }, h("h2", {}, "Folder"), h("p", { class: "small mono muted" }, d.dir),
          h("button", { class: "btn danger", onclick: async () => {
            if (!(await askConfirm(`Delete the model ${m.label}? Its folder (sprites, training sets, checkpoints) is moved to .deleted, not erased.`, { danger: true, ok: "Delete", title: "Delete the model" }))) return;
            const r = await attempt(() => api("/models/" + mid, { method: "DELETE" }), "Model deleted");
            if (r) { await loadCatalog(); location.hash = "#/models"; }
          } }, "Delete this model")))));
  };

  const draw = () => {
    drawHead();
    ({ sprites: drawSprites, groups: drawGroups, previews: drawPreviews, settings: drawSettings }[tab] || drawSprites)();
  };
  const warn = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", warn);
  onLeave(() => window.removeEventListener("beforeunload", warn));
  draw();
}

// ---------------------------------------------------------------- Jobs

// ---------------------------------------------------------------- Palettes

routes.palettes = async (main, parts) => {
  await loadCatalog();
  await loadPalettes();
  main.append(head("Palettes", "Colours, and a tuning, put on what is generated: lighten a faction that comes out too dark, keep a game's colours, reuse another game's. Set a group's palette in Models (Groups) or below, or pick one for a single run on Generate and Batch.",
    h("button", { class: "btn", onclick: () => importInput.click() }, "Import a file"),
    h("button", { class: "btn primary", onclick: () => newFromGroup() }, "New palette")));
  const importInput = h("input", { type: "file", accept: ".hex,.gpl,.json,.txt,.png,.gif,.bmp", hidden: true });
  // readFile returns a promise of a data URL: import once it is read.
  importInput.onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const r = await attempt(() => readFile(f).then((data) => api("/palettes/import", { body: { name: f.name, data } })));
    if (r) { toast(`${r.label}: ${r.colours.length} colours imported`); current = r.id; await loadPalettes(); drawList(); drawEditor(); }
  };
  main.append(importInput);
  const layout = h("div", { class: "split" });
  const listPanel = h("div", { class: "panel" });
  const editor = h("div", { class: "stack" });
  layout.append(listPanel, editor);
  main.append(layout);
  let current = parts[0] || (S.palettes[0] && S.palettes[0].id) || "";
  let edit = null;
  let dirty = false;
  const sample = store.get("palSample", { model: "", group: "" });

  const drawList = () => {
    listPanel.innerHTML = "";
    if (!S.palettes.length) {
      listPanel.append(h("div", { class: "empty" }, "No palette yet.", h("br"), "Take one from a group's sprites, import a .hex / .gpl file (Lospec, Aseprite, GIMP), or start empty.",
        h("br"), h("button", { class: "btn sm", style: { marginTop: "10px" }, onclick: () => newEmpty() }, "Start empty")));
      return;
    }
    listPanel.append(h("div", { class: "list" }, S.palettes.map((p) => h("div", { class: "list-item pal-item" + (p.id === current ? " on" : ""), tabindex: 0,
      onclick: async () => { if (dirty && !(await askConfirm("The changes to this palette are not saved.", { title: "Leave without saving?", ok: "Leave" }))) return; current = p.id; dirty = false; history.replaceState(null, "", "#/palettes/" + p.id); drawList(); drawEditor(); } },
    h("div", { style: { minWidth: 0, flex: 1 } }, h("div", { class: "t" }, p.label),
      swatchStrip(p.colours, p.tune),
      h("div", { class: "s" }, `${p.colours.length} colours, ${({ tune: "tuning only", snap: "snap", match: "match light, snap" })[p.mode]}`,
        p.used_by.length ? `, used by ${p.used_by.length} group${p.used_by.length === 1 ? "" : "s"}` : ""))))),
    h("button", { class: "btn sm ghost", style: { marginTop: "10px" }, onclick: () => newEmpty() }, "Start an empty one"));
  };

  const newEmpty = async () => {
    const label = await askText("New empty palette", { input: "Name", value: "Brighter", hint: "Starts as a tuning only (brightness +25); take colours or add them by hand afterwards.", ok: "Create" });
    if (!label) return;
    const id = label.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_|_$/g, "") || "palette";
    if (S.palettes.some((p) => p.id === id)) return toast("A palette has that name already.", true);
    const r = await attempt(() => api("/palettes/" + id, { method: "PUT", body: { label, colours: [], mode: "tune", tune: { brightness: 25 } } }));
    if (r) { current = id; await loadPalettes(); drawList(); drawEditor(); }
  };

  const newFromGroup = async () => {
    const ms = visibleModels();
    let mid = sample.model && ms.some((x) => x.id === sample.model) ? sample.model : ms[0].id;
    let gid = sample.group || "", kid = "", n = 32, how = "auto", batch = "", useIt = true, refMid = "";
    const batches = (await api("/results")).filter((r) => r.type === "batch" && !r.empty);
    batch = (batches[0] || {}).path || "";
    const m = $("#modal");
    const form = h("div", {});
    const HOW = {
      auto: "No colours: a tuning computed so the group's light, contrast and saturation match the model's other groups. The quick fix for a group that comes out darker or duller than the rest.",
      model: "The colours of a model's sprites (a group, a kind), merged down to a number of colours, each weighted by how much of the art it covers.",
      batch: "The colours of a batch's results (all, or one group's), merged the same way: to reuse the colours of a batch you liked.",
    };
    const draw = () => {
      const mm = modelById(mid);
      if (gid && !mm.groups.some((g) => g.id === gid)) gid = "";
      if (how === "auto" && !gid) gid = (mm.groups.find((g) => g.enabled) || mm.groups[0] || {}).id || "";
      form.innerHTML = "";
      form.append(seg([{ id: "auto", label: "Automatic tuning" }, { id: "model", label: "A model's colours" }, { id: "batch", label: "A batch's colours" }], how, (v) => { how = v; draw(); }),
        h("p", { class: "small muted" }, HOW[how]));
      if (how === "batch") {
        form.append(batches.length ? h("div", { class: "field" }, h("label", {}, "Batch"),
          h("select", { onchange: (e) => { batch = e.target.value; } }, batches.map((r) => h("option", { value: r.path, selected: r.path === batch }, `${r.title} (${r.count} sprites)`))))
          : h("div", { class: "note warn" }, "No batch with results yet."));
      }
      form.append(h("div", { class: "field", style: { marginTop: "12px" } }, h("label", {}, "Model"),
        h("select", { onchange: (e) => { mid = e.target.value; gid = ""; kid = ""; draw(); } }, ms.map((x) => h("option", { value: x.id, selected: x.id === mid }, x.label)))));
      form.append(h("div", { class: "inline-fields", style: { marginTop: "12px" } },
        h("div", { class: "field" }, h("label", {}, "Group"), h("select", { onchange: (e) => { gid = e.target.value; } },
          how === "auto" ? null : h("option", { value: "" }, "Every group"), mm.groups.map((g) => h("option", { value: g.id, selected: g.id === gid }, g.label)))),
        how === "model" && mm.kinds.length ? h("div", { class: "field" }, h("label", {}, "Kind"), h("select", { onchange: (e) => { kid = e.target.value; } }, h("option", { value: "" }, "Every kind"), mm.kinds.map((k) => h("option", { value: k.id, selected: k.id === kid }, k.label)))) : null,
        how !== "auto" ? h("div", { class: "field" }, h("label", {}, "Colours at most"), h("input", { type: "number", min: 2, max: 64, value: n, oninput: (e) => { n = e.target.value; } })) : null));
      if (how === "auto") {
        // what the group is compared with: the other groups of a model that has several
        const several = ms.filter((x) => x.groups.length > 1);
        if (!refMid || !ms.some((x) => x.id === refMid)) refMid = mm.groups.length > 1 ? mm.id : (several[0] || mm).id;
        form.append(h("div", { class: "field", style: { marginTop: "12px" } }, h("label", {}, "Make it look like"),
          h("select", { onchange: (e) => { refMid = e.target.value; } }, ms.map((x) => h("option", { value: x.id, selected: x.id === refMid },
            x.groups.length > 1 ? `${x.label}: every group but this one` : `${x.label}: every sprite`))),
          h("div", { class: "hint" }, "A one-group model (a single faction) has no other group: compare it with the model holding every faction.")));
      }
      if (how === "auto") form.append(h("label", { class: "check small", style: { marginTop: "12px" } },
        h("input", { type: "checkbox", checked: useIt, onchange: (e) => { useIt = e.target.checked; } }), "Make it the group's palette (everything generated for the group gets it)"));
    };
    draw();
    const go = async () => {
      let r;
      if (how === "auto") {
        const mm = modelById(mid);
        const g = mm.groups.find((x) => x.id === gid);
        const t = await attempt(() => api("/palettes/autotune", { body: { sprites: { model: mid, group: gid }, reference: modelById(refMid).groups.length > 1 ? { model: refMid, group: gid, others: true } : { model: refMid } } }));
        if (!t) return;
        let id = (g.id + "_auto").replace(/[^a-z0-9_]/g, "_"), k = 2;
        while (S.palettes.some((p) => p.id === id)) id = `${g.id}_auto_${k++}`;
        r = await attempt(() => api("/palettes/" + id, { method: "PUT", body: { label: `${g.label}: automatic`, colours: [], mode: "tune", tune: t.tune,
          source: `tuning computed: ${t.what} (light ${t.before.value.toFixed(2)} to ${t.after.value.toFixed(2)}, target ${t.target.value.toFixed(2)})` } }));
        if (r && useIt) {
          const d = await api("/models/" + mid);
          d.model.groups.forEach((x) => { if (x.id === gid) x.palette = id; });
          await attempt(() => api("/models/" + mid, { method: "PUT", body: { groups: d.model.groups } }));
          await loadCatalog();
        }
      } else {
        const body = how === "batch" ? { batch, group: gid, n: Number(n) } : { model: mid, group: gid, kind: kid, n: Number(n) };
        r = await attempt(() => api("/palettes/extract", { body }));
      }
      if (!r) return;
      closeModal();
      toast(how === "auto" ? `${r.label}: brightness ${r.tune.brightness}, contrast ${r.tune.contrast}, saturation ${r.tune.saturation}` : `${r.label}: ${r.colours.length} colours`);
      current = r.id; sample.model = mid; sample.group = gid; store.set("palSample", sample);
      await loadPalettes(); drawList(); drawEditor();
    };
    m.innerHTML = "";
    m.append(h("div", { class: "modal-box", style: { maxWidth: "620px" } }, h("h2", {}, "New palette"), form,
      h("div", { class: "row", style: { marginTop: "16px" } },
        h("button", { class: "btn primary", onclick: (e) => { e.target.disabled = true; e.target.textContent = "Working…"; go().finally(() => { e.target.disabled = false; e.target.textContent = "Make the palette"; }); } }, "Make the palette"),
        h("button", { class: "btn ghost", onclick: closeModal }, "Cancel"))));
    m.hidden = false;
  };

  const drawEditor = async () => {
    editor.innerHTML = "";
    const p = S.palettes.find((x) => x.id === current);
    if (!p) { edit = null; return; }
    if (!edit || edit.id !== p.id || !dirty) edit = JSON.parse(JSON.stringify(p));
    const save = async () => {
      const r = await attempt(() => api("/palettes/" + edit.id, { method: "PUT", body: edit }), "Palette saved");
      if (r) { dirty = false; await loadPalettes(); drawList(); drawEditor(); }
    };
    const changed = () => { dirty = true; saveBtn.disabled = false; drawSwatches(); drawPreview(); };
    const saveBtn = h("button", { class: "btn primary", disabled: !dirty, onclick: save }, "Save");

    // colours
    const sw = h("div", {});
    const drawSwatches = () => {
      sw.innerHTML = "";
      const tuned = tuneColours(edit.colours, edit.tune);
      sw.append(h("div", { class: "swatch-edit" }, edit.colours.map((c, i) => {
        const pick = h("input", { type: "color", value: c, oninput: (e) => { edit.colours[i] = e.target.value; changed(); } });
        return h("div", { class: "swatch", title: `${c}${edit.weights[i] ? `, ${edit.weights[i]} px` : ""}${tuneOn(edit.tune) ? ` → ${rgbHex(tuned[i])} tuned` : ""}` },
          h("label", { style: { background: c } }, pick),
          h("span", { class: "tuned", style: { background: rgbHex(tuned[i]) } }),
          h("button", { class: "x", type: "button", title: "Remove", onclick: () => { edit.colours.splice(i, 1); if (edit.weights.length) edit.weights.splice(i, 1); changed(); } }, "×"));
      }), h("label", { class: "swatch add", title: "Add a colour" }, "+", h("input", { type: "color", value: "#808080", onchange: (e) => {
        edit.colours.push(e.target.value); if (edit.weights.length) edit.weights.push(1); changed();
      } }))));
      if (edit.colours.length) sw.append(h("p", { class: "small muted" }, "Top: the colour, click to change it. Bottom strip: after the tuning. Sorted by how they were made; " + (edit.weights.length ? "hover shows how much of the art each covered." : "")));
    };

    // tuning
    const slider = (key, label, min, max, hint) => {
      const out = h("span", { class: "mono small", style: { minWidth: "44px", textAlign: "right" } }, String(edit.tune[key] || 0));
      return h("div", { class: "field tune" }, h("label", { title: hint }, label, hint ? h("span", { class: "muted small" }, " · " + hint) : null),
        h("div", { class: "row", style: { flexWrap: "nowrap" } },
          h("input", { type: "range", min, max, step: 1, value: edit.tune[key] || 0, style: { flex: 1 },
            oninput: (e) => { edit.tune[key] = Number(e.target.value); out.textContent = e.target.value; changed(); },
            ondblclick: (e) => { edit.tune[key] = 0; e.target.value = 0; out.textContent = "0"; changed(); } }), out));
    };

    // preview
    const prev = h("div", {});
    const drawPreview = async () => {
      prev.innerHTML = "";
      const ms = visibleModels();
      if (!sample.model || !ms.some((x) => x.id === sample.model)) sample.model = ms[0].id;
      const mm = modelById(sample.model);
      const d = await api("/models/" + mm.id);
      const ss = d.sprites.filter((x) => !sample.group || x.group === sample.group).slice(0, 8);
      prev.innerHTML = "";
      prev.append(h("div", { class: "row", style: { marginBottom: "10px" } },
        h("select", { onchange: (e) => { sample.model = e.target.value; sample.group = ""; store.set("palSample", sample); drawPreview(); } }, ms.map((x) => h("option", { value: x.id, selected: x.id === mm.id }, x.label))),
        h("select", { onchange: (e) => { sample.group = e.target.value; store.set("palSample", sample); drawPreview(); } }, h("option", { value: "" }, "Every group"),
          mm.groups.map((g) => h("option", { value: g.id, selected: g.id === sample.group }, g.label))),
        h("span", { class: "muted small" }, "Top: the sprite now. Bottom: with this palette, as generation will make it.")));
      if (!ss.length) { prev.append(h("p", { class: "muted small" }, "No sprite here.")); return; }
      prev.append(h("div", { class: "pal-preview " + ltClass() }, ss.map((x) => {
        const z = Math.max(1, Math.min(4, Math.floor(160 / Math.max(x.w || 64, x.h || 64))));
        return h("div", { class: "pal-pair", title: x.name }, palettePreview(x.path, null, z), palettePreview(x.path, edit, z));
      })));
    };

    // where it is used
    const uses = h("div", {});
    const drawUses = () => {
      uses.innerHTML = "";
      const all = visibleModels().flatMap((m) => m.groups.map((g) => ({ m, g })));
      let pick = "";
      uses.append(h("p", { class: "small" }, p.used_by.length ? ["Used by: ", h("b", {}, p.used_by.join(", ")), ". Everything generated for these groups gets it, unless Generate or Batch picks another."]
        : "No group uses it yet: it is applied only when you pick it on Generate or Batch."),
      h("div", { class: "row" },
        h("select", { onchange: (e) => { pick = e.target.value; } }, h("option", { value: "" }, "Use it for the group…"),
          all.map(({ m, g }) => h("option", { value: m.id + "/" + g.id }, `${m.label}: ${g.label}` + (g.palette && g.palette !== p.id ? ` (now ${g.palette})` : g.palette === p.id ? " (already)" : "")))),
        h("button", { class: "btn sm", onclick: async () => {
          if (!pick) return;
          const [mid, gid] = pick.split("/");
          const d = await api("/models/" + mid);
          d.model.groups.forEach((g) => { if (g.id === gid) g.palette = p.id; });
          if (await attempt(() => api("/models/" + mid, { method: "PUT", body: { groups: d.model.groups } }), "Group updated")) {
            await loadCatalog(); await loadPalettes(); drawList(); drawEditor();
          }
        } }, "Set")));
    };

    // automatic tuning: measure some sprites, match the light, contrast and saturation of others
    const autoRow = h("div", { class: "auto-tune" });
    const colRow = h("div", { class: "auto-tune" });
    const drawAuto = async () => {
      // Every model of the project, so any group (another model's too) can be measured or matched.
      const ms = visibleModels();
      const mm = modelById(sample.model && ms.some((x) => x.id === sample.model) ? sample.model : ms[0].id);
      const batches = (await api("/results")).filter((r) => r.type === "batch" && !r.empty);
      const gs = mm.groups;
      const g0 = sample.group || (gs[0] || {}).id;
      const allGroups = [...new Map(ms.flatMap((m) => m.groups).map((g) => [g.id, g])).values()];
      const opt = (spec, label, sel) => h("option", { value: JSON.stringify(spec), selected: !!sel }, label);
      const batchOpts = (withGroups) => batches.length ? h("optgroup", { label: "Batch results" }, batches.flatMap((r) => [opt({ batch: r.path }, `${r.title}: every result`)].concat(
        withGroups ? allGroups.map((g) => opt({ batch: r.path, group: g.id }, `${r.title}: ${g.label} results`)) : []))) : null;
      const groupOpts = (label, sel) => ms.map((m) => h("optgroup", { label: m.label },
        m.groups.map((g) => opt({ model: m.id, group: g.id }, label(m, g), sel && m.id === mm.id && g.id === g0))));
      const srcSel = h("select", {}, groupOpts((m, g) => `${g.label}: its sprites`, true), batchOpts(true));
      // default reference: the other groups of a model that has several (the measured group's own model, or the first such)
      const refModel = mm.groups.length > 1 ? mm : ms.find((m) => m.groups.length > 1) || mm;
      const refSel = h("select", {},
        h("optgroup", { label: "Several groups" }, ms.flatMap((m) => [
          m.groups.length > 1 ? opt({ model: m.id, others: true, auto: "others" }, `${m.label}: every group but the measured one`, m.id === refModel.id) : null,
          opt({ model: m.id }, `${m.label}: every sprite`, m.id === refModel.id && m.groups.length < 2)])),
        groupOpts((m, g) => `${m.label}: ${g.label}`, false), batchOpts(false));
      const out = h("span", { class: "muted small" });
      const btn = h("button", { class: "btn sm", onclick: async () => {
        const src = JSON.parse(srcSel.value);
        const ref = JSON.parse(refSel.value);
        if (ref.auto === "others") ref.group = src.group || g0;  // "every group but the measured one", by group id
        delete ref.auto;
        btn.disabled = true; btn.textContent = "Measuring…";
        const r = await attempt(() => api("/palettes/autotune", { body: { sprites: src, reference: ref, hue: edit.tune.hue || 0 } }));
        btn.disabled = false; btn.textContent = "Auto-tune";
        if (!r) return;
        edit.tune = r.tune;
        if (edit.mode !== "tune") { edit.mode = "tune"; toast("Switched to Tuning only: the automatic tuning is measured on the sprites' own colours."); }
        dirty = true;
        drawEditor();
        toast(`Light ${r.before.value.toFixed(2)} → ${r.after.value.toFixed(2)} (target ${r.target.value.toFixed(2)}), saturation ${r.before.saturation.toFixed(2)} → ${r.after.saturation.toFixed(2)}. Save to keep it.`);
      } }, "Auto-tune");
      // take colours: the same sources
      const colSel = h("select", {},
        h("optgroup", { label: "Whole models" }, ms.map((m) => opt({ model: m.id }, `${m.label}: every sprite`))),
        groupOpts((m, g) => `${g.label}: its sprites`, true), batchOpts(true));
      const nIn = h("input", { type: "number", min: 2, max: 64, value: 32, style: { width: "70px" } });
      const colBtn = h("button", { class: "btn sm", onclick: async () => {
        colBtn.disabled = true;
        const r = await attempt(() => api("/palettes/colours", { body: { sprites: JSON.parse(colSel.value), n: Number(nIn.value) } }));
        colBtn.disabled = false;
        if (!r) return;
        edit.colours = r.colours; edit.weights = r.weights; edit.source = r.what;
        if (edit.mode === "tune") edit.mode = "match";
        dirty = true;
        drawEditor();
        toast(`${r.colours.length} colours from ${r.what}. Save to keep them.`);
      } }, edit.colours.length ? "Replace the colours" : "Use these colours");
      colRow.innerHTML = "";
      colRow.append(h("span", { class: "label" }, "Take colours from"), colSel, h("span", { class: "small" }, "at most"), nIn, colBtn,
        h("div", { class: "hint", style: { width: "100%" } }, "For the same colours on every sprite: take them from the sprites whose look you want (the group's, a batch you liked), then use Match light, then snap."));
      autoRow.innerHTML = "";
      autoRow.append(h("span", { class: "label" }, "Automatic"), h("span", { class: "small" }, "Tune"), srcSel, h("span", { class: "small" }, "to look like"), refSel, btn, out,
        h("div", { class: "hint", style: { width: "100%" } }, "Sets the sliders so the first sprites get the mean light, contrast and saturation of the second. Measuring a batch's results tunes what the LoRA actually draws."));
    };
    drawAuto();

    const modeSeg = seg([{ id: "tune", label: "Tuning only" }, { id: "snap", label: "Snap to the colours" }, { id: "match", label: "Match light, then snap" }],
      edit.mode, (v) => { edit.mode = v; dirty = true; drawEditor(); });
    const MODE_HINT = {
      tune: "The render keeps its own colours, only adjusted by the tuning: the simplest fix for a group that comes out too dark or dull.",
      snap: "Every pixel takes the nearest colour of the palette (after tuning): the sprite uses exactly these colours.",
      match: "The render's light and shade are first spread like the palette's (a dark render gets the palette's brightness), then snapped: for renders much darker or lighter than the palette.",
    };
    const modeHint = h("p", { class: "small muted" }, MODE_HINT[edit.mode]);
    const noColours = edit.mode !== "tune" && !edit.colours.length;
    const noOp = edit.mode === "tune" && !changesSomething(edit.tune);
    const warn = noColours ? h("div", { class: "note bad", style: { margin: "8px 0" } }, "This palette has no colours, so Snap and Match have nothing to snap to: the sprites would come out unchanged. Take colours below (Take colours from…), or choose Tuning only.")
      : noOp ? h("div", { class: "note warn", style: { margin: "8px 0" } }, "Every slider is at 0 and there are no colours: this palette changes nothing yet. Move the sliders, use Auto-tune, or take colours.") : null;

    editor.append(
      h("div", { class: "panel" },
        h("div", { class: "panel-head" }, h("div", {},
          h("input", { type: "text", value: edit.label, style: { fontSize: "18px", fontWeight: 600, minWidth: "260px" }, oninput: (e) => { edit.label = e.target.value; dirty = true; saveBtn.disabled = false; } }),
          h("div", { class: "small muted", style: { marginTop: "4px" } }, h("span", { class: "mono" }, edit.id), edit.source ? " · " + edit.source : "")),
        h("div", { class: "row" }, saveBtn,
          h("button", { class: "btn ghost danger", onclick: async () => {
            if (!(await askConfirm(`Delete the palette ${p.label}?` + (p.used_by.length ? ` The groups using it (${p.used_by.join(", ")}) go back to no palette.` : ""), { danger: true, ok: "Delete", title: "Delete the palette" }))) return;
            if (await attempt(() => api("/palettes/" + p.id, { method: "DELETE" }), "Palette deleted")) { current = ""; dirty = false; await loadPalettes(); await loadCatalog(); current = (S.palettes[0] || {}).id || ""; drawList(); drawEditor(); }
          } }, "Delete"))),
        h("div", { class: "label" }, "How it is applied"), modeSeg, modeHint, warn,
        h("div", { class: "label", style: { marginTop: "14px" } }, "Tuning ", h("span", { class: "muted small" }, "(double-click a slider to reset it)")),
        h("div", { class: "tune-grid" },
          slider("brightness", "Brightness", -100, 100, "lifts the darks"),
          slider("contrast", "Contrast", -100, 100, ""),
          slider("saturation", "Saturation", -100, 100, ""),
          slider("hue", "Hue", -180, 180, "degrees"),
          slider("light", "Light from the top", -100, 100, "for sprites lit from below")),
        autoRow,
        h("div", { class: "label", style: { marginTop: "14px" } }, `Colours (${edit.colours.length})`), sw, colRow,
        h("label", { class: "check small", style: { marginTop: "10px" } }, h("input", { type: "checkbox", checked: edit.words, onchange: (e) => { edit.words = e.target.checked; dirty = true; saveBtn.disabled = false; } }),
          "Also name these colours in the prompt (instead of the colours read from the group's sprites)"),
        h("div", { class: "row", style: { marginTop: "14px" } }, h("span", { class: "label" }, "Export"),
          ["hex", "gpl", "png", "json"].map((f) => h("a", { class: "btn sm", href: `/api/palettes/${p.id}/export?format=${f}`, download: `${p.id}.${f}`,
            title: { hex: "One colour per line (Lospec, Aseprite)", gpl: "GIMP, Krita, Inkscape palette", png: "A strip of swatches", json: "Everything, tuning included, to import elsewhere" }[f] }, "." + f)),
          dirty ? h("span", { class: "muted small" }, "save first: exports are made from the saved palette") : null)),
      h("div", { class: "panel" }, h("h2", {}, "Preview"), prev),
      h("div", { class: "panel" }, h("h2", {}, "Groups"), uses));
    drawSwatches();
    drawPreview();
    drawUses();
  };

  onLeave(() => { if (dirty) toast("Palette changes not saved", true); });
  drawList();
  drawEditor();
};

routes.jobs = async (main, parts) => {
  if (parts[0]) return jobDetail(main, parts[0]);
  main.append(head("Jobs", "Everything the app ran. GPU jobs run one at a time, in order; training sets are built at once.",
    h("button", { class: "btn ghost", onclick: async () => { if (await attempt(() => api("/jobs", { method: "DELETE" }), "Finished jobs cleared")) route(); } }, "Clear finished")));
  const box = h("div", { class: "panel" });
  main.append(box);
  let timer;
  onLeave(() => clearTimeout(timer));
  const draw = async () => {
    const jobs = await api("/jobs");
    box.innerHTML = "";
    if (!jobs.length) { box.append(h("div", { class: "empty" }, "No jobs yet.")); return; }
    box.append(h("table", {}, h("thead", {}, h("tr", {}, ["Job", "Status", "Progress", "Started", "Took", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, jobs.map((j) => h("tr", { class: "clickable", onclick: () => { location.hash = "#/jobs/" + j.id; } },
        h("td", {}, j.title, h("div", { class: "faint small" }, j.kind)),
        h("td", {}, statusLabel(j)),
        h("td", { style: { width: "220px" } }, j.status === "running" ? [progressBar(j), h("div", { class: "small muted" }, (j.progress && j.progress.text) || "")] : null),
        h("td", { class: "small muted nowrap" }, fmtTime(j.started || j.created)),
        h("td", { class: "small muted nowrap" }, j.started ? fmtDur((j.ended || Date.now() / 1000) - j.started) : ""),
        h("td", { class: "nowrap" }, ["running", "queued"].includes(j.status)
          ? h("button", { class: "btn sm danger", onclick: (e) => { e.stopPropagation(); attempt(() => api(`/jobs/${j.id}/cancel`, { body: {} }), "Cancelled").then(draw); } }, "Cancel")
          : ["failed", "cancelled", "interrupted"].includes(j.status)
            ? h("button", { class: "btn sm", onclick: (e) => { e.stopPropagation(); attempt(() => api(`/jobs/${j.id}/retry`, { body: {} }), "Queued again").then(draw); } }, "Run again") : null))))));
    if (jobs.some((j) => ["running", "queued"].includes(j.status))) timer = setTimeout(draw, 3000);
  };
  draw();
};

async function jobDetail(main, id) {
  const j = await api("/jobs/" + id);
  main.append(head(j.title, null, h("a", { class: "btn ghost", href: "#/jobs" }, "All jobs")));
  const resBox = h("div", {});
  const panel = jobPanel(id, async (done) => {
    if (done.kind !== "generate") return;
    const r = await api("/jobs/" + id).catch(() => null);
    if (!r || !r.result) return;
    const res = r.result;
    const drawR = () => {
      resBox.innerHTML = "";
      resBox.append(h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Result"),
        h("a", { class: "btn sm", href: "#/results?path=" + encodeURIComponent(res.path) }, "Open in Results")),
        h("div", { class: "sprite-row" },
          res.orig ? tile({ path: res.orig, name: "before", w: res.orig_size && res.orig_size[0], h: res.orig_size && res.orig_size[1] }, { orig: true, refresh: drawR }) : null,
          res.variants.map((v) => tile(v, { pickAs: res.target, refresh: drawR, touchup: (x) => touchUp(x, res) })))));
    };
    await loadPicks();
    drawR();
  }, { detail: true });
  onLeave(() => panel.stop());
  main.append(panel, resBox);
  const logBox = h("pre", { class: "log", style: { maxHeight: "60vh" } });
  main.append(h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Log"), h("span", { class: "faint small mono" }, j.log)),
    h("p", { class: "small muted mono" }, j.cmd.join(" ")),
    j.out ? h("p", { class: "small" }, "Output: ", h("a", { href: "#/results?path=" + encodeURIComponent(j.out) }, j.out)) : null, logBox));
  let timer;
  onLeave(() => clearTimeout(timer));
  const pull = async () => {
    const atEnd = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 30;
    logBox.textContent = await api(`/jobs/${id}/log`).catch(() => "");
    if (atEnd) logBox.scrollTop = logBox.scrollHeight;
    const cur = await api("/jobs/" + id).catch(() => null);
    if (cur && ["running", "queued"].includes(cur.status)) timer = setTimeout(pull, 2500);
  };
  pull();
}

// ---------------------------------------------------------------- Settings

routes.settings = async (main) => {
  const d = await api("/settings");
  const s = Object.assign({}, d.settings);
  main.append(head("Settings", "Where the sprites, results and models are. Empty a field to go back to its default."));
  const labels = {
    px_input: ["Source folder \"input\"", "a folder of sprites that models import from (import rules, Import from a folder)"],
    px_game: ["Source folder \"game\"", "a second source, e.g. a copy of a game's whole sprite folder"],
    sources: ["More source folders", "one per line, name=path, e.g. tiles=/srv/sprites/other_game/tiles"],
    px_output: ["Results", "where every run writes its sprites"],
    comfy_url: ["ComfyUI address", "the API the generator talks to"],
    comfy_dir: ["ComfyUI folder", "its models/loras holds the installed LoRAs"],
    lora_home: ["Training folder", "training sets, checkpoints and previews, one folder per category"],
    comfy_service: ["ComfyUI service", "the systemd unit the app stops and starts"],
    train_stops_comfy: ["Stop ComfyUI while training", "auto (only on cards with less than 15 GB), always or never"],
  };
  const form = h("form", { class: "panel", onsubmit: async (e) => {
    e.preventDefault();
    if (await attempt(() => api("/settings", { method: "PUT", body: s }), "Settings saved")) { await loadCatalog(); route(); }
  } });
  for (const [k, [label, hint]] of Object.entries(labels)) {
    const check = d.checks[k];
    form.append(h("div", { class: "field" }, h("label", { for: "s-" + k }, label, check && check !== "ok" ? h("span", { class: "err-text" }, `  (${check})`) : null),
      k === "sources" ? h("textarea", { id: "s-" + k, rows: 3, class: "mono", oninput: (e) => { s[k] = e.target.value; } }, s[k] || "")
        : h("input", { id: "s-" + k, type: "text", value: s[k], placeholder: d.defaults[k], oninput: (e) => { s[k] = e.target.value; } }),
      h("div", { class: "hint" }, hint)));
  }
  form.append(h("div", { style: { marginTop: "18px" } }, h("button", { class: "btn primary", type: "submit" }, "Save settings")));
  const missing = Object.entries(d.scripts_found).filter(([, ok]) => !ok).map(([n]) => n);
  main.append(h("div", { class: "grid-2" }, form, h("div", { class: "stack" },
    h("div", { class: "panel" }, h("h2", {}, "Checks"),
      h("table", {}, h("tbody", {},
        h("tr", {}, h("td", {}, "Scripts"), h("td", { class: missing.length ? "err-text" : "ok-text" }, missing.length ? "missing: " + missing.join(", ") : "found in " + d.scripts)),
        h("tr", {}, h("td", {}, "ComfyUI control"), h("td", { class: d.can_control_comfy ? "ok-text" : "err-text" }, d.can_control_comfy ? "the app can stop and start it" : "not allowed: add the sudo rule from run.sh")),
        Object.entries(d.checks).map(([k, v]) => h("tr", {}, h("td", {}, labels[k][0]), h("td", { class: v === "ok" ? "ok-text" : "err-text" }, v === "ok" ? "found" : v)))))),
    h("div", { class: "panel" }, h("h2", {}, "Picked sprites"),
      h("p", { class: "muted small" }, "Export picks (Results) copies every starred variant to the results folder's picked/, named after the sprite it replaces, ready to copy into the game."),
      h("button", { class: "btn", onclick: exportPicks }, "Export picks")))));
};

// ---------------------------------------------------------------- start

setTray(S.tray);
pollStatus();
route();
