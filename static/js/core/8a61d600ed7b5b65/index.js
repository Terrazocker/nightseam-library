/*
 * NightSeam core - public surface.
 *
 * Shells import this module through the baked stable stub `core.js`
 * (scripts/build_core.py copies this tree to content-hashed URLs and derives
 * the stub's explicit export list from THIS file). Keep the list explicit -
 * no `export *` - so the no-bundler import checker can resolve every named
 * import and the drift check can compare the stub against it.
 */

export {
  NO_BLOCK, BINS, BIN_DEG, MASK_W, MASK_H, WORLD_BINS,
  HORIZON_LANDMARK_KINDS, normalizeHorizonLandmarks,
  normalizeHorizonProvenance, normalizeHorizonConfidence, profileDifference,
  rebuildMask, altOf, rowOfAlt, xOfAz, azOfX, wrapsFor,
  profileFromMask, normalizeProfileBins, profileSegments, fovBinRel,
  visibleSkyBands, skyBandPolygons,
  computeProfile, clearMask, ceilFor, bandTopFor,
  overhangRuns, assumedOpenRuns, assumedOpenHatchPolys,
  CROP_MIN_FOV, cropKeptBand, cropStrokes, clipProfileToFov, cropPano,
} from './horizon.js';

export {
  fmtSize, sizeArcmin, fmtFov, fmtFovPair, framePct, magVisible,
  SKY_ZOOM_MIN, SKY_ZOOM_MAX, skyClampZoom, skyViewMatrix,
  skyScreenToBase, skyClampFocus,
} from './format.js';

export {
  TYPE_LABELS, TYPE_COLORS, isCustomId, searchBlob, wikiTitles,
  TIER_BANDS, TIER_ORDER, tierForRank, assignPopularityTiers,
} from './catalog.js';

export {
  offsetEastMin, zoneOffsetAtLocalNoon, wallFromUtcMs, utcFromWallMs,
} from './time.js';

export {
  HISTORY_KEYS, HISTORY_KEYS_DESKTOP, HISTORY_LIMIT,
  snapshot as snapshotPanoState, applySnapshot as applyPanoState,
  newHistory, historyCommit, historyCanUndo, historyCanRedo,
  historyUndo, historyRedo, historyMarkSaved, historyDirty,
} from './panohistory.js';
export { HISTORY_LIMIT as PANO_HISTORY_LIMIT } from './panohistory.js';

export {
  PLANNER_CHANGE_EVENT, nextPlannerRevision, plannerChange, panoramaRevision,
} from './plannerstate.js';

export {
  julianDay, dateFromJD, gmstDeg, precessJ2000ToDate, precessDateToJ2000,
  refractionDeg, altaz, altazFrame, altazFrameApply,
  sunRaDec, moonRaDec, moonIllumination, moonAge, sunAlt, sepDeg,
} from './astro.js';

export {
  sunCrossings, nightInfo, pickDarkWindow, scrubWindowMs,
  scrubBounds, scrubClamp,
} from './planning.js';

export { solarSessionWindows, solarSession } from './solar_sessions.js';

export {
  aboveHorizon, sampleVisible, statusOf, effectiveMinAlt,
  VISIBILITY_REASON, visibilityReason, summarizeVisibility,
  ADV_TONIGHT_MAG_CAP, TONIGHT_MAG_UNCAPPED, vmagKnownAndUnder, inTonightPool,
} from './visibility.js';

export {
  MOON_TYPE_FACTOR, moonSensitivity, moonMinDist, effectiveMoonGlow,
  moonPenalty, bortleMoonFactor, popularityFactor, nightStats, nightStatsBatch,
  computeNightTracks, scoreNightTracks,
} from './scoring.js';
