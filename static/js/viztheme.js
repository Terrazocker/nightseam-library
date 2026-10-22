/* Theme-owned palette for canvas visualizations.
 *
 * Canvas drawing cannot consume CSS variables directly. Keep the palette in
 * themes.css, read it once per render, and let the Sky Map use the same
 * semantic roles as the rest of the interface. This is presentation only: no
 * chart geometry belongs here. Fallbacks mirror the Workbench tokens so a
 * missing stylesheet still draws a legible map.
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
  galaxy: "#DF9398",
  open_cluster: "#FBD46F",
  globular: "#A0E992",
  nebula: "#87BEFC",
  planetary: "#AA8CFA",
  supernova_remnant: "#FBA457",
  asterism: "#6FD3C8",
  star_cloud: "#E7ECE8",
});

export function vizTheme() {
  const root = typeof getComputedStyle === "function"
    ? getComputedStyle(document.documentElement) : null;
  const read = (name, fallback) => root
    ? root.getPropertyValue(name).trim() || fallback : fallback;
  const types = {};
  for (const [type, variable] of Object.entries(TYPE_VARS)) {
    types[type] = read(variable, FALLBACK_TYPES[type]);
  }

  return {
    canvas: read("--viz-canvas-bg", "#0B1115"),
    gridStrong: read("--viz-grid-strong", "#304049"),
    dim: read("--viz-dim", "#9CAEB1"),
    text: read("--viz-text", "#E7ECE8"),
    fieldStar: read("--viz-field-star", "rgba(231,236,232,.30)"),
    constellationLine: read("--viz-constellation-line", "#73B6DB"),
    constellationStar: read("--viz-constellation-star", "rgba(231,236,232,.55)"),
    constellationLabel: read("--viz-constellation-label", "rgba(156,174,177,.78)"),
    objectOutline: read("--viz-object-outline", "rgba(0,0,0,.65)"),
    types,
  };
}
