"use strict";

/* ================= panorama import / calibration / masking ================= */

import { $, clamp, esc, api, toast, num } from "./util.js";
import { S, announcePlannerChange } from "./state.js";
import { loadNight } from "./night.js";
import { profileSegments, cropKeptBand, fovBinRel,
         HORIZON_LANDMARK_KINDS, normalizeHorizonLandmarks,
         normalizeHorizonProvenance, normalizeHorizonConfidence,
         profileDifference,
         snapshotPanoState as coreSnapshotPano,
         applyPanoState as coreApplySnapshot,
         HISTORY_KEYS_DESKTOP, panoramaRevision } from "./core.js";

export { profileSegments };

const NBIN = 720;
let _profileObserver = null;
let _saveInFlight = null;

export const Pano = {
  cur: null,        // active record being edited
  img: null,        // HTMLImageElement
  maskCv: null,     // offscreen mask canvas
  painting: false,
  cropping: false,
  cropApplying: false,
  crop: null,       // side-band selection in canvas px
  tool: "b",
  pending: null,    // {rec, fields} captured by scheduleSave, flushed on Save/leave
  undo: [],         // pre-gesture snapshots (core/panohistory.js format)
  redo: [],         // undone snapshots, invalidated by a new edit
  metadataGesture: false,
  loupeOn: true,
  lastPos: null,    // last pointer position over canvas (css px)
};

window.Pano = Pano;

function announcePanoChanged(cause = "edit") {
  const rec = Pano.cur;
  announcePlannerChange("panorama", {
    cause,
    panoId: rec ? rec.id : null,
    panoRevision: panoramaRevision(rec),
  });
}

function setSaveStatus(text, cls = "") {
  const el = $("#panoSaveStatus");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("dirty", cls === "dirty");
  el.classList.toggle("failed", cls === "failed");
}

Pano.init = function () {
  $("#btnUploadPano").addEventListener("click", () => $("#panoFile").click());
  $("#panoFile").addEventListener("change", e => { uploadFile(e.target.files[0]); e.target.value=""; });
  // the mode buttons only exist in current index.html; a stale cached page
  // without them must not kill init (import would then fail silently)
  const mb = $("#modeObstacle"), ms = $("#modeSky");
  if (mb && ms) {
    mb.addEventListener("click", () => setPaintMode("obstacle"));
    ms.addEventListener("click", () => setPaintMode("sky"));
  }
  $("#toolBrush").addEventListener("click", () => setTool("b"));
  $("#toolErase").addEventListener("click", () => setTool("e"));
  $("#toolCrop").addEventListener("click", () => setTool("crop"));
  $("#btnCropCancel").addEventListener("click", () => cancelCrop());
  $("#btnCropApply").addEventListener("click", () => applyCrop());
  $("#btnClearMask").addEventListener("click", clearMask);
  $("#btnUndo").addEventListener("click", undoPaint);
  const br = $("#btnRedo");
  if (br) br.addEventListener("click", redoPaint);
  $("#chkLoupe").addEventListener("change", e => { Pano.loupeOn = e.target.checked; render(); });
  // "block above band": opt OUT of the default open-above assumption
  const ba = $("#chkBlockAbove");
  if (ba) ba.addEventListener("change", e => {
    if (!Pano.cur) return;
    Pano.cur.block_above = !!e.target.checked;
    render();
    scheduleSave();
  });
  $("#btnCalN").addEventListener("click", () => {
    if (!Pano.cur) return;
    Pano.calibrating = true;
    $("#calNHint").classList.remove("hidden");
    setTool("none");
    render();
  });
  $("#btnSaveProfile").addEventListener("click", () => { recomputeProfile(); requestSave(); });
  $("#btnSetActive").addEventListener("click", activateCurrent);
  $("#btnDelPano").addEventListener("click", deleteCurrent);
  for (const id of ["panoProvSource", "panoProvDate", "panoProvOperator",
                    "panoProvOrientation", "panoProvNotes"]) {
    const el = $("#" + id);
    if (!el) continue;
    el.addEventListener("input", () => updateMetadataFromUI(true));
    el.addEventListener("change", () => { Pano.metadataGesture = false; updateMetadataFromUI(false); });
  }
  $("#btnAddPanoLandmark").addEventListener("click", addPanoLandmark);
  $("#btnReadPanoProfile").addEventListener("click", readPanoProfileBin);
  $("#panoProfileAz").addEventListener("change", () => { readPanoProfileBin(); drawProfilePlot(); });
  $("#btnApplyPanoProfile").addEventListener("click", applyPanoProfileBin);
  const profileCanvas = $("#panoProfileCanvas");
  profileCanvas?.addEventListener("click", (event) => {
    const rect = profileCanvas.getBoundingClientRect();
    const left = 34, right = 8, width = Math.max(1, rect.width - left - right);
    const az = clamp((event.clientX - rect.left - left) / width * 360, 0, 359.5);
    $("#panoProfileAz").value = (Math.round(az * 2) / 2).toFixed(1);
    readPanoProfileBin();
    drawProfilePlot();
  });
  profileCanvas?.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = num($("#panoProfileAz").value) || 0;
    const next = event.key === "Home" ? 0 : event.key === "End" ? 359.5
      : clamp(current + (event.key === "ArrowRight" ? 0.5 : -0.5), 0, 359.5);
    $("#panoProfileAz").value = next.toFixed(1);
    readPanoProfileBin();
    drawProfilePlot();
  });
  $("#panoProfileCompare").addEventListener("change", () => {
    comparePanoProfile();
    render();
  });

  for (const [id, valId] of [
      ["azOffset","azOffVal"], ["altBottom","abVal"],
      ["altTop","atVal"], ["fovAz","fovVal"]]) {
    $(`#${id}`).addEventListener("input", e => {
      $(`#${valId}`).textContent = e.target.value + "\u00B0";
      if (id === "azOffset" || id === "fovAz") invalidateMask();  // grid only
      render(); scheduleSave();
    });
  }

  const cv = $("#panoCanvas");
  cv.addEventListener("pointerdown", e => {
    if (Pano.cropApplying) return;
    if (!Pano.img || !Pano.img.complete) return;
    if (Pano.calibrating) { calibrateAt(e); return; }
    cv.setPointerCapture(e.pointerId);
    if (Pano.tool === "crop") {
      pushUndo();
      Pano.crop = { x0: evX(e), x1: evX(e) };   // side band, full height
      Pano.cropping = true;
    } else {
      pushUndo();
      Pano.painting = true;
      dab(e); render();
    }
  });
  let _loupeRaf = null;
  cv.addEventListener("pointermove", e => {
    Pano.lastPos = { x: evX(e), y: evY(e) };
    if (Pano.cropping) {
      Pano.crop.x1 = evX(e); render();
    } else if (Pano.painting) {
      dab(e); render();
    } else if (Pano.loupeOn && (Pano.tool === "b" || Pano.tool === "e")) {
      if (!_loupeRaf) _loupeRaf = requestAnimationFrame(() => {
        _loupeRaf = null;
        updateLoupe();
      });
    }
  });
  cv.addEventListener("pointerleave", () => {
    Pano.lastPos = null;
    const lcv = $("#loupeCv");
    if (lcv) lcv.classList.add("hidden");
  });
  window.addEventListener("pointerup", () => {
    if (Pano.painting) { Pano.painting = false; scheduleSave(); }
    if (Pano.cropping) {
      Pano.cropping = false;
      const c = normCrop();
      if (!c || c.w < 4) Pano.crop = null;
      render();
    }
  });
  document.addEventListener("keydown", e => {
    if (Pano.cropApplying) return;
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
    // tab panes are toggled with .active, never with .hidden
    if (!$("#tab-horizon").classList.contains("active")) return;
    const t = e.target;
    const tag = ((t && t.tagName) || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" ||
        (t && t.isContentEditable)) return;      // keep native text undo
    undoPaint();
    e.preventDefault();
  });

  if (S.panos.length) select(S.panos.find(p=>p.active) || S.panos[0]);
};

