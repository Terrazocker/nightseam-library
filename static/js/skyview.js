/* Sky view: polar whole-dome chart */

import { $, clamp, esc, fmtHM, TYPE_COLOR, TYPE_LABEL } from "./util.js";
import { S } from "./state.js";
import { statusOf, desigLine, activeProfile, activeTopProfile, activeBandTop,
         activePaintMode, shortName, aboveHorizon, magVisible, objScore,
         skyClampZoom, skyViewMatrix, skyScreenToBase, windowEndISO,
         skyClampFocus } from "./derive.js";
import { openDetail } from "./detail.js";
import { passesFilters } from "./targets.js";
import { CONSTELLATIONS, FIELD_STARS } from "./skydata.js";
import { altaz as coreAltaz, julianDay as coreJulianDay } from "./core.js";
import { vizTheme } from "./viztheme.js";

let SKY_HOVER = null;
let SKY_POINTS = [];
let SKY_SELECTED_ID = null;
let SKY_ZOOM = 1;
let SKY_FX = null;    // focus point (base coords) under the viewport centre
let SKY_FY = null;
let _drag = null;
let _suppressClick = false;

export function resetSkyView() {
  SKY_ZOOM = 1; SKY_FX = null; SKY_FY = null;
  renderSky();
}

function applyView(ctx, dpr, W, H) {
  const m = skyViewMatrix(SKY_ZOOM, W, H, SKY_FX, SKY_FY);
  ctx.setTransform(dpr * m.a, 0, 0, dpr * m.d, dpr * m.e, dpr * m.f);
}

/* css px (relative to canvas) -> base chart coords */
function screenToBase(sx, sy, W, H) {
  return skyScreenToBase(SKY_ZOOM, W, H, SKY_FX, SKY_FY, sx, sy);
}

function clampFocus(W, H) {
  [SKY_FX, SKY_FY] = skyClampFocus(W, H, SKY_FX, SKY_FY);
}

export function bindSky() {
  const cv = $("#skyCanvas");

  cv.addEventListener("mousemove", skyHover);
  cv.addEventListener("mouseleave", () => {
    SKY_HOVER = null; $("#skyTip").classList.add("hidden"); renderSky();
  });

  // pan / zoom / click (distinguished by drag distance)
  cv.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    const rect = cv.getBoundingClientRect();
    cv.setPointerCapture(e.pointerId);
    _drag = { sx: e.clientX, sy: e.clientY,
              fx: SKY_FX == null ? rect.width/2 : SKY_FX,
              fy: SKY_FY == null ? rect.height/2 : SKY_FY,
              moved: false };
  });
  cv.addEventListener("pointermove", e => {
    if (_drag && (e.buttons & 1)) {
      if (SKY_ZOOM <= 1) return;
      const rect = cv.getBoundingClientRect();
      const dx = e.clientX - _drag.sx, dy = e.clientY - _drag.sy;
      if (!(_drag.moved) && Math.hypot(dx, dy) > 4) {
        _drag.moved = true;
        $("#skyTip").classList.add("hidden");
        SKY_HOVER = null;
      }
      if (_drag.moved) {
        SKY_FX = _drag.fx - dx / SKY_ZOOM;
        SKY_FY = _drag.fy - dy / SKY_ZOOM;
        clampFocus(rect.width, rect.height);
        renderSky();
      }
      return;
    }
    skyHover(e);
  });
  window.addEventListener("pointerup", () => {
    if (_drag && _drag.moved) { _suppressClick = true; setTimeout(() => { _suppressClick = false; }, 0); }
    _drag = null;
  });
  cv.addEventListener("click", () => {
    if (_suppressClick) return;
    if (SKY_HOVER) {
      SKY_SELECTED_ID = SKY_HOVER.id;
      renderSky();
    }
  });
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const lx = e.clientX - rect.left, ly = e.clientY - rect.top;
    const [bx, by] = screenToBase(lx, ly, rect.width, rect.height);
    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    SKY_ZOOM = skyClampZoom(SKY_ZOOM * factor);
    if (SKY_ZOOM > 1) {
      // keep the point under the cursor fixed while zooming
      SKY_FX = bx - (lx - rect.width/2) / SKY_ZOOM;
      SKY_FY = by - (ly - rect.height/2) / SKY_ZOOM;
      clampFocus(rect.width, rect.height);
    } else { SKY_FX = null; SKY_FY = null; }
    renderSky();
  }, { passive: false });
  $("#skyZoomReset").addEventListener("click", resetSkyView);
  // uranometria-style: double-click anywhere on the chart resets the zoom
  cv.addEventListener("dblclick", e => { e.preventDefault(); resetSkyView(); });

  $("#showLabels").addEventListener("change", renderSky);
  $("#showMoon").addEventListener("change", renderSky);
  $("#showMinAlt").addEventListener("change", renderSky);
  $("#showAll").addEventListener("change", renderSky);
  $("#showCons").addEventListener("change", renderSky);
  $("#skyMag").addEventListener("input", e => {
    $("#skyMagVal").textContent = (+e.target.value).toFixed(1);
    SKY_HOVER = null; renderSky();
  });

  // object-type color legend (click a chip to grey that type out)
  const lg = $("#typeLegend");
  if (lg) {
    // the chips are click-only in the markup: give them a keyboard path
    lg.addEventListener("keydown", e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const chip = e.target.closest("[data-type]");
      if (!chip) return;
      e.preventDefault();
      chip.click();
    });
    const V = vizTheme();
    lg.innerHTML = Object.keys(TYPE_COLOR).map(t =>
      `<span class="lgchip${S.disabledTypes.has(t) ? " off" : ""}" data-type="${t}" role="button" tabindex="0" style="--c:${V.types[t] || TYPE_COLOR[t]}"><i></i>${TYPE_LABEL[t] || t}</span>`
    ).join("") +
    `<span class="lgchip" style="--c:${V.favorite}"><i></i>&#9733; session target</span>`;
    lg.addEventListener("click", e => {
      const chip = e.target.closest("[data-type]");
      if (!chip) return;
      const t = chip.dataset.type;
      S.disabledTypes.has(t) ? S.disabledTypes.delete(t) : S.disabledTypes.add(t);
      chip.classList.toggle("off", S.disabledTypes.has(t));
      SKY_HOVER = null;
      renderSky();
    });
  }
}

