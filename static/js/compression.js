/* Manual session Rice conversion and portable library copies. */
import { $, api, restoreFocus } from "./util.js";
import { beginCompression, chooseFolderPath, loadLibrary } from "./archive.js";

let selection = null, job = null, release = null, timer = null, opener = null;
let inertState = [], starting = false;
let generation = 0, openRequest = 0, lastJobId = null, uncertain = false;
const active = value => value && ["running", "cancelling"].includes(value.status);
const status = text => { $("#compressionStatus").textContent = text; };
const bytes = value => `${(value / (1024 ** 3)).toFixed(3)} GiB`;
const phases = { waiting: "Waiting for the archive", preparing: "Checking files", compressing: "Compressing files",
  uncompressing: "Uncompressing files", checking_originals: "Checking originals before replacement",
  publishing: "Writing and replacing files", committing: "Updating catalog", cleaning: "Removing retained originals",
  refreshing: "Refreshing library" };

function verification(mode) {
  $("#compressionVerification").hidden = mode === "uncompress";
  $("#compressionVerification").dataset.mode = $("#compressionSafeMode").checked ? "safe" : "fast";
}

function render(value) {
  job = value;
  lastJobId = value.id;
  const running = active(job);
  if (job.compression_mode) $("#compressionSafeMode").checked = job.compression_mode === "safe";
  verification(job.mode);
  $("#compressionSafeMode").disabled = running;
  if (running) {
    $("#compressionDestination").hidden = job.mode !== "copy";
    $("#compressionTitle").textContent = job.mode === "copy" ? "Copying library with lossless Rice" : job.mode === "compress" ? "Compressing session losslessly" : "Uncompressing session";
    $("#compressionHelp").textContent = job.mode === "copy" ? "Copying the library to your selected destination. Closing this window lets it continue." : "Converting the selected whole session. Closing this window lets it continue.";
  }
  $("#compressionStart").disabled = running || !selection;
  $("#compressionCancel").disabled = !running || job.status === "cancelling";
  $("#compressionPath").disabled = running;
  $("#compressionBrowse").disabled = running;
  $("#compressionProgress").max = Math.max(1, job.total || 0);
  $("#compressionProgress").value = job.processed || 0;
  $("#compressionPrepareLabel").textContent = `Preparing files · ${job.processed || 0} / ${job.total || 0}`;
  $("#compressionWriteProgress").max = Math.max(1, job.publication_total || 0);
  $("#compressionWriteProgress").value = job.published || 0;
  $("#compressionWriteLabel").textContent = `${job.mode === "copy" ? "Writing destination files" : "Writing and replacing files"} · ${job.published || 0} / ${job.publication_total || 0}`;
  const counts = `${job.processed || 0} / ${job.total || 0} files · ${job.elapsed_seconds || 0}s elapsed`;
  const skipped = [];
  if (job.skipped_missing) skipped.push(`${job.skipped_missing} missing`);
  if (job.skipped_unsupported) skipped.push(`${job.skipped_unsupported} unsupported`);
  const details = $("#compressionSkipped"), files = $("#compressionSkippedFiles");
  details.hidden = !skipped.length;
  files.replaceChildren();
  for (const entry of job.skipped_files || []) {
    const item = document.createElement("li");
    item.textContent = `${entry.file} — ${entry.reason === "missing" ? "not found on disk" : "unsupported for lossless Rice"}`;
    files.appendChild(item);
  }
  if (running) status(`${phases[job.phase] || job.phase} · ${counts}${job.status === "cancelling" ? " · Cancelling and restoring originals…" : ""}`);
  else if (job.status === "completed") {
    const savings = job.bytes_before ? ` · ${bytes(job.bytes_before)} → ${bytes(job.bytes_after)}` : "";
    status(`Complete · ${counts} · ${job.converted} converted · ${job.skipped} skipped${skipped.length ? ` (${skipped.join(" · ")})` : ""}${savings}${job.destination ? `. Saved to ${job.destination}` : ""}`);
  } else if (job.status === "cancelled") status("Cancelled. Session originals are retained; incomplete copies were removed.");
  else status(`Failed: ${job.error || "Unknown conversion error"}`);
}

function monitor() {
  clearTimeout(timer);
  return poll(++generation);
}

async function poll(ticket) {
  if (ticket !== generation) return;
  clearTimeout(timer);
  try {
    const value = await api(job ? `/api/library/compression/${job.id}` : "/api/library/compression", { silent: true });
    if (ticket !== generation) return;
    if (uncertain && (!value || value.id === lastJobId)) {
      uncertain = false;
      release?.(); release = null;
      $("#compressionStart").disabled = false;
      $("#compressionSafeMode").disabled = false;
      status("No new conversion was found. Start was not confirmed; you can try again.");
      return;
    }
    uncertain = false;
    if (!value) {
      release?.(); release = null;
      $("#compressionStart").disabled = false;
      $("#compressionSafeMode").disabled = false;
      status("No conversion is running. You can start again.");
      return;
    }
    if (!active(value)) {
      if (value.status === "completed") {
        render({ ...value, status: "running", phase: "refreshing" });
        if (!await loadLibrary()) throw new Error("Library refresh is still pending");
        if (ticket !== generation) return;
      }
      render(value);
      release?.(); release = null;
      if (value.status !== "completed") await loadLibrary();
      return;
    }
    render(value);
    release ||= beginCompression();
  } catch (error) {
    if (ticket !== generation) return;
    status(`Could not refresh conversion status: ${error.message}. Retrying…`);
    if (error.status === 404) job = null;
  }
  if (ticket === generation) timer = setTimeout(() => poll(ticket), 750);
}