/* ---------------- paint mode (obstacle | sky) ---------------- */

function paintMode() {
  return (Pano.cur && Pano.cur.paint_mode === "sky") ? "sky" : "obstacle";
}

function updateModeButtons() {
  const m = paintMode();
  const b = $("#modeObstacle"), s = $("#modeSky");
  if (b) b.classList.toggle("active", m === "obstacle");
  if (s) s.classList.toggle("active", m === "sky");
}

/* Ruling C (DE-03/WE-02) - PURE state transitions, shared with the pin in
 * tests/js/pano_mode_semantics.mjs so the shell and its test cannot drift.
 * A DELIBERATE paint-mode switch is mode-specific for a record with NO strokes
 * (the stored values were authored in the other mode), so the profile is
 * re-derived for the new mode; the switch is ONE undoable step because the undo
 * snapshot below carries the whole editable state. Neither helper runs on
 * open/load/save. */

/** Canonical blank for the record's CURRENT mode: obstacle = nothing blocks,
 *  sky = every column fully blocked. The same values recomputeProfile()'s
 *  stroke-less branch produces, and the same rule the web applies through core
 *  clearMask(). */
export function blankProfileForMode(rec) {
  const sky = (rec.paint_mode === "sky");
  rec.profile = new Array(NBIN).fill(sky ? 90 : -90);
  rec.profile_top = sky ? new Array(NBIN).fill(90) : [];
  return rec.profile;
}

function ensureHorizonMetadata(rec) {
  if (!rec) return rec;
  rec.landmarks = normalizeHorizonLandmarks(rec.landmarks);
  rec.provenance = normalizeHorizonProvenance(rec.provenance);
  rec.confidence = normalizeHorizonConfidence(rec.confidence);
  return rec;
}

function metadataTouch() {
  if (!Pano.metadataGesture) {
    pushPanoUndo(Pano.cur, Pano.undo);
    Pano.redo = [];
    Pano.metadataGesture = true;
  }
}

function profileBinIndex() {
  const n = num($("#panoProfileAz")?.value);
  const a = Number.isFinite(n) ? ((n % 360) + 360) % 360 : 0;
  return Math.min(NBIN - 1, Math.max(0, Math.round(a * 2)));
}

function updateMetadataFromUI(mark = true) {
  if (!Pano.cur) return;
  if (mark) metadataTouch();
  Pano.cur.provenance = normalizeHorizonProvenance({
    source: $("#panoProvSource")?.value,
    capturedAt: $("#panoProvDate")?.value,
    operator: $("#panoProvOperator")?.value,
    orientation: $("#panoProvOrientation")?.value,
    notes: $("#panoProvNotes")?.value,
  });
  scheduleSave();
}

function renderPanoLandmarks() {
  const box = $("#panoLandmarks");
  if (!box || !Pano.cur) return;
  box.innerHTML = Pano.cur.landmarks.map((lm, i) => `
    <div class="landmark-row" data-lm-index="${i}" role="listitem">
      <input data-lm="name" type="text" maxlength="96" value="${esc(lm.name)}" aria-label="Landmark ${i + 1} name">
      <select data-lm="kind" aria-label="Landmark ${i + 1} type">${HORIZON_LANDMARK_KINDS.map((v) => `<option value="${v}"${lm.kind === v ? " selected" : ""}>${v}</option>`).join("")}</select>
      <input data-lm="azimuth" type="number" min="0" max="359.99" step="0.5" value="${lm.azimuth}" aria-label="Landmark ${i + 1} azimuth">
      <input data-lm="altitude" type="number" min="-90" max="90" step="0.5" value="${lm.altitude}" aria-label="Landmark ${i + 1} altitude">
      <button class="ghost" data-lm-remove type="button" aria-label="Remove ${esc(lm.name)}">Remove</button>
    </div>`).join("")
    || '<p class="hint">No named landmarks yet.</p>';
  box.querySelectorAll("[data-lm]").forEach((el) => el.addEventListener("change", () => {
    const row = el.closest("[data-lm-index]");
    const i = Number(row?.dataset.lmIndex);
    if (!Number.isInteger(i) || !Pano.cur.landmarks[i]) return;
    metadataTouch();
    const key = el.dataset.lm;
    Pano.cur.landmarks[i] = { ...Pano.cur.landmarks[i], [key]: key === "name" || key === "kind" ? el.value : num(el.value) };
    Pano.cur.landmarks = normalizeHorizonLandmarks(Pano.cur.landmarks);
    scheduleSave();
  }));
  box.querySelectorAll("[data-lm-remove]").forEach((el) => el.addEventListener("click", () => {
    const row = el.closest("[data-lm-index]");
    const i = Number(row?.dataset.lmIndex);
    if (!Number.isInteger(i)) return;
    metadataTouch();
    Pano.cur.landmarks.splice(i, 1);
    renderPanoLandmarks();
    scheduleSave();
  }));
}

function addPanoLandmark() {
  if (!Pano.cur) return;
  const name = $("#panoLandmarkName")?.value.trim();
  const az = num($("#panoLandmarkAz")?.value);
  const alt = num($("#panoLandmarkAlt")?.value);
  if (!name || !Number.isFinite(az) || !Number.isFinite(alt)) return;
  metadataTouch();
  Pano.cur.landmarks.push({
    id: `landmark-${Date.now().toString(36)}`,
    name, kind: $("#panoLandmarkKind")?.value || "other",
    azimuth: az, altitude: alt, notes: "",
  });
  Pano.cur.landmarks = normalizeHorizonLandmarks(Pano.cur.landmarks);
  $("#panoLandmarkName").value = "";
  $("#panoLandmarkAz").value = "";
  $("#panoLandmarkAlt").value = "";
  renderPanoLandmarks();
  scheduleSave();
}

function readPanoProfileBin() {
  if (!Pano.cur) return;
  const i = profileBinIndex();
  const lower = Number(Pano.cur.profile?.[i] ?? -90);
  const upper = Number(Pano.cur.profile_top?.[i] ?? -90);
  $("#panoProfileAlt").value = String(lower);
  $("#panoProfileTop").value = String(upper);
  const c = normalizeHorizonConfidence(Pano.cur.confidence)[i];
  $("#panoProfileConfidence").value = c == null ? "" : String(c);
  const summary = $("#panoProfileBinSummary");
  if (summary) {
    const lowerText = lower <= -90 ? "no lower obstruction" : `lower obstruction ${lower.toFixed(1)}°`;
    const upperText = Pano.cur.block_above && upper > -90 ? ` · upper boundary ${upper.toFixed(1)}°` : "";
    const confidence = c == null ? "confidence not recorded" : `confidence ${(c * 100).toFixed(0)}%`;
    summary.textContent = `Azimuth ${(i / 2).toFixed(1)}° · ${lowerText}${upperText} · ${confidence}.`;
  }
}