// compass convention: azimuth clockwise, North top, East right
function azToXY(cx, cy, R, azDeg, altDeg) {
  const r = R * (1 - altDeg/90);
  const a = azDeg * Math.PI/180;
  return [cx + r*Math.sin(a), cy - r*Math.cos(a)];
}

function skyGeom(cv) {
  const W = cv.clientWidth, H = cv.clientHeight;
  return {W, H, cx: W/2, cy: H/2, R: Math.min(W,H)/2 - 26};
}

/* ---------------- faded constellation backdrop ---------------- */

function activeSite() {
  if (!S.cfg || !S.cfg.sites) return null;
  return S.cfg.sites.find(s => s.id === S.cfg.active_site_id) || S.cfg.sites[0] || null;
}

/* Decorative backdrop projection: J2000 field stars plotted WITHOUT
   refraction or precession (core altaz with both flags off) - the old local
   gmstDeg/altAzFast copies were removed in Phase 2 concept 6. */
function backdropAltAz(raH, decD, latD, lonD, jd) {
  return coreAltaz(raH * 15, decD, jd, latD, lonD, false, false);
}

function drawSkyBackdrop(ctx, cx, cy, R, V) {
  const site = activeSite();
  if (!site || !S.night) return;
  const tl = S.night.timeline;
  const tIso = tl.times_iso[Math.min(S.tIdx, tl.n - 1)];
  const jd = coreJulianDay(new Date(tIso + "Z"));

  // keep everything inside the horizon circle — but ONLY at full zoom-out:
  // once zoomed, the viewport is a RECTANGLE over the chart (uranometria
  // style), so the disc edge can leave the screen and you see more sky than
  // a circular magnifier would show.
  ctx.save();
  if (SKY_ZOOM <= 1) {
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, 7);
    ctx.clip();
  }

  const proj = (raH, decD) => {
    const [a, z] = backdropAltAz(raH, decD, site.lat, site.lon, jd);
    return { a, z };
  };

  // faint field stars
  ctx.fillStyle = V.fieldStar;
  for (const st of FIELD_STARS) {
    const { a, z } = proj(st[0], st[1]);
    if (a <= 0.5) continue;
    const [x, y] = azToXY(cx, cy, R, z, a);
    ctx.beginPath();
    ctx.arc(x, y, 1.05 * st[2], 0, 7);
    ctx.fill();
  }

  // constellation stick figures
  ctx.strokeStyle = V.constellation;
  ctx.lineWidth = 1;
  for (const con of CONSTELLATIONS) {
    if (!con.g.length) continue;
    const pts = con.s.map(([ra, de]) => proj(ra, de));
    ctx.beginPath();
    for (const [i0, i1] of con.g) {
      if (pts[i0].a <= 0 || pts[i1].a <= 0) continue;
      const [x0, y0] = azToXY(cx, cy, R, pts[i0].z, pts[i0].a);
      const [x1, y1] = azToXY(cx, cy, R, pts[i1].z, pts[i1].a);
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
    }
    ctx.stroke();
  }
  ctx.fillStyle = V.constellationStar;
  for (const con of CONSTELLATIONS) {
    for (const [ra, de] of con.s) {
      const { a, z } = proj(ra, de);
      if (a <= 0) continue;
      const [x, y] = azToXY(cx, cy, R, z, a);
      ctx.beginPath(); ctx.arc(x, y, 1.4, 0, 7); ctx.fill();
    }
  }

  // names
  ctx.fillStyle = V.constellationLabel;
  ctx.font = "10px Segoe UI";
  ctx.textAlign = "center";
  for (const con of CONSTELLATIONS) {
    if (!con.t.length) continue;
    const { a, z } = proj(con.t[0], con.t[1]);
    if (a <= 5) continue;
    const [x, y] = azToXY(cx, cy, R, z, a);
    ctx.fillText(con.n, x, y);
  }
  ctx.textAlign = "left";
  ctx.restore();
}

