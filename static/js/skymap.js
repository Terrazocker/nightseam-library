/* Static sky map: whole-sky RA/Dec chart of the ingested image library.
   Unlike the planner's Sky View this chart never moves - objects sit at
   their catalog/header coordinates and dots scale with capture-session count. */

import { $, clamp, esc, TYPE_COLOR, TYPE_LABEL } from "./util.js";
import { S } from "./state.js";
import { CONSTELLATIONS, FIELD_STARS } from "./skydata.js";
import { vizTheme } from "./viztheme.js";
import { catalogIcon } from "./catalog_icons.js";

let SM_POINTS = [];
let SM_HOVER = null;
let SM_SELECTED = null;
let SM_KEYBOARD_FOCUS = null;
let SM_VIEW = { width: 0, height: 0, scale: 1 };
let SM_DRAW_FRAME = null;

const MAP_TYPE_ORDER = [
  "galaxy", "nebula", "open_cluster", "globular", "planetary",
  "supernova_remnant",
];
const MAP_TYPE_COLORS = Object.freeze({
  galaxy: "#DF9398",
  nebula: "#87BEFC",
  open_cluster: "#FBD46F",
  globular: "#A0E992",
  planetary: "#AA8CFA",
  supernova_remnant: "#FBA457",
});

/* RA hours 0..24 -> x, Dec -90..+90 -> y (0h left, north up) */
function xy(raH, decD, W, H) {
  return [raH / 24 * W, (1 - (decD + 90) / 180) * H];
}

/** DE-12: same rule as the Sky View legend - the chips are built once, so a
 *  toggle made in the other chart must be reflected here on every render. */
function syncTypeLegend() {
  const lg = $("#smLegend");
  if (!lg) return;
  const types = [...MAP_TYPE_ORDER];
  const available = new Set((S.library?.objects || []).map((object) => object.otype).filter(Boolean));
  for (const extra of ["asterism", "star_cloud"]) {
    if (available.has(extra)) types.push(extra);
  }
  const current = [...lg.querySelectorAll("[data-type]")].map((chip) => chip.dataset.type);
  if (types.length !== current.length || types.some((type, index) => type !== current[index])) {
    const initialTheme = vizTheme();
    lg.innerHTML = types.map((type) => {
      const color = MAP_TYPE_COLORS[type] || initialTheme.types[type] || TYPE_COLOR[type];
      return `<button class="lgchip${S.disabledTypes.has(type) ? " off" : ""}" type="button" data-type="${type}" aria-pressed="${!S.disabledTypes.has(type)}" style="--c:${color}"><i aria-hidden="true"></i><span>${TYPE_LABEL[type] || type}</span></button>`;
    }).join("");
  }
  const V = vizTheme();
  lg.querySelectorAll("[data-type]").forEach(chip => {
    chip.classList.toggle("off", S.disabledTypes.has(chip.dataset.type));
    chip.style.setProperty("--c", MAP_TYPE_COLORS[chip.dataset.type] ||
      V.types[chip.dataset.type] || TYPE_COLOR[chip.dataset.type] || V.text);
    chip.setAttribute("aria-pressed", String(!S.disabledTypes.has(chip.dataset.type)));
  });
}

export function renderSkyMap() {
  syncTypeLegend();
  // Keep selection feedback immediate while combining layout/DPR/tab and
  // pointer invalidations into one bitmap draw after the catalog has laid out.
  if (!document.body.classList.contains("catalog-only")) {
    drawSkyMap();
    return;
  }
  renderSelection();
  if (SM_DRAW_FRAME !== null) return;
  SM_DRAW_FRAME = requestAnimationFrame(() => {
    SM_DRAW_FRAME = null;
    drawSkyMap();
  });
}

