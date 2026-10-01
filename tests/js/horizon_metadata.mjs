import {
  BINS, HORIZON_LANDMARK_KINDS, NO_BLOCK,
  normalizeHorizonLandmarks, normalizeHorizonProvenance,
  normalizeHorizonConfidence, profileDifference,
} from '../../core/horizon.js';

const fail = (m) => { throw new Error(m); };
const landmarks = normalizeHorizonLandmarks([
  { id: 'roof', name: 'East roof', kind: 'roof', azimuth: 361, altitude: 12.345, notes: 'visible' },
  { id: 'bad id/quote', name: 'Pole', azimuth: -10, altitude: 100 },
  { name: '', azimuth: 2, altitude: 3 },
]);
if (landmarks.length !== 2) fail('landmark normalization should retain two valid records');
if (landmarks[0].azimuth !== 1 || landmarks[0].altitude !== 12.35) fail('landmark coordinates should wrap and round');
if (!HORIZON_LANDMARK_KINDS.includes(landmarks[1].kind)) fail('unknown landmark kind should normalize');

const provenance = normalizeHorizonProvenance({ source: 'plate_solve', capturedAt: '2026-09-20', notes: '<unsafe>' });
if (provenance.source !== 'plate_solve' || provenance.capturedAt !== '2026-09-20') fail('provenance normalization failed');
if (normalizeHorizonProvenance({ source: 'not-a-source' }).source !== 'unknown') fail('unknown provenance should be explicit');

const confidence = normalizeHorizonConfidence([0.25, 2, -1]);
if (confidence.length !== BINS || confidence[0] !== 0.25 || confidence[1] !== 1 || confidence[2] !== 0) fail('confidence vector should be clamped to 720 bins');
if (normalizeHorizonConfidence([null])[0] !== null) fail('unassessed confidence must remain null, not become zero');

const a = new Array(BINS).fill(NO_BLOCK);
const b = a.slice(); b[20] = 12; b[40] = 30;
const diff = profileDifference(a, b);
if (diff.compared !== BINS || diff.maxAbs !== 120 || diff.azAtMax !== 20) fail('profile comparison should report the largest bin and azimuth');
console.log('horizon metadata contract held');