/** DE-12: the legend chips are rendered ONCE (in bindSky), so a type toggled off
 *  in the OTHER chart's legend left this one claiming the type is shown - both
 *  charts filter by the shared S.disabledTypes. Resync on every render, so the
 *  legend and the chart can never disagree. */
function syncTypeLegend() {
  const lg = $("#typeLegend");
  if (!lg) return;
  const V = vizTheme();
  lg.querySelectorAll("[data-type]").forEach(chip => {
    chip.classList.toggle("off", S.disabledTypes.has(chip.dataset.type));
    chip.style.setProperty("--c", V.types[chip.dataset.type] || TYPE_COLOR[chip.dataset.type] || V.text);
  });
  const session = lg.querySelector(".lgchip:not([data-type])");
  if (session) session.style.setProperty("--c", V.favorite);
}

export function renderSky() {
  syncTypeLegend();
  const cv = $("#skyCanvas");
  if (!cv || !cv.clientWidth || !S.night) return;
  const dpr = window.devicePixelRatio || 1;
  const {W,H} = skyGeom(cv);
  // integer geometry -> crisp strokes and text on any display scaling
  const cx = Math.round(W/2), cy = Math.round(H/2);
  const R = Math.round(Math.min(W,H)/2 - 26);
  cv.width=W*dpr; cv.height=H*dpr;
  const ctx=cv.getContext("2d");
  const V = vizTheme();
  ctx.setTransform(1,0,0,1,0,0);
  ctx.clearRect(0,0,cv.width,cv.height);
  applyView(ctx, dpr, W, H);
  clampFocus(W, H);
  ctx.textBaseline = "alphabetic";

  // faded constellation backdrop (toggleable)
  if (($("#showCons")||{}).checked !== false) drawSkyBackdrop(ctx, cx, cy, R, V);

  // obstacle silhouette (filled region + boundary stroke only)
  const prof = activeProfile();
  if (prof && prof.length === 720) {
    const elevAt = az => {
      const raw = prof[Math.floor(((az%360)+360)%360/0.5)] ?? -99;
      return raw > 0.25 ? raw : 0;    // ignore stray/noise bins
    };

    ctx.beginPath();
    for (let az=0; az<=360; az+=1) {
      const [x,y] = azToXY(cx,cy,R,az,clamp(elevAt(az),-2,89));
      az===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
    }
    ctx.lineTo(...azToXY(cx,cy,R,360,0));
    for (let az=360; az>=0; az-=2) ctx.lineTo(...azToXY(cx,cy,R,az,0));
    ctx.closePath();
    ctx.fillStyle=V.obstacleFill;
    ctx.fill();

    // solid boundary line: obstacle mode only. In sky mode the fully
    // blocked columns sit at 89 deg and would draw a spurious inner
    // circle with radial connectors (a "triangle") instead of a clean edge.
    if (activePaintMode() !== "sky") {
      ctx.beginPath();
      for (let az=0; az<=360; az+=1) {
        const [x,y] = azToXY(cx,cy,R,az,clamp(elevAt(az),-2,89));
        az===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
      }
      ctx.strokeStyle=V.obstacleStroke; ctx.lineWidth=1.4;
      ctx.stroke();
    }
  }

  // sky-mode upper ceiling: blocked above the painted sky band too.
  // Columns without a real ceiling (fully blocked -> 90, painted-to-top ->
  // 90.5) are covered by the bottom silhouette already, so only band
  // columns draw the blocked-above region: one polygon per contiguous run,
  // closing through the zenith (no per-column wedges -> no radial lines).
  const profTop = activeTopProfile();
  if (profTop && profTop.length === 720) {
    const topVal = az => {
      const raw = profTop[Math.floor(((az % 360) + 360) % 360 / 0.5)] ?? 90;
      return raw > 0.25 && raw < 88 ? Math.min(89, raw) : NaN;
    };

    ctx.fillStyle = V.obstacleFill;
    let run = false;
    for (let az = 0; az <= 360; az += 1) {
      const v = topVal(az);
      if (v >= 0) {
        const [x, y] = azToXY(cx, cy, R, az, clamp(v, -2, 89));
        if (!run) { ctx.beginPath(); ctx.moveTo(x, y); run = true; }
        else ctx.lineTo(x, y);
      } else if (run) {
        ctx.lineTo(cx, cy);
        ctx.closePath();
        ctx.fill();
        run = false;
      }
    }
    if (run) { ctx.lineTo(cx, cy); ctx.closePath(); ctx.fill(); }

    // top edge line over band columns only
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    let pen = false;
    for (let az = 0; az <= 360; az += 1) {
      const v = topVal(az);
      if (!(v >= 0)) { pen = false; continue; }
      const [x, y] = azToXY(cx, cy, R, az, clamp(v, -2, 89));
      pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      pen = true;
    }
    ctx.strokeStyle = V.obstacleStroke; ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // sky mode, DEFAULT open above: HATCH the area above the painted band (it is
  // free only because of the open-above assumption) - never shade it as
  // blocked, so the sky view reads as the obstruction view inverted.
  const rawTop = activeBandTop();
  if (rawTop && rawTop.length === 720 && !profTop) {
    const rawVal = az => {
      const raw = rawTop[Math.floor(((az % 360) + 360) % 360 / 0.5)] ?? 90;
      return raw > 0.25 && raw < 88 ? Math.min(89, raw) : NaN;
    };
    const hatchPoly = (pts) => {
      ctx.save();
      ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
      ctx.clip();
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const [x, y] of pts) {
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
      const diag = (maxX - minX) + (maxY - minY);
      ctx.strokeStyle = V.assumedHatch;
      ctx.lineWidth = 1;
      for (let o = -diag; o < diag; o += 9) {
        ctx.beginPath();
        ctx.moveTo(minX + o, minY);
        ctx.lineTo(minX + o + diag, minY + diag);
        ctx.stroke();
      }
      ctx.restore();
    };
    let runPts = null;
    const flush = () => {
      if (runPts && runPts.length > 1) { runPts.push([cx, cy]); hatchPoly(runPts); }
      runPts = null;
    };
    for (let az = 0; az <= 360; az += 1) {
      const v = rawVal(az);
      if (v >= 0) {
        if (!runPts) runPts = [];
        runPts.push(azToXY(cx, cy, R, az, clamp(v, -2, 89)));
      } else {
        flush();
      }
    }
    flush();
  }

  // horizon legend: explain the obstruction shading and, in sky mode, that the
  // area above the painted band is only open because of the assumption
  const pl = $("#skyPanoLegend");
  if (pl) {
    const mode = activePaintMode();
    const blocked = !!activeProfile();
    const openAbove = mode === "sky" && !!activeBandTop() && !activeTopProfile();
    pl.innerHTML =
      (blocked ? `<span class="lgchip" style="--c:${V.obstacleStroke}"><i></i>blocked by horizon</span>` : "") +
      (openAbove ? '<span class="lgchip hatch"><i></i>above band: open (assumed)</span>' : "");
  }

  // min-altitude band: the area below the min-useful-alt slider is treated
  // as blocked (no visible windows), so shade it from the horizon up to the
  // slider's ring. Toggleable with #showMinAlt.
  if (($("#showMinAlt") || {}).checked !== false && S.minAlt > 0) {
    const r1 = R * (1 - Math.min(89, S.minAlt) / 90);
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.moveTo(cx + r1, cy);        // lift the pen: no radial seam at east
    ctx.arc(cx, cy, r1, Math.PI * 2, 0, true);
    ctx.closePath();
    ctx.fillStyle = V.minFill;
    ctx.fill();
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.arc(cx, cy, r1, 0, 7);
    ctx.strokeStyle = V.minStroke;
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = V.minLabel;
    ctx.font = "10px Consolas, monospace";
    ctx.textAlign = "left";
    ctx.fillText(Math.round(S.minAlt) + "\u00B0 min alt", cx + 6, cy - r1 - 4);
  }

  // grid rings (10-degree steps)
  ctx.lineWidth=1;
  ctx.font="10px Consolas, monospace";
  for (let alt=10; alt<=80; alt+=10) {
    const ry = cy-R*(1-alt/90);
    ctx.strokeStyle=V.gridStrong;
    ctx.beginPath(); ctx.arc(cx,cy,R*(1-alt/90),0,7); ctx.stroke();
    ctx.fillStyle=V.axis;
    ctx.fillText(alt+"\u00B0", cx+3, ry-3);
  }
  ctx.fillStyle=V.axis;
  ctx.fillText("0\u00B0", cx+3, cy+11);

  // cardinal labels
  const CARD=[["N",0],["E",90],["S",180],["W",270]];
  for (const [lab,az] of CARD) {
    const a=az*Math.PI/180;
    const x=cx+(R+12)*Math.sin(a), y=cy-(R+12)*Math.cos(a);
    ctx.fillStyle=V.text; ctx.font="bold 13px Segoe UI";
    ctx.textAlign="center"; ctx.textBaseline="middle";
    ctx.fillText(lab,x,y);
  }
  ctx.textAlign="left"; ctx.textBaseline="alphabetic";

  // objects (dots) + collected label candidates, decluttered afterwards
  const labels = $("#showLabels").checked;
  const magEl = $("#skyMag") || {};
  const magLimit = +(magEl.value ?? 14);
  const magMax = +(magEl.max ?? 14);
  const bigCatalog = S.night.objects.length > 3000;   // advanced mode
  const labelMagMax = bigCatalog ? 8.5 : 99;          // label bright ones only
  const favs = new Set(S.cfg.favorites || []);
  const pts=[];
  const labelCands=[];
  for (const o of S.night.objects) {
    const m=S.meta[o.id]; if(!m) continue;
    const isHover=SKY_HOVER&&SKY_HOVER.id===o.id;
    // keep the sky view in sync with the Targets tab filters
    const showAll = ($("#showAll")||{}).checked !== false;
    if (!isHover && !showAll && !passesFilters(m, o)) continue;
    if (!isHover && S.disabledTypes.has(m.otype)) continue;  // legend off
    // magnitude limit (shared rule with the web chart)
    if (!isHover && !magVisible(m.vmag, magLimit, magMax)) continue;
    const alt=o.alt[S.tIdx], az=o.az[S.tIdx];
    if (alt<-3) continue;
    const [x,y]=azToXY(cx,cy,R,az,alt);
    const blocked=alt<=o.ceil[S.tIdx];
    const col=isHover?V.target:(blocked?V.blockedDot:(V.types[m.otype]||TYPE_COLOR[m.otype]||V.text));
    ctx.fillStyle=col;
    ctx.globalAlpha = alt>0?1:.35;
    const dotR=isHover?4.5:m.vmag&&m.vmag<6?3.4:2.4;
    ctx.beginPath(); ctx.arc(x,y,dotR,0,7); ctx.fill();
    ctx.globalAlpha=1;
    if (favs.has(o.id) && alt>-3) {
      // yellow ring marks session targets
      ctx.strokeStyle=V.favoriteOuter; ctx.lineWidth=3.2;
      ctx.beginPath(); ctx.arc(x,y,dotR+3.4,0,7); ctx.stroke();
      ctx.strokeStyle=V.favorite; ctx.lineWidth=1.8;
      ctx.beginPath(); ctx.arc(x,y,dotR+3.4,0,7); ctx.stroke();
    }
    pts.push({x,y,id:o.id});
    if (labels && alt>0 && shortName(m) && (m.vmag ?? 99) <= labelMagMax) {
      labelCands.push({ x, y, alt,
        text: shortName(m), isHover: !!isHover,
        prio: m.vmag != null ? m.vmag : 12 });
    }
  }

  // place names greedily so they never stack on top of each other
  if (labels && labelCands.length) {
    ctx.font = "bold 11px Segoe UI";
    const placed = [];
    labelCands.sort((a,b) =>
      (b.isHover - a.isHover) || (a.prio - b.prio));
    for (const c of labelCands) {
      if (!c.isHover && c.alt <= 0) continue;
      const tx = Math.round(c.x) + 5, ty = Math.round(c.y) - 5;
      const w = ctx.measureText(c.text).width + 8, h = 14;
      let ok = true;
      for (const p of placed) {
        if (Math.abs(tx + w/2 - p.x) < (w + p.w)/2 &&
            Math.abs(ty - p.y) < h) { ok = false; break; }
      }
      if (!ok && !c.isHover) continue;
      placed.push({ x: tx + w/2, y: ty, w });
      // dark halo under bright text for contrast on the busy backdrop
      ctx.save();
      ctx.shadowColor = V.targetShadow;
      ctx.shadowBlur = V.labelBlur;
      ctx.fillStyle = c.isHover ? V.target : V.targetLabel;
      ctx.fillText(c.text, tx, ty);
      ctx.restore();
    }
  }

  if ($("#showMoon").checked) drawSunMoon(ctx,cx,cy,R,V);

  SKY_POINTS=pts;
  drawSkyInspector();
  const summary = $("#skyChartSummary");
  if (summary) {
    const iso = S.night.times_iso && S.night.times_iso[S.tIdx];
    const when = iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "the selected time";
    summary.textContent = `All-sky polar chart at ${when}; ${pts.length} catalog objects are plotted. Center is zenith, rim is horizon, and the selected horizon profile is shown as blocked sky.`;
  }
}

function drawSkyInspector() {
  const selected = $("#skySelectedTarget");
  const chart = $("#skySelectedAltitude");
  const visible = $("#skyVisibleNow");
  const horizon = $("#skyHorizonStatus");
  const when = $("#skySelectedTime");
  if (!selected || !chart || !visible || !S.night) return;
  const idx = Math.max(0, Math.min(S.tIdx, S.night.timeline.n - 1));
  const plotted = new Set(SKY_POINTS.map((p) => p.id));
  const candidates = S.night.objects.filter((o) => S.meta[o.id] && plotted.has(o.id));
  let object = candidates.find((o) => o.id === SKY_SELECTED_ID);
  if (!object) object = candidates.find((o) => statusOf(o, idx).code === "vis") || candidates[0] || null;
  if (object) SKY_SELECTED_ID = object.id;
  const iso = S.night.timeline.times_iso[idx];
  if (when) when.textContent = fmtHM(iso);
  if (!object) {
    selected.innerHTML = '<span class="hint">No catalog target is plotted at this magnitude limit.</span>';
    horizon.textContent = "";
    visible.innerHTML = '<p class="hint">No targets are visible at the selected time.</p>';
    drawSelectedAltitude(chart, null, idx);
    return;
  }
  const meta = S.meta[object.id];
  const alt = Number(object.alt?.[idx]);
  const az = Number(object.az?.[idx]);
  const state = statusOf(object, idx);
  const best = object.best && object.best[1] > object.best[0]
    ? `${fmtHM(S.night.timeline.times_iso[object.best[0]])}–${fmtHM(windowEndISO(S.night.timeline.times_iso, object.best[1], S.night.timeline.step_min))}`
    : "No clear window in the scoring span";
  selected.innerHTML = `<strong>${esc(meta.primary_name)}</strong><span class="sub">${esc(desigLine(meta))}</span>
    <div class="sky-selected-facts"><span>Altitude <b>${Number.isFinite(alt) ? `${alt.toFixed(1)}°` : "—"}</b></span>
    <span>Azimuth <b>${Number.isFinite(az) ? `${az.toFixed(0)}°` : "—"}</b></span>
    <span>Status <b class="statuspill st-${esc(state.code)}">${esc(state.text)}</b></span>
    <span>Best window <b>${esc(best)}</b></span></div>
    <button class="btn small" id="skySelectedDetail" type="button">Open target details</button>`;
  $("#skySelectedDetail")?.addEventListener("click", () => openDetail(object.id, { opener: chart }));
  if (horizon) horizon.textContent = `Selected time ${fmtHM(iso)} · minimum useful altitude ${S.minAlt.toFixed(0)}° · ${state.text}.`;
  drawSelectedAltitude(chart, object, idx);

  const visibleNow = candidates.filter((o) => statusOf(o, idx).code === "vis")
    .sort((a, b) => objScore(b) - objScore(a) || (S.meta[b.id].popularity || 0) - (S.meta[a.id].popularity || 0))
    .slice(0, 8);
  visible.innerHTML = visibleNow.length ? visibleNow.map((o, i) => {
    const m = S.meta[o.id];
    return `<button class="sky-visible-target${o.id === object.id ? " selected" : ""}" type="button" data-sky-target="${esc(o.id)}">
      <span class="mono">${String(i + 1).padStart(2, "0")}</span><strong>${esc(m.primary_name)}</strong><span class="mono">${Number(o.alt[idx]).toFixed(0)}°</span></button>`;
  }).join("") : '<p class="hint">No catalog targets are visible at this time.</p>';
  visible.querySelectorAll("[data-sky-target]").forEach((button) => button.addEventListener("click", () => {
    SKY_SELECTED_ID = button.dataset.skyTarget;
    renderSky();
  }));
}

function drawSelectedAltitude(canvas, object, idx) {
  const width = canvas.clientWidth || 300;
  const height = canvas.clientHeight || 100;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const style = getComputedStyle(document.documentElement);
  const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  const L = 27, R = 8, T = 8, B = 20, plotW = width - L - R, plotH = height - T - B;
  const y = (alt) => T + (90 - Math.max(-10, Math.min(90, alt))) / 100 * plotH;
  ctx.font = "9px Consolas, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  [0, 30, 60, 90].forEach((alt) => {
    const yy = y(alt);
    ctx.strokeStyle = token("--border", "#35434A");
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(width - R, yy); ctx.stroke();
    ctx.fillStyle = token("--dim", "#A8B5B8"); ctx.fillText(`${alt}°`, L - 4, yy);
  });
  if (!object?.alt?.length) return;
  const max = object.alt.length - 1;
  const x = (i) => L + i / Math.max(1, max) * plotW;
  ctx.setLineDash([4, 3]); ctx.strokeStyle = token("--warn", "#C38B32");
  ctx.beginPath(); ctx.moveTo(L, y(S.minAlt)); ctx.lineTo(width - R, y(S.minAlt)); ctx.stroke();
  ctx.setLineDash([]); ctx.beginPath();
  object.alt.forEach((alt, i) => i ? ctx.lineTo(x(i), y(alt)) : ctx.moveTo(x(i), y(alt)));
  ctx.strokeStyle = token("--accent", "#6DA89B"); ctx.lineWidth = 1.8; ctx.stroke();
  ctx.strokeStyle = token("--text", "#E8EFED"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x(idx), T); ctx.lineTo(x(idx), T + plotH); ctx.stroke();
  const times = S.night.timeline.times_iso;
  ctx.fillStyle = token("--dim", "#A8B5B8"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  if (times?.length) {
    ctx.fillText(fmtHM(times[0]), L, T + plotH + 5);
    ctx.fillText(fmtHM(times.at(-1)), width - R, T + plotH + 5);
  }
}

