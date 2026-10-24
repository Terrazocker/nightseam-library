/* Image Archive mode: mode switching, FITS import, library table */

import { $, esc, api, toast, setBusy, fmtSize, fmtDT, TYPE_LABEL } from "./util.js";
import { S } from "./state.js";
import { renderSkyMap } from "./skymap.js";
import { catalogIcon } from "./catalog_icons.js";

let _bound = false;
let _selectedLibraryObject = null;
let _selectedLibrarySession = 0;
let _importFrameOverride = null;

function emptyStateMarkup(icon, heading, lines, className = "", actions = "") {
  const text = (Array.isArray(lines) ? lines : [lines]).filter(Boolean);
  const descriptions = text.length
    ? `<p>${text.map((line) => `<span>${esc(line)}</span>`).join("")}</p>` : "";
  const extra = className ? ` ${className}` : "";
  const buttons = actions ? `<div class="empty-actions">${actions}</div>` : "";
  return `<div class="empty-state${extra}">${catalogIcon(icon, "empty-icon")}<strong>${esc(heading)}</strong>${descriptions}${buttons}</div>`;
}

/* A compact one-line notice for secondary regions (rails, inspector). */
function inlineNoticeMarkup(icon, heading, line, className = "") {
  const extra = className ? ` ${className}` : "";
  return `<div class="empty-state inline${extra}">${catalogIcon(icon, "empty-icon")}<div><strong>${esc(heading)}</strong>${line ? `<p>${esc(line)}</p>` : ""}</div></div>`;
}

const IMPORT_ACTIONS = '<button class="button primary" type="button" data-empty-import="files">Import exposures…</button>' +
  '<button class="button" type="button" data-empty-import="folder">Import a folder…</button>';

const FRAME_NAMES = { light: "Lights", dark: "Darks", flat: "Flats", bias: "Bias" };

function syncFrameSelector() {
  // An empty override means Auto: each exposure's header decides.
  const chosenFrame = _impSession?.frame || _importFrameOverride || "";
  for (const button of document.querySelectorAll("[data-frame-override]")) {
    const active = button.dataset.frameOverride === chosenFrame;
    button.setAttribute("aria-pressed", String(active));
  }
  const help = $("#frameTypeHelp");
  if (help) {
    const name = FRAME_NAMES[chosenFrame] || chosenFrame;
    help.textContent = _impSession
      ? `${name} will be attached to the selected object and session. Original capture dates are preserved.`
      : _importFrameOverride
      ? `The next import is treated as ${name.toLowerCase()}, overriding each header. Choose Auto to read headers again.`
      : "Auto reads the frame type from each exposure's header and treats unlabeled files as lights.";
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
const FOLDER_POLL_MS = 400;
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
  const selectors = "#btnTopIngest, #btnIngest, #btnIngestFolder, #fitFolder, #btnTopReindex, #btnArcReindex, #btnTopExport, #btnTopCopy, #btnTopHealth, #btnClearResults, #btnArcSave, #btnArcBrowse, #arcDir, #siteLat, #siteLon, #btnSiteSave, #compressOnIngest, #ingestSafeMode, #libEditSave, [data-edit], [data-frame-override], [data-library-add], [data-rice-mode], [data-imp], [data-empty-import]";
  const controls = [...document.querySelectorAll(selectors)];
  const previous = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  return () => controls.forEach((control, i) => { control.disabled = previous[i]; });
}

export function archiveBusy() {
  return Boolean(_archiveOperation);
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
    progress.innerHTML = '<progress class="import-bar" aria-hidden="true"></progress><span class="import-files"></span><span class="import-elapsed" aria-live="off"></span><span class="import-skipped" hidden></span><span class="import-restored" hidden></span><span class="import-failed" hidden></span><span class="import-pending" hidden></span><span class="import-request-error" hidden></span>';
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
    const bar = progress.querySelector(".import-bar");
    bar.max = Math.max(1, total);
    bar.value = activity.processed;
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

function countResults(activity, batch) {
  const confirmed = batch.filter(result => !result.unconfirmed && !result.unattempted);
  activity.processed += confirmed.length;
  activity.skipped += batch.filter(result => result.ok && result.skipped).length;
  activity.restored += batch.filter(result => result.ok && result.restored).length;
  activity.failed += confirmed.filter(result => !result.ok).length;
  activity.unconfirmed += batch.filter(result => result.unconfirmed).length;
  activity.update();
}

// A picked folder imports as one continuous server job: files keep staging
// while earlier ones publish, and outcomes arrive per file, in order.
async function importFolderJob(folder, list, options, activity, results) {
  let job;
  try {
    job = await api("/api/images/folder/jobs", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, files: list.map(f => f.rel), ...options }) });
  } catch (err) {
    return;                                 // api() already toasted; nothing started
  }
  for (let misses = 0; ;) {
    await new Promise(resolve => setTimeout(resolve, FOLDER_POLL_MS));
    let state;
    try {
      state = await api(`/api/images/folder/jobs/${job.id}?since=${results.length}`, { silent: true });
      misses = 0;
    } catch (err) {
      if (++misses < 10) continue;
      const lost = list.slice(results.length).map(f => ({ file: f.name, ok: false, unconfirmed: true,
        error: "Lost contact with the folder import; file outcome unconfirmed" }));
      results.push(...lost);
      countResults(activity, lost);
      return;
    }
    results.push(...state.results);
    countResults(activity, state.results);
    if (state.status !== "running") return;
  }
}

