/* global application state */

import { PLANNER_CHANGE_EVENT, nextPlannerRevision, plannerChange } from "./core.js";

export const IMG_VER = 2;   // bump to make browsers refetch object images

export const S = {
  hostCapabilities: { native_folder_picker: false },
  cfg: null,          // /api/state
  catalog: [],        // catalog meta
  catalogProvenance: { objects: {} }, // generated source/alias sidecar
  meta: {},           // id -> meta
  night: null,        // computed night (raw tracks; derived flags live on objects)
  tIdx: 0,            // scrub index
  filteredIds: [],
  currentId: null,
  minAlt: 25,
  panos: [],          // full panorama records incl profile
  plannerRevision: 0, // monotonic cross-view invalidation revision
  library: null,      // /api/library
  disabledTypes: new Set(),   // object types greyed out on the sky charts
  lastRows: null,     // last filtered targets rows (pagination)
  activeSiteId: null, // currently active site id
};

/** Publish the shared cross-shell planner invalidation contract. */
export function announcePlannerChange(kind, fields = {}) {
  S.plannerRevision = nextPlannerRevision(S.plannerRevision);
  if (typeof document !== "undefined" && typeof CustomEvent !== "undefined") {
    document.dispatchEvent(new CustomEvent(PLANNER_CHANGE_EVENT, {
      detail: plannerChange(kind, S.plannerRevision, fields),
    }));
  }
  return S.plannerRevision;
}
