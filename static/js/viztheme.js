/* Theme-owned palette for canvas visualizations.
 *
 * Canvas drawing cannot consume CSS variables directly. Keep the palette in
 * themes.css, read it once per render, and let every chart use the same
 * semantic roles. This is presentation only: no chart geometry or planning
 * state belongs here.
 */

const TYPE_VARS = Object.freeze({
  galaxy: "--viz-type-galaxy",
  open_cluster: "--viz-type-open-cluster",
  globular: "--viz-type-globular",
  nebula: "--viz-type-nebula",
  planetary: "--viz-type-planetary",
  supernova_remnant: "--viz-type-supernova-remnant",
  asterism: "--viz-type-asterism",
  star_cloud: "--viz-type-star-cloud",
});

const FALLBACK_TYPES = Object.freeze({
  galaxy: "#ffd166",
  open_cluster: "#7cc4ff",
  globular: "#9be0ff",
  nebula: "#7bf1a8",
  planetary: "#8fe3d9",
  supernova_remnant: "#ff8fa3",
  asterism: "#c9b8ff",
  star_cloud: "#e0e6ff",
});

export function vizTheme() {
  const root = typeof getComputedStyle === "function"
    ? getComputedStyle(document.documentElement) : null;
  const read = (name, fallback) => root
    ? root.getPropertyValue(name).trim() || fallback : fallback;
  const number = (name, fallback) => {
    const value = Number(read(name, String(fallback)));
    return Number.isFinite(value) ? value : fallback;
  };
  const dash = (name, fallback) => {
    const values = read(name, fallback.join(" ")).split(/[\s,]+/)
      .map(Number).filter(value => Number.isFinite(value) && value >= 0);
    return values.length ? values : fallback;
  };
  const types = {};
  for (const [type, variable] of Object.entries(TYPE_VARS)) {
    types[type] = read(variable, FALLBACK_TYPES[type]);
  }

  return {
    canvas: read("--viz-canvas-bg", "#081022"),
    grid: read("--viz-grid", "rgba(124,196,255,.16)"),
    gridStrong: read("--viz-grid-strong", "#39445f"),
    gridSub: read("--viz-grid-sub", "rgba(124,196,255,.10)"),
    axis: read("--viz-axis", "rgba(127,151,184,.85)"),
    dim: read("--viz-dim", "#98a2b8"),
    text: read("--viz-text", "#e9edf6"),
    fieldStar: read("--viz-field-star", "rgba(165,175,200,.32)"),
    constellation: read("--viz-constellation", "rgba(150,160,185,.30)"),
    constellationStar: read("--viz-constellation-star", "rgba(185,194,220,.50)"),
    constellationLabel: read("--viz-constellation-label", "rgba(139,147,168,.62)"),
    obstacleFill: read("--viz-obstacle-fill", "rgba(255,107,107,.18)"),
    obstacleStroke: read("--viz-obstacle-stroke", "rgba(255,107,107,.75)"),
    blockedDot: read("--viz-blocked-dot", "rgba(255,93,93,.40)"),
    ceiling: read("--viz-ceiling", "#98a2b8"),
    ceilingTop: read("--viz-ceiling-top", "#ffd166"),
    assumedHatch: read("--viz-assumed-hatch", "rgba(210,225,255,.28)"),
    minFill: read("--viz-min-fill", "rgba(120,140,180,.10)"),
    minStroke: read("--viz-min-stroke", "rgba(150,165,200,.55)"),
    minLabel: read("--viz-min-label", "rgba(150,165,200,.85)"),
    below: read("--viz-below", "#4a5468"),
    bad: read("--viz-bad", "#ff6b6b"),
    warn: read("--viz-warn", "#ffb86b"),
    good: read("--viz-good", "#7bf1a8"),
    goodFill: read("--viz-good-fill", "rgba(53,208,127,.09)"),
    moon: read("--viz-moon", "#e6e9ff"),
    moonLine: read("--viz-moon-line", "rgba(143,211,255,.85)"),
    moonLabel: read("--viz-moon-label", "#555e73"),
    moonMarker: read("--viz-moon-marker", "#ffffff"),
    moonMarkerShadow: read("--viz-moon-marker-shadow", "#081022"),
    moonMarkerOutline: read("--viz-moon-marker-outline", "#ffffff"),
    sun: read("--viz-sun", "#ffd166"),
    sunLine: read("--viz-sun-line", "#ffd166"),
    sunLabel: read("--viz-sun-label", "#8a763a"),
    target: read("--viz-target", "#e9edf6"),
    targetLabel: read("--viz-target-label", "#e9edf6"),
    targetSelected: read("--viz-target-selected", read("--viz-target", "#e9edf6")),
    targetSecondary: read("--viz-target-secondary", read("--viz-target", "#e9edf6")),
    targetBlocked: read("--viz-target-blocked", read("--viz-bad", "#ff6b6b")),
    targetShadow: read("--viz-target-shadow", "rgba(0,0,0,.9)"),
    favorite: read("--viz-favorite", "#ffd700"),
    favoriteOuter: read("--viz-favorite-outer", "rgba(0,0,0,.55)"),
    crosshair: read("--viz-crosshair", "rgba(255,255,255,.5)"),
    crosshairFill: read("--viz-crosshair-fill", "#0b1526"),
    crosshairStroke: read("--viz-crosshair-stroke", "#8fd3ff"),
    objectOutline: read("--viz-object-outline", "rgba(0,0,0,.5)"),
    moonBand: read("--viz-moon-band", "rgba(143,211,255,.12)"),
    horizonLine: read("--viz-horizon-line", "rgba(255,255,255,.22)"),
    twilightShade: read("--viz-twilight-shade", "rgba(90,130,255,.07)"),
    twilight: [1, 2, 3, 4, 5, 6].map(i =>
      read(`--viz-twilight-${i}`, "#0a2036")),
    sunBlur: number("--viz-sun-glow-blur", 0),
    moonBlur: number("--viz-moon-glow-blur", 0),
    labelBlur: number("--viz-label-shadow-blur", 4),
    guideMinDash: dash("--viz-guide-min-dash", [8, 4]),
    guideMoonDash: dash("--viz-guide-moon-dash", [4, 3, 1, 3]),
    guideCeilingDash: dash("--viz-guide-ceiling-dash", [3, 4]),
    guideCeilingTopDash: dash("--viz-guide-ceiling-top-dash", [10, 3, 2, 3]),
    types,
  };
}