async function open(request) {
  if (document.querySelector('[role="dialog"]:not(.hidden)')) return;
  selection = request;
  const requestId = ++openRequest, openedGeneration = generation;
  opener = document.activeElement;
  inertState = [...document.querySelectorAll("body > header, body > main")].map(element => [element, element.inert]);
  inertState.forEach(([element]) => { element.inert = true; });
  $("#compressionModal").classList.remove("hidden");
  $("#compressionClose").focus();
  $("#compressionDestination").hidden = request.mode !== "copy";
  verification(request.mode);
  $("#compressionTitle").textContent = request.mode === "copy" ? "Copy library with lossless Rice" : request.mode === "uncompress" ? "Uncompress session" : "Compress session losslessly";
  $("#compressionHelp").textContent = request.mode === "uncompress"
    ? "Restores this session’s Rice files to ordinary FITS. Files compressed by NightSeam must match their recorded original SHA-256 before replacement."
    : request.mode === "copy" ? "Copy your whole library to another disk as usable individual files. Supported integer FITS use lossless Rice compression."
    : "Compresses supported exposures in the selected session, including attached calibration exposures. Choose Fast to skip restoration checks or Safe to restore and compare every file. Floating point, unsupported and missing files are skipped and reported. Originals stay available until all replacements and the catalog switch succeed.";
  $("#compressionStart").disabled = true;
  status("Checking for an existing conversion…");
  try {
    const latest = await api("/api/library/compression", { silent: true });
    if (requestId !== openRequest || openedGeneration !== generation) return;
    lastJobId = latest?.id || null;
    if (active(latest)) {
      // A resumed job may differ from the action used to reopen this dialog.
      // Choose a fresh session/action after it finishes before starting again.
      selection = null;
      job = latest;
      release ||= beginCompression();
      await monitor();
    } else {
      job = null;
      $("#compressionStart").disabled = false;
      $("#compressionCancel").disabled = true;
      $("#compressionPath").disabled = false;
      $("#compressionBrowse").disabled = false;
      $("#compressionSafeMode").disabled = false;
      $("#compressionProgress").value = 0;
      $("#compressionWriteProgress").value = 0;
      $("#compressionPrepareLabel").textContent = "Preparing files · 0 / 0";
      $("#compressionWriteLabel").textContent = "Writing and replacing files · 0 / 0";
      $("#compressionSkipped").hidden = true;
      $("#compressionSkippedFiles").replaceChildren();
      status("Ready.");
    }
  } catch (error) { if (requestId === openRequest) status(`Could not check conversion status: ${error.message}. Close and try again.`); }
}

function close() {
  ++openRequest;
  $("#compressionModal").classList.add("hidden");
  inertState.forEach(([element, inert]) => { element.inert = inert; });
  restoreFocus(opener, $("#btnTools"));
}

async function start() {
  if (starting || active(job) || !selection) return;
  if (selection.mode === "copy" && !$("#compressionPath").value.trim()) {
    status("Choose a destination folder first."); $("#compressionPath").focus(); return;
  }
  release = beginCompression();
  if (!release) return;
  starting = true;
  ++generation;
  clearTimeout(timer);
  $("#compressionStart").disabled = true;
  $("#compressionSafeMode").disabled = true;
  try {
    const body = { ...selection };
    body.compression_mode = $("#compressionSafeMode").checked ? "safe" : "fast";
    if (body.mode === "copy") body.destination = $("#compressionPath").value.trim();
    job = await api("/api/library/compression", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), silent: true });
    render(job);
    await monitor();
  } catch (error) {
    if (error.status >= 400 && error.status < 500) {
      release?.(); release = null;
      $("#compressionStart").disabled = false;
      $("#compressionSafeMode").disabled = false;
      status(`Could not start conversion: ${error.message}`);
      return;
    }
    // A response can be lost after a job started. Poll before unlocking/retrying.
    status(`Start response was not confirmed: ${error.message}. Checking status…`);
    job = null;
    uncertain = true;
    await monitor();
  } finally { starting = false; }
}

export function bindCompression() {
  $("#compressionSafeMode").addEventListener("change", () => verification(selection?.mode || job?.mode));
  $("#btnTopCopy").addEventListener("click", () => open({ mode: "copy" }));
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-rice-mode]");
    if (!button || button.disabled) return;
    open({ mode: button.dataset.riceMode, anchor_id: Number(button.dataset.libraryAnchor),
      image_ids: button.dataset.riceIds.split(',').map(Number) });
  });
  $("#compressionStart").addEventListener("click", start);
  $("#compressionClose").addEventListener("click", close);
  $("#compressionModal").addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
  });
  $("#compressionBrowse").addEventListener("click", async () => {
    try {
      const path = await chooseFolderPath("destination", $("#compressionPath").value);
      if (path === undefined) { status("Enter an absolute destination path above."); $("#compressionPath").focus(); return; }
      if (path) $("#compressionPath").value = path;
    } catch (error) { status(`Could not choose destination: ${error.message}`); }
  });
  $("#compressionCancel").addEventListener("click", async () => {
    if (!active(job)) return;
    const ticket = generation, jobId = job.id;
    try {
      const value = await api(`/api/library/compression/${jobId}/cancel`, { method: "POST", silent: true });
      if (ticket === generation && job?.id === jobId) {
        if (active(value)) render(value);
        else await monitor();
      }
    }
    catch (error) { if (ticket === generation) status(`Cancellation was not confirmed: ${error.message}. Retry or wait for status.`); }
  });
  // Reconnect to work after a page reload without starting it again.
  const ticket = generation, opening = openRequest;
  api("/api/library/compression", { silent: true }).then(value => {
    if (ticket === generation && opening === openRequest && active(value) && !starting && !job) { job = value; release ||= beginCompression(); monitor(); }
  }).catch(() => {});
}
