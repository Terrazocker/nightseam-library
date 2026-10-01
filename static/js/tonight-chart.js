import { visibilityReason } from "./core.js";

/* Small canvas helpers for the Tonight overview, kept pure for direct tests. */

export function overviewGridRange(gridLength, allowedRange, selectedIndex) {
  const last = Math.max(0, Math.trunc(Number(gridLength) || 0) - 1);
  const lo = Math.max(0, Math.min(last, Math.trunc(Number(allowedRange?.[0]) || 0)));
  const hi = Math.max(lo, Math.min(last,
    Math.trunc(Number(allowedRange?.[1]) || 0)));
  const selected = Math.max(0, Math.min(last,
    Math.trunc(Number(selectedIndex) || 0)));
  return { lo, hi, selected };
}

export function strokeBlockedRuns(ctx, alt, ceil, ceilTop, minAlt,
  lo, hi, xAt, yAt, color) {
  ctx.strokeStyle = color;
  let open = false;
  for (let i = lo; i <= hi; i++) {
    const value = Number(alt[i]);
    const lower = Number.isFinite(Number(ceil?.[i])) ? Number(ceil[i]) : -90;
    const upper = ceilTop?.[i] == null ? null : Number(ceilTop[i]);
    const blocked = Number.isFinite(value) &&
      visibilityReason(value, minAlt, lower, upper, true) === "blocked_by_horizon";
    if (!blocked) {
      if (open) ctx.stroke();
      open = false;
      continue;
    }
    const x = xAt(i), y = yAt(value);
    if (!open) {
      ctx.beginPath();
      ctx.moveTo(x, y);
      open = true;
    } else ctx.lineTo(x, y);
  }
  if (open) ctx.stroke();
}