function moonAzAtIndex(i){
  return S.night.moon_az[i]!=null ? S.night.moon_az[i] : 0;
}
function sunAzAtIndex(i){
  return (S.night.sun_az && S.night.sun_az[i])!=null ? S.night.sun_az[i] : 0;
}

function drawSunMoon(ctx,cx,cy,R,V){
  const sa=S.night.sun_alt[S.tIdx];
  const ma=S.night.moon_alt[S.tIdx];
  // selected-time visibility: apparent altitude above the horizon (same rule
  // as the web chart and as the catalog dots below) - never a -6 floor
  if(aboveHorizon(sa)){
    const [x,y]=azToXY(cx,cy,R,sunAzAtIndex(S.tIdx),sa);
    ctx.fillStyle=V.sun;
    ctx.beginPath();ctx.arc(x,y,7,0,7);ctx.fill();
    ctx.strokeStyle=V.sunLine;ctx.lineWidth=1;
    ctx.beginPath();ctx.arc(x,y,11,0,7);ctx.stroke();
    ctx.fillStyle=V.sunLabel;ctx.font="10px Segoe UI";ctx.textAlign="center";
    ctx.fillText("Sun",x,y+22);ctx.textAlign="left";
  }
  if(aboveHorizon(ma)){
    const [x,y]=azToXY(cx,cy,R,moonAzAtIndex(S.tIdx),ma);
    ctx.fillStyle=V.moon;
    ctx.beginPath();ctx.arc(x,y,6.5,0,7);ctx.fill();
    ctx.fillStyle=V.moonLabel;ctx.font="10px Segoe UI";ctx.textAlign="center";
    ctx.fillText(Math.round(S.night.moon_illum*100)+"%",x,y+19);ctx.textAlign="left";
  }
}

