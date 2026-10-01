/* Tonight tab: summary cards + ranked pick lists */

import { $, esc, parseUTC, fmtHM, fmtT, phaseName } from "./util.js";
import { S } from "./state.js";
import { statusOf, objScore, compactWindowText, desigLine,
         curTimeISO, popularityFactor, effectiveMinAlt, scrubWindowRange } from "./derive.js";
import { inTonightPool, TONIGHT_MAG_UNCAPPED, dateFromJD } from "./core.js";
import { openDetail } from "./detail.js";
import { vizTheme } from "./viztheme.js";
import { overviewGridRange, strokeBlockedRuns } from "./tonight-chart.js";

let _overviewRows = [];
let _overviewVisible = new Set();
let _overviewResize = null;

export function bindTonight() {
  document.addEventListener("click", e => {
    const row = e.target.closest("#tonightRows [data-tonight-id]");
    if (row && row.dataset.tonightId) openDetail(row.dataset.tonightId);
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest && e.target.closest("#tonightRows [data-tonight-id]");
    if (row && row.dataset.tonightId) { e.preventDefault(); openDetail(row.dataset.tonightId); }
  });
  const overview = document.querySelector(".tonight-overview");
  if (overview && typeof ResizeObserver === "function") {
    _overviewResize = new ResizeObserver(() => drawTonightOverview());
    _overviewResize.observe(overview);
  }
  window.addEventListener("resize", drawTonightOverview);
  $("#tonightOverviewLegend")?.addEventListener("change", (event) => {
    const input = event.target.closest("input[data-overview-id]");
    if (!input) return;
    if (input.checked) _overviewVisible.add(input.dataset.overviewId);
    else _overviewVisible.delete(input.dataset.overviewId);
    drawTonightOverview();
  });
}