function applyPanoProfileBin() {
  if (!Pano.cur) return;
  const alt = num($("#panoProfileAlt")?.value);
  const top = num($("#panoProfileTop")?.value);
  if (!Number.isFinite(alt) || alt < -90 || alt > 90.5) return;
  if (paintMode() === "sky" && (!Number.isFinite(top) || top < -90 || top > 90.5)) return;
  metadataTouch();
  const i = profileBinIndex();
  Pano.cur.strokes = [];
  Pano.cur.profile = Array.isArray(Pano.cur.profile) && Pano.cur.profile.length === NBIN
    ? Pano.cur.profile.slice() : new Array(NBIN).fill(paintMode() === "sky" ? 90 : -90);
  Pano.cur.profile_top = Array.isArray(Pano.cur.profile_top) && Pano.cur.profile_top.length === NBIN
    ? Pano.cur.profile_top.slice() : new Array(NBIN).fill(-90);
  Pano.cur.profile[i] = +alt.toFixed(1);
  if (paintMode() === "sky") Pano.cur.profile_top[i] = +top.toFixed(1);
  Pano.cur.confidence = normalizeHorizonConfidence(Pano.cur.confidence);
  const cv = $("#panoProfileConfidence")?.value;
  Pano.cur.confidence[i] = cv === "" ? null : Math.max(0, Math.min(1, num(cv)));
  invalidateMask();
  render();
  scheduleSave();
}

function comparePanoProfile() {
  const out = $("#panoProfileCompareSummary"), sel = $("#panoProfileCompare");
  if (!out || !sel || !sel.value || !Pano.cur) {
    if (out) out.textContent = "Choose another horizon to compare profile bins.";
    return;
  }
  const other = S.panos.find((p) => p.id === sel.value);
  if (!other) return;
  const d = profileDifference(Pano.cur.profile, other.profile);
  out.textContent = d.compared
    ? `${other.name}: mean difference ${d.meanAbs.toFixed(1)}°; maximum ${d.maxAbs.toFixed(1)}° at azimuth ${d.azAtMax.toFixed(1)}° (${d.compared} bins compared).`
    : `${other.name}: no comparable profile bins.`;
}

/** Switch the record's paint mode. Returns true when it changed. */
export function setEditableMode(rec, m) {
  const want = (m === "sky") ? "sky" : "obstacle";
  const cur = (rec.paint_mode === "sky") ? "sky" : "obstacle";
  if (want === cur) return false;
  rec.paint_mode = want;
  if (strokeList(rec).length === 0) blankProfileForMode(rec);
  return true;
}

/** Undo snapshots carry the WHOLE editable state, not just the strokes: a mode
 *  switch or a Clear must be undoable, and a stroke-less record's profile is
 *  authoritative (an empty mask cannot re-derive it).
 *
 *  The snapshot itself is core/panohistory.js - ONE implementation shared with
 *  the web editor (shell-unification pass), including the two rules that are
 *  easy to get wrong: the stored profile is captured ONLY when there are no
 *  strokes (a mask reproduces a stroke-y profile exactly, and a stroke-less one
 *  is authoritative - ruling C, DE-03/WE-02), and the editable set is exactly
 *  the calibration bounds + paint mode + blockAbove + strokes. `HISTORY_KEYS_
 *  DESKTOP` maps the canonical names onto this shell's snake_case record.
 *
 *  Sequencing stays shell-side and deliberately differs from the web: this
 *  editor captures the PRE-gesture state on pointer down (so undo steps back to
 *  the moment before the gesture), while the web commits post-gesture states
 *  into its step list. */
export function pushPanoUndo(rec, stack) {
  if (!rec) return;
  stack.push(coreSnapshotPano(rec, HISTORY_KEYS_DESKTOP));
  if (stack.length > 60) stack.shift();
}

export function popPanoUndo(rec, stack) {
  if (!rec || !stack.length) return false;
  coreApplySnapshot(rec, stack.pop(), HISTORY_KEYS_DESKTOP);
  return true;
}

function setPaintMode(m) {
  if (!Pano.cur || paintMode() === m) return;
  pushPanoUndo(Pano.cur, Pano.undo);   // ruling C: the switch is ONE undoable step
  setEditableMode(Pano.cur, m);        // mode + canonical blank for the new mode
  invalidateMask();
  updateModeButtons();
  syncUndoButtons();
  recomputeProfile();
  scheduleSave();
}

/* ---------------- undo / redo ----------------
   The snapshots are core/panohistory.js (ONE implementation, shared with the web
   editor). This shell keeps its PRE-gesture stack: pointer-down captures the
   state the gesture is about to change, so an undo returns to the moment before
   it - a different sequencing from the web's post-gesture step list, on top of
   the same snapshot contract. */

function pushUndo() {
  pushPanoUndo(Pano.cur, Pano.undo);
  // a NEW edit invalidates the redo branch (the same rule as the web's
  // historyCommit dropping its forward entries)
  Pano.redo = [];
  syncUndoButtons();
}

function undoPaint() {
  if (!Pano.cur || !Pano.undo.length) return;
  // the redo entry is the state the undo is LEAVING, captured before the pop
  Pano.redo.push(coreSnapshotPano(Pano.cur, HISTORY_KEYS_DESKTOP));
  if (!popPanoUndo(Pano.cur, Pano.undo)) return;
  invalidateMask();
  syncEditorInputs(Pano.cur);
  syncUndoButtons();
  render();
  scheduleSave();
}

function redoPaint() {
  if (!Pano.cur || !Pano.redo.length) return;
  const snap = Pano.redo.pop();
  coreApplySnapshot(Pano.cur, snap, HISTORY_KEYS_DESKTOP);
  Pano.undo.push(snap);
  invalidateMask();
  syncEditorInputs(Pano.cur);
  syncUndoButtons();
  render();
  scheduleSave();
}

/** Undo/Redo availability, so a button never invites a no-op click. */
function syncUndoButtons() {
  const u = $("#btnUndo"), r = $("#btnRedo");
  if (u) u.disabled = !Pano.undo.length;
  if (r) r.disabled = !Pano.redo.length;
}

/* ---------------- landmark calibration ---------------- */

function calibrateAt(e) {
  const rect = $("#panoCanvas").getBoundingClientRect();
  const frac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
  const azOff = +$("#azOffset").value || 0;
  const fov = +$("#fovAz").value || 360;
  const clickedAz = (azOff + frac * fov) % 360;
  const bearing = prompt(
    "Compass bearing of this point, in degrees (N=0, E=90, S=180, W=270):",
    clickedAz.toFixed(1));
  if (bearing != null) {
    const b = num(bearing);
    if (!isNaN(b)) {
      pushUndo();
      const newOff = (((b - frac * fov) % 360) + 360) % 360;
      $("#azOffset").value = newOff.toFixed(1);
      $("#azOffVal").textContent = newOff.toFixed(1) + "\u00B0";
      invalidateMask();          // calibration moved -> grid + line shift
      recomputeProfile();        // profile bins follow the new offset
      scheduleSave();
    }
  }
  Pano.calibrating = false;
  $("#calNHint").classList.add("hidden");
  setTool("b");
}