function skyHover(ev){
  const cv=$("#skyCanvas");
  const rect=cv.getBoundingClientRect();
  const mx=ev.clientX-rect.left, my=ev.clientY-rect.top;
  const [bx,by]=screenToBase(mx,my,rect.width,rect.height);
  let best=null,bd=196;
  for(const p of SKY_POINTS){
    const dx=p.x-bx,dy=p.y-by,d=dx*dx+dy*dy;
    if(d<bd){bd=d;best=p;}
  }
  // DE-11: `best` is an element of the freshly REBUILT point array, so comparing
  // object identity against the previous render's hover was always true and every
  // mousemove repainted the whole chart. Compare the stable id - the tooltip
  // already compares by value.
  const key = best ? best.id : null;
  const prev = SKY_HOVER ? SKY_HOVER.id : null;
  if (key !== prev) { SKY_HOVER = best; renderSky(); }
  if(best){
    const o=S.night.objects.find(x=>x.id===best.id);
    const m=S.meta[best.id];
    const tip=$("#skyTip");
    tip.innerHTML=`<b>${esc(m.primary_name)}</b> <span style="color:#8b93a8">${esc(desigLine(m))}</span><br>`+
      `alt ${o.alt[S.tIdx].toFixed(1)}\u00B0 &middot; az ${o.az[S.tIdx].toFixed(0)}\u00B0<br>${statusOf(o,S.tIdx).text}`;
    tip.classList.remove("hidden");
    // place next to the cursor, flipped when it would leave the window
    tip.style.left=(ev.clientX+16)+"px";
    tip.style.top=(ev.clientY+14)+"px";
    const r=tip.getBoundingClientRect();
    if(r.right>window.innerWidth-6) tip.style.left=(ev.clientX-r.width-12)+"px";
    if(r.bottom>window.innerHeight-6) tip.style.top=(ev.clientY-r.height-10)+"px";
  } else $("#skyTip").classList.add("hidden");
}
