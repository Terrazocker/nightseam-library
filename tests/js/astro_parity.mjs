/*
 * Node harness for the Python <-> JavaScript astronomy parity test.
 *
 * Usage:  node tests/js/astro_parity.mjs <desktop|web|window>
 *         (reads a JSON array of case objects on stdin, writes JSON on stdout)
 *
 *   desktop / web : precession + alt/az (+ gmst, sun, moon for web)
 *   window        : static/js/derive.js's longestRunJS / windowEndISO - the
 *                   clear-window contract shared with app/nightcalc.py
 *
 * tests/test_js_parity.py feeds the same inputs to app/astro.py and to this
 * harness, then compares the numbers. It exists because the three engines
 * (Python, static/js/astro.js, web/astro.js) agreed with each other while all
 * three were wrong (see tests/test_astro.py for the reference values that pin
 * the actual mathematics).
 */
const engine = process.argv[2];
if (!["desktop", "web", "window", "darkwindow", "pick", "crosszone",
     "scoring", "ceil", "size", "status", "frame", "advice", "minalt",
     "shortcut", "nightstats", "mag", "skyzoom", "abovehorizon",
     "scrubwindow", "scrubgrid", "timezone", "tier", "wikititles"].includes(engine)) {
  console.error("usage: node astro_parity.mjs <engine>");
  process.exit(2);
}

/* zone helpers shared by the cross-zone and darkwindow engines */
const tzOffMin = (zone, date) => {
  if (!zone) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, timeZoneName: "longOffset", hour12: false,
    }).formatToParts(date);
    const p = parts.find((x) => x.type === "timeZoneName");
    if (!p || !p.value) return null;
    if (p.value === "GMT" || p.value === "UTC") return 0;
    const m = p.value.match(/GMT([+-])(\d{1,2}):?(\d{2})?/);
    if (!m) return null;
    return (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0));
  } catch { return null; }
};
const wallToUtc = (date, zone) => {
  const off = tzOffMin(zone, date);
  if (off == null) return date;
  return new Date(date.getTime() - (off + date.getTimezoneOffset()) * 60000);
};

const mod = engine === "desktop"
  ? await import(new URL("../../static/js/astro.js", import.meta.url).href)
  : engine === "window"
    ? await import(new URL("../../static/js/derive.js", import.meta.url).href)
    : await import(new URL("../../web/astro.js", import.meta.url).href);

const input = await new Promise((resolve, reject) => {
  let buf = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (c) => { buf += c; });
  process.stdin.on("end", () => resolve(buf));
  process.stdin.on("error", reject);
});

const cases = JSON.parse(input);