function evX(e) {
  const r = $("#panoCanvas").getBoundingClientRect();
  return clamp(e.clientX - r.left, 0, r.width);
}
function evY(e) {
  const r = $("#panoCanvas").getBoundingClientRect();
  return clamp(e.clientY - r.top, 0, r.height);
}

/* selected side-crop band in canvas pixels: full height between two cuts */
function normCrop() {
  if (!Pano.crop) return null;
  const x0 = Math.min(Pano.crop.x0, Pano.crop.x1);
  const x1 = Math.max(Pano.crop.x0, Pano.crop.x1);
  return { x: x0, w: x1 - x0 };
}

Pano.onShow = function () {
  renderList(); render();
  const panel = $(".pano-profile-panel");
  if (panel && typeof ResizeObserver === "function") {
    _profileObserver?.disconnect();
    _profileObserver = new ResizeObserver(drawProfilePlot);
    _profileObserver.observe(panel);
  }
};
Pano.onHide = function () { _profileObserver?.disconnect(); void flushSave(); };
Pano.render = render;   // exposed for the global resize handler

Pano.refreshOverlay = function () {
  if (Pano.cur && Pano.img && $("#tab-horizon")?.classList.contains("active")) render();
};

/* ---------------- data plumbing ---------------- */

async function refreshPanos() {
  S.panos = await api("/api/panoramas");
  S.panos.forEach(ensureHorizonMetadata);
  renderList();
}

async function uploadFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await api("/api/panorama", { method: "POST", body: fd });
    await refreshPanos();
    const rec = S.panos.find(p => p.id === res.id);
    if (rec) await select(rec);
  } catch (err) {
    console.error("pano import failed:", err);
    alert(err.message);
  }
}

function renderList() {
  const box = $("#panoList");
  if (!S.panos.length) { box.innerHTML = '<p class="hint">No panoramas yet.</p>'; return; }
  box.innerHTML = S.panos.map(p => `
    <div class="pano-item ${p.active ? "active" : ""}" data-id="${esc(p.id)}">
      <span class="nm">${esc(p.name)}</span>
      ${p.profile && p.profile.length ? '<small class="hint">saved</small>' : '<small class="hint">draft</small>'}
      <button data-sel="${esc(p.id)}" class="ghost">Edit</button>
    </div>`).join("");
  box.querySelectorAll("[data-sel]").forEach(b =>
    b.addEventListener("click", () =>
      select(S.panos.find(p => p.id === b.dataset.sel))));
}

async function select(rec) {
  // A pending explicit save belongs to the PREVIOUS panorama, and its payload
  // is captured when it is scheduled - so FLUSH it before switching. The
  // payload carries its own record and can never write into the record we are
  // switching to.
  await flushSave();
  Pano.cur = ensureHorizonMetadata(rec);
  Pano.metadataGesture = false;
  // DE-02: the offscreen mask cache belongs to ONE record, but `render()` only
  // rebuilds it when the canvas SIZE changes - so two panoramas with the same
  // aspect reused the previous mask. `select()` writes the calibration fields
  // programmatically (no `input` event), so the gesture-path invalidation never
  // ran here either. Drop the cache with the record it belongs to.
  invalidateMask();
  Pano.crop = null;
  // A pending save belongs to the PREVIOUS panorama: it was flushed above and
  // never cancelled. Undo history belongs to ONE panorama: without this,
  // undoing after a switch could write the previous panorama's strokes into
  // this one.
  Pano.undo = [];
  Pano.redo = [];
  updateModeButtons();
  syncUndoButtons();
  $("#calibBox").classList.remove("hidden");
  syncEditorInputs(rec);
  setSaveStatus("Saved");
  Pano.img = new Image();
  Pano.img.onload = () => { $("#panoError").classList.add("hidden"); render(); };
  Pano.img.onerror = () => {
    $("#panoError").textContent =
      "Panorama image file is missing on disk - please re-import it.";
    $("#panoError").classList.remove("hidden");
    render();
  };
  // timestamp busts the browser cache after crops replaced the file
  Pano.img.src = `/api/panorama/${rec.id}/image?v=${Date.now()}`;
  renderList();
}

async function activateCurrent() {
  if (!Pano.cur) return;
  await api("/api/settings", { method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ active_pano_id: Pano.cur.id })});
  await refreshPanos();
  await loadNight();
}

async function deleteCurrent() {
  if (!Pano.cur || !confirm(`Delete "${Pano.cur.name}"?`)) return;
  await api("/api/panorama/" + Pano.cur.id, { method: "DELETE" });
  Pano.cur = null; Pano.img = null;
  $("#panoCanvas").getContext("2d").clearRect(0,0,9999,9999);
  $("#calibBox").classList.add("hidden");
  $("#panoEmpty").classList.remove("hidden");
  await refreshPanos();
  await loadNight();
}

/* ---------------- painting ---------------- */

function setTool(t) {
  Pano.tool = t;
  $("#toolBrush").classList.toggle("active", t === "b");
  $("#toolErase").classList.toggle("active", t === "e");
  $("#toolCrop").classList.toggle("active", t === "crop");
  const cropUI = t === "crop";
  $("#btnCropApply").classList.toggle("hidden", !cropUI);
  $("#btnCropCancel").classList.toggle("hidden", !cropUI);
  $("#cropHint").classList.toggle("hidden", !cropUI);
  if (!cropUI) { Pano.crop = null; render(); }
}

/* ---------------- cropping ---------------- */

function cancelCrop() { setTool("b"); }

async function applyCrop() {
  const c = normCrop();
  const W = cvW(), H = cvH();
  if (Pano.cropApplying || !Pano.cur || !c || c.w < 4 || !Pano.img || !Pano.img.complete) return;
  const rec = Pano.cur;
  const image = Pano.img;
  const fovOld = +$("#fovAz").value || 360;
  const azOld = +$("#azOffset").value || 0;
  // Crop publishes new pixels and calibration together. Finish earlier saves
  // first, and keep the editor stable until that publication is acknowledged.
  Pano.cropApplying = true;
  const controls = [...document.querySelectorAll("#tab-horizon button, #tab-horizon input, #tab-horizon select, #tab-horizon textarea")];
  const disabled = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  try {
    if (!await flushSave() || Pano.cur?.id !== rec.id) return;
    const keepL = Math.round(c.x / W * image.naturalWidth);
    const keepR = Math.round((W - (c.x + c.w)) / W * image.naturalWidth);
    let sx = keepL, sw = image.naturalWidth - keepL - keepR;
    if (sw < 16) return alert("Selection removes too much of the image.");

    // cap output width: phone panoramas are often 10k+ px wide
    const MAXW = 4096;
    let outW = sw, outH = image.naturalHeight;
    if (outW > MAXW) {
      outH = Math.max(1, Math.round(outH * MAXW / outW));
      outW = MAXW;
    }
    const cv = document.createElement("canvas");
    cv.width = outW; cv.height = outH;
    cv.getContext("2d").drawImage(image,
        sx, 0, sw, image.naturalHeight,
        0, 0, outW, outH);
    const blob = await new Promise(res => cv.toBlob(res, "image/png"));
    if (!blob) return alert("Cropping failed");
    if (Pano.cur?.id !== rec.id) return;

    // recalibrate with the SHARED kept-band helper (core/horizon.js): the kept
    // band [c.x, c.x+c.w] shrinks the FOV to its fraction and moves the left-edge
    // azimuth; the 45 deg floor + 2 dp azimuth live there for BOTH shells. The old
    // inline fovOld*(1-c.w/W) computed the REMOVED fraction and only happened to
    // agree at exactly half width.
    const { fov: fovNew, azOffset: azNew } =
        cropKeptBand(fovOld, azOld,
                     { x: c.x, w: c.w }, W);

    const fd = new FormData();
    fd.append("file", blob, `${rec.id}.png`);
    fd.append("az_offset", azNew.toFixed(2));
    fd.append("fov", String(fovNew));
    await api(`/api/panorama/${rec.id}/image`, { method: "POST", body: fd });
    await refreshPanos();
    const upd = S.panos.find(p => p.id === rec.id);
    if (upd && Pano.cur?.id === rec.id) await select(upd);
    setTool("b");
  } catch (err) { alert(err.message); }
  finally {
    controls.forEach((control, i) => { control.disabled = disabled[i]; });
    syncUndoButtons();
    Pano.cropApplying = false;
  }
}

