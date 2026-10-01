import {
  VISIBILITY_REASON,
  visibilityReason,
  summarizeVisibility,
  scoreNightTracks,
} from '../../core/index.js';

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(visibilityReason(25, 20, 30, null) === VISIBILITY_REASON.BLOCKED_BY_HORIZON,
  'a ceiling explains a blocked target');
assert(visibilityReason(15, 20, -90, null) === VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE,
  'a low target explains the minimum-altitude failure');
assert(visibilityReason(40, 20, -90, null, false) === VISIBILITY_REASON.OUTSIDE_SELECTED_WINDOW,
  'a sample outside the selected window is explained as such');
assert(summarizeVisibility({ visibleSamples: 3, stepMin: 6 }).visibleMinutes === 18,
  'summary reports usable minutes');

const obj = { id: 'x', ra_hours: 0, dec_deg: 0, otype: 'galaxy' };
const makeTracks = (values) => ({
  n: values.length - 1, count: values.length,
  alt: Float64Array.from(values), az: new Float64Array(values.length),
});
const stats = (values, minAlt, pano = null, range = [0, values.length - 1]) =>
  scoreNightTracks(makeTracks(values), [obj], 2460000, 1 / 1440,
    minAlt, pano, { range })[0];

const clear = stats([-5, 25, 30, -5], 20);
assert(clear.visibilityReason === VISIBILITY_REASON.VISIBLE,
  'a clear target reports visible');
assert(clear.bestRun === 2 && clear.bestEnd === clear.bestStart + 2 / 1440,
  'the usable interval has an explicit end boundary');
assert(clear.culminationAlt === 30 && clear.culminationJd === clear.transitJd,
  'culmination carries both time and altitude');
assert(clear.riseJd != null && clear.setJd != null, 'rise and set crossings are exposed');

const outside = stats([-5, 25, 30, -5], 20, null, [0, 0]);
assert(outside.visibilityReason === VISIBILITY_REASON.OUTSIDE_SELECTED_WINDOW,
  'a target usable only outside the selected span says so');
const blocked = stats([35, 35, 35], 20, {
  profile: new Array(720).fill(40), paintMode: 'obstacle', fov: 360, azOffset: 0,
});
assert(blocked.visibilityReason === VISIBILITY_REASON.BLOCKED_BY_HORIZON,
  'a target below the horizon profile names the obstruction');
const low = stats([10, 15, 10], 20);
assert(low.visibilityReason === VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE,
  'a target below the useful altitude names that threshold');
const never = stats([-10, -5, -8], 20);
assert(never.visibilityReason === VISIBILITY_REASON.NEVER_RISES,
  'a target that never rises is distinguished');

console.log('visibility explainability contract held');
