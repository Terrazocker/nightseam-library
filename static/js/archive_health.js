/* Read-only scans, retained reports, and confirmed acknowledgement actions. */
import { $, api, toast, restoreFocus } from "./util.js";
import { S } from "./state.js";
import { beginArchiveHealth, loadLibrary } from "./archive.js";

const ACTIVE = new Set(["running", "cancelling"]);
const LABELS = { archive_unavailable: "Archive unavailable", missing: "Missing exposure",
  unreadable: "Unreadable", unreadable_directory: "Unreadable folder", unsafe_path: "Unsafe path",
  no_baseline: "No integrity baseline", incomplete_metadata: "Incomplete baseline metadata",
  unstable: "Changed during scan", content_changed: "Contents changed",
  metadata_changed: "Metadata changed", untracked: "Not indexed" };
let job = null, timer = null, release = null, running = false, acknowledging = false;
let opener = null, inertState = [], generation = 0, openRequest = 0, announcement = "";

function finish() {
  running = false;
  if (timer) clearTimeout(timer);
  timer = null;
  release?.();
  release = null;
}

function announce(message) {
  if (message !== announcement) {
    announcement = message;
    $("#healthStatus").textContent = message;
  }
}

function render(report) {
  job = report;
  running = ACTIVE.has(report.status);
  const busy = running || acknowledging || report.acknowledging;
  if (!busy) finish();
  $("#healthQuick").disabled = Boolean(busy);
  $("#healthDeep").disabled = Boolean(busy);
  $("#healthCancel").disabled = !running || report.status === "cancelling";
  $("#healthDownload").disabled = Boolean(busy);
  $("#healthArchive").textContent = report.archive || S.cfg?.image_dir || "";
  const progress = $("#healthProgress");
  progress.max = Math.max(1, report.total || 0);
  if (running && ["waiting", "discovering"].includes(report.phase)) progress.removeAttribute("value");
  else progress.value = report.processed || 0;
  const findings = Object.values(report.finding_counts || {}).reduce((sum, count) => sum + count, 0);
  const resolved = report.acknowledged_count || 0;
  $("#healthCounters").textContent = `${resolved ? "Original scan: " : ""}${report.processed || 0} / ${report.total || 0} exposures checked · ${Math.floor(report.elapsed_seconds || 0)}s · ${report.verified || 0} contents verified · ${report.metadata_unchanged || 0} unchanged by metadata · ${report.unverified || 0} unverified`;
  const phases = { waiting: "Waiting for the current archive operation to finish...",
    discovering: "Inspecting archive folders...", checking: "Checking exposures..." };
  const message = acknowledging || report.acknowledging ? "Rechecking the exposure and recording acknowledgement..."
    : report.status === "cancelling" ? "Cancellation requested; waiting for the scan to stop..."
    : running ? `${report.mode === "deep" ? "Deep verification" : "Quick check"}: ${phases[report.phase] || "Checking archive..."}`
    : report.status === "completed" ? `Scan complete. ${findings - resolved} open finding${findings - resolved === 1 ? "" : "s"}${resolved ? ` · ${resolved} acknowledged` : ""}.${report.mode === "quick" ? " File contents were not verified." : ""}`
    : report.status === "cancelled" ? "Scan cancelled. Results are partial; unchecked files are not verified."
    : report.status === "unavailable" ? "Archive unavailable. Results are incomplete; missing exposures were not verified."
    : report.error || "Scan failed. Results are incomplete.";
  announce(message);
  const sources = report.baseline_sources || {};
  $("#healthBaseline").textContent = sources.existing_fingerprint || sources.first_observed || sources.reindex
    ? "Some baselines are earlier recorded fingerprints or reindex observations, not guaranteed import-time contents. Scans preserve them."
    : sources.user_accepted ? "Some baselines were explicitly accepted by the user. Deep verification compares against those accepted contents."
    : "Deep verification compares preserved import hashes. Scans never establish or replace an integrity baseline.";
  const list = $("#healthFindings");
  list.replaceChildren();
  const sessions = report.deletion_sessions || [];
  const pending = report.pending_deletions || 0;
  $("#healthAcceptAll").hidden = !pending || report.status !== "completed";
  $("#healthAcceptAll").disabled = Boolean(busy);
  $("#healthAcceptAll").textContent = `Accept all deletions (${pending})`;
  const grouped = new Map();
  for (const session of sessions) {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = `${session.object_name} · ${session.label}`;
    const count = document.createElement("p");
    count.textContent = `${session.pending_count} missing exposure${session.pending_count === 1 ? "" : "s"} to acknowledge${session.acknowledged_count ? ` · ${session.acknowledged_count} acknowledged` : ""}`;
    item.append(title, count);
    if (session.pending_count && report.status === "completed") {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Acknowledge session";
      button.disabled = Boolean(busy);
      button.addEventListener("click", () => acknowledge(null, session));
      item.append(button);
    }
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Exposure details";
    const members = document.createElement("ul");
    details.append(summary, members);
    item.append(details);
    list.append(item);
    grouped.set(session.id, members);
  }
  for (const finding of report.findings || []) {
    const item = document.createElement("li");
    const label = document.createElement("strong");
    label.textContent = LABELS[finding.code] || finding.code;
    const path = document.createElement("span");
    path.className = "mono";
    path.textContent = finding.path || "Archive folder";
    const description = document.createElement("p");
    description.textContent = finding.message;
    item.append(label, path, description);
    if (finding.acknowledged_at) {
      const resolvedLabel = document.createElement("p");
      resolvedLabel.textContent = finding.acknowledgement === "forget_missing"
        ? "Deletion acknowledged · catalog record removed." : "Current contents accepted · integrity baseline updated.";
      item.appendChild(resolvedLabel);
    } else if (report.status === "completed" && finding.image_id && ["missing", "content_changed"].includes(finding.code)) {
      const action = document.createElement("button");
      action.type = "button";
      action.textContent = finding.code === "missing" ? "Acknowledge deletion" : "Accept current contents";
      action.disabled = Boolean(busy);
      action.addEventListener("click", () => acknowledge(finding));
      item.appendChild(action);
    }
    (finding.code === "missing" && grouped.get(finding.session_id) || list).appendChild(item);
  }
  const shown = (report.findings || []).length;
  $("#healthReportLimit").textContent = report.omitted_findings
    ? `Showing ${shown} of ${findings} exposure details. Session and all actions cover retained deletions only. The report retains the first ${report.finding_limit}; ${report.omitted_findings} further findings are omitted. Run another check after acknowledgement for remaining deletions.`
    : findings > shown ? `Showing the first ${shown} of ${findings} exposure details. Session and all actions include every retained deletion, including those beyond this display. Download the report for all details.` : "";
}

