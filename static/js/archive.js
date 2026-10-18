/* Image Archive mode: mode switching, FITS import, library table */

import { $, esc, api, toast, setBusy, fmtSize, fmtDT, TYPE_LABEL } from "./util.js";
import { S } from "./state.js";
import { renderSkyMap } from "./skymap.js";
import { catalogIcon } from "./catalog_icons.js";

let _bound = false;
let _selectedLibraryObject = null;
let _selectedLibrarySession = 0;
let _importFrameOverride = null;

function emptyStateMarkup(icon, heading, lines, className = "") {
  const text = (Array.isArray(lines) ? lines : [lines]).filter(Boolean);
  const descriptions = text.length
    ? `<p>${text.map((line) => `<span>${esc(line)}</span>`).join("")}</p>` : "";
  const extra = className ? ` ${className}` : "";
  return `<div class="empty-state${extra}">${catalogIcon(icon, "empty-icon")}<strong>${esc(heading)}</strong>${descriptions}</div>`;
}

function syncFrameSelector() {
  const defaultFrame = "light";
  const chosenFrame = _impSession?.frame || _importFrameOverride || defaultFrame;
  for (const button of document.querySelectorAll("[data-frame-override]")) {
    const active = button.dataset.frameOverride === chosenFrame;
    button.setAttribute("aria-pressed", String(active));
  }
  const help = $("#frameTypeHelp");
  if (help) {
    help.textContent = _impSession
      ? `${_impSession.frame} exposures will be attached to the selected object and session. Original capture dates are preserved.`
      : _importFrameOverride
      ? `${_importFrameOverride} exposures will override the recorded frame type for the next import. Click the selected type to use exposure metadata.`
      : "Exposure metadata determines the frame type. Lights is the default when no frame type is recorded. Select a type to override the exposure metadata for the next import.";
  }
}

/* ---------------- state / archive folder ---------------- */

export function applyArchiveUI() {
  const dir = (S.cfg && S.cfg.image_dir) || "";
  const inp = $("#arcDir");
  if (inp) inp.value = dir;
  $("#archiveInfo").textContent = dir;
  $("#archiveInfo").title = "Image archive: " + dir;
  $("#archiveInfo").classList.toggle("hidden", !dir);
  $("#siteLat").value = S.cfg?.observing_site?.latitude ?? "";
  $("#siteLon").value = S.cfg?.observing_site?.longitude ?? "";
}

/* ---------------- import pane ---------------- */

const BATCH = 96;
const COMPRESSED_BATCH = 24;
const MAX_BATCH_BYTES = 512 * 1024 * 1024;
let _archiveOperation = null;
let _reindexErrorNotice = null;

