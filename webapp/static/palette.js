// palette.js: the palette arithmetic of Sprite Studio, shared by the page
// (palette previews) and the tests (tests/test_palette_js.py runs it in
// Node and compares it with pxtool.py). No DOM here.
//
// The same arithmetic as pxtool.py (tune_rgb, relight, apply_palette), so the
// preview shows what generation will make. Keep the two in step.

const TUNE_KEYS = ["hue", "saturation", "brightness", "contrast"];
const hexRgb = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
const rgbHex = (c) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");

function tunePixel(r, g, b, t) {
  let x = [r / 255, g / 255, b / 255];
  const mx = Math.max(...x), mn = Math.min(...x), d = mx - mn;
  let v = mx, s = mx > 0 ? d / mx : 0, hh = 0;
  if (d > 0) {
    if (mx === x[0]) hh = (((x[1] - x[2]) / d) % 6 + 6) % 6;
    else if (mx === x[1]) hh = (x[2] - x[0]) / d + 2;
    else hh = (x[0] - x[1]) / d + 4;
    hh /= 6;
  }
  hh = (hh + (t.hue || 0) / 360) % 1;
  if (hh < 0) hh += 1; // as Python's %
  s = Math.min(1, Math.max(0, s * (1 + (t.saturation || 0) / 100)));
  v = Math.pow(v, Math.pow(2, -(t.brightness || 0) / 50));
  v = Math.min(1, Math.max(0, (v - 0.5) * (1 + (t.contrast || 0) / 100) + 0.5));
  const i = Math.floor(hh * 6) % 6, f = hh * 6 - Math.floor(hh * 6);
  const p = v * (1 - s), q = v * (1 - s * f), u = v * (1 - s * (1 - f));
  x = [[v, u, p], [q, v, p], [p, v, u], [p, q, v], [u, p, v], [v, p, q]][i];
  return x.map((c) => Math.min(255, Math.max(0, Math.round(c * 255))));
}
const tuneOn = (t) => !!t && TUNE_KEYS.some((k) => Number(t[k] || 0) !== 0);
const changesSomething = (t) => tuneOn(t) || Number((t || {}).light || 0) !== 0;
const tuneColours = (cols, t) => cols.map((c) => (tuneOn(t) ? tunePixel(...hexRgb(c), t) : hexRgb(c)));
const lum = (c) => c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114;
function snapTo(c, pal) {
  let best = 0, bd = Infinity;
  for (let i = 0; i < pal.length; i++) {
    const p = pal[i];
    const d = 3 * (c[0] - p[0]) ** 2 + 4 * (c[1] - p[1]) ** 2 + 2 * (c[2] - p[2]) ** 2;
    if (d < bd) { bd = d; best = i; }
  }
  return pal[best];
}

/** pxtool.relight: top edges of the silhouette brightened, bottom edges darkened (strength > 0: light from the top). */
function relight(data, w, strength) {
  const s = Number(strength || 0) / 100;
  if (!s || !w) return;
  const H = data.length / 4 / w;
  const op = (x, y) => y >= 0 && y < H && data[(y * w + x) * 4 + 3] > 127;
  let top = -1, bot = -1;
  for (let y = 0; y < H; y++) for (let x = 0; x < w; x++) if (op(x, y)) { if (top < 0) top = y; bot = y; }
  if (top < 0) return;
  const r = Math.max(2, Math.floor((bot - top + 1) / 12 + 0.5));
  const f = new Float64Array(w * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < w; x++) {
    if (!op(x, y)) continue;
    let up = r, down = r;
    for (let k = 1; k <= r; k++) if (!op(x, y - k)) { up = k; break; }
    for (let k = 1; k <= r; k++) if (!op(x, y + k)) { down = k; break; }
    f[y * w + x] = 1 + s * (down - up) / (r - 1);
  }
  for (let i = 0; i < w * H; i++) {
    if (!f[i]) continue;
    for (let c = 0; c < 3; c++) data[i * 4 + c] = Math.min(255, Math.max(0, Math.floor(data[i * 4 + c] * f[i] + 0.5)));
  }
}

/** The palette on RGBA pixel data (an ImageData's data, w pixels wide), in place. */
function applyPalette(data, pal, w) {
  relight(data, w, (pal.tune || {}).light);
  const idx = [];
  for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 127) idx.push(i);
  if (!idx.length) return;
  const t = pal.tune || {};
  const mode = pal.mode || "snap";
  if (mode === "tune" || !(pal.colours || []).length) {
    if (!tuneOn(t)) return;
    for (const i of idx) { const c = tunePixel(data[i], data[i + 1], data[i + 2], t); data[i] = c[0]; data[i + 1] = c[1]; data[i + 2] = c[2]; }
    return;
  }
  const cols = tuneColours(pal.colours, t);
  let px = idx.map((i) => [data[i], data[i + 1], data[i + 2]]);
  if (mode === "match") {
    let w = (pal.weights || []).length === cols.length ? pal.weights.map(Number) : cols.map(() => 1);
    if (!(w.reduce((a, b) => a + b, 0) > 0)) w = cols.map(() => 1);
    const order = cols.map((c, i) => i).sort((a, b) => lum(cols[a]) - lum(cols[b]));
    const total = w.reduce((a, b) => a + b, 0);
    let acc = 0;
    const cum = order.map((i) => (acc += w[i]) / total);
    const L = px.map(lum);
    const rankOrder = L.map((v, i) => i).sort((a, b) => L[a] - L[b]);
    const rank = new Array(L.length);
    rankOrder.forEach((pi, r) => { rank[pi] = (r + 0.5) / L.length; });
    px = px.map((c, k) => {
      let j = cum.findIndex((q) => q >= rank[k]);
      if (j < 0) j = order.length - 1;
      const target = lum(cols[order[j]]);
      if (L[k] < 1) return [target, target, target].map(Math.round);
      return c.map((v) => Math.min(255, Math.max(0, Math.round(v * target / L[k]))));
    });
  }
  idx.forEach((i, k) => { const c = snapTo(px[k], cols); data[i] = c[0]; data[i + 1] = c[1]; data[i + 2] = c[2]; });
}

if (typeof module !== "undefined") {
  module.exports = { TUNE_KEYS, hexRgb, rgbHex, tunePixel, tuneOn, changesSomething, tuneColours, lum, snapTo, relight, applyPalette };
}