function drawSkyMap() {
  const cv = $("#skymapCanvas");
  // The packaged catalog has a dedicated plot cell with axes; the source-run
  // planner shell places its canvas directly in the map wrapper.
  const plot = $("#skymapPlot") || $("#skymapWrap");
  if (!cv || !plot || !plot.clientWidth || !plot.clientHeight) return;
  // Marker/label density stays in CSS pixels; only the backing bitmap uses DPR.
  const scale = 1;
  const H = Math.max(1, Math.floor(Math.min(plot.clientHeight, plot.clientWidth / 2)));
  const W = Math.max(2, H * 2);
  $("#skymapRaAxis")?.classList.toggle("compact", W < 520);
  $("#skymapDecAxis")?.classList.toggle("compact", H < 200);
  const left = Math.max(0, (plot.clientWidth - W) / 2);
  const top = Math.max(0, (plot.clientHeight - H) / 2);
  // Axes are siblings of the plot, so their fitted rectangle belongs on the
  // shared wrapper. End tick centers use the same coordinates as grid lines.
  const wrap = $("#skymapWrap");
  const geometry = wrap || plot;
  geometry.style.setProperty("--canvas-width", `${W}px`);
  geometry.style.setProperty("--canvas-height", `${H}px`);
  geometry.style.setProperty("--canvas-left", `${left}px`);
  geometry.style.setProperty("--canvas-top", `${top}px`);
  for (const id of ["skymapRaAxis", "skymapDecAxis"]) {
    const ticks = [...($("#" + id)?.children || [])];
    ticks.forEach((tick, index) => tick.style.setProperty("--tick-position",
      `${index / Math.max(1, ticks.length - 1) * 100}%`));
  }
  cv.style.width = `${W}px`;
  cv.style.height = `${H}px`;
  SM_VIEW = { width: W, height: H, scale };
  const dpr = window.devicePixelRatio || 1;
  const backingWidth = Math.max(1, Math.round(W * dpr));
  const backingHeight = Math.max(1, Math.round(H * dpr));
  if (cv.width !== backingWidth) cv.width = backingWidth;
  if (cv.height !== backingHeight) cv.height = backingHeight;
  const ctx = cv.getContext("2d");
  const V = vizTheme();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = V.canvas;
  ctx.fillRect(0, 0, W, H);

  const L = S.library;
  const objs = (L && L.objects) || [];
  if (SM_SELECTED) {
    SM_SELECTED = objs.find((object) => object.name === SM_SELECTED.name) || null;
  }
  renderSelection();
  const showCons = ($("#smCons") || {}).checked !== false;
  const showLabels = ($("#smLabels") || {}).checked !== false;

  if (showCons) drawConstellations(ctx, W, H, V, showLabels, scale);

  // Reference grid: RA every two hours and Dec every thirty degrees.
  const line = Math.max(1, scale);
  ctx.strokeStyle = V.gridStrong;
  ctx.lineWidth = line;
  ctx.setLineDash([6 * scale, 4 * scale]);
  for (let h = 0; h <= 24; h += 2) {
    const x = h / 24 * W;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }
  for (let d = -90; d <= 90; d += 30) {
    const y = (1 - (d + 90) / 180) * H;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.strokeStyle = getComputedStyle(document.documentElement)
    .getPropertyValue("--border2").trim() || V.gridStrong;
  ctx.lineWidth = line;
  ctx.strokeRect(line / 2, line / 2, W - line, H - line);

  // library objects
  const pts = [];
  const accent = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent").trim() || "#F9C85D";
  for (const o of objs) {
    if (o.ra_hours == null || o.dec_deg == null) continue;
    if (S.disabledTypes.has(o.otype)) continue;      // legend off
    const sessions = captureSessionCount(o) ?? 0;
    // A bounded square-root response makes repeat capture sessions visible
    // without letting a large archive overwhelm neighboring objects. Counts,
    // not image volume or integration time, determine this CSS-pixel radius.
    const r = (3 + 9 * Math.sqrt(sessions / (sessions + 4))) * scale;
    const col = MAP_TYPE_COLORS[o.otype] || V.types[o.otype] || TYPE_COLOR[o.otype] || V.text;
    const isHover = SM_HOVER && SM_HOVER.name === o.name;
    const isSelected = SM_SELECTED && SM_SELECTED.name === o.name;
    for (const wOff of [0, 1]) {            // wrap objects near RA 0h/24h
      const x = (o.ra_hours / 24 + wOff) * W;
      if (x < -20 || x > W + 20) continue;
      const y = (1 - (o.dec_deg + 90) / 180) * H;
      if (y < -20 || y > H + 20) continue;
      ctx.fillStyle = col;
      ctx.globalAlpha = isHover || isSelected ? 1 : 0.8;
      ctx.beginPath(); ctx.arc(x, y, isHover ? r + 2 : r, 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = isSelected ? accent : V.objectOutline;
      ctx.lineWidth = (isSelected ? 2 : 1) * scale;
      ctx.beginPath(); ctx.arc(x, y, isSelected ? r + 3 : r + 1.5, 0, 7); ctx.stroke();
      pts.push({ x, y, r, name: o.name, obj: o });
    }
  }

  const labelCount = drawObjectLabels(ctx, pts, W, H, V, showLabels, accent);

  SM_POINTS = pts;
  if (SM_KEYBOARD_FOCUS && !SM_POINTS.some((point) => point.name === SM_KEYBOARD_FOCUS))
    SM_KEYBOARD_FOCUS = null;
  if (!SM_KEYBOARD_FOCUS) $("#skymapCanvas")?.setAttribute("aria-label", "Image library sky map");
  const summary = $("#skymapChartSummary");
  if (summary) {
    summary.textContent = "Image library sky map; " + pts.length +
      " library objects are plotted; " + labelCount +
      " object names are shown. Marker size shows capture-session count; unavailable counts use the smallest marker. Focus the chart and use arrow keys to browse objects, Home or End for the first or last object, and Enter or Space to select. You can also select a marker with the pointer. Right ascension runs left to right and declination runs from south to north.";
  }
}

function captureSessionCount(object) {
  const value = object?.n_sessions;
  const count = Number(value);
  if ((typeof value === "number" || typeof value === "string" && value.trim() !== "") &&
      Number.isFinite(count) && count >= 0) return Math.floor(count);
  return Array.isArray(object?.sessions) ? object.sessions.length : null;
}

function captureSessionText(object) {
  const count = captureSessionCount(object);
  return count == null ? "capture sessions unavailable" :
    `${count} capture session${count === 1 ? "" : "s"}`;
}

function drawObjectLabels(ctx, points, W, H, V, showLabels, accent) {
  ctx.save();
  ctx.font = "600 12px Segoe UI, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  const placed = [], labels = [], named = new Set();
  const budget = Math.max(3, Math.min(24, Math.floor(W * H / 14000)));
  const selected = point => point.name === SM_SELECTED?.name;
  const hovered = point => point.name === SM_HOVER?.name;
  // Selected and hovered objects get first choice, followed by the most-used
  // archive objects. This is presentation priority, never catalog/scoring math.
  const candidates = [...points].sort((a, b) =>
    Number(selected(b)) - Number(selected(a)) ||
    Number(hovered(b)) - Number(hovered(a)) ||
    (Number(b.obj.n_images) || 0) - (Number(a.obj.n_images) || 0) ||
    (Number(b.obj.total_exposure) || 0) - (Number(a.obj.total_exposure) || 0) ||
    String(a.name).localeCompare(String(b.name)) ||
    Math.abs(a.x - W / 2) - Math.abs(b.x - W / 2));
  const intersects = (a, b, gap = 3) =>
    a.x < b.x + b.w + gap && a.x + a.w + gap > b.x &&
    a.y < b.y + b.h + gap && a.y + a.h + gap > b.y;
  const anchor = (point, box) => ({
    x: clamp(point.x, box.x, box.x + box.w),
    y: clamp(point.y, box.y, box.y + box.h),
  });
  const distanceToBox = (point, box) => {
    const edge = anchor(point, box);
    return Math.hypot(edge.x - point.x, edge.y - point.y);
  };
  for (const point of candidates) {
    const forced = selected(point) || hovered(point);
    if ((!showLabels && !forced) || (!forced && named.size >= budget) || named.has(point.name)) continue;
    let text = String(point.name || point.obj.designation || "Library object");
    const maxWidth = Math.min(240, Math.max(48, W * .4));
    if (ctx.measureText(text).width > maxWidth) {
      const chars = Array.from(text);
      while (chars.length > 1 && ctx.measureText(chars.join("") + "…").width > maxWidth) chars.pop();
      text = chars.join("") + "…";
    }
    const width = ctx.measureText(text).width + 8, height = 18;
    const distance = point.r + 6;
    const alternatives = [
      [point.x + distance, point.y - height / 2],
      [point.x - distance - width, point.y - height / 2],
      [point.x - width / 2, point.y - distance - height],
      [point.x - width / 2, point.y + distance],
      [point.x + distance, point.y - distance - height],
      [point.x + distance, point.y + distance],
      [point.x - distance - width, point.y - distance - height],
      [point.x - distance - width, point.y + distance],
    ].map(([x, y]) => ({x: clamp(x, 3, Math.max(3, W - width - 3)),
      y: clamp(y, 3, Math.max(3, H - height - 3)), w: width, h: height}))
      .sort((a, b) => distanceToBox(point, a) - distanceToBox(point, b));
    const clear = box => !placed.some(other => intersects(box, other)) &&
      !points.some(other => intersects(box,
        {x: other.x - other.r, y: other.y - other.r, w: other.r * 2, h: other.r * 2}, 2));
    let box = alternatives.find(clear);
    // Focus feedback stays named even in a dense cluster or with names hidden.
    if (!box && forced) box = alternatives.find(candidate =>
      !placed.some(other => intersects(candidate, other))) || alternatives[0];
    if (!box) continue;
    placed.push(box);
    named.add(point.name);
    labels.push({point, box, text});
  }
  // Draw links beneath every name so a link can never cut through another
  // label. Sparse objects stay directly named; displaced or ambiguous labels
  // have a short, theme-owned line from their own marker's edge to the name.
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  for (const {point, box} of labels) {
    const length = distanceToBox(point, box);
    const ambiguous = points.some(other => other.name !== point.name &&
      distanceToBox(other, box) <= length + 2);
    if (length <= point.r + 7 && !ambiguous) continue;
    const edge = anchor(point, box);
    const radius = point.r + (selected(point) ? 3 : hovered(point) ? 2 : 1.5);
    if (length <= radius) continue;
    ctx.strokeStyle = selected(point) ? accent : V.dim;
    ctx.beginPath();
    ctx.moveTo(point.x + (edge.x - point.x) * radius / length,
      point.y + (edge.y - point.y) * radius / length);
    ctx.lineTo(edge.x, edge.y);
    ctx.stroke();
  }
  for (const {point, box, text} of labels) {
    ctx.fillStyle = V.canvas;
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = selected(point) ? accent : V.text;
    ctx.fillText(text, box.x + 4, box.y + 13);
  }
  ctx.restore();
  return named.size;
}

function renderSelection() {
  const panel = $("#skymapSelectionContent");
  if (!panel) return;
  if (!SM_SELECTED) {
    const hasObjects = (S.library?.objects || []).some((object) =>
      object.ra_hours != null && object.dec_deg != null);
    panel.innerHTML = `<div class="empty-state selected-map-empty">${catalogIcon("file", "empty-icon")}
      <strong>${hasObjects ? "No plotted object selected." : "No mapped exposures yet."}</strong>
      <p>${hasObjects
        ? "Select a plotted object to see its archive summary."
        : "Import exposures with object coordinates to populate this map."}</p></div>`;
    return;
  }
  const object = SM_SELECTED;
  const type = object.otype ? (TYPE_LABEL[object.otype] || object.otype) : "Unknown type";
  const ra = Number.isFinite(Number(object.ra_hours))
    ? `${Number(object.ra_hours).toFixed(2)}h` : "—";
  const dec = Number.isFinite(Number(object.dec_deg))
    ? `${Number(object.dec_deg).toFixed(2)}°` : "—";
  const sessions = captureSessionCount(object);
  const images = Number(object.n_images) || 0;
  panel.innerHTML = `<p class="library-map-name">${esc(object.name || "Unknown object")}</p>
    <p class="hint">${esc(object.designation || type)}${object.designation ? ` · ${esc(type)}` : ""}</p>
    <dl class="library-map-facts">
      <div><dt>Images</dt><dd>${images}</dd></div>
      <div><dt>Capture sessions</dt><dd>${sessions ?? "—"}</dd></div>
      <div><dt>Integration</dt><dd>${esc(fmtDurShort(object.total_exposure))}</dd></div>
      <div><dt>RA / Dec</dt><dd>${esc(ra)} · ${esc(dec)}</dd></div>
    </dl>`;
}

function fmtDurShort(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${sec}s`;
}

function drawConstellations(ctx, W, H, V, showNames, scale) {
  ctx.save();
  ctx.fillStyle = V.fieldStar;
  for (const st of FIELD_STARS) {
    const [x, y] = xy(st[0], st[1], W, H);
    if (y < 0 || y > H) continue;
    ctx.beginPath(); ctx.arc(x, y, Math.max(.5, 1.05 * st[2] * scale), 0, 7); ctx.fill();
  }

  ctx.strokeStyle = V.targetSecondary;
  ctx.globalAlpha = .42;
  ctx.lineWidth = Math.max(1, .8 * scale);
  for (const con of CONSTELLATIONS) {
    if (!con.g.length) continue;
    const pts = con.s.map(([ra, de]) => xy(ra, de, W, H));
    ctx.beginPath();
    for (const [i0, i1] of con.g) {
      const [x0, y0] = pts[i0], [x1, y1] = pts[i1];
      if (Math.abs(x1 - x0) > W / 2) {       // line crosses the RA wrap seam
        ctx.moveTo(x0, y0); ctx.lineTo(x1 + (x0 < x1 ? -W : W), y1);
        ctx.moveTo(x0 + (x0 < x1 ? W : -W), y0); ctx.lineTo(x1, y1);
      } else {
        ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      }
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  ctx.fillStyle = V.constellationStar;
  for (const con of CONSTELLATIONS) {
    for (const [ra, de] of con.s) {
      const [x, y] = xy(ra, de, W, H);
      if (y < 0 || y > H) continue;
      ctx.beginPath(); ctx.arc(x, y, Math.max(.6, 1.4 * scale), 0, 7); ctx.fill();
    }
  }

  if (showNames) {
    ctx.fillStyle = V.constellationLabel;
    ctx.font = `${10 * scale}px Segoe UI, sans-serif`;
    ctx.textAlign = "center";
    for (const con of CONSTELLATIONS) {
      if (!con.t.length) continue;
      const [x, y] = xy(con.t[0], con.t[1], W, H);
      if (y < 0 || y > H) continue;
      ctx.fillText(con.n, x, y);
    }
  }
  ctx.textAlign = "left";
  ctx.restore();
}

/* ---------------- hover / click ---------------- */

function pointerInMap(event) {
  const rect = $("#skymapCanvas").getBoundingClientRect();
  return {
    inside: rect.width > 0 && rect.height > 0 && event.clientX >= rect.left &&
      event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom,
    x: (event.clientX - rect.left) * (SM_VIEW.width / Math.max(1, rect.width)),
    y: (event.clientY - rect.top) * (SM_VIEW.height / Math.max(1, rect.height)),
  };
}

function nearestPoint(x, y) {
  let best = null;
  const radius = 15 * SM_VIEW.scale;
  let bestDistance = radius * radius;
  for (const point of SM_POINTS) {
    const dx = point.x - x, dy = point.y - y;
    const distance = dx * dx + dy * dy;
    if (distance < bestDistance) { bestDistance = distance; best = point; }
  }
  return best;
}

function onMove(ev) {
  SM_KEYBOARD_FOCUS = null;
  $("#skymapCanvas")?.setAttribute("aria-label", "Image library sky map");
  const { x, y, inside } = pointerInMap(ev);
  const best = inside ? nearestPoint(x, y) : null;
  // DE-11: value comparison - `best` is rebuilt on every render, so the identity
  // test was always true and this reallocated (width/height reassignment) a large
  // canvas on EVERY mousemove.
  const key = best ? best.name : null;
  const prev = SM_HOVER ? SM_HOVER.name : null;
  if (key !== prev) { SM_HOVER = best; renderSkyMap(); }
  const tip = $("#skymapTip");
  if (best) {
    const o = best.obj;
    const sess = captureSessionText(o);
    tip.innerHTML =
      `<b>${esc(o.name)}</b>${o.designation ? ` <span style="color:var(--dim)">${esc(o.designation)}</span>` : ""}<br>` +
      `${esc(o.otype ? (TYPE_LABEL[o.otype] || "unknown") : "unknown")} \u00B7 ` +
      `${o.n_images} image${o.n_images === 1 ? "" : "s"} \u00B7 ` +
      `<b>${fmtDurShort(o.total_exposure)}</b> \u00B7 ${sess}` +
      (o.ra_hours != null ? `<br>RA ${o.ra_hours.toFixed(2)}h \u00B7 Dec ${o.dec_deg.toFixed(2)}\u00B0` : "");
    tip.classList.remove("hidden");
    // place next to the cursor, flipped when it would leave the window
    tip.style.left = (ev.clientX + 16) + "px";
    tip.style.top = (ev.clientY + 14) + "px";
    const r = tip.getBoundingClientRect();
    if (r.right > window.innerWidth - 6) tip.style.left = (ev.clientX - r.width - 12) + "px";
    if (r.bottom > window.innerHeight - 6) tip.style.top = (ev.clientY - r.height - 10) + "px";
    tip.style.left = Math.max(6, parseFloat(tip.style.left)) + "px";
    tip.style.top = Math.max(6, parseFloat(tip.style.top)) + "px";
  } else tip.classList.add("hidden");
}

function focusMapPoint(index) {
  const point = SM_POINTS[index];
  if (!point) return;
  SM_KEYBOARD_FOCUS = point.name;
  SM_HOVER = point;
  renderSkyMap();
  const canvas = $("#skymapCanvas");
  if (canvas) canvas.setAttribute("aria-label",
    `${point.obj.name || "Library object"}, ${captureSessionText(point.obj)}. Use the arrow keys to browse plotted objects, then Enter to select.`);
}

async function selectMapPoint(point, canvas) {
  if (!point) return;
  SM_HOVER = point;
  const object = point.obj;
  SM_SELECTED = object;
  renderSkyMap();
  if (document.body.classList.contains("catalog-only")) {
    return;
  }
  if (object.object_id && S.meta && S.meta[object.object_id]) {
    const { openDetail } = await import("./detail.js");
    openDetail(object.object_id, { opener: canvas });
  }
}

function onMapKeydown(event) {
  if (!SM_POINTS.length) return;
  const current = SM_POINTS.findIndex((point) => point.name === SM_KEYBOARD_FOCUS);
  if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const forward = event.key === "ArrowRight" || event.key === "ArrowDown";
    const next = event.key === "Home" ? 0 : event.key === "End" ? SM_POINTS.length - 1
      : current < 0 ? (forward ? 0 : SM_POINTS.length - 1)
        : (current + (forward ? 1 : -1) + SM_POINTS.length) % SM_POINTS.length;
    focusMapPoint(next);
    return;
  }
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  const selected = current >= 0 ? current : SM_SELECTED
    ? SM_POINTS.findIndex((point) => point.name === SM_SELECTED.name) : 0;
  const index = selected >= 0 ? selected : 0;
  focusMapPoint(index);
  void selectMapPoint(SM_POINTS[index], $("#skymapCanvas"));
}

export function bindSkyMap() {
  const cv = $("#skymapCanvas");
  cv.addEventListener("keydown", onMapKeydown);
  cv.addEventListener("blur", () => {
    SM_KEYBOARD_FOCUS = null;
    SM_HOVER = null;
    cv.setAttribute("aria-label", "Image library sky map");
    renderSkyMap();
  });
  cv.addEventListener("mousemove", onMove);
  cv.addEventListener("mouseleave", () => {
    SM_KEYBOARD_FOCUS = null;
    SM_HOVER = null;
    cv.setAttribute("aria-label", "Image library sky map");
    $("#skymapTip").classList.add("hidden"); renderSkyMap();
  });
  cv.addEventListener("click", async (event) => {
    const { x, y, inside } = pointerInMap(event);
    const closest = inside ? nearestPoint(x, y) : null;
    if (!closest) return;
    await selectMapPoint(closest, cv);
  });
  for (const id of ["smLabels", "smCons"]) {
    $(`#${id}`).addEventListener("change", renderSkyMap);
  }

  // object-type color legend (click a chip to grey that type out)
  const lg = $("#smLegend");
  if (lg) {
    syncTypeLegend();
    lg.addEventListener("click", e => {
      const chip = e.target.closest("[data-type]");
      if (!chip) return;
      const t = chip.dataset.type;
      S.disabledTypes.has(t) ? S.disabledTypes.delete(t) : S.disabledTypes.add(t);
      chip.classList.toggle("off", S.disabledTypes.has(t));
      chip.setAttribute("aria-pressed", String(!S.disabledTypes.has(t)));
      SM_HOVER = null;
      renderSkyMap();
    });
  }

  // The source planner retains its existing chart lifecycle. The standalone
  // catalog owns one observer and one scheduler for every geometry invalidation.
  if (!document.body.classList.contains("catalog-only")) return;
  const plot = $("#skymapPlot");
  const observer = plot && typeof ResizeObserver === "function"
    ? new ResizeObserver(renderSkyMap) : null;
  observer?.observe(plot);
  window.addEventListener("resize", renderSkyMap);
  window.visualViewport?.addEventListener("resize", renderSkyMap);
  let dprQuery = null;
  const watchDpr = () => {
    dprQuery?.removeEventListener("change", onDprChange);
    dprQuery = window.matchMedia?.(`(resolution: ${window.devicePixelRatio || 1}dppx)`) || null;
    dprQuery?.addEventListener("change", onDprChange);
  };
  const onDprChange = () => { renderSkyMap(); watchDpr(); };
  watchDpr();
  window.addEventListener("pagehide", () => {
    observer?.disconnect();
    dprQuery?.removeEventListener("change", onDprChange);
    window.removeEventListener("resize", renderSkyMap);
    window.visualViewport?.removeEventListener("resize", renderSkyMap);
    if (SM_DRAW_FRAME !== null) cancelAnimationFrame(SM_DRAW_FRAME);
    SM_DRAW_FRAME = null;
  }, { once: true });
}