function elapsedLabel(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  const tail = `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  return hours ? `${hours}:${tail}` : tail;
}

function lockArchiveActions() {
  const selectors = "#btnTopIngest, #btnIngest, #btnIngestFolder, #fitFolder, #btnTopReindex, #btnArcReindex, #btnTopExport, #btnTopCopy, #btnTopHealth, #btnClearResults, #btnArcSave, #btnArcBrowse, #arcDir, #siteLat, #siteLon, #btnSiteSave, #compressOnIngest, #ingestSafeMode, #libEditSave, [data-edit], [data-frame-override], [data-library-add], [data-rice-mode], [data-imp]";
  const controls = [...document.querySelectorAll(selectors)];
  const previous = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  return () => controls.forEach((control, i) => { control.disabled = previous[i]; });
}

export function beginArchiveHealth() {
  if (_archiveOperation) {
    toast("Wait for the current archive operation to finish.", "info", 4000);
    return null;
  }
  _archiveOperation = "health";
  const unlock = lockArchiveActions();
  $("#btnTopHealth").disabled = false;
  setBusy(true, "Checking archive health...");
  return () => { unlock(); _archiveOperation = null; setBusy(false); };
}

export function beginCompression() {
  if (_archiveOperation) {
    toast("Wait for the current archive operation to finish.", "info", 4000);
    return null;
  }
  _archiveOperation = "compression";
  const unlock = lockArchiveActions();
  $("#btnTopCopy").disabled = false;
  setBusy(true, "Converting FITS files...");
  return () => { unlock(); _archiveOperation = null; renderLibrary(); setBusy(false); };
}

function startImportActivity(total) {
  const progress = $("#ingestProgress");
  if (progress) {
    progress.hidden = false;
    progress.innerHTML = '<span class="import-files"></span><span class="import-elapsed" aria-live="off"></span><span class="import-skipped" hidden></span><span class="import-restored" hidden></span><span class="import-failed" hidden></span><span class="import-pending" hidden></span><span class="import-request-error" hidden></span>';
  }
  const busy = $("#busy");
  const clock = document.createElement("span");
  clock.className = "busy-elapsed";
  clock.setAttribute("aria-live", "off");
  busy?.appendChild(clock);
  const activity = {total, processed: 0, skipped: 0, restored: 0, failed: 0, unconfirmed: 0,
    started: performance.now(), elapsed: null};
  const update = (active = true) => {
    const elapsed = elapsedLabel(activity.elapsed ?? performance.now() - activity.started);
    const label = `Importing ${activity.processed}/${total}` +
      (activity.skipped ? ` · ${activity.skipped} already imported` : "") +
      (activity.restored ? ` · ${activity.restored} original${activity.restored === 1 ? "" : "s"} restored` : "") +
      (activity.failed ? ` · ${activity.failed} failed` : "");
    if (active) {
      setBusy(true, label);
      const text = busy?.querySelector(".busy-txt");
      if (text) text.innerHTML = `<span class="busy-action">Importing </span>${activity.processed}/${total}` +
        (activity.failed ? `<span class="busy-failures"> · ${activity.failed} failed</span>` : "");
    }
    clock.textContent = `· ${elapsed}`;
    if (busy) busy.title = `${label} · ${elapsed} elapsed`;
    if (!progress) return;
    progress.querySelector(".import-files").textContent = `${activity.processed} / ${total} files processed`;
    progress.querySelector(".import-elapsed").textContent = `${elapsed} elapsed`;
    const skipped = progress.querySelector(".import-skipped");
    skipped.hidden = !activity.skipped;
    skipped.textContent = `${activity.skipped} already imported`;
    const restored = progress.querySelector(".import-restored");
    restored.hidden = !activity.restored;
    restored.textContent = `${activity.restored} original${activity.restored === 1 ? "" : "s"} restored`;
    const failed = progress.querySelector(".import-failed");
    failed.hidden = !activity.failed;
    failed.textContent = `${activity.failed} failed`;
    const pending = progress.querySelector(".import-pending");
    const remaining = total - activity.processed - activity.unconfirmed;
    pending.hidden = active || !remaining;
    pending.textContent = `${remaining} not attempted`;
    const requestError = progress.querySelector(".import-request-error");
    requestError.hidden = !activity.unconfirmed;
    requestError.textContent = `Request failed: ${activity.unconfirmed} file outcomes unconfirmed.`;
  };
  update();
  // Update the clock independently so elapsed time does not trigger live
  // announcements of the file count every second.
  const timer = setInterval(() => {
    const elapsed = elapsedLabel(performance.now() - activity.started);
    clock.textContent = `· ${elapsed}`;
    if (progress) progress.querySelector(".import-elapsed").textContent = `${elapsed} elapsed`;
  }, 1000);
  activity.update = update;
  activity.finish = () => {
    activity.elapsed = performance.now() - activity.started;
    clearInterval(timer);
    update(false);
    clock.remove();
    setBusy(false);
  };
  return activity;
}

const importChunks = function* (files, maxFiles) {
  let chunk = [], bytes = 0;
  for (const file of files) {
    if (chunk.length && (chunk.length >= maxFiles || bytes + file.size > MAX_BATCH_BYTES)) {
      yield chunk;
      chunk = [];
      bytes = 0;
    }
    // A large exposure travels alone; the server enforces its per-file limit.
    chunk.push(file);
    bytes += file.size;
  }
  if (chunk.length) yield chunk;
};

async function ingestFiles(files, frame, session, sessionAnchorId, source = "files") {
  if (_archiveOperation) {
    toast("Wait for the current archive operation to finish.", "info", 4000);
    return;
  }
  const list = Array.from(files || []).filter(
    f => /\.(fit|fits|fts|fz)(\.gz)?$/i.test(f.name));
  if (!list.length) {
    toast(source === "folder"
      ? "No supported FITS exposures found in this folder or its subfolders."
      : "No supported exposures selected (FITS, .fz or FITS.gz).", "err", 4000);
    return;
  }
  const fromFrameSelector = frame == null && session == null;
  const selectedFrame = fromFrameSelector ? _importFrameOverride : frame;
  const compress = $("#compressOnIngest").checked;
  const compressionMode = $("#ingestSafeMode").checked ? "safe" : "fast";
  const batchSize = compress ? COMPRESSED_BATCH : BATCH;
  // one run id per import action: the server merges the per-chunk history
  // rows, so a whole session stays ONE entry in "Recent imports"
  const runId = crypto.randomUUID();
  const results = [];
  _archiveOperation = "import";
  renderIngestResults([]);
  const unlock = lockArchiveActions();
  const activity = startImportActivity(list.length);
  try {
    let submitted = 0;
    for (const chunk of importChunks(list, batchSize)) {
      submitted += chunk.length;
      // The server streams parts as they arrive: options and the declared
      // file count must precede the files so each file can publish once ready.
      const fd = new FormData();
      if (selectedFrame) fd.append("frame", selectedFrame);
      if (session) fd.append("session", session);
      if (sessionAnchorId) fd.append("session_anchor_id", sessionAnchorId);
      fd.append("run_id", runId);
      fd.append("compress", String(compress));
      fd.append("compression_mode", compressionMode);
      fd.append("file_count", String(chunk.length));
      for (const f of chunk) fd.append("files", f);
      let res;
      try {
        // FITS batches can take longer than the helper's generic 30s timeout.
        res = await api("/api/images/ingest", { method: "POST", body: fd, timeout: 0 });
        if (!Array.isArray(res.results)) throw new Error("Invalid import response");
      } catch (err) {
        for (const f of chunk) results.push({ file: f.name, ok: false,
          error: "Import request failed; file outcome unconfirmed" });
        activity.unconfirmed = chunk.length;
        activity.update();
        break;
      }
      results.push(...res.results);
      const confirmed = res.results.filter(result => !result.unconfirmed && !result.unattempted);
      activity.processed += confirmed.length;
      activity.skipped += res.results.filter(result => result.ok && result.skipped).length;
      activity.restored += res.results.filter(result => result.ok && result.restored).length;
      activity.failed += confirmed.filter(result => !result.ok).length;
      activity.unconfirmed += res.results.filter(result => result.unconfirmed).length;
      activity.update();
      if (res.results.some(result => result.unconfirmed || result.unattempted)) {
        for (const file of list.slice(submitted)) results.push({
          file: file.name, ok: false, unattempted: true,
          error: "Not imported because the earlier request could not finish." });
        break;
      }
    }
    renderIngestResults(results);
    S.library = null;                       // stale until refetched
  } finally {
    activity.finish();
    unlock();
    _archiveOperation = null;
    if (fromFrameSelector && selectedFrame) {
      _importFrameOverride = null;
      syncFrameSelector();
    }
  }
  await loadLibrary();
}

export async function exportLibrary() {
  if (_archiveOperation) {
    toast("Wait for the current archive operation to finish.", "info", 4000);
    return;
  }
  _archiveOperation = "export";
  const unlock = lockArchiveActions();
  setBusy(true, "Preparing library export...");
  try {
    const response = await fetch("/api/library/export?format=csv");
    if (!response.ok) {
      let message = response.statusText || `HTTP ${response.status}`;
      try { message = (await response.json()).detail || message; } catch (err) {}
      throw new Error(message);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "nightseam-library.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Keep the blob alive while the browser/native download handler takes over.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast("Library export download started.", "info", 4000);
  } catch (err) {
    toast(`Library export failed: ${err.message || "Could not download export"}`);
  } finally {
    setBusy(false);
    unlock();
    _archiveOperation = null;
  }
}

function renderIngestResults(results) {
  const box = $("#ingestResults");
  if (!box) return;
  _ingestCache = results;
  const clear = $("#btnClearResults");
  const summary = $("#ingestSummary");
  if (!results.length) {
    if (clear) clear.hidden = true;
    if (summary) summary.textContent = "";
    box.innerHTML = emptyStateMarkup("file", "Imported sessions appear here.",
      "Files will be copied to your archive folder and added to the catalog.");
    return;
  }
  const ok = results.filter(r => r.ok && !r.skipped && !r.restored).length;
  const skipped = results.filter(r => r.ok && r.skipped).length;
  const restored = results.filter(r => r.ok && r.restored).length;
  const compressed = results.filter(r => r.ok && r.compression === "rice").length;
  const fails = results.filter(r => !r.ok);
  if (clear) clear.hidden = false;
  if (summary) summary.textContent = `${ok} of ${results.length} files imported` +
    (skipped ? ` · ${skipped} already imported` : "") +
    (restored ? ` · ${restored} original${restored === 1 ? "" : "s"} restored` : "") +
    (compressed ? ` · ${compressed} Rice compressed` : "") +
    (fails.length ? ` · ${fails.length} failed` : "");
  box.innerHTML = results.map((result) => {
    const object = result.object || {};
    const objectName = object.name || object.object_name || "";
    const alreadyImported = result.ok && result.skipped;
    const originalRestored = result.ok && result.restored;
    const status = alreadyImported ? "skipped" : originalRestored ? "restored" : result.ok ? "ok" : "err";
    const description = alreadyImported
      ? `Already imported${objectName ? ` · ${objectName}` : ""}`
      : originalRestored ? `Original restored${objectName ? ` · ${objectName}` : ""}`
      : objectName || (result.ok ? "Imported" : "");
    return `<div class="ingest-item ${status}">
      ${catalogIcon(alreadyImported || originalRestored ? "file" : result.ok ? "check" : "warning", "ingest-item-icon")}
      <span class="nm" title="${esc(result.file)}">${esc(result.file)}</span>
      <span class="obj">${esc(description)}${result.compression === "rice" ? ` · Rice compressed${result.compression_mode === "fast" ? ' · Fast (restoration unchecked)' : result.compression_mode === "safe" ? ' · Safe (restoration verified)' : ''}` : result.compression_reason ? ` · ${esc(result.compression_reason)}` : ''}</span>
      ${result.ok ? "" : `<span class="bad">${esc(result.error || "failed")}</span>`}
    </div>`;
  }).join("");
}

let _ingestCache = [];

/* ---------------- recent imports (per-session history) ---------------- */

let _impSession = null;     // pending {anchorId, frame} for the calibration picker

function renderImports(list) {
  const box = $("#importHistory");
  if (!box) return;
  if (!list || !list.length) {
    box.innerHTML = emptyStateMarkup("clock", "Recent imports appear here.",
      "You can also add darks, flats, or bias to an existing session.");
    return;
  }
  box.innerHTML = list.map(b => {
    const prefix = `${b.slug}/${b.session}/`.toLowerCase();
    const anchor = (S.library?.objects || []).flatMap(object => object.images || [])
      .find(image => Number.isSafeInteger(image.id) && image.id > 0 &&
        String(image.file || "").replaceAll("\\", "/").toLowerCase().startsWith(prefix));
    const anchorAttrs = anchor ? `data-imp-anchor="${anchor.id}"`
      : 'disabled title="This session is no longer available in the library."';
    const dur = b.total_exposure > 0 ? ` \u00B7 ${fmtDur(b.total_exposure)}` : "";
    const loc = `${esc(b.slug)}/${esc(b.session)}/${esc(b.frame)}s`;
    return `<div class="imp-item">
      <div class="imp-head">
        <b>${esc(b.object_name || b.slug)}</b>
        <span class="hint">${esc(b.session)} \u00B7 ${esc(b.frame)} \u00D7 ${b.n_files}${dur}</span>
      </div>
      <div class="imp-path mono" title="archive folder">${loc}</div>
      <div class="imp-btns">
        <button class="ghost" data-imp="dark" ${anchorAttrs}>+ darks</button>
        <button class="ghost" data-imp="flat" ${anchorAttrs}>+ flats</button>
        <button class="ghost" data-imp="bias" ${anchorAttrs}>+ bias</button>
        <button class="ghost imp-open icon-button" data-open="${esc(b.slug)}/${esc(b.session)}" title="Open this session's folder" aria-label="Open this session's folder">${catalogIcon("folder")}</button>
      </div>
    </div>`;
  }).join("");
}

async function loadImports() {
  try {
    const list = await api("/api/library/imports", { silent: true });
    renderImports(list);
  } catch (err) { /* non-fatal */ }
}

function openFolder(rel) {
  api("/api/library/open_folder", {
    method: "POST", silent: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rel })
  }).then(() => toast("Opening archive folder in the file manager.", "info", 3000))
    .catch(err => toast(`Cannot open archive folder: ${err.message}`, "err", 6000));
}

/* ---------------- library pane ---------------- */

let _editTarget = null;

export async function loadLibrary() {
  try {
    S.library = await api("/api/library");
    S.cfg = await api("/api/state");
    applyArchiveUI();
    renderLibrary();
    loadImports();
    if ($("#tab-image-catalog.active") && $("#tab-skymap.active")) renderSkyMap();
    return true;
  } catch (err) { /* api() already toasted */ return false; }
}

function fmtDur(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
  if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

export function renderLibrary() {
  if (!$("#libraryObjectList")) return;
  const L = S.library;
  const unit = S.cfg?.size_unit || "arcmin";
  const allObjects = [...(L?.objects || [])];
  if (!allObjects.length) {
    _selectedLibraryObject = null;
    _selectedLibrarySession = 0;
    renderLibraryObjectHeader(null, unit);
    $("#libraryObjectList").innerHTML = emptyStateMarkup(
      "folder", "No objects yet", "Import exposures to build your image library.",
      "objects-empty-state");
    $("#librarySessions").innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "database", "No capture sessions yet", "Import exposures to build your image library.",
      "session-empty-state")}</td></tr>`;
    $("#librarySessionFacts").innerHTML = emptyStateMarkup("file",
      "No session selected", ["Choose a capture session", "from the library to view details", "and frames."],
      "selected-session-empty");
    $("#librarySessionActions").innerHTML = "";
    $("#librarySessionActions").hidden = true;
    $("#libraryFrameCount").textContent = "0 frames";
    $("#librarySessionCount").textContent = "0 sessions";
    $("#libObjectCount").textContent = "0 objects";
    return;
  }
  const sort = $("#libSort").value;
  allObjects.sort((a, b) => {
    if (sort === "integ") return b.total_exposure - a.total_exposure;
    if (sort === "sessions") return b.n_sessions - a.n_sessions;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  const q = $("#libSearch").value.trim().toLocaleLowerCase();
  const objs = q ? allObjects.filter((o) =>
    [o.name, o.designation, o.otype].some((x) => String(x || "").toLocaleLowerCase().includes(q))) : allObjects;
  const selected = objs.find((o) => o.name === _selectedLibraryObject) || objs[0] || null;
  if (selected?.name !== _selectedLibraryObject) {
    _selectedLibrarySession = 0;
  }
  _selectedLibraryObject = selected?.name || null;
  const selectedSessions = selected?.sessions || [];
  if (_selectedLibrarySession >= selectedSessions.length) {
    _selectedLibrarySession = 0;
  }
  const selectedSession = selectedSessions[_selectedLibrarySession] || null;
  $("#libObjectCount").textContent = q
    ? `${objs.length} of ${allObjects.length} objects` : `${allObjects.length} objects`;
  $("#libraryObjectList").innerHTML = objs.length ? objs.map((o) => {
    const key = esc(o.name || "");
    return `<button class="library-object${o.name === _selectedLibraryObject ? " active" : ""}" type="button" data-library-object="${key}" aria-pressed="${o.name === _selectedLibraryObject}">
      <span><b>${esc(o.name || "?")}</b><small>${esc(o.designation || "no designation")}</small></span>
      <small>${o.n_sessions} session${o.n_sessions === 1 ? "" : "s"}</small>
    </button>`;
  }).join("") : '<p class="hint">No objects match this search.</p>';
  renderLibraryObjectHeader(selected, unit);
  if (!selected) {
    $("#librarySessions").innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "database", "No matching objects", "Adjust or clear the search to see capture sessions.",
      "session-empty-state")}</td></tr>`;
    $("#librarySessionCount").textContent = "0 sessions";
    $("#libraryFrameCount").textContent = "0 frames";
    $("#librarySessionFacts").innerHTML = emptyStateMarkup("file",
      "No object selected", "Choose an object from the list to view its sessions and frames.",
      "selected-session-empty");
    $("#librarySessionActions").innerHTML = "";
    $("#librarySessionActions").hidden = true;
    return;
  }
  renderLibrarySessions(selected, selectedSessions, selectedSession);
}

function renderLibraryObjectHeader(object, unit) {
  const identity = $("#libraryHeroIdentity");
  if (!object) {
    identity.innerHTML = '<h1>Library</h1><p class="hint">No object selected</p>';
    $("#libSummary").innerHTML = libraryMetricHTML(null);
    return;
  }
  const key = esc(object.name || "?");
  const type = TYPE_LABEL[object.otype] || object.otype || "Unknown type";
  const size = fmtSize(object.size_am, unit).replace(/[′″°]/g, "");
  const units = { arcmin: ["′", "arcminutes", "degrees"], deg: ["°", "degrees", "arcseconds"], arcsec: ["″", "arcseconds", "arcminutes"] };
  const [symbol, name, next] = units[unit] || units.arcmin;
  identity.innerHTML = `<h1>Library</h1><div class="library-active-object"><strong>${key}</strong>
    ${!object.object_id ? `<button class="ghost lib-edit" type="button" data-edit="${key}">Edit object</button>` : ""}
    <button class="ghost lib-open icon-button" type="button" data-openobj="${key}" title="Open object folder" aria-label="Open object folder">${catalogIcon("folder")}</button></div>
    <p class="library-object-description">${esc([object.designation, type].filter(Boolean).join(" · "))} · <span class="mono">Size ${esc(size)}<button class="library-size-unit" type="button" aria-label="Size units: ${name}. Switch to ${next}" title="Click to switch to ${next}">${symbol}</button></span></p>`;
  $("#libSummary").innerHTML = libraryMetricHTML(object);
}

function libraryObjectRollup(sessions) {
  const totals = { light: 0, dark: 0, flat: 0, bias: 0, lightExposure: 0, filters: new Set() };
  for (const session of sessions || []) {
    const types = Object.entries(session.types || {});
    for (const kind of ["light", "dark", "flat", "bias"]) {
      const entry = types.find(([name]) => String(name).toLowerCase().includes(kind));
      if (!entry) continue;
      totals[kind] += Number(entry[1]?.n) || 0;
      if (kind === "light") totals.lightExposure += Number(entry[1]?.exposure) || 0;
    }
    for (const [name] of Object.entries(session.filters || {}))
      if (name !== "?") totals.filters.add(name);
  }
  return totals;
}

function libraryMetricHTML(object) {
  const total = libraryObjectRollup(object?.sessions || []);
  const fields = [
    ["Sessions", (object?.sessions || []).length.toLocaleString()],
    ["Images", (Number(object?.n_images) || 0).toLocaleString()],
    ["Light integration", fmtDur(total.lightExposure)],
  ];
  return fields.map(([label, value]) =>
    `<div class="library-metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join("");
}