async function poll() {
  const pollGeneration = generation;
  try {
    const report = await api(job ? `/api/library/health/${job.id}` : "/api/library/health", { silent: true });
    if (pollGeneration !== generation) return;
    if (!report) {
      finish();
      $("#healthQuick").disabled = false;
      $("#healthDeep").disabled = false;
      $("#healthCancel").disabled = true;
      announce("No health scan is running. Choose a check to start.");
      return;
    }
    render(report);
    if (!running && !acknowledging && !report.acknowledging) { finish(); return; }
  } catch (error) {
    if (pollGeneration !== generation) return;
    if (error.status === 404) job = null;
    announce(`Could not refresh scan status: ${error.message}. Retrying; archive work may still be running.`);
  }
  timer = setTimeout(poll, 750);
}

async function start(mode) {
  if (running || acknowledging || job?.acknowledging) return;
  release = beginArchiveHealth();
  if (!release) return;
  generation += 1;
  running = true;
  job = null;
  $("#healthQuick").disabled = true;
  $("#healthDeep").disabled = true;
  $("#healthCancel").disabled = true;
  $("#healthDownload").disabled = true;
  $("#healthFindings").replaceChildren();
  announce("Starting archive health scan...");
  try {
    render(await api("/api/library/health", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }), silent: true }));
  } catch (error) {
    announce(`Could not confirm scan start: ${error.message}. Checking server status...`);
  }
  await poll();
}