if (engine === "size") {
  // display formatting: the web formatter (web/format.js) must produce exactly
  // what the desktop's static/js/util.js::fmtSize does - catalog sizes are
  // arcminutes shown in ONE user-chosen unit, single-axis entries ("330",
  // "1.4") included. cases: [[size_am, unit], ...]
  const [webFmt, deskUtil] = await Promise.all([
    import(new URL("../../web/format.js", import.meta.url).href),
    import(new URL("../../static/js/util.js", import.meta.url).href),
  ]);
  process.stdout.write(JSON.stringify(cases.map(([s, unit]) => {
    const web = webFmt.fmtSize(s, unit);
    const desktop = deskUtil.fmtSize(s, unit);
    return { size: s, unit, web, desktop, ok: web === desktop };
  })));
} else if (engine === "mag") {
  // magnitude-limit rule: desktop derive.js::magVisible vs web format.js
  const [desk, webFmt] = await Promise.all([
    import(new URL("../../static/js/derive.js", import.meta.url).href),
    import(new URL("../../web/format.js", import.meta.url).href),
  ]);
  process.stdout.write(JSON.stringify(cases.map((c) => ({
    vmag: c.vmag, limit: c.limit, max: c.max,
    desktop: desk.magVisible(c.vmag, c.limit, c.max),
    web: webFmt.magVisible(c.vmag, c.limit, c.max),
  }))));
} else if (engine === "abovehorizon") {
  // selected-time visibility rule: desktop derive.js vs web astro.js
  const [desk, web] = await Promise.all([
    import(new URL("../../static/js/derive.js", import.meta.url).href),
    import(new URL("../../web/astro.js", import.meta.url).href),
  ]);
  process.stdout.write(JSON.stringify(cases.map((c) => ({
    alt: c.alt,
    desktop: desk.aboveHorizon(c.alt),
    web: web.aboveHorizon(c.alt),
  }))));
} else if (engine === "skyzoom") {
  // square-zoom geometry: desktop derive.js vs web format.js. Case:
  // {zoom, W, H, fx, fy, sx, sy} - fx/fy null = centered.
  const [desk, webFmt] = await Promise.all([
    import(new URL("../../static/js/derive.js", import.meta.url).href),
    import(new URL("../../web/format.js", import.meta.url).href),
  ]);
  const one = (m, c) => ({
    matrix: m.skyViewMatrix(c.zoom, c.W, c.H, c.fx, c.fy),
    base: m.skyScreenToBase(c.zoom, c.W, c.H, c.fx, c.fy, c.sx, c.sy),
    focus: m.skyClampFocus(c.W, c.H, c.fx, c.fy),
    zoom: m.skyClampZoom(c.zoom),
  });
  process.stdout.write(JSON.stringify(cases.map((c) => ({
    W: c.W, H: c.H, zoom: c.zoom,
    desktop: one(desk, c), web: one(webFmt, c),
  }))));
} else if (engine === "pick") {
  // unit-test the twilight-preference rule (cascade + polar fallback) with
  // synthetic night info; TZ=UTC makes the fallback times comparable with
  // app/planning.py's local_utc() output
  const info = (flags) => ({
    sunset: flags.sun ? new Date(2026, 5, 21, 19, 0, 0) : null,
    sunrise: flags.sun ? new Date(2026, 5, 22, 4, 0, 0) : null,
    civilDusk: flags.civil ? new Date(2026, 5, 21, 20, 0, 0) : null,
    civilDawn: flags.civil ? new Date(2026, 5, 22, 3, 0, 0) : null,
    nauticalDusk: flags.naut ? new Date(2026, 5, 21, 21, 0, 0) : null,
    nauticalDawn: flags.naut ? new Date(2026, 5, 22, 2, 0, 0) : null,
    astroDusk: flags.astro ? new Date(2026, 5, 21, 22, 0, 0) : null,
    astroDawn: flags.astro ? new Date(2026, 5, 22, 1, 0, 0) : null,
  });
  const day = new Date(2026, 5, 21, 12, 0, 0);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    const w = mod.pickDarkWindow(info(c), day);
    return {
      level: w.level,
      dusk: w.dusk ? w.dusk.toISOString() : null,
      dawn: w.dawn ? w.dawn.toISOString() : null,
      zoneOffsetMin: day.getTimezoneOffset(),
    };
  })));
} else if (engine === "darkwindow") {
  // real night windows for a site that sits in the harness's own zone; when a
  // case carries `tz`, the SITE's zone is used for the noon anchor (localNoon),
  // so the polar fallback is built in the site's local time like planning.py
  const out = cases.map((c) => {
    const [y, m, d] = c.date.split("-").map(Number);
    const noon = new Date(y, m - 1, d, 12, 0, 0);        // browser-local noon
    const anchor = c.tz ? wallToUtc(noon, c.tz) : noon;  // site-local noon
    const info = mod.nightInfo(noon, c.lat, c.lon);
    const w = mod.pickDarkWindow(info, noon, anchor);
    return {
      level: w.level,
      dusk: w.dusk ? w.dusk.toISOString() : null,
      dawn: w.dawn ? w.dawn.toISOString() : null,
      zoneOffsetMin: noon.getTimezoneOffset(),
    };
  });
  process.stdout.write(JSON.stringify({ windows: out }));
} else if (engine === "crosszone") {
  /* A site in another timezone than the browser.
   *
   * Case fields: {date, lat, lon, tz} where tz is the SITE's IANA zone and the
   * PROCESS zone (TZ) is the browser's. Prints the window the web resolves with
   * the SITE-anchored scan (what app.js passes now via siteNoon()) and, for
   * documentation, the window the old browser-anchored scan produced. */
  const span = (w) => (w.dusk && w.dawn)
    ? Math.round((w.dawn - w.dusk) / 3600000 * 10) / 10 : null;
  const out = cases.map((c) => {
    const [y, m, d] = c.date.split("-").map(Number);
    const wallNoon = new Date(y, m - 1, d, 12, 0, 0);   // site wall clock
    const info = mod.nightInfo(wallNoon, c.lat, c.lon);
    const browser = mod.pickDarkWindow(info, wallNoon);
    const siteInfo = mod.nightInfo(wallNoon, c.lat, c.lon, wallToUtc(wallNoon, c.tz));
    const site = mod.pickDarkWindow(siteInfo, wallNoon);
    return {
      site: { level: site.level, dusk: site.dusk.toISOString(),
              dawn: site.dawn.toISOString(), spanH: span(site) },
      browserAnchored: { level: browser.level, dusk: browser.dusk.toISOString(),
                         dawn: browser.dawn.toISOString(), spanH: span(browser) },
      zoneOffsetMin: wallNoon.getTimezoneOffset(),
      siteOffsetMin: -tzOffMin(c.tz, wallNoon),      // minutes EAST of UTC
    };
  });
  process.stdout.write(JSON.stringify(out));
} else if (engine === "scoring") {
  /* Compare the moon-aware scoring model of BOTH clients on one input grid.
   * The curves live in static/js/derive.js (desktop) and web/astro.js (web);
   * this mode prints both so the Python test can assert they are identical. */
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  const web = await import(new URL("../../web/astro.js", import.meta.url).href);
  const pairs = {
    desktop: {
      MOON_TYPE_FACTOR: desk.MOON_TYPE_FACTOR,
      moonSensitivity: (ot) => desk.moonSensitivity(ot),
      moonMinDist: (f, a) => desk.moonMinDist(f, a),
      effectiveMoonGlow: (i, s, f, a) => desk.effectiveMoonGlow(i, s, f, a),
      moonPenalty: (g, t, b) => desk.moonPenalty(g, t, b),
      bortleMoonFactor: (b) => desk.bortleMoonFactor(b),
    },
    web: {
      MOON_TYPE_FACTOR: web.MOON_TYPE_FACTOR,
      moonSensitivity: (ot) => web.moonSensitivity(ot),
      moonMinDist: (f, a) => web.moonMinDist(f, a),
      effectiveMoonGlow: (i, s, f, a) => web.effectiveMoonGlow(i, s, f, a),
      moonPenalty: (g, t, b) => web.moonPenalty(g, t, b),
      bortleMoonFactor: (b) => web.bortleMoonFactor(b),
    },
  };
  const out = { MOON_TYPE_FACTOR: {}, sensitivity: [], minDist: [], glow: [], penalty: [], bortle: [] };
  for (const [k, v] of Object.entries(pairs.desktop.MOON_TYPE_FACTOR)) {
    out.MOON_TYPE_FACTOR[k] = [v, pairs.web.MOON_TYPE_FACTOR[k]];
  }
  for (const ot of Object.keys(pairs.desktop.MOON_TYPE_FACTOR).concat(["unknown"])) {
    out.sensitivity.push([ot, pairs.desktop.moonSensitivity(ot), pairs.web.moonSensitivity(ot)]);
  }
  for (const f of ["narrowband", "oiii", "lrgb", "other"]) {
    for (const age of [0, 3.7, 7.38, 11, 14.76, 14.9]) {
      out.minDist.push([f, age, pairs.desktop.moonMinDist(f, age), pairs.web.moonMinDist(f, age)]);
      for (const illum of [0, 0.25, 0.62, 1]) {
        for (const sep of [0, 20, 45, 90, 120, 179.9]) {
          out.glow.push([f, age, illum, sep,
            pairs.desktop.effectiveMoonGlow(illum, sep, f, age),
            pairs.web.effectiveMoonGlow(illum, sep, f, age)]);
        }
      }
    }
  }
  for (const g of [0, 0.3, 1]) {
    for (const t of [0.5, 1.0, 1.6]) {
      for (const b of [0.3, 0.64, 1.12]) {
        out.penalty.push([g, t, b, pairs.desktop.moonPenalty(g, t, b), pairs.web.moonPenalty(g, t, b)]);
      }
    }
  }
  for (let bortle = 1; bortle <= 9; bortle++) {
    out.bortle.push([bortle, pairs.desktop.bortleMoonFactor(bortle), pairs.web.bortleMoonFactor(bortle)]);
  }
  process.stdout.write(JSON.stringify(out));
} else if (engine === "ceil") {
  /* Horizon lookup parity: web/horizon.js::ceilFor vs app/nightcalc.ceil_lookup.
   * Case: {mode, profile, profileTop, az} - the profile arrays are 720 numbers
   * or empty, exactly as the API/pano editor produce them. */
  const H = await import(new URL("../../web/horizon.js", import.meta.url).href);
  const out = cases.map((c) => {
    const rec = {
      paintMode: c.mode,
      profile: c.profile,
      profileTop: c.profileTop,
      blockAbove: !!c.blockAbove,
      fov: c.fov == null ? 360 : c.fov,
      azOffset: c.azOffset || 0,
    };
    const r = H.ceilFor(rec, c.az);
    return { ceil: r.ceil, ceilTop: r.ceilTop };
  });
  process.stdout.write(JSON.stringify(out));
} else if (engine === "timezone") {
  /* Zone offsets from core/time.js. Case: {tz, ms} -> offset at an instant,
   * {tz, date} -> offset at local noon of YYYY-MM-DD (minutes east of UTC), or
   * {kind:"wall", tz, ms} -> wall<->UTC shift round trip (machine offset 0). */
  const T = await import(new URL("../../core/time.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    if (c.kind === "wall") {
      const off = T.offsetEastMin(c.tz, c.ms);
      const wall = T.wallFromUtcMs(c.ms, off, 0);
      return { wall, back: T.utcFromWallMs(wall, off, 0) };
    }
    return c.date != null
      ? T.zoneOffsetAtLocalNoon(c.tz, c.date)
      : T.offsetEastMin(c.tz, c.ms);
  })));
} else if (engine === "status") {
  /* E4: desktop derive.js statusOf must mirror the vis[] visibility predicate
   * (incl. the sky-mode ceil_top ceiling). Case: {alt, ceil, ceilTop, minAlt}.
   * ceilTop absent (undefined) means "no upper ceiling" (90.5 sentinel). */
  const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    S.minAlt = c.minAlt;
    const o = { alt: [c.alt], ceil: [c.ceil],
                ceil_top: c.ceilTop == null ? undefined : [c.ceilTop] };
    return { code: desk.statusOf(o, 0).code };
  })));
} else if (engine === "frame") {
  /* E3: frame coverage - web/format.js framePct (object span / frame width,
   * arcminutes) vs the desktop's frameCoverage (object/field, worse of both
   * axes). Case: {side, sizeAm, fovArcmin} or {side:"desktop", sizeAm, fovW,
   * fovH}. Values above 100 = does not fit in one frame. */
  const webFmt = await import(new URL("../../web/format.js", import.meta.url).href);
  const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    if (c.side === "web") return { pct: webFmt.framePct(c.sizeAm, c.fovArcmin) };
    S.cfg = { rigs: [{ id: "r", fov_w: c.fovW, fov_h: c.fovH }], active_rig_id: "r" };
    const cov = desk.frameCoverage({ size_am: c.sizeAm });
    return cov ? { pct: cov.pct, fits: cov.fits, frames: +cov.frames.toFixed(2) }
               : { pct: null };
  })));
} else if (engine === "wikititles") {
  /* Wikipedia page candidates (0.6.32): the stored `wiki` title if any, then the
   * full "Messier N" (a bare "M101" is a DISAMBIGUATION page - motorways,
   * highways), then the NGC/IC designation, then the display name. Deduped, and
   * never empty for a real object. The same list is built in app/images.py for
   * the desktop, whose chain then also skips disambiguation pages. */
  const core = await import(new URL("../../web/core.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => core.wikiTitles(c.obj))));
} else if (engine === "tier") {
  /* Popularity TIERS (0.6.30): the catalog exposes a letter, never the score.
   * The bands are RANK bands, because the score distribution is far too
   * heavy-tailed for score cut-offs (rank 1 = 166,350; rank 1,500 = 19; the last
   * ~4,000 rows sit at 1-4). Case: {rank} -> {tier}, or
   * {objs:[{id,popularity}]} -> {tiers:{id:tier}, order}. Imported through the
   * SHIPPED stub, so this also proves the baked core carries the helpers. */
  const core = await import(new URL("../../web/core.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    if (c.objs) {
      const m = core.assignPopularityTiers(c.objs);
      return { tiers: Object.fromEntries(m), order: core.TIER_ORDER };
    }
    return { tier: core.tierForRank(c.rank) };
  })));
} else if (engine === "advice") {
  /* E7: the filter recommendation model - desktop derive.js filterAdvice vs
   * web/horizon.js filterAdvice must agree on (ok, text). Case: {otype, bb,
   * ha, oiii} = filter ownership. */
  const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  const H = await import(new URL("../../web/horizon.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    S.cfg = { filters: { broadband: !!c.bb, narrowband_ha: !!c.ha,
                         narrowband_oiii: !!c.oiii } };
    const d = desk.filterAdvice({ otype: c.otype });
    const w = H.filterAdvice(c.otype, { fltBb: !!c.bb, fltHa: !!c.ha,
                                        fltOiii: !!c.oiii });
    return { desk: d ? { ok: d.cls === "ok", text: d.text } : null,
             web: w ? { ok: w.ok, text: w.text } : null };
  })));
} else if (engine === "minalt") {
  /* E5: the authoritative min-altitude rule - desktop derive.js
   * effectiveMinAlt vs web/horizon.js effectiveMinAlt. Case: {bortle,
   * explicit, value}. */
  const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  const H = await import(new URL("../../web/horizon.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    S.cfg = { bortle: c.bortle,
              min_useful_alt: c.explicit ? c.value : null,
              min_useful_alt_explicit: !!c.explicit };
    const d = desk.effectiveMinAlt(S.cfg);
    H.setPrefs({ minAlt: c.explicit ? c.value : null, bortle: c.bortle });
    return { desk: d, web: H.effectiveMinAlt() };
  })));
} else if (engine === "shortcut") {
  /* E8: keyboard scrub steps - desktop derive.js vs web/astro.js */
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  const web = await import(new URL("../../web/astro.js", import.meta.url).href);
  process.stdout.write(JSON.stringify({
    desk: [desk.SCRUB_STEP_MIN, desk.SCRUB_SHIFT_STEP_MIN],
    web: [web.SCRUB_STEP_MIN, web.SCRUB_SHIFT_STEP_MIN],
  }));
} else if (engine === "nightstats") {
  /* 11.2: the shared web scoring loop (web/nightrank.js) - absolute values.
   * Case: {obj:{ra_hours,dec_deg}, jd0, jd1, step, minAlt, lat, lon,
   *        pano?, ctx?}. */
  const NS = await import(new URL("../../web/nightrank.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    const st = NS.nightStats(c.obj, c.jd0, c.jd1, c.step, c.minAlt,
                             c.pano || null, c.lat, c.lon, c.ctx || {});
    return { n: st.n, visN: st.visN, blocked: st.blocked,
             bestRun: st.bestRun, bestStart: st.bestStart,
             pct: st.pct, blockedPct: st.blockedPct,
             sClassic: +st.sClassic.toFixed(6), sMoon: +st.sMoon.toFixed(6),
             maxAlt: +st.maxAlt.toFixed(6) };
  })));
} else if (engine === "window") {
  const out = cases.map((c) => {
    const best = mod.longestRunJS(c.flags);
    return {
      best,
      endISO: best ? mod.windowEndISO(c.times, best[1], c.step) : null,
    };
  });
  process.stdout.write(JSON.stringify(out));
} else if (engine === "scrubwindow") {
  /* Scrub-range policy at BOTH states: desktop derive.js vs web/astro.js.
   * Case: {viewFullDay, nightStart, nightEnd, fullStart, fullEnd, probes[]}
   * with epoch-ms bounds; returns the chosen [start,end] and the clamped
   * probes, so the Python side can check the planner window AND parity. */
  const desk = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  const web = await import(new URL("../../web/astro.js", import.meta.url).href);
  const one = (m, c) => {
    const b = m.scrubBounds(c.viewFullDay, c.nightStart, c.nightEnd,
                            c.fullStart, c.fullEnd);
    return {
      start: b.start, end: b.end,
      clamps: (c.probes || []).map((t) => m.scrubClamp(t, b.start, b.end)),
    };
  };
  process.stdout.write(JSON.stringify(cases.map((c) => ({
    desk: one(desk, c), web: one(web, c),
  }))));
} else if (engine === "scrubgrid") {
  /* ITEM A: the REAL desktop 24 h grid. Given the timeline + crossings Python
   * computed, return the SCRUB index range (sunset..sunrise, or the whole grid
   * with view_full_day) and the SCORING index range (dusk..dawn) that
   * static/js/derive.js selects. */
  const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
  const D = await import(new URL("../../static/js/derive.js", import.meta.url).href);
  process.stdout.write(JSON.stringify(cases.map((c) => {
    S.night = {
      timeline: { times_iso: c.times, n: c.times.length, step_min: c.step },
      dusk: c.dusk, dawn: c.dawn, sunset: c.sunset, sunrise: c.sunrise,
    };
    S.cfg = { view_full_day: !!c.viewFullDay };
    const [lo, hi] = D.scrubWindowRange();
    const [sl, sh] = D.nightIndexRange();
    return { lo, hi, scoringLo: sl, scoringHi: sh };
  })));
} else {
  const out = cases.map((c) => {
    const r = {};
    const [ra, dec] = mod.precessJ2000ToDate(c.ra, c.dec, c.jd);
    r.ra = ra;
    r.dec = dec;
    if (typeof mod.precessDateToJ2000 === "function") {
      const [ira, idec] = mod.precessDateToJ2000(c.ra, c.dec, c.jd);
      r.inv_ra = ira;
      r.inv_dec = idec;
    }
    if (typeof mod.gmstDeg === "function") r.gmst = mod.gmstDeg(c.jd);

    if (engine === "web") {
      const [alt, az] = mod.altaz(c.ra, c.dec, c.jd, c.lat, c.lon);
      r.alt = alt;
      r.az = az;
      const [sra, sdec] = mod.sunRaDec(c.jd);
      r.sun_ra = sra;
      r.sun_dec = sdec;
      const [mra, mdec] = mod.moonRaDec(c.jd);
      r.moon_ra = mra;
      r.moon_dec = mdec;
      // Selected-time sky altitude via the SAME wrappers app.js uses - the
      // chart used to pass ra*15 (degrees as hours), which only shows up here.
      r.sun_alt = mod.sunAltAz(c.jd, c.lat, c.lon)[0];
      r.moon_alt = mod.moonAltAz(c.jd, c.lat, c.lon)[0];
      // 11.1: moon illumination/age + julianDay round-trip parity
      r.illum = mod.moonIllumination(c.jd);
      r.age = mod.moonAge(c.jd);
      r.jd_rt = mod.julianDay(mod.dateFromJD(c.jd));
    } else {
      // the desktop engine takes J2000 coords and precesses once per object at
      // the grid midpoint (derive.js: synthesizeAdvancedTracks) - mirror that
      // usage instead of handing raw J2000 coords to the sample loop
      const [pr, pd] = mod.precessJ2000ToDate(c.ra, c.dec, c.jd);
      const frame = mod.altazFrame(c.lat, c.lon, [c.jd]);
      const [alt, az] = mod.altazFrameApply(frame, pr, pd, 0);
      r.alt = alt;
      r.az = az;
    }
    return r;
  });
  process.stdout.write(JSON.stringify(out));
}