function imageFolderPath(image) {
  const parts = String(image?.file || "").split(/[\\/]/).filter(Boolean);
  return parts.length ? parts.slice(0, -1).join("/") : null;
}

function sessionImages(object, session) {
  const images = object?.images || [];
  if (Array.isArray(session?.image_ids)) {
    const ids = new Set(session.image_ids);
    return images.filter(image => ids.has(image.id));
  }
  const start = session?.start ? Date.parse(session.start) : null;
  const end = session?.end ? Date.parse(session.end) : null;
  return images.filter((image) => {
    const groupingDate = image.session_date_obs ?? image.date_obs;
    const t = groupingDate ? Date.parse(groupingDate) : NaN;
    return session?.undated ? !Number.isFinite(t)
      : Number.isFinite(t) && t >= start && t <= end;
  });
}

function sessionFolderPaths(object, session) {
  const folders = sessionImages(object, session).map(imageFolderPath).filter((folder) => folder !== null);
  return [...new Map(folders.map((folder) => [folder.toLowerCase(), folder])).values()];
}

function selectLibrarySession(index) {
  _selectedLibrarySession = index;
  renderLibrary();
  $(`#librarySessions [data-select-session="${index}"]`)?.focus({ preventScroll: true });
}

function sessionCompression(session) {
  const counts = session.compression || {};
  const compressed = Number(counts.compressed) || 0;
  const plain = Number(counts.uncompressed) || 0;
  const unknown = Math.max(Number(counts.unknown) || 0, (Number(session.n_images) || 0) - compressed - plain);
  const state = unknown || !compressed && !plain ? "unknown"
    : compressed && plain ? "mixed" : compressed ? "compressed" : "uncompressed";
  const label = { compressed: "Compressed", uncompressed: "Uncompressed", mixed: "Mixed compression", unknown: "Compression unknown" }[state];
  const detail = `${label} · ${compressed} compressed, ${plain} uncompressed` +
    (unknown ? `, ${unknown} unknown. Reindex the archive to identify older files.` : "");
  return { state, label, detail, icon: state === "uncompressed" ? "file" : state };
}