function cvW() { return $("#panoCanvas").clientWidth; }
function cvH() { return $("#panoCanvas").clientHeight; }

function dab(ev) {
  const rect = $("#panoCanvas").getBoundingClientRect();
  const nx = clamp((ev.clientX - rect.left) / rect.width, 0, 1);
  const ny = clamp((ev.clientY - rect.top) / rect.height, 0, 1);
  const r = (+$("#brushSize").value) / 2 / rect.width;
  Pano.cur.strokes.push({ m: Pano.tool, x: nx, y: ny, r });
  invalidateMask();
}

function confirmDialog({ title, body, ok = "OK", cancel = "Cancel" }) {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "modal";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "panoConfirmTitle");
    el.innerHTML = `<div class="modal-box" style="max-width:440px">
      <h3 id="panoConfirmTitle">${esc(title)}</h3>
      <p class="hint">${esc(body)}</p>
      <div class="rowbtns" style="justify-content:flex-end">
        <button class="ghost" data-cancel>${esc(cancel)}</button>
        <button class="danger" data-ok>${esc(ok)}</button>
      </div>
    </div>`;
    const done = (value) => { el.remove(); resolve(value); };
    el.addEventListener("keydown", (event) => {
      if (event.key === "Escape") done(false);
    });
    el.addEventListener("click", (event) => {
      if (event.target === el) done(false);
    });
    el.querySelector("[data-cancel]").addEventListener("click", () => done(false));
    el.querySelector("[data-ok]").addEventListener("click", () => done(true));
    document.body.appendChild(el);
    el.querySelector("[data-ok]").focus();
  });
}

async function clearMask() {
  if (!Pano.cur) return;
  const ok = await confirmDialog({
    title: "Clear horizon mask?",
    body: "Remove all paint from this panorama? You can undo this change.",
    ok: "Clear",
  });
  if (!ok) return;
  pushPanoUndo(Pano.cur, Pano.undo);   // Clear is undoable too (the ONE deliberate discard)
  Pano.cur.strokes = [];
  Pano.cur.profile = [];
  Pano.cur.profile_top = [];
  invalidateMask();
  syncUndoButtons();
  render();
  scheduleSave();
}

/* ---------------- rendering ---------------- */

function invalidateMask() { Pano.maskCv = null; }

/* stamp one stroke as filled circles; when the panorama covers a full
   circle, dabs touching an edge are duplicated on the opposite edge so the
   mask stays continuous across the azimuth seam */
function stampDab(ctx, s, W, H, wraps) {
  const r = Math.max(2, s.r * W);
  const cx0 = s.x * W;
  const xs = [cx0];
  if (wraps) {
    if (cx0 - r < 0) xs.push(cx0 + W);
    if (cx0 + r > W) xs.push(cx0 - W);
  }
  for (const cx of xs) {
    ctx.beginPath();
    ctx.arc(cx, s.y * H, r, 0, 7);
    ctx.fill();
  }
}

/** PY-03: a stored `strokes` that is not an array (a hand-edited or legacy row)
 *  must never crash the editor. The import and PUT boundaries reject the shape
 *  now - this is the last-resort guard, shared by every consumer here. */
function strokeList(rec) {
  return (rec && Array.isArray(rec.strokes)) ? rec.strokes : [];
}

function buildMask(W, H) {
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = paintMode() === "sky" ? "#4fd97a" : "#ff5d5d";
  const wraps = (+$("#fovAz").value || 360) >= 359.5;
  for (const s of strokeList(Pano.cur)) {
    ctx.globalCompositeOperation = s.m === "e" ? "destination-out" : "source-over";
    stampDab(ctx, s, W, H, wraps);
  }
  return cv;
}

function render() {
  const cv = $("#panoCanvas");
  const stage = $("#panoStage");
  if (!Pano.cur || !Pano.img || !Pano.img.complete) {
    $("#panoEmpty").classList.toggle("hidden", !!Pano.cur);
    drawProfilePlot();
    return;
  }
  $("#panoEmpty").classList.add("hidden");

  const W = stage.clientWidth - 2;
  const ih = Pano.img.naturalHeight / Pano.img.naturalWidth;
  const H = Math.max(1, Math.round(W * ih));
  stage.style.aspectRatio = `${Pano.img.naturalWidth} / ${Pano.img.naturalHeight}`;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.height = H + "px";

  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(Pano.img, 0, 0, W, H);

  // mask layer
  if (!Pano.maskCv || Pano.maskCv.width !== W || Pano.maskCv.height !== H)
    Pano.maskCv = buildMask(W, H);
  ctx.drawImage(Pano.maskCv, 0, 0);

  drawCalibGrid(ctx, W, H);
  drawProfileLine(ctx, W, H);

  // crop overlay: dim the side bands marked for removal
  const c = normCrop();
  if (c && Pano.tool === "crop") {
    ctx.fillStyle = "rgba(5,7,12,.6)";
    ctx.fillRect(0, 0, c.x, H);
    ctx.fillRect(c.x + c.w, 0, W - c.x - c.w, H);
    ctx.strokeStyle = "#4f9cff";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 5]);
    for (const lx of [c.x, c.x + c.w]) {
      ctx.beginPath(); ctx.moveTo(lx, 0); ctx.lineTo(lx, H); ctx.stroke();
    }
    ctx.setLineDash([]);
    const fov = +$("#fovAz").value || 360;
    const azOff = +$("#azOffset").value || 0;
    ctx.fillStyle = "#9cc4ff";
    ctx.font = "11px Consolas, monospace";
    const lTrim = (c.x / W * fov).toFixed(1);
    const rTrim = ((W - c.x - c.w) / W * fov).toFixed(1);
    ctx.fillText(`-\u00A0${lTrim}\u00B0`, 6, 18);
    ctx.fillText(`-\u00A0${rTrim}\u00B0`, W - 74, 18);
    ctx.fillText(`FOV \u2192 ${(fov - lTrim - rTrim).toFixed(0)}\u00B0`, c.x + 8, 18);
  }
  const summary = $("#panoChartSummary");
  if (summary && Pano.cur) {
    const mode = paintMode() === "sky" ? "sky-band" : "obstacle";
    const open = paintMode() === "sky" && !Pano.cur.block_above ? " Above-band sky is assumed open." : "";
    summary.textContent = `Panorama horizon editor in ${mode} mode. Azimuth offset ${(+$("#azOffset").value || 0).toFixed(1)} degrees, field of view ${(+$("#fovAz").value || 360).toFixed(1)} degrees, altitude range ${(+$("#altBottom").value || 0).toFixed(1)} to ${(+$("#altTop").value || 45).toFixed(1)} degrees.${open}`;
  }
  drawProfilePlot();
}

