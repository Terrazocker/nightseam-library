/* JS port of app/astro.py (Meeus algorithms) - used to synthesize altitude
   tracks for the ~12k advanced-catalog objects directly in the browser, so
   the night payload stays small. Mirrors the Python engine: J2000 precession,
   sidereal time, Saemundsson refraction. */

/* Phase 2, concept 6: the bodies moved to core/astro.js (shared with the web
   shell); this module keeps the desktop's import surface for derive.js and
   the parity harness. */
import {
  julianDay, precessJ2000ToDate, altazFrame, altazFrameApply,
} from "./core.js";

export {
  julianDay, precessJ2000ToDate, altazFrame, altazFrameApply,
};

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const J2000 = 2451545.0;