function renderLibrarySessions(object, sessions, activeSession) {
  const heading = $("#librarySessionsHeading");
  const count = $("#librarySessionCount");
  if (heading) heading.textContent = "Capture sessions";
  if (count) count.textContent = `${sessions.length} sessions`;
  const list = $("#librarySessions");
  if (!list) return;
  if (!sessions.length) {
    list.innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "database", "No capture sessions yet", "Import exposures to build your image library.",
      "session-empty-state")}</td></tr>`;
    $("#libraryFrameCount").textContent = "0 frames";
    $("#librarySessionFacts").innerHTML = emptyStateMarkup("file",
      "No session selected", ["Choose a capture session", "from the library to view details", "and frames."],
      "selected-session-empty");
    $("#librarySessionActions").innerHTML = "";
    $("#librarySessionActions").hidden = true;
    return;
  }
  list.innerHTML = sessions.map((s, i) => {
    const when = `${s.label || "Session"} · ` + (s.undated ? "No date recorded" : `${fmtDT(s.start)} – ${fmtDT(s.end)}`);
    const compression = sessionCompression(s);
    const filters = Object.keys(s.filters || {}).filter((name) => name !== "?").join(" · ");
    const folders = sessionFolderPaths(object, s);
    const typeCount = (needle) => Object.entries(s.types || {}).reduce((sum, [name, value]) =>
      name.toLowerCase().includes(needle) ? sum + (Number(value.n) || 0) : sum, 0);
    const folder = folders.length ? `<button class="library-folder-link" type="button" data-open-library-folder="${esc(folders[0])}" title="${esc(folders.join(" · ") || "Archive root")}">${esc(folders[0].split("/").slice(-2).join("/") || "Archive root")}${folders.length > 1 ? ` +${folders.length - 1}` : ""}</button>` : '<span class="hint">Not recorded</span>';
    const lightEntry = Object.entries(s.types || {}).find(([name]) => name.toLowerCase().includes("light"));
    const lightExposure = Number(lightEntry?.[1]?.exposure) || 0;
    return `<tr class="library-session${sessions.indexOf(activeSession) === i ? " active" : ""}" data-library-session="${i}" tabindex="-1" aria-selected="${sessions.indexOf(activeSession) === i}">
      <td><button class="session-select" type="button" data-select-session="${i}" title="${esc(when + ' · ' + compression.detail)}"><span class="session-compression" data-compression="${compression.state}" role="img" aria-label="${esc(compression.detail)}" title="${esc(compression.detail)}">${catalogIcon(compression.icon)}</span><span class="session-select-label">${esc(when)}</span></button></td>
      <td class="mono">${typeCount("light")}</td><td class="mono">${typeCount("dark")}</td><td class="mono">${typeCount("flat")}</td><td class="mono">${typeCount("bias")}</td>
      <td class="mono" title="Light frames only">${esc(fmtDur(lightExposure))}</td><td>${esc(filters || "—")}</td><td>${folder}</td></tr>`;
  }).join("");
  if (!activeSession) {
    $("#libraryFrameCount").textContent = "0 frames";
    $("#librarySessionFacts").innerHTML = emptyStateMarkup("file",
      "No session selected", ["Choose a session", "from the library to view details", "and frames."],
      "selected-session-empty");
    $("#librarySessionActions").innerHTML = "";
    $("#librarySessionActions").hidden = true;
    return;
  }
  const typeLabels = { light: "Lights", dark: "Darks", flat: "Flats", bias: "Bias" };
  const typeRows = Object.entries(activeSession.types || {}).map(([kind, value]) =>
    `<div><span>${esc(typeLabels[kind] || kind)}</span><b>${Number(value.n) || 0} · ${esc(fmtDur(value.exposure))}</b></div>`).join("");
  const lightEntry = Object.entries(activeSession.types || {}).find(([name]) => name.toLowerCase().includes("light"));
  const lightExposure = Number(lightEntry?.[1]?.exposure) || 0;
  const filterText = Object.keys(activeSession.filters || {}).map(esc).join(" · ");
  const folders = sessionFolderPaths(object, activeSession);
  const compression = sessionCompression(activeSession);
  $("#libraryFrameCount").textContent = `${activeSession.n_images} frames`;
  $("#librarySessionFacts").innerHTML = `<p class="session-date">${esc(activeSession.undated ? "No date recorded" : `${fmtDT(activeSession.start)} – ${fmtDT(activeSession.end)}`)}</p>
    <dl class="library-session-facts"><div><dt>Session</dt><dd>${esc(activeSession.label || "Session")}</dd></div>
    <div><dt>Storage</dt><dd title="${esc(compression.detail)}">${esc(compression.label)}</dd></div>
    ${activeSession.session_start && activeSession.session_end ? `<div><dt>Session window</dt><dd>${esc(fmtDT(activeSession.session_start))} – ${esc(fmtDT(activeSession.session_end))}</dd></div>` : ""}
    <div><dt>Total exposure</dt><dd>${esc(fmtDur(activeSession.exposure))}</dd></div>
    <div><dt>Light integration</dt><dd>${esc(fmtDur(lightExposure))}</dd></div></dl>
    <div class="library-frame-rollup">${typeRows || '<p class="hint">No frame-type rollup recorded.</p>'}</div>
    <p class="library-filter-rollup"><span class="hint">Filters</span><br>${filterText || "No filter recorded"}</p>
    <div class="library-folder-rollup"><span class="hint">Archive folders</span><div class="library-archive-folders" role="group" aria-label="Archive folders">${folders.length ? folders.map((rel) => `<button class="library-folder-link" data-open-library-folder="${esc(rel)}" title="${esc(rel || "Archive root")}" type="button">${esc(rel || "Archive root")}</button>`).join("") : "Not recorded"}</div></div>`;
  const actions = $("#librarySessionActions");
  if (actions) {
    actions.hidden = false;
    const rel = folders[0] || "";
    const anchor = sessionImages(object, activeSession).find(image =>
      Number.isSafeInteger(image.id) && image.id > 0);
    const canTargetSession = Boolean(anchor);
    const targetAttrs = anchor ? `data-library-anchor="${anchor.id}"`
      : 'disabled title="Reload the library to select a recorded exposure for this session."';
    actions.innerHTML = `<h3>Actions</h3><div class="btnrow">
      <button class="ghost" type="button" data-library-add="dark" ${targetAttrs}>+ Darks</button>
      <button class="ghost" type="button" data-library-add="flat" ${targetAttrs}>+ Flats</button>
      <button class="ghost" type="button" data-library-add="bias" ${targetAttrs}>+ Bias</button>
      <button class="ghost" type="button" data-open-library-folder="${esc(rel)}" ${folders.length ? "" : "disabled"}>Open folder</button>
      <button class="ghost" type="button" data-rice-mode="compress" data-rice-ids="${(activeSession.image_ids || []).join(',')}" ${targetAttrs}>Compress losslessly</button>
      <button class="ghost" type="button" data-rice-mode="uncompress" data-rice-ids="${(activeSession.image_ids || []).join(',')}" ${targetAttrs}>Uncompress</button>
      </div>${canTargetSession ? "" : '<p class="hint">Reload the library before adding calibration exposures.</p>'}`;
    if (_archiveOperation) actions.querySelectorAll('[data-library-add], [data-rice-mode]').forEach(button => { button.disabled = true; });
  }
}

function openEdit(name) {
  if (_archiveOperation) {
    toast("Wait for archive health work to finish.", "info", 4000);
    return;
  }
  const o = (S.library.objects || []).find(x => x.name === name);
  if (!o || o.object_id) return;
  _editTarget = o;
  $("#libEditName").value = o.name || "";
  $("#libEditType").value = o.otype || "";
  $("#libEditModal").classList.remove("hidden");
  $("#libEditName").focus();
}

async function saveEdit() {
  if (_archiveOperation === "health") {
    toast("Wait for archive health work to finish.", "info", 4000);
    return;
  }
  if (!_editTarget) return;
  const o = _editTarget;
  const newName = $("#libEditName").value.trim();
  const otype = $("#libEditType").value;
  if (!newName) { toast("Name must not be empty.", "err", 3000); return; }
  try {
    await api("/api/library/edit", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: o.name, new_name: newName, otype }) });
    $("#libEditModal").classList.add("hidden");
    _editTarget = null;
    S.library = null;
    await loadLibrary();
    renderSkyMap();
  } catch (err) { /* api() already toasted */ }
}

/* ---------------- bindings ---------------- */

export function bindArchive() {
  if (_bound) return;
  _bound = true;
  document.querySelectorAll("[data-frame-override]").forEach((button) => {
    button.addEventListener("click", () => {
      const requested = button.dataset.frameOverride;
      _importFrameOverride = _importFrameOverride === requested ? null : requested;
      syncFrameSelector();
    });
  });
  syncFrameSelector();

  const mode = $("#ingestSafeMode"), modeGroup = $("#ingestCompressionMode");
  const modeInfo = $("#ingestModeInfo"), modeTip = $("#ingestModeTip");
  // Portal the explanation out of the scrolling import card so it cannot clip.
  document.body.appendChild(modeTip);
  let pinnedInfo = false, infoHideTimer = null;
  const showModeInfo = show => {
    clearTimeout(infoHideTimer);
    modeTip.hidden = !show;
    modeInfo.setAttribute("aria-expanded", String(show));
    if (show) {
      const anchor = modeGroup.getBoundingClientRect(), box = modeTip.getBoundingClientRect();
      modeTip.style.left = `${Math.max(8, Math.min(anchor.left, innerWidth - box.width - 8))}px`;
      const below = anchor.bottom + 6;
      modeTip.style.top = `${Math.max(8, below + box.height <= innerHeight - 8 ? below : anchor.top - box.height - 6)}px`;
    }
  };
  const deferModeInfoHide = () => {
    clearTimeout(infoHideTimer);
    infoHideTimer = setTimeout(() => {
      if (!pinnedInfo && !modeGroup.matches(":hover") && !modeTip.matches(":hover") && !modeGroup.contains(document.activeElement)) showModeInfo(false);
    }, 180);
  };
  const syncMode = () => {
    mode.disabled = Boolean(_archiveOperation) || !$("#compressOnIngest").checked;
    modeGroup.dataset.mode = mode.checked ? "safe" : "fast";
  };
  $("#compressOnIngest").addEventListener("change", syncMode);
  mode.addEventListener("change", syncMode);
  modeGroup.addEventListener("mouseenter", () => showModeInfo(true));
  modeGroup.addEventListener("mouseleave", deferModeInfoHide);
  modeTip.addEventListener("mouseenter", () => showModeInfo(true));
  modeTip.addEventListener("mouseleave", deferModeInfoHide);
  modeGroup.addEventListener("focusin", () => showModeInfo(true));
  modeGroup.addEventListener("focusout", event => { if (!pinnedInfo && !modeGroup.contains(event.relatedTarget)) deferModeInfoHide(); });
  modeInfo.addEventListener("click", () => { pinnedInfo = !pinnedInfo; showModeInfo(pinnedInfo); });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !modeTip.hidden) { event.preventDefault(); event.stopPropagation(); pinnedInfo = false; showModeInfo(false); }
  });
  document.addEventListener("pointerdown", event => { if (!modeGroup.contains(event.target) && !modeTip.contains(event.target)) { pinnedInfo = false; showModeInfo(false); } });
  const repositionModeInfo = () => { if (!modeTip.hidden) showModeInfo(true); };
  document.addEventListener("scroll", repositionModeInfo, true);
  window.addEventListener("resize", repositionModeInfo);
  syncMode();

  // archive folder
  $("#btnArcBrowse").addEventListener("click", async () => {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.pick_archive_dir !== "function") {
      toast("Folder dialog is only available in the native window - type the path instead.",
            "err", 6000);
      return;
    }
    const dir = await bridge.pick_archive_dir($("#arcDir").value.trim());
    if (dir) {
      $("#arcDir").value = dir;
      $("#btnArcSave").click();
    }
  });
  $("#btnArcSave").addEventListener("click", async () => {
    const v = $("#arcDir").value.trim();
    if (!v) {
      $("#arcDirHint").textContent = "Empty = default folder is used.";
      return;
    }
    try {
      await api("/api/settings", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_dir: v })});
      S.cfg = await api("/api/state");
      applyArchiveUI();
      toast("Archive folder saved.", "info", 3000);
    } catch (err) { /* api() already toasted */ }
  });
  $("#btnSiteSave").addEventListener("click", async () => {
    const latText = $("#siteLat").value.trim();
    const lonText = $("#siteLon").value.trim();
    const latitude = Number(latText), longitude = Number(lonText);
    if ((latText || lonText) && (!latText || !lonText || !Number.isFinite(latitude) ||
        !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180)) {
      toast("Enter both coordinates: latitude −90 to 90 and longitude −180 to 180.", "err", 5000);
      return;
    }
    try {
      await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ observing_site: latText ? { latitude, longitude } : null }) });
      S.cfg = await api("/api/state");
      applyArchiveUI();
      toast(latText ? "Observing location saved. Reindex existing exposures to refresh sessions." : "Observing location fallback cleared.", "info", 5000);
    } catch (err) { /* api() already toasted */ }
  });
  $("#btnArcReindex").addEventListener("click", async () => {
    if (_archiveOperation) return;
    _reindexErrorNotice?.remove();
    _reindexErrorNotice = null;
    _archiveOperation = "reindex";
    const unlock = lockArchiveActions();
    setBusy(true, "Scanning archive folder...");
    try {
      // a big archive needs more than the default 30 s request timeout
      const res = await api("/api/library/reindex",
        { method: "POST", signal: new AbortController().signal, silent: true });
      const conflicts = res.conflicts || 0;
      toast(`Reindex: ${res.added} added, ${res.repaired || 0} repaired, ${res.skipped} already known, ${res.failed} failed, ${conflicts} ambiguous.` +
            (conflicts ? " Ambiguous files were left unchanged; inspect the archive before retrying." : ""),
            conflicts ? "err" : "info", conflicts ? 0 : 6000);
      S.library = null;
      await loadLibrary();
    } catch (err) {
      _reindexErrorNotice = toast(`Reindex stopped: ${err.message}`, "err", 0);
      _reindexErrorNotice.classList.add("archive-reindex-error");
    }
    finally { setBusy(false); unlock(); _archiveOperation = null; }
  });
  if ($("#arcDir")) {
    $("#arcDir").addEventListener("keydown", e => {
      if (e.key === "Enter") $("#btnArcSave").click();
    });
  }

  // import
  $("#btnIngest").addEventListener("click", () => {
    _impSession = null;
    syncFrameSelector();
    $(".calibration-target")?.remove();
    $("#fitFiles").click();
  });
  $("#fitFiles").addEventListener("change", e => {
    ingestFiles(e.target.files);
    e.target.value = "";
  });
  $("#btnIngestFolder").addEventListener("click", () => {
    if (_archiveOperation) {
      toast("Wait for the current archive operation to finish.", "info", 4000);
      return;
    }
    _impSession = null;
    syncFrameSelector();
    $(".calibration-target")?.remove();
    $("#fitFolder").click();
  });
  $("#fitFolder").addEventListener("change", e => {
    // Snapshot before resetting so the same directory can be selected again.
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    ingestFiles(files, undefined, undefined, undefined, "folder");
  });
  let calFrame = "dark";   // set by the per-session +darks/+flats/+bias buttons
  const pickCalibration = (frame, anchorId, description) => {
    if (_archiveOperation) {
      toast("Wait for the current archive operation to finish.", "info", 4000);
      return;
    }
    if (!Number.isSafeInteger(anchorId) || anchorId <= 0) {
      toast("Reload the library before adding calibration exposures.", "err", 5000);
      return;
    }
    _impSession = { anchorId, frame };
    calFrame = frame;
    syncFrameSelector();
    $("#catalog-tab-import").click();
    $(".calibration-target")?.remove();
    const note = document.createElement("p");
    note.className = "calibration-target hint";
    note.textContent = `Adding ${frame} exposures to ${description}. Original capture dates are preserved.`;
    $("#dropZone").before(note);
    $("#fitFilesCal").click();
  };
  const dz = $("#dropZone");
  if (dz) {
    dz.addEventListener("dragover", e => {
      e.preventDefault();
      dz.classList.add("over");
    });
    dz.addEventListener("dragleave", () => dz.classList.remove("over"));
    dz.addEventListener("drop", e => {
      e.preventDefault();
      dz.classList.remove("over");
      ingestFiles(e.dataTransfer.files);
    });
  }
  $("#btnClearResults").addEventListener("click", () => {
    renderIngestResults([]);
    const progress = $("#ingestProgress");
    if (progress) progress.hidden = true;
  });

  // library controls
  document.addEventListener("library:units-changed", renderLibrary);
  $("#libSort").addEventListener("change", renderLibrary);
  $("#libraryHeroIdentity").addEventListener("click", async e => {
    const unitButton = e.target.closest(".library-size-unit");
    if (unitButton) {
      const units = ["arcmin", "deg", "arcsec"];
      const next = units[(units.indexOf(S.cfg?.size_unit || "arcmin") + 1) % units.length];
      unitButton.disabled = true;
      try {
        await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ size_unit: next }) });
        S.cfg.size_unit = next;
        renderLibrary();
        $(".library-size-unit")?.focus();
      } catch (err) { unitButton.disabled = false; }
      return;
    }
    const ed = e.target.closest(".lib-edit");
    if (ed) { openEdit(ed.dataset.edit); return; }
    const op = e.target.closest(".lib-open");
    if (op) {
      const o = (S.library.objects || []).find(x => x.name === op.dataset.openobj);
      const slug = o && o.images && o.images[0]
        ? (imageFolderPath(o.images[0]) || "").split("/")[0] : op.dataset.openobj;
      openFolder(slug);
      return;
    }
  });
  $("#libSearch").addEventListener("input", renderLibrary);
  $("#libraryObjectList").addEventListener("click", (e) => {
    const button = e.target.closest("[data-library-object]");
    if (!button) return;
    _selectedLibraryObject = button.dataset.libraryObject;
    _selectedLibrarySession = 0;
    renderLibrary();
  });
  $("#librarySessions").addEventListener("click", (e) => {
    const folder = e.target.closest("[data-open-library-folder]");
    if (folder) { e.stopPropagation(); openFolder(folder.dataset.openLibraryFolder); return; }
    const button = e.target.closest("[data-library-session]");
    if (!button) return;
    selectLibrarySession(Number(button.dataset.librarySession) || 0);
  });
  $("#librarySessions").addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-library-session]");
    if (!row || e.target.closest("[data-open-library-folder]") || !["Enter", " "].includes(e.key)) return;
    e.preventDefault();
    selectLibrarySession(Number(row.dataset.librarySession) || 0);
  });
  $("#librarySessionRail").addEventListener("click", (e) => {
    const folderButton = e.target.closest("[data-open-library-folder]");
    if (folderButton) {
      const rel = folderButton.dataset.openLibraryFolder;
      openFolder(rel);
      return;
    }
    const addButton = e.target.closest("[data-library-add]");
    if (!addButton) return;
    pickCalibration(addButton.dataset.libraryAdd, Number(addButton.dataset.libraryAnchor),
      `${_selectedLibraryObject || "the selected object"}, selected session`);
  });
  const impBox = $("#importHistory");
  if (impBox) {
    impBox.addEventListener("click", e => {
      const open = e.target.closest(".imp-open");
      if (open) { openFolder(open.dataset.open); return; }
      const imp = e.target.closest("[data-imp]");
      if (imp) {
        const item = imp.closest(".imp-item");
        pickCalibration(imp.dataset.imp, Number(imp.dataset.impAnchor),
          item.querySelector(".imp-head b").textContent);
      }
    });
  }
  $("#fitFilesCal").addEventListener("change", e => {
    const t = _impSession;
    _impSession = null;
    syncFrameSelector();
    ingestFiles(e.target.files, calFrame, null, t?.anchorId);
    e.target.value = "";
  });
  $("#fitFilesCal").addEventListener("cancel", () => {
    _impSession = null;
    syncFrameSelector();
    $(".calibration-target")?.remove();
  });

  // library edit modal
  $("#libEditClose").addEventListener("click", () => {
    $("#libEditModal").classList.add("hidden");
    _editTarget = null;
  });
  $("#libEditCancel").addEventListener("click", () => {
    $("#libEditModal").classList.add("hidden");
    _editTarget = null;
  });
  $("#libEditModal").addEventListener("mousedown", e => {
    if (e.target.id === "libEditModal") {
      $("#libEditModal").classList.add("hidden");
      _editTarget = null;
    }
  });
  $("#libEditSave").addEventListener("click", saveEdit);
  $("#libEditName").addEventListener("keydown", e => {
    if (e.key === "Enter") saveEdit();
  });
  // DE-13: every other dialog in the shell closes on Escape (detail.js,
  // weather.js) - this one was click-only, so a keyboard user had to Tab to
  // Cancel. The shared focus trap only implements Tab.
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if ($("#libEditModal").classList.contains("hidden")) return;
    e.preventDefault();
    $("#libEditModal").classList.add("hidden");
    _editTarget = null;
  });
}