function drawProfilePlot() {
  const cv = $("#panoProfileCanvas");
  if (!cv || !cv.clientWidth) return;
  const W = cv.clientWidth, H = cv.clientHeight || 166;
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const style = getComputedStyle(document.documentElement);
  const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  const L = 34, R = 8, T = 8, B = 22;
  const pw = Math.max(1, W - L - R), ph = Math.max(1, H - T - B);
  const y = (alt) => T + (90 - Math.max(0, Math.min(90, alt))) / 90 * ph;
  ctx.fillStyle = color("--panel", "#10181B"); ctx.fillRect(0, 0, W, H);
  ctx.font = "10px Consolas, monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const alt of [0, 30, 60, 90]) {
    const yy = y(alt);
    ctx.strokeStyle = color("--border-soft", "#1E2B31");
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(W - R, yy); ctx.stroke();
    ctx.fillStyle = color("--dim", "#9CAEB1"); ctx.fillText(`${alt}°`, L - 5, yy);
  }
  const p = Pano.cur?.profile;
  const valid = Array.isArray(p) && p.length === NBIN;
  if (valid) {
    ctx.beginPath();
    p.forEach((value, i) => {
      const alt = Number(value);
      const xx = L + i / (NBIN - 1) * pw;
      const yy = y(alt < 0 ? 0 : alt);
      i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy);
    });
    ctx.strokeStyle = color("--bad", "#C45B52"); ctx.lineWidth = 1.5; ctx.stroke();
    const top = Pano.cur?.profile_top;
    if (Pano.cur?.block_above && Array.isArray(top) && top.length === NBIN) {
      ctx.save(); ctx.setLineDash([5, 4]); ctx.beginPath();
      top.forEach((value, i) => {
        const xx = L + i / (NBIN - 1) * pw;
        const yy = y(Number(value) < 0 ? 0 : Number(value));
        i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy);
      });
      ctx.strokeStyle = color("--warn", "#E8A948"); ctx.stroke(); ctx.restore();
    }
  }
  const selectedBin = profileBinIndex();
  const selectedX = L + selectedBin / (NBIN - 1) * pw;
  ctx.strokeStyle = color("--accent", "#F0C36A"); ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(selectedX, T); ctx.lineTo(selectedX, T + ph); ctx.stroke(); ctx.setLineDash([]);
  if (valid) {
    const selectedAlt = Number(p[selectedBin]);
    if (Number.isFinite(selectedAlt) && selectedAlt > -90) {
      ctx.fillStyle = color("--accent", "#F0C36A");
      ctx.beginPath(); ctx.arc(selectedX, y(selectedAlt), 3.2, 0, Math.PI * 2); ctx.fill();
    }
  }
  ctx.fillStyle = color("--dim", "#9CAEB1"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const [az, label] of [[0, "N · 0°"], [90, "E · 90°"], [180, "S · 180°"], [270, "W · 270°"], [360, "N · 360°"]]) {
    ctx.fillText(label, L + az / 360 * pw, H - B + 5);
  }
  const summary = $("#panoProfileSummary");
  if (summary) summary.textContent = valid
    ? `Stored horizon profile with ${NBIN} world-azimuth bins. The lower edge uses the obstruction color${Pano.cur?.block_above ? " and the upper blocked edge is dashed" : ""}.`
    : "No stored horizon profile is available.";
}

/* magnifier overlay: samples the composite panorama canvas, works on hover
   AND while painting */
function updateLoupe() {
  const lcv = $("#loupeCv");
  const cv = $("#panoCanvas");
  if (!lcv || !cv || !Pano.img || !Pano.img.complete) return;
  const p = Pano.lastPos;
  if (!p) { lcv.classList.add("hidden"); return; }
  const dpr = window.devicePixelRatio || 1;
  const R = 66, Z = 3;
  const sw = 2 * R / Z * dpr;
  const sx = clamp(p.x * dpr - sw / 2, 0, Math.max(0, cv.width - sw));
  const sy = clamp(p.y * dpr - sw / 2, 0, Math.max(0, cv.height - sw));
  lcv.classList.remove("hidden");
  const ctx = lcv.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, lcv.width, lcv.height);
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, lcv.width, lcv.height);
  ctx.imageSmoothingEnabled = false;
  try { ctx.drawImage(cv, sx, sy, sw, sw, 0, 0, lcv.width, lcv.height); } catch (_) {}
  // DE-10: the sample is `sw` DEVICE px wide and is drawn into the loupe's fixed
  // backing store, so the cursor's offset inside the sample maps by
  // lcv.width/sw. The old `offset / Z` (Z = 3) put the marker ~10x too close to
  // the origin - it never marked the sampled pixel.
  const ix = (p.x * dpr - sx) * lcv.width / sw;
  const iy = (p.y * dpr - sy) * lcv.height / sw;
  ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(ix, iy, 4, 0, 7); ctx.stroke();
}

function drawCalibGrid(ctx, W, H) {
  const azOff = +$("#azOffset").value;
  const ab = +$("#altBottom").value, at = +$("#altTop").value;
  const fov = +$("#fovAz").value || 360;

  // elevation lines
  ctx.strokeStyle = "rgba(120,160,255,.28)";
  ctx.fillStyle = "rgba(150,185,255,.8)";
  ctx.font = "10px Consolas, monospace";
  ctx.lineWidth = 1;
  for (let e = Math.ceil(ab / 15) * 15; e <= at; e += 15) {
    const y = H * (1 - (e - ab) / (at - ab));
    if (y < 8 || y > H - 4) continue;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.fillText(e + "\u00B0", 4, y - 3);
  }

  // compass ticks: azimuth runs azOff .. azOff+fov across the width
  const CARD = { 0: "N", 90: "E", 180: "S", 270: "W" };
  const firstTick = Math.ceil(azOff / 30) * 30;
  for (let a = firstTick; a <= azOff + fov; a += 30) {
    const x = (a - azOff) / fov * W;
    if (x < -1 || x > W + 1) continue;
    const cardA = ((a % 360) + 360) % 360;
    const major = cardA % 90 === 0 && fov >= 90 ? true : false;
    ctx.strokeStyle = major ? "rgba(255,209,102,.75)" : "rgba(255,209,102,.35)";
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, major ? 16 : 8); ctx.stroke();
    if (major && CARD[cardA] !== undefined) {
      ctx.fillStyle = "#ffd166";
      ctx.font = "bold 12px Segoe UI";
      ctx.fillText(CARD[cardA], x + 3, 14);
    }
  }
}