/** Draw a compact white phase disc on the altitude graph's Moon track. */
export function drawMoonPhaseMarker(ctx, x, y, phase, theme) {
  const radius = 4.6;
  const rawIllumination = Number(phase && phase.illumination);
  const illumination = Number.isFinite(rawIllumination)
    ? Math.max(0, Math.min(1, rawIllumination)) : 0.5;
  const waxing = !phase || phase.waxing !== false;
  const terminator = (2 * illumination - 1) * radius;

  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = theme.moonMarkerShadow;
  ctx.fill();

  if (illumination > 0.005) {
    ctx.fillStyle = theme.moonMarker;
    ctx.beginPath();
    if (illumination >= 0.995) {
      ctx.arc(x, y, radius, 0, Math.PI * 2);
    } else {
      const ellipseRadius = Math.max(0.08, Math.abs(terminator));
      ctx.moveTo(x, y - radius);
      if (waxing) {
        ctx.arc(x, y, radius, -Math.PI / 2, Math.PI / 2, false);
        ctx.ellipse(x, y, ellipseRadius, radius, 0,
          Math.PI / 2, -Math.PI / 2, terminator < 0);
      } else {
        ctx.arc(x, y, radius, -Math.PI / 2, Math.PI / 2, true);
        ctx.ellipse(x, y, ellipseRadius, radius, 0,
          Math.PI / 2, -Math.PI / 2, terminator > 0);
      }
      ctx.closePath();
    }
    ctx.fill();
  }
  ctx.restore();

  ctx.strokeStyle = theme.moonMarkerOutline;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.stroke();
}