async function acknowledge(finding, session = null) {
  if (!job || running || acknowledging || job.acknowledging) return;
  const action = !finding || finding.code === "missing" ? "forget_missing" : "accept_contents";
  const scope = session ? "session" : finding ? "image" : "all";
  const count = session ? session.pending_count : job.pending_deletions;
  const message = scope !== "image"
    ? `Acknowledge deletion of ${count} missing exposures${session ? ` in ${session.object_name} · ${session.label}` : " across all sessions in this report"}?\n\nTheir catalog records will be removed and library counts will update. Existing exposures stay in the catalog. No files will be deleted.${job.omitted_findings ? "\n\nOnly retained findings are included. Run another check afterwards for omitted findings." : ""}`
    : action === "forget_missing"
    ? `Acknowledge intentional deletion of this exposure?\n\n${finding.path}\n\nIts catalog record will be removed and library counts will update. No files will be deleted.`
    : `Accept the changed contents of this exposure?\n\n${finding.path}\n\nThe contents observed by the Deep scan will become the new integrity baseline. Only accept this if the change was expected.`;
  if (!window.confirm(message)) return;
  release = beginArchiveHealth();
  if (!release) return;
  const acknowledgementId = job.id, acknowledgementGeneration = ++generation;
  acknowledging = true;
  render(job);
  let failure = null;
  try {
    const result = await api(`/api/library/health/${acknowledgementId}/acknowledge`, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(scope === "image" ? { image_id: finding.image_id } : { scope,
        ...(session ? { session_id: session.id } : {}) }), action, confirmed: true }), silent: true });
    if (acknowledgementGeneration === generation) render(result);
  } catch (error) { failure = error.message; }
  finally {
    if (acknowledgementGeneration === generation) {
      acknowledging = false;
      // Reconcile a lost response before permitting another archive mutation.
      await poll();
      await loadLibrary();
      if (!running && !job?.acknowledging && !$("#archiveHealthModal").classList.contains("hidden")) $("#healthQuick").focus();
      if (failure) toast(`Acknowledgement was not confirmed: ${failure}`, "err");
    }
  }
}

function close() {
  openRequest += 1;
  $("#archiveHealthModal").classList.add("hidden");
  for (const [element, wasInert] of inertState) element.inert = wasInert;
  inertState = [];
  restoreFocus(opener);
}

async function open() {
  if (document.querySelector('[role="dialog"]:not(.hidden)')) return;
  const requestId = ++openRequest;
  opener = document.activeElement;
  inertState = [...document.querySelectorAll("body > header, body > main")].map(element => [element, element.inert]);
  inertState.forEach(([element]) => { element.inert = true; });
  $("#archiveHealthModal").classList.remove("hidden");
  $("#healthArchive").textContent = S.cfg?.image_dir || "";
  $("#healthClose").focus();
  if (running || acknowledging) return;
  const openedGeneration = generation;
  try {
    const latest = await api("/api/library/health", { silent: true });
    if (openedGeneration !== generation || requestId !== openRequest) return;
    if (latest) {
      render(latest);
      if (running || latest.acknowledging) {
        release ||= beginArchiveHealth();
        await poll();
      }
    }
  } catch (error) {
    if (openedGeneration === generation && requestId === openRequest) announce(`Could not load health status: ${error.message}`);
  }
}

export function bindArchiveHealth() {
  $("#healthAcceptAll").addEventListener("click", () => acknowledge(null));
  $("#btnTopHealth").addEventListener("click", open);
  $("#healthClose").addEventListener("click", close);
  $("#archiveHealthModal").addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
  });
  $("#healthQuick").addEventListener("click", () => start("quick"));
  $("#healthDeep").addEventListener("click", () => start("deep"));
  $("#healthCancel").addEventListener("click", async () => {
    if (!job || !running) return;
    const cancelGeneration = generation, cancelId = job.id;
    $("#healthCancel").disabled = true;
    try {
      const result = await api(`/api/library/health/${cancelId}/cancel`, { method: "POST", silent: true });
      if (cancelGeneration === generation && job?.id === cancelId) render(result);
    } catch (error) {
      if (cancelGeneration !== generation || job?.id !== cancelId) return;
      $("#healthCancel").disabled = false;
      announce(`Cancellation was not confirmed: ${error.message}. Retry or wait for refreshed status.`);
    }
  });
  $("#healthDownload").addEventListener("click", async () => {
    if (!job || running) return;
    try {
      const response = await fetch(`/api/library/health/${job.id}/report`);
      if (!response.ok) throw new Error(`Report download failed (HTTP ${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "nightseam-archive-health.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) { toast(error.message); }
  });
}