function strokeSegments(ctx, segments) {
  ctx.beginPath();
  for (const s of segments) {
    s.forEach(([x, y], k) => k ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  }
  ctx.stroke();
}

function drawProfileLine(ctx, W, H) {
  const prof = Pano.cur.profile;
  if (!prof || prof.length !== NBIN) return;
  const ab = +$("#altBottom").value, at = +$("#altTop").value;
  const azOff = +$("#azOffset").value || 0;
  const fov = +$("#fovAz").value || 360;
  const segs = profileSegments(prof, W, H, ab, at, azOff, fov);
  const sky = paintMode() === "sky";
  const blockAbove = sky && !!Pano.cur.block_above;
  const tops = (Pano.cur.profile_top && Pano.cur.profile_top.length === NBIN)
      ? Pano.cur.profile_top : null;
  // the band top is a real ceiling ONLY with "block above band"; by default
  // everything above the band is free sky (open above)
  const topSegs = blockAbove && tops
      ? profileSegments(tops, W, H, ab, at, azOff, fov, 88)
      : null;

  // blocked regions: below the obstruction line (and in sky mode also
  // above the painted band's top edge when block-above is on)
  ctx.fillStyle = "rgba(255,93,93,.14)";
  for (const s of segs) {
    ctx.beginPath();
    ctx.moveTo(s[0][0], H);
    s.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(s[s.length - 1][0], H);
    ctx.closePath();
    ctx.fill();
  }
  // sky mode, default open above: hatch the region ABOVE the painted band (it
  // is only visible because of the open-above assumption)
  if (sky && !blockAbove && tops) {
    for (const s of profileSegments(tops, W, H, ab, at, azOff, fov, 88)) {
      const xs = s.map(([x]) => x);
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      ctx.save();
      ctx.beginPath();
      s.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.lineTo(maxX, 0);
      ctx.lineTo(minX, 0);
      ctx.closePath();
      ctx.clip();
      ctx.strokeStyle = "rgba(210,225,255,.28)";
      ctx.lineWidth = 1;
      const diag = (maxX - minX) + H;
      for (let o = -diag; o < diag; o += 9) {
        ctx.beginPath();
        ctx.moveTo(minX + o, H);
        ctx.lineTo(minX + o + diag, 0);
        ctx.stroke();
      }
      ctx.restore();
    }
  }
  if (topSegs) {
    for (const s of topSegs) {
      ctx.beginPath();
      ctx.moveTo(s[0][0], 0);
      s.forEach(([x, y]) => ctx.lineTo(x, y));
      ctx.lineTo(s[s.length - 1][0], 0);
      ctx.closePath();
      ctx.fill();
    }
  }

  // solid obstruction line: obstacle mode only. In sky mode the fully
  // blocked columns sit at 90 deg (top of the image) and would draw a
  // spurious line along the edge with radial connectors.
  if (paintMode() === "obstacle") {
    ctx.strokeStyle = "#ff5d5d";
    ctx.lineWidth = 2;
    strokeSegments(ctx, segs);
  }
  if (topSegs) {
    ctx.setLineDash([6, 4]);
    strokeSegments(ctx, topSegs);
    ctx.setLineDash([]);
  }

  const compareId = $("#panoProfileCompare")?.value;
  const compare = compareId ? S.panos.find((p) => p.id === compareId) : null;
  if (compare && Array.isArray(compare.profile) && compare.profile.length === NBIN) {
    const compareSegs = profileSegments(compare.profile, W, H, ab, at, azOff, fov, 90.5);
    ctx.save();
    ctx.strokeStyle = "rgba(120,190,255,.95)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 3]);
    strokeSegments(ctx, compareSegs);
    if (sky && compare.block_above && Array.isArray(compare.profile_top)
        && compare.profile_top.length === NBIN) {
      strokeSegments(ctx, profileSegments(compare.profile_top, W, H,
                                           ab, at, azOff, fov, 90.5));
    }
    ctx.restore();
  }
}

/* ---------------- obstruction profile from mask ---------------- */

function recomputeProfile() {
  if (!Pano.cur) return;
  const sky = paintMode() === "sky";
  // Invariant (mirrors web/horizon.js::computeProfile): strokes win when
  // present; with NO strokes the stored profile is authoritative. Deriving an
  // empty profile from an empty mask would silently remove a horizon we cannot
  // re-derive, so a stroke-less imported panorama must not be blanked just by
  // saving. clearMask()/setPaintMode() blank the profile explicitly first
  // (profile=[]), which the length check below turns into a canonical blank.
  if (strokeList(Pano.cur).length === 0) {
    if (!Array.isArray(Pano.cur.profile) || Pano.cur.profile.length !== NBIN) {
      Pano.cur.profile = new Array(NBIN).fill(sky ? 90 : -90);
    }
    if (!Array.isArray(Pano.cur.profile_top)
        || (sky && Pano.cur.profile_top.length !== NBIN)) {
      Pano.cur.profile_top = sky ? new Array(NBIN).fill(90) : [];
    }
    invalidateMask();
    render();
    return;
  }
  const W2 = NBIN, H2 = 180;
  const cv = document.createElement("canvas");
  cv.width = W2; cv.height = H2;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#fff";
  const wraps = (+$("#fovAz").value || 360) >= 359.5;
  for (const s of strokeList(Pano.cur)) {
    ctx.globalCompositeOperation = s.m === "e" ? "destination-out" : "source-over";
    stampDab(ctx, s, W2, H2, wraps);
  }
  const data = ctx.getImageData(0, 0, W2, H2).data;
  const ab = +$("#altBottom").value, at = +$("#altTop").value;
  const azOff = +$("#azOffset").value;
  const fov = +$("#fovAz").value || 360;
  const prof = [];
  const profTop = [];
  for (let i = 0; i < NBIN; i++) {
    // FOV-relative position from the ONE core rule (CR3): a bin is in frame when
    // any part of its span overlaps [0, fov], so the bin straddling the frame
    // start maps to the left edge of the image instead of wrapping to 359.x and
    // clamping to the far column - which made an in-frame azimuth read the
    // out-of-frame sentinel for any azOffset that is not a multiple of 0.5.
    const rel = fovBinRel(i, azOff, fov);
    if (rel === null) {
      // outside the image: unblocked in obstacle mode, blocked in sky mode
      prof.push(sky ? 90 : -99);
      profTop.push(sky ? 90 : 90);
      continue;
    }
    const x = Math.min(W2 - 1, Math.floor(rel / fov * W2));
    if (sky) {
      // painted band = the visible sky. profile = bottom edge of the band
      // (blocked below), profile_top = top edge (blocked above - e.g. a
      // balcony out of frame). Unpainted column -> fully blocked.
      let topY = -1, botY = -1;
      for (let y = 0; y < H2; y++) {
        if (data[(y * W2 + x) * 4 + 3] > 40) { topY = y; break; }
      }
      for (let y = H2 - 1; y >= 0; y--) {
        if (data[(y * W2 + x) * 4 + 3] > 40) { botY = y; break; }
      }
      if (topY < 0) {
        prof.push(90);
        profTop.push(90);
      } else {
        const eBottom = at - (botY / H2) * (at - ab);
        const eTop = at - (topY / H2) * (at - ab);
        prof.push(+(eBottom).toFixed(1));
        // open above when the band reaches (near) the top of the frame:
        // brush strokes rarely touch the very edge, so a band within ~4
        // degrees of the calibration top counts as "painted to the top"
        // and leaves the sky above open. A band that stops well below the
        // top means the strip above it is unpainted -> blocked -> its top
        // edge becomes the ceiling (e.g. a balcony).
        profTop.push(eTop >= at - 4
                     ? 90.5
                     : +(eTop).toFixed(1));
      }
    } else {
      // top edge of the painted obstacle
      let top = -1;
      for (let y = 0; y < H2; y++) {
        if (data[(y * W2 + x) * 4 + 3] > 40) { top = y; break; }
      }
      prof.push(top < 0 ? -99 : +(at - (top / H2) * (at - ab)).toFixed(1));
    }
  }
  Pano.cur.profile = prof;
  Pano.cur.profile_top = sky ? profTop : [];
  invalidateMask();
  render();
}

/* ---------------- persistence ---------------- */

function syncRecordFromUI(rec) {
  rec.az_offset = +$("#azOffset").value;
  rec.alt_bottom = +$("#altBottom").value;
  rec.alt_top = +$("#altTop").value;
  rec.fov = +$("#fovAz").value;
  rec.paint_mode = paintMode();
  const block = $("#chkBlockAbove");
  rec.block_above = block ? !!block.checked : !!rec.block_above;
}

function syncEditorInputs(rec) {
  if (!rec) return;
  ensureHorizonMetadata(rec);
  const fields = [
    ["#azOffset", "#azOffVal", rec.az_offset ?? 0],
    ["#altBottom", "#abVal", rec.alt_bottom ?? 0],
    ["#altTop", "#atVal", rec.alt_top ?? 45],
    ["#fovAz", "#fovVal", rec.fov ?? 360],
  ];
  for (const [input, label, value] of fields) {
    const el = $(input), out = $(label);
    if (el) el.value = value;
    if (out) out.textContent = value + "\u00B0";
  }
  const block = $("#chkBlockAbove");
  if (block) block.checked = !!rec.block_above;
  const prov = rec.provenance || {};
  const setValue = (id, value) => { const el = $(id); if (el) el.value = value ?? ""; };
  setValue("#panoProvSource", prov.source || "unknown");
  setValue("#panoProvDate", prov.capturedAt);
  setValue("#panoProvOperator", prov.operator);
  setValue("#panoProvOrientation", prov.orientation);
  setValue("#panoProvNotes", prov.notes);
  const cmp = $("#panoProfileCompare");
  if (cmp) {
    cmp.innerHTML = '<option value="">Compare with…</option>'
      + S.panos.filter((p) => p.id !== rec.id)
        .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  }
  renderPanoLandmarks();
  readPanoProfileBin();
  comparePanoProfile();
  updateModeButtons();
}

/** The editable fields a save sends for `rec`, captured when the edit is
 * queued - so a flush after the user switched panoramas still sends the
 * values of the record it belongs to (DE-04). Deep copies keep a later edit
 * from rewriting an already queued explicit-save payload. */
function currentPanoFields(rec) {
  return {
    az_offset: rec.az_offset,
    alt_bottom: rec.alt_bottom,
    alt_top: rec.alt_top,
    fov: rec.fov,
    strokes: Array.isArray(rec.strokes) ? rec.strokes.map((s) => ({ ...s })) : [],
    profile: Array.isArray(rec.profile) ? rec.profile.slice() : [],
    profile_top: Array.isArray(rec.profile_top) ? rec.profile_top.slice() : [],
    paint_mode: rec.paint_mode,
    block_above: !!rec.block_above,
    landmarks: normalizeHorizonLandmarks(rec.landmarks),
    provenance: normalizeHorizonProvenance(rec.provenance),
    confidence: normalizeHorizonConfidence(rec.confidence),
  };
}

function scheduleSave() {
  const rec = Pano.cur;
  if (!rec) return;
  // derive the profile NOW: the flush may run when the DOM - and this record -
  // are already gone (a panorama switch or a window close)
  syncRecordFromUI(rec);
  recomputeProfile();
  const fields = currentPanoFields(rec);
  Object.assign(rec, fields);
  Pano.pending = { rec, fields };
  setSaveStatus("Unsaved changes", "dirty");
  announcePanoChanged("edit");
}

/** Explicit persistence boundary for the desktop editor. */
function requestSave() {
  scheduleSave();
  void flushSave();
}

/** Persist the pending edit (no-op when there is none). Called by Save, a
 *  panorama switch, navigation away from Horizon, and the unload guard.
 *
 *  DE-05: the WHOLE continuation is inside the guard. `api()` rethrows, so the
 *  list refresh used to reject the timer callback with nothing to catch it; it
 *  also replaced the entire `S.panos` array and rebound `Pano.cur`, which threw
 *  once the record had been deleted (`deleteCurrent` nulls `Pano.cur`) and could
 *  discard a DIFFERENT panorama's unsaved strokes. The merge below touches only
 *  the record that was actually saved, and only while the user is still on it. */
async function flushSave() {
  // A caller with no pending payload must still wait for an earlier explicit
  // Save. Otherwise a crop can race its PUT and have its new FOV overwritten.
  while (_saveInFlight) {
    if (!await _saveInFlight) return false;
  }
  const job = Pano.pending;
  Pano.pending = null;
  if (!job) return true;
  const saving = persistSave(job);
  _saveInFlight = saving;
  try {
    return await saving;
  } finally {
    if (_saveInFlight === saving) _saveInFlight = null;
  }
}

async function persistSave(job) {
  if (Pano.cur === job.rec) setSaveStatus("Saving...");
  try {
    await api("/api/panorama/" + job.rec.id, { method: "PUT",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(job.fields)});
    const list = await api("/api/panoramas");
    const upd = list.find(p => p.id === job.rec.id);
    if (upd) {
      const idx = S.panos.findIndex(p => p.id === job.rec.id);
      const stillEditing = Pano.cur === job.rec;
      // A second gesture may have been edited while this request was in
      // flight. Keep the local record and its list entry until that newer
      // payload is acknowledged; replacing it here would silently discard the
      // newer in-memory edit even though Pano.pending still held it.
      if (idx >= 0 && (!stillEditing || !Pano.pending)) S.panos[idx] = upd;
      if (stillEditing && !Pano.pending) {
        Pano.cur = upd;
        Pano.metadataGesture = false;
        setSaveStatus("Saved");
      }
    }
    return true;
  } catch {
    // api() already toasted; keep the local painting
    if (!Pano.pending) Pano.pending = job;
    if (Pano.cur === job.rec) setSaveStatus("Save failed", "failed");
    return false;
  }
}

// DE-04: closing the window inside the debounce window used to discard the edit
// with no trace at all. `pagehide` is the event that fires on a WebView2 /
// app-mode window close, and `keepalive` lets the request outlive the document.
window.addEventListener("pagehide", () => {
  const job = Pano.pending;
  if (!job) return;
  Pano.pending = null;
  try {
    fetch("/api/panorama/" + job.rec.id, {
      method: "PUT", keepalive: true,
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(job.fields),
    });
  } catch {
    /* the document is going away; there is nothing useful to report */
  }
});
