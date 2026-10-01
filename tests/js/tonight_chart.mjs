import assert from "node:assert/strict";
import { overviewGridRange, strokeBlockedRuns } from "../../static/js/tonight-chart.js";

const calls = [];
const ctx = {
  beginPath() { calls.push(["begin"]); },
  moveTo(x, y) { calls.push(["move", x, y]); },
  lineTo(x, y) { calls.push(["line", x, y]); },
  stroke() { calls.push(["stroke"]); },
};
const alt = [12, 20, 35, -2, 50, 12, 70];
const ceil = [-90, 25, 25, -90, 60, -90, -90];
const ceilTop = [null, null, null, null, null, null, 60];
const point = (i) => i;

strokeBlockedRuns(ctx, alt, ceil, ceilTop, 15,
  0, alt.length - 1, point, point, "red");

assert.deepEqual(calls.filter(([name]) => name === "move"), [
  ["move", 1, 20], ["move", 4, 50], ["move", 6, 70],
], "each separated blocked interval starts a new stroke");
assert.equal(calls.filter(([name]) => name === "stroke").length, 3,
  "each horizon-blocked run is stroked independently");
assert(!calls.some((call) => call[0] === "move" && (call[1] === 0 || call[1] === 3 || call[1] === 5)),
  "below-minimum and below-horizon samples must not be red");

const fullDay = overviewGridRange(241, [0, 240], 188);
assert.deepEqual(fullDay, { lo: 0, hi: 240, selected: 188 },
  "full-day overview keeps the actual selected sample instead of clamping it to the dark window");