export function drawTonightOverview() {
  const cv = $("#tonightAltitudeOverview");
  const N = S.night;
  if (!cv || !N || !cv.clientWidth) return;
  const timeline = N.timeline?.times_iso || [];
  if (!timeline.length) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight || 250;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  const ctx = cv.getContext("2d");
  const V = vizTheme();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = V.canvas; ctx.fillRect(0, 0, W, H);
  const L = 44, R = 12, T = 18, B = 28;
  const pw = Math.max(1, W - L - R), ph = Math.max(1, H - T - B);
  const ms = (iso) => iso ? Date.parse(iso.endsWith("Z") ? iso : iso + "Z") : NaN;
  const dusk = ms(N.dusk), dawn = ms(N.dawn);
  const at = (target) => {
    let best = 0, distance = Infinity;
    timeline.forEach((iso, i) => {
      const d = Math.abs(ms(iso) - target);
      if (d < distance) { distance = d; best = i; }
    });
    return best;
  };
  const allowed = scrubWindowRange();
  const { lo, hi, selected: markerIdx } = overviewGridRange(
    timeline.length, allowed, S.tIdx);
  const span = Math.max(1, hi - lo);
  const xAt = (i) => L + (i - lo) / span * pw;
  const yAt = (alt) => T + (90 - Math.max(0, Math.min(90, alt))) / 90 * ph;
  const duskX = Number.isFinite(dusk) ? xAt(at(dusk)) : L;
  const dawnX = Number.isFinite(dawn) ? xAt(at(dawn)) : W - R;
  ctx.fillStyle = V.twilightShade;
  if (duskX > L) ctx.fillRect(L, T, duskX - L, ph);
  if (dawnX < W - R) ctx.fillRect(dawnX, T, W - R - dawnX, ph);
  for (let alt = 0; alt <= 90; alt += 30) {
    const y = yAt(alt);
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(W - R, y);
    ctx.strokeStyle = alt === 0 ? V.gridStrong : V.grid; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = V.axis; ctx.font = "11px Consolas, monospace";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(`${alt}°`, L - 8, y);
  }
  const minAlt = effectiveMinAlt(S.cfg);
  const minY = yAt(minAlt);
  ctx.save(); ctx.setLineDash(V.guideMinDash); ctx.strokeStyle = V.minStroke;
  ctx.beginPath(); ctx.moveTo(L, minY); ctx.lineTo(W - R, minY); ctx.stroke(); ctx.restore();
  ctx.fillStyle = V.minLabel; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.font = "10px Consolas, monospace"; ctx.fillText(`${minAlt}° min`, L + 4, minY - 3);

  const markerX = xAt(markerIdx);
  ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = V.targetSelected; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(markerX, T); ctx.lineTo(markerX, T + ph); ctx.stroke(); ctx.restore();
  ctx.fillStyle = V.targetSelected; ctx.textAlign = markerX > W - R - 48 ? "right" : "left";
  ctx.textBaseline = "top"; ctx.font = "600 11px Consolas, monospace";
  ctx.fillText(fmtT(parseUTC(timeline[markerIdx])), markerX + (markerX > W - R - 48 ? -4 : 4), 3);

  const colors = [V.targetSelected, V.targetSecondary, V.targetSecondary];
  _overviewRows.forEach(({ o, m }, rowIndex) => {
    if (!_overviewVisible.has(m.id)) return;
    const alt = o.alt || [];
    const ceil = o.ceil || [];
    const ceilTop = o.ceil_top || null;
    ctx.lineWidth = rowIndex === 0 ? 2.2 : 1.7;
    ctx.strokeStyle = colors[rowIndex] || V.targetSecondary;
    ctx.beginPath(); let started = false;
    for (let i = lo; i <= hi; i++) {
      const a = Number(alt[i]);
      if (!Number.isFinite(a) || a < -10) { started = false; continue; }
      const x = xAt(i), y = yAt(a);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // Red is reserved for samples the shared predicate classifies as horizon
    // or ceiling blocked. Below-minimum and below-horizon samples keep the
    // target's base series color.
    ctx.lineWidth = 2.3;
    strokeBlockedRuns(ctx, alt, ceil, ceilTop, minAlt,
      lo, hi, xAt, yAt, V.targetBlocked);
  });
  const tickCount = Math.min(6, Math.max(2, Math.floor(pw / 125)));
  ctx.fillStyle = V.axis; ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.font = "10px Consolas, monospace";
  for (let n = 0; n < tickCount; n++) {
    const i = Math.round(lo + span * n / (tickCount - 1));
    const x = xAt(i);
    ctx.beginPath(); ctx.moveTo(x, T + ph); ctx.lineTo(x, T + ph + 4);
    ctx.strokeStyle = V.gridStrong; ctx.stroke();
    ctx.fillText(fmtHM(timeline[i]), x, T + ph + 7);
  }
}

export function renderTonight() {
  if (!S.night) return;
  const N = S.night;
  // The candidate pool is core/visibility.js::inTonightPool - ONE rule shared
  // with the web, so the two apps cannot disagree about which objects may reach
  // the headline list. `TONIGHT_MAG_UNCAPPED`: the desktop's payload is built
  // for the whole catalog, so it has no browser-scoring reason to cap it (the
  // web passes its own ADV_TONIGHT_MAG_CAP there).
  const objs = N.objects
    .filter(o => inTonightPool((S.meta[o.id] || {}).vmag, !!(o.adv || (S.meta[o.id] || {})._adv),
                               TONIGHT_MAG_UNCAPPED))
    .map(o => ({o, m:S.meta[o.id]})).filter(x => x.m);
  const ranked = objs.filter(x => x.o.best && x.o.best[1] > x.o.best[0])
    .sort((a, b) => objScore(b.o) - objScore(a.o) ||
      popularityFactor(b.m) - popularityFactor(a.m));
  const shown = ranked.slice(0, 16);
  const maxScore = Math.max(0, ...shown.map(({o}) => objScore(o)));
  const now = objs.filter(x => statusOf(x.o, S.tIdx).code === "vis")
    .sort((a, b) => objScore(b.o) - objScore(a.o) ||
      popularityFactor(b.m) - popularityFactor(a.m)).slice(0, 8);
  const darkH = N.dusk && N.dawn ? (() => {
    const mins = Math.max(0, Math.round((new Date(N.dawn+"Z") - new Date(N.dusk+"Z"))/60000));
    return `${Math.floor(mins/60)}h ${String(mins%60).padStart(2,"0")}m`;
  })() : "\u2014";
  const site = (S.cfg?.sites || []).find(
    (s) => String(s.id) === String(S.activeSiteId)) || (S.cfg?.sites || [])[0];
  const selected = parseUTC(curTimeISO());
  const brief = $("#nightBrief");
  if (brief) brief.innerHTML = `
    <div class="night-brief-title"><p class="eyebrow">Night plan</p><h1>Tonight</h1><span>${esc(site?.name || "Active site")} · ${esc(N.date_local || "")}</span></div>
    <div class="night-brief-facts"><span><small>Selected</small><b>${fmtT(selected)}</b></span>
      <span><small>${esc(N.twilight_level || "Dark")} window · duration</small><b>${fmtHM(N.dusk)}–${fmtHM(N.dawn)} · ${darkH}</b></span>
      <span><small>Moon phase</small><b>${Math.round((N.moon_illum || 0)*100)}% · ${phaseName(N.moon_illum || 0)}</b></span></div>
    <div class="night-brief-times" aria-label="Sun and twilight times">
      <span>Sunset <b>${fmtHM(N.sunset)}</b></span><span>Dusk <b>${fmtHM(N.dusk)}</b></span>
      <span>Dawn <b>${fmtHM(N.dawn)}</b></span><span>Sunrise <b>${fmtHM(N.sunrise)}</b></span>
    </div>`;

  $("#tonightRankCount").textContent = `${ranked.length} targets with a clear window tonight`;
  $("#tonightRows").innerHTML = shown.length ? shown.map(({o, m}, i) => {
    const st = statusOf(o, S.tIdx);
    const alt = Number(o.alt?.[S.tIdx]);
    const score = objScore(o);
    const relative = maxScore > 0 && score > 0 ? Math.round(100 * score / maxScore) : null;
    const best = compactWindowText(o);
    const durationMin = o.best ? Math.max(0, (o.best[1] - o.best[0]) * S.night.timeline.step_min) : 0;
    const duration = durationMin ? `${Math.floor(durationMin / 60)}h ${String(Math.round(durationMin % 60)).padStart(2, "0")}m` : "—";
    const rise = Number.isFinite(o.rise_jd) && dateFromJD(o.rise_jd) > selected
      ? fmtT(dateFromJD(o.rise_jd)) : "—";
    const reason = st.code === "vis" ? "Above minimum altitude; horizon clear" : st.text;
    return `<tr data-tonight-id="${esc(m.id)}" tabindex="0" role="button" aria-label="Open details for ${esc(m.primary_name)}">
      <td class="mono rank">${String(i + 1).padStart(2, "0")}</td>
      <td><strong>${esc(m.primary_name)}</strong><small>${esc(desigLine(m))}</small></td>
      <td class="mono num">${relative == null ? "—" : relative}</td>
      <td class="mono num">${Number.isFinite(o.max_alt) ? `${o.max_alt.toFixed(0)}°` : (Number.isFinite(alt) ? `${alt.toFixed(0)}°` : "—")}</td>
      <td class="mono">${esc(best)} <small>· ${duration}</small></td>
      <td>${esc(reason)}</td><td class="mono">${rise === "—" ? "—" : `Rises ${esc(rise)}`}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="7" class="hint">No targets have a clear window tonight.</td></tr>';

  const later = objs.filter(x => x.o.best && x.o.best[0] > S.tIdx)
                    .sort((a,b) => (a.o.best[0]-b.o.best[0]) || (objScore(b.o)-objScore(a.o)))
                    .slice(0,8);

  const overviewIds = [];
  if (S.currentId && objs.some((x) => x.o.id === S.currentId)) overviewIds.push(S.currentId);
  for (const item of [...now, ...later]) {
    if (!overviewIds.includes(item.o.id)) overviewIds.push(item.o.id);
    if (overviewIds.length >= 3) break;
  }
  _overviewRows = overviewIds.map((id) => objs.find((x) => x.o.id === id)).filter(Boolean);
  const priorVisible = _overviewVisible;
  const priorIds = new Set(_overviewRows.map(({m}) => m.id));
  _overviewVisible = new Set(_overviewRows
    .filter(({m}) => !priorVisible.size || !priorIds.has(m.id) || priorVisible.has(m.id))
    .map(({m}) => m.id));
  const overviewLegend = $("#tonightOverviewLegend");
  if (overviewLegend) {
    overviewLegend.innerHTML = _overviewRows.map(({ o, m }, i) => {
      const color = i === 0 ? "var(--viz-target-selected)" : "var(--viz-target-secondary)";
      return `<label class="series-key" style="--series-color:${color}">
        <input type="checkbox" data-overview-id="${esc(m.id)}" ${_overviewVisible.has(m.id) ? "checked" : ""}>
        <span class="series-swatch" aria-hidden="true"></span>${esc(m.messier || m.ngcic || m.primary_name)}</label>`;
    }).join("") || '<span class="hint">No target tracks available.</span>';
  }
  const overviewSummary = $("#tonightOverviewSummary");
  if (overviewSummary) overviewSummary.textContent = _overviewRows.length
    ? `Apparent altitude tracks for ${_overviewRows.map(({m}) => m.primary_name).join(", ")}, sampled every six minutes across the selected ${S.cfg.view_full_day ? "full day" : "night"} scrub range. Gold identifies the selected target; blue identifies the other displayed targets. Red segments are horizon or ceiling blocked, amber segments are below your minimum altitude, and gray segments are below the horizon. Shaded time is outside dusk to dawn. The dashed vertical line and target markers follow the selected time.`
    : "No target tracks are available for this night.";
  drawTonightOverview();

}