// `folder` is a native-picker token: files are then scan entries the server
// reads in place, named by their path relative to that folder.
async function ingestFiles(files, frame, session, sessionAnchorId, source = "files", folder = null) {
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
    const chunks = folder ? [] : importChunks(list, batchSize);
    if (folder) await importFolderJob(folder, list, { frame: selectedFrame || "", session: session || "",
      session_anchor_id: sessionAnchorId || null, run_id: runId,
      compress, compression_mode: compressionMode }, activity, results);
    for (const chunk of chunks) {
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
      countResults(activity, res.results);
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

// The Windows window's Browse… or, in the Linux browser build, the desktop
// folder dialog run by the server. Resolves to a path, null when cancelled,
// or undefined when no dialog exists here.
export async function chooseFolderPath(purpose, current) {
  const bridge = window.pywebview?.api;
  if (typeof bridge?.pick_archive_dir === "function") return await bridge.pick_archive_dir(current);
  if (!S.cfg?.folder_dialog) return undefined;
  const chosen = await api("/api/folder-dialog", { method: "POST", timeout: 0, silent: true,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ purpose, current }) });
  return chosen.path || null;
}

// `pick` resolves to {token, name}, or null when the dialog is cancelled.
async function importPickedFolder(pick) {
  let picked;
  try {
    picked = await pick();
  } catch (err) {
    toast(`The folder dialog could not open${err?.message ? `: ${err.message}` : "."}`, "err", 6000);
    return;
  }
  if (!picked?.token) return;
  if (_archiveOperation) {
    toast("Wait for the current archive operation to finish.", "info", 4000);
    return;
  }
  _archiveOperation = "folder";
  const unlock = lockArchiveActions();
  setBusy(true, `Reading ${picked.name}...`);
  let scan;
  try {
    scan = await api("/api/images/folder/scan", { method: "POST", timeout: 0,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: picked.token }) });
  } catch (err) {
    return;                                 // api() already toasted
  } finally {
    setBusy(false);
    unlock();
    _archiveOperation = null;
  }
  if (scan.archive_skipped) toast("Your archive folder is inside this folder; its files were skipped.", "info", 6000);
  if (scan.unreadable_folders) toast(`${scan.unreadable_folders} subfolder${scan.unreadable_folders === 1 ? "" : "s"} could not be read and ${scan.unreadable_folders === 1 ? "was" : "were"} skipped.`, "err", 8000);
  const files = scan.files.map(f => ({ name: f.rel.split("/").pop(), size: f.size, rel: f.rel }));
  await ingestFiles(files, undefined, undefined, undefined, "folder", picked.token);
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
    box.innerHTML = emptyStateMarkup("file", "No import in progress",
      "Each file's outcome appears here while you import.");
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
    box.innerHTML = emptyStateMarkup("clock", "No recent imports",
      "Completed imports are listed here so you can add darks, flats or bias to their sessions.");
    return;
  }
  // History rows are per frame type; one capture session is one entry.
  const groups = new Map();
  for (const row of list) {
    const key = `${row.slug}/${row.session}`.toLowerCase();
    if (!groups.has(key)) groups.set(key, { slug: row.slug, session: row.session,
      name: row.object_name || row.slug, first: null, frames: new Map() });
    const group = groups.get(key);
    if (row.first_obs && (!group.first || row.first_obs < group.first)) group.first = row.first_obs;
    const frame = group.frames.get(row.frame) || { n: 0, exposure: 0 };
    frame.n += Number(row.n_files) || 0;
    frame.exposure += Number(row.total_exposure) || 0;
    group.frames.set(row.frame, frame);
  }
  const frameOrder = (kind) => { const i = ["light", "dark", "flat", "bias"].indexOf(kind); return i < 0 ? 9 : i; };
  box.innerHTML = [...groups.values()].map(g => {
    const prefix = `${g.slug}/${g.session}/`.toLowerCase();
    const anchor = (S.library?.objects || []).flatMap(object => object.images || [])
      .find(image => Number.isSafeInteger(image.id) && image.id > 0 &&
        String(image.file || "").replaceAll("\\", "/").toLowerCase().startsWith(prefix));
    const anchorAttrs = anchor ? `data-imp-anchor="${anchor.id}"`
      : 'disabled title="This session is no longer available in the library."';
    const loc = `${esc(g.slug)}/${esc(g.session)}`;
    const frames = [...g.frames].sort(([a], [b]) => frameOrder(a) - frameOrder(b)).map(([kind, f]) =>
      `${FRAME_NAMES[kind] || kind} \u00D7 ${f.n}${f.exposure > 0 ? ` \u00B7 ${fmtDur(f.exposure)}` : ""}`);
    const day = g.first ? fmtDT(g.first).split(" ")[0] : "";
    return `<div class="imp-item">
      <div class="imp-head">
        <b>${esc(g.name)}</b>
        ${day ? `<span class="imp-date mono">${esc(day)}</span>` : ""}
      </div>
      <div class="imp-frames">${frames.map((text) => `<span>${esc(text)}</span>`).join("")}</div>
      <div class="imp-path mono" title="Archive folder: ${loc}">${loc}</div>
      <div class="imp-btns" role="group" aria-label="Add calibration exposures to this session">
        <span class="imp-btns-label" aria-hidden="true">Add to session</span>
        <button type="button" data-imp="dark" ${anchorAttrs}>+ Darks</button>
        <button type="button" data-imp="flat" ${anchorAttrs}>+ Flats</button>
        <button type="button" data-imp="bias" ${anchorAttrs}>+ Bias</button>
        <button type="button" class="imp-open icon-button" data-open="${loc}" title="Open this session's folder" aria-label="Open this session's folder">${catalogIcon("folder")}</button>
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

function clearSessionDetails(heading, line) {
  $("#libraryFrameCount").textContent = "0 frames";
  $("#librarySessionFacts").innerHTML = inlineNoticeMarkup("file", heading, line, "selected-session-empty");
  $("#librarySessionActions").innerHTML = "";
  $("#librarySessionActions").hidden = true;
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
    $("#libraryObjectList").innerHTML = inlineNoticeMarkup(
      "folder", "No objects yet", "Objects appear as you import.", "objects-empty-state");
    $("#librarySessions").innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "database", "Your library is empty",
      ["Import FITS exposures to start your catalog.",
       "They are grouped by object into capture sessions, one per night."],
      "session-empty-state onboarding", _archiveOperation
        ? IMPORT_ACTIONS.replaceAll("<button ", "<button disabled ") : IMPORT_ACTIONS)}</td></tr>`;
    clearSessionDetails("No session selected", "Session details appear here after your first import.");
    $("#librarySessionCount").textContent = "0 sessions";
    $("#libObjectCount").textContent = "0 objects";
    return;
  }
  const sort = $("#libSort").value;
  // "Integration time" means light integration, the figure the workspace
  // shows; calibration exposure must not lift an object up the list.
  const lightIntegration = new Map(sort === "integ" ? allObjects.map((o) =>
    [o, libraryObjectRollup(o.sessions).lightExposure]) : []);
  allObjects.sort((a, b) => {
    if (sort === "integ") return lightIntegration.get(b) - lightIntegration.get(a)
      || String(a.name || "").localeCompare(String(b.name || ""));
    if (sort === "sessions") return b.n_sessions - a.n_sessions;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  const q = $("#libSearch").value.trim().toLocaleLowerCase();
  const objs = q ? allObjects.filter((o) =>
    [o.name, o.designation, o.otype, TYPE_LABEL[o.otype]].some((x) => String(x || "").toLocaleLowerCase().includes(q))) : allObjects;
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
    ? `${objs.length} of ${allObjects.length} objects` : plural(allObjects.length, "object");
  $("#libraryObjectList").innerHTML = objs.length ? objs.map((o) => {
    const key = esc(o.name || "");
    const active = o.name === _selectedLibraryObject;
    const type = TYPE_LABEL[o.otype] || o.otype || "";
    const detail = [o.designation && o.designation !== o.name ? o.designation : "", type].filter(Boolean).join(" · ");
    return `<button class="library-object${active ? " active" : ""}" type="button" data-library-object="${key}" aria-pressed="${active}">
      <span class="library-object-text"><b>${esc(o.name || "?")}</b><small>${esc(detail || "No designation")}</small></span>
      <span class="library-object-count" title="${esc(plural(o.n_sessions, "capture session"))}">${Number(o.n_sessions) || 0}<span class="visually-hidden"> ${o.n_sessions === 1 ? "session" : "sessions"}</span></span>
    </button>`;
  }).join("") : '<p class="hint">No objects match this search.</p>';
  renderLibraryObjectHeader(selected, unit);
  if (!selected) {
    $("#librarySessions").innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "search", "No matching objects", "Adjust or clear the search to see capture sessions.",
      "session-empty-state")}</td></tr>`;
    $("#librarySessionCount").textContent = "0 sessions";
    clearSessionDetails("No object selected", "Choose an object to see its sessions.");
    return;
  }
  renderLibrarySessions(selected, selectedSessions, selectedSession);
}

function plural(count, noun) {
  const n = Number(count) || 0;
  return `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
}

function renderLibraryObjectHeader(object, unit) {
  const identity = $("#libraryHeroIdentity");
  if (!object) {
    const empty = !(S.library?.objects || []).length;
    identity.innerHTML = `<h1 class="object-title">Library</h1><p class="object-subtitle">${
      empty ? "Nothing imported yet" : "No object selected"}</p>`;
    $("#libSummary").innerHTML = empty ? "" : libraryMetricHTML(null);
    return;
  }
  const key = esc(object.name || "?");
  const type = TYPE_LABEL[object.otype] || object.otype || "Unknown type";
  const size = fmtSize(object.size_am, unit).replace(/[′″°]/g, "");
  const units = { arcmin: ["′", "arcminutes", "degrees"], deg: ["°", "degrees", "arcseconds"], arcsec: ["″", "arcseconds", "arcminutes"] };
  const [symbol, name, next] = units[unit] || units.arcmin;
  const designation = object.designation && object.designation !== object.name ? object.designation : "";
  identity.innerHTML = `<div class="object-heading library-active-object"><h1 class="object-title"><strong>${key}</strong></h1>
    <div class="object-actions">
    ${!object.object_id ? `<button class="button small lib-edit" type="button" data-edit="${key}">Edit</button>` : ""}
    <button class="button small lib-open" type="button" data-openobj="${key}" title="Open this object's archive folder">${catalogIcon("folder")}<span>Open folder</span></button></div></div>
    <p class="object-subtitle library-object-description">${esc([designation, type].filter(Boolean).join(" · "))} · <span class="mono">Size ${esc(size)}<button class="library-size-unit" type="button" aria-label="Size units: ${name}. Switch to ${next}" title="Switch to ${next}">${symbol}</button></span></p>`;
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

function lastSessionDay(sessions) {
  const starts = (sessions || []).filter((s) => !s.undated && s.start).map((s) => s.start).sort();
  return starts.length ? sessionWhen({ start: starts.at(-1), end: starts.at(-1) }).day : "—";
}

function libraryMetricHTML(object) {
  const total = libraryObjectRollup(object?.sessions || []);
  const fields = [
    ["Sessions", (object?.sessions || []).length.toLocaleString()],
    ["Images", (Number(object?.n_images) || 0).toLocaleString()],
    ["Light integration", fmtDur(total.lightExposure)],
    ["Last session", lastSessionDay(object?.sessions)],
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

function selectLibrarySession(index, { reveal = false } = {}) {
  _selectedLibrarySession = index;
  renderLibrary();
  const button = $(`#librarySessions [data-select-session="${index}"]`);
  button?.focus({ preventScroll: true });
  if (reveal) button?.closest("tr")?.scrollIntoView({ block: "nearest" });
}

/* Select an object and keep keyboard focus on its (re-rendered) button. */
function selectLibraryObject(name, { reveal = false } = {}) {
  _selectedLibraryObject = name;
  _selectedLibrarySession = 0;
  renderLibrary();
  const button = [...document.querySelectorAll("#libraryObjectList [data-library-object]")]
    .find((node) => node.dataset.libraryObject === name);
  button?.focus({ preventScroll: true });
  if (reveal) button?.scrollIntoView({ block: "nearest" });
}

/* Arrow keys, Home and End move through a list; selection follows focus. */
function listStep(key, index, count) {
  const steps = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: count - 1 };
  if (!(key in steps) || !count) return null;
  return Math.max(0, Math.min(count - 1, steps[key]));
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

/* Split a session into a calendar day and a local time range. */
function sessionWhen(session) {
  if (session.undated) return { day: "No date recorded", time: "" };
  const [startDay, startTime] = fmtDT(session.start).split(" ");
  const [endDay, endTime] = fmtDT(session.end).split(" ");
  if (!startTime) return { day: startDay, time: "" };
  if (!endTime || (endDay === startDay && endTime === startTime)) return { day: startDay, time: startTime };
  if (endDay === startDay) return { day: startDay, time: `${startTime}–${endTime}` };
  // Overnight sessions end on the next calendar day; say so compactly.
  const nextDay = Date.parse(`${endDay}T00:00Z`) - Date.parse(`${startDay}T00:00Z`) === 86400000;
  return { day: startDay, time: nextDay
    ? `${startTime}–${endTime} +1d` : `${startTime} – ${endDay.slice(5)} ${endTime}` };
}

function sessionTypeCount(session, needle) {
  return Object.entries(session.types || {}).reduce((sum, [name, value]) =>
    name.toLowerCase().includes(needle) ? sum + (Number(value.n) || 0) : sum, 0);
}

function sessionLightExposure(session) {
  const entry = Object.entries(session.types || {}).find(([name]) => name.toLowerCase().includes("light"));
  return Number(entry?.[1]?.exposure) || 0;
}

function folderButton(rel, label, className = "library-folder-link") {
  return `<button class="${className}" type="button" data-open-library-folder="${esc(rel)}" title="Open ${esc(rel || "archive root")}">${catalogIcon("folder")}<span>${esc(label)}</span></button>`;
}

function renderLibrarySessions(object, sessions, activeSession) {
  const heading = $("#librarySessionsHeading");
  const count = $("#librarySessionCount");
  if (heading) heading.textContent = "Capture sessions";
  if (count) count.textContent = plural(sessions.length, "session");
  const list = $("#librarySessions");
  if (!list) return;
  if (!sessions.length) {
    list.innerHTML = `<tr><td colspan="8">${emptyStateMarkup(
      "database", "No capture sessions", "This object has no recorded exposures.",
      "session-empty-state")}</td></tr>`;
    clearSessionDetails("No session selected", "");
    return;
  }
  const cellCount = (n) => n ? String(n) : '<span class="zero">0</span>';
  list.innerHTML = sessions.map((s, i) => {
    const when = sessionWhen(s);
    const title = `${s.label || "Session"} · ` + (s.undated ? "No date recorded" : `${fmtDT(s.start)} – ${fmtDT(s.end)}`);
    const compression = sessionCompression(s);
    const filters = Object.keys(s.filters || {}).filter((name) => name !== "?").join(" · ");
    const folders = sessionFolderPaths(object, s);
    // Session folders carry long generated names; the full paths live in the
    // tooltip and the inspector, so the row offers a short, stable action.
    const folder = folders.length
      ? `<button class="library-folder-link" type="button" data-open-library-folder="${esc(folders[0])}" title="${esc(folders.join(" · ") || "Archive root")}">${catalogIcon("folder")}<span>Open</span></button>`
      : '<span class="zero">—</span>';
    const lightExposure = sessionLightExposure(s);
    const active = sessions.indexOf(activeSession) === i;
    return `<tr class="library-session${active ? " active" : ""}" data-library-session="${i}" tabindex="-1" aria-selected="${active}">
      <td><button class="session-select" type="button" data-select-session="${i}" title="${esc(title + ' · ' + compression.detail)}"><span class="session-compression" data-compression="${compression.state}" role="img" aria-label="${esc(compression.detail)}" title="${esc(compression.detail)}">${catalogIcon(compression.icon)}</span><span class="session-select-label"><span class="session-day">${esc(when.day)}</span>${s.label ? `<span class="session-kind">${esc(s.label)}</span>` : ""}<span class="session-time">${esc(when.time)}</span></span></button></td>
      <td class="num">${cellCount(sessionTypeCount(s, "light"))}</td><td class="num">${cellCount(sessionTypeCount(s, "dark"))}</td><td class="num">${cellCount(sessionTypeCount(s, "flat"))}</td><td class="num">${cellCount(sessionTypeCount(s, "bias"))}</td>
      <td class="num duration" title="Light frames only">${lightExposure ? esc(fmtDur(lightExposure)) : '<span class="zero">—</span>'}</td><td title="${esc(filters)}">${filters ? esc(filters) : '<span class="zero">—</span>'}</td><td>${folder}</td></tr>`;
  }).join("");
  if (!activeSession) {
    clearSessionDetails("No session selected", "Choose a capture session to see its frames and folders.");
    return;
  }
  const typeLabels = { light: "Lights", dark: "Darks", flat: "Flats", bias: "Bias" };
  const typeOrder = (kind) => { const i = ["light", "dark", "flat", "bias"].indexOf(kind); return i < 0 ? 9 : i; };
  const typeRows = Object.entries(activeSession.types || {}).sort(([a], [b]) => typeOrder(a) - typeOrder(b)).map(([kind, value]) =>
    `<div><span>${esc(typeLabels[kind] || kind)}</span><b>${Number(value.n) || 0} · ${esc(fmtDur(value.exposure))}</b></div>`).join("");
  const filterNames = Object.keys(activeSession.filters || {});
  const folders = sessionFolderPaths(object, activeSession);
  const compression = sessionCompression(activeSession);
  const when = sessionWhen(activeSession);
  const windowText = activeSession.session_start && activeSession.session_end
    ? sessionWhen({ start: activeSession.session_start, end: activeSession.session_end }).time : "";
  $("#libraryFrameCount").textContent = plural(activeSession.n_images, "frame");
  $("#librarySessionFacts").innerHTML = `<p class="session-date"><strong>${esc(when.day)}</strong><span>${esc([activeSession.label || "Session", when.time].filter(Boolean).join(" · "))}</span></p>
    <dl class="library-session-facts">
    <div><dt>Storage</dt><dd title="${esc(compression.detail)}">${esc(compression.label)}</dd></div>
    ${windowText ? `<div><dt>Session window</dt><dd>${esc(windowText)}</dd></div>` : ""}
    <div><dt>Total exposure</dt><dd>${esc(fmtDur(activeSession.exposure))}</dd></div>
    <div><dt>Light integration</dt><dd>${esc(fmtDur(sessionLightExposure(activeSession)))}</dd></div></dl>
    <section><h3 class="section-label">Frames</h3><div class="library-frame-rollup">${typeRows || '<p class="hint">No frame types recorded.</p>'}</div></section>
    <section class="library-filter-rollup"><h3 class="section-label">Filters</h3>${filterNames.length
      ? `<div class="chip-list">${filterNames.map((name) => `<span class="chip">${esc(name === "?" ? "Unknown" : name)}</span>`).join("")}</div>`
      : '<p class="hint">No filter recorded</p>'}</section>
    <section class="library-folder-rollup"><h3 class="section-label">Archive folders</h3><div class="library-archive-folders" role="group" aria-label="Archive folders">${folders.length
      ? folders.map((rel) => folderButton(rel, rel || "Archive root")).join("") : '<p class="hint">Not recorded</p>'}</div></section>`;
  const actions = $("#librarySessionActions");
  if (actions) {
    actions.hidden = false;
    const rel = folders[0] || "";
    const anchor = sessionImages(object, activeSession).find(image =>
      Number.isSafeInteger(image.id) && image.id > 0);
    const canTargetSession = Boolean(anchor);
    const targetAttrs = anchor ? `data-library-anchor="${anchor.id}"`
      : 'disabled title="Reload the library to select a recorded exposure for this session."';
    const ids = (activeSession.image_ids || []).join(",");
    // Offer only the storage change that applies; mixed or unknown offers both.
    const riceAttrs = (mode, title) => {
      if (!anchor) return targetAttrs;
      const done = compression.state === (mode === "compress" ? "compressed" : "uncompressed");
      return done ? `disabled title="Every exposure in this session is already ${mode === "compress" ? "compressed" : "uncompressed"}."`
        : `${targetAttrs} title="${title}"`;
    };
    actions.innerHTML = `<button class="button" type="button" data-open-library-folder="${esc(rel)}" ${folders.length ? "" : "disabled"}>${catalogIcon("folder")}<span>Open session folder</span></button>
      <div><h3 class="section-label" id="sessionCalibrationLabel">Add calibration frames</h3>
      <div class="button-row even" role="group" aria-labelledby="sessionCalibrationLabel">
      <button class="button" type="button" data-library-add="dark" ${targetAttrs}>+ Darks</button>
      <button class="button" type="button" data-library-add="flat" ${targetAttrs}>+ Flats</button>
      <button class="button" type="button" data-library-add="bias" ${targetAttrs}>+ Bias</button>
      </div></div>
      <div><h3 class="section-label" id="sessionStorageLabel">Storage · lossless Rice</h3>
      <div class="button-row even" role="group" aria-labelledby="sessionStorageLabel">
      <button class="button" type="button" data-rice-mode="compress" data-rice-ids="${ids}" ${riceAttrs("compress", "Convert to lossless Rice-compressed FITS")}>Compress</button>
      <button class="button" type="button" data-rice-mode="uncompress" data-rice-ids="${ids}" ${riceAttrs("uncompress", "Restore ordinary FITS files")}>Uncompress</button>
      </div></div>${canTargetSession ? "" : '<p class="hint">Reload the library before adding calibration exposures.</p>'}`;
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
      _importFrameOverride = button.dataset.frameOverride || null;
      syncFrameSelector();
    });
  });
  syncFrameSelector();
  renderIngestResults([]);

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
    const compress = $("#compressOnIngest").checked;
    mode.disabled = Boolean(_archiveOperation) || !compress;
    modeGroup.dataset.mode = mode.checked ? "safe" : "fast";
    // Verification only matters once compression is chosen.
    modeGroup.hidden = !compress;
    if (!compress) { pinnedInfo = false; showModeInfo(false); }
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
    let dir;
    try {
      dir = await chooseFolderPath("archive", $("#arcDir").value.trim());
    } catch (err) {
      toast(`The folder dialog could not open: ${err.message}`, "err", 6000);
      return;
    }
    if (dir === undefined) {
      toast("No folder dialog is available here - type the path instead.", "err", 6000);
      return;
    }
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
    // The Windows window and the Linux desktop dialog read the folder in
    // place: no browser upload prompt and no copy of every file through the
    // loopback socket. Other browsers upload the folder's files.
    const bridge = window.pywebview?.api;
    if (typeof bridge?.pick_import_folder === "function") importPickedFolder(() => bridge.pick_import_folder());
    else if (S.cfg?.folder_dialog) importPickedFolder(() => api("/api/images/folder/pick",
      { method: "POST", timeout: 0, silent: true }));
    else $("#fitFolder").click();
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
    selectLibraryObject(button.dataset.libraryObject);
  });
  $("#libraryObjectList").addEventListener("keydown", (e) => {
    const button = e.target.closest("[data-library-object]");
    if (!button) return;
    const buttons = [...$("#libraryObjectList").querySelectorAll("[data-library-object]")];
    const next = listStep(e.key, buttons.indexOf(button), buttons.length);
    if (next === null) return;
    e.preventDefault();
    selectLibraryObject(buttons[next].dataset.libraryObject, { reveal: true });
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
    if (!row || e.target.closest("[data-open-library-folder]")) return;
    const index = Number(row.dataset.librarySession) || 0;
    if (["Enter", " "].includes(e.key)) {
      e.preventDefault();
      selectLibrarySession(index);
      return;
    }
    const next = listStep(e.key, index, $("#librarySessions").querySelectorAll("tr[data-library-session]").length);
    if (next === null) return;
    e.preventDefault();
    selectLibrarySession(next, { reveal: true });
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
