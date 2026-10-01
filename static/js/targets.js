/* Targets tab: filters, sorting, paginated table rendering */

import { $, $$, esc, api, fmtSize, fmtHM, TYPE_LABEL, TYPE_COLOR, constName } from "./util.js";
import { S } from "./state.js";
import { statusOf, compactWindowText, desigLine,
         objScore, filterAdvice, frameCoverage, coverageCell,
         popularityFactor } from "./derive.js";
import { assignPopularityTiers, TIER_ORDER, dateFromJD } from "./core.js";

const PAGE_SIZE = 20;
let _page = 0;

/* Popularity TIERS (A-F) - the same method as the web: the ORDER is shown as a
 * letter, the score never is. The map is built over S.catalog, which IS the 200
 * most popular rows in standard mode (so their ranks 1..200 are the global ones)
 * and the whole catalogue in advanced mode; it is rebuilt whenever that array is
 * replaced (catalog reload / advanced-mode toggle). */
let _tiers = null, _tiersFor = null;
function tiers() {
  if (_tiersFor !== S.catalog) {
    _tiers = assignPopularityTiers(S.catalog);
    _tiersFor = S.catalog;
  }
  return _tiers;
}
function tierOf(m) { return tiers().get(m.id) || "F"; }
function tierIdx(m) {
  const i = TIER_ORDER.indexOf(tierOf(m));
  return i < 0 ? TIER_ORDER.length : i;
}
function tierTitle(m) {
  return (m.popularity || 0) > 0
    ? `tier ${tierOf(m)} of A-F: community observing popularity (A = most observed)`
    : `tier ${tierOf(m)}: no popularity data for this object`;
}

/* Min visibility (0.6.30): the user's own gate on the TARGET LIST - the slider
 * in the filter bar, 0 = off. Deliberately NOT part of `passesFilters`, which
 * the sky chart also uses: this control says "omit from the list". */
function minVisPct() {
  const el = $("#fMinVis");
  const raw = el ? el.value : (S.cfg && S.cfg.min_vis != null ? S.cfg.min_vis : 1);
  const v = Number(raw);
  return Number.isFinite(v) ? Math.max(0, Math.min(100, Math.round(v))) : 1;
}
function minVisHides(o) {
  const mv = minVisPct();
  return mv > 0 && !$("#fSearch").value.trim() &&
         o.obs_pct != null && o.obs_pct < mv;
}

function resetPage() { _page = 0; }

export function bindFilters() {
  let deb;
  $("#fSearch").addEventListener("input", () => {
    clearTimeout(deb); deb = setTimeout(() => { resetPage(); renderTargets(); }, 120);
  });
  $("#fType").addEventListener("change", () => { resetPage(); renderTargets(); });
  $("#fMag").addEventListener("input", e => {
    $("#magVal").textContent = (+e.target.value).toFixed(1);
    resetPage(); renderTargets();
  });
  // Min visibility: the list re-renders live (it only replaces #targetRows, so
  // the slider itself survives the drag), the setting is persisted debounced.
  let _mvPersist = null;
  $("#fMinVis").addEventListener("input", e => {
    const v = Math.round(+e.target.value);
    $("#fMinVisVal").textContent = v > 0 ? `${v}%` : "off";
    if (S.cfg) S.cfg.min_vis = v;
    clearTimeout(_mvPersist);
    _mvPersist = setTimeout(() => api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" }, silent: true,
      body: JSON.stringify({ min_vis: v })
    }).catch(() => {}), 400);
    resetPage(); renderTargets();
  });
  $("#fUnblocked").addEventListener("change", () => { resetPage(); renderTargets(); });
  $("#fFav").addEventListener("change", () => { resetPage(); renderTargets(); });
  $("#fRig").addEventListener("change", () => { resetPage(); renderTargets(); });
  $$("input[name='fVisibility']").forEach((el) => el.addEventListener("change", () => { resetPage(); renderTargets(); }));
  $("#fConst").addEventListener("change", () => { resetPage(); renderTargets(); });
  $("#fSort").addEventListener("change", () => { resetPage(); renderTargets(); });
  $("#btnFilterClear").addEventListener("click", () => {
    $("#fSearch").value = "";
    $("#fConst").value = "";
    $$("#fType option").forEach((option) => { option.selected = false; });
    const all = $("input[name='fVisibility'][value='all']");
    if (all) all.checked = true;
    $("#fMag").value = "11"; $("#magVal").textContent = "11.0";
    $("#fMinVis").value = "0"; $("#fMinVisVal").textContent = "off";
    $("#fUnblocked").checked = false; $("#fFav").checked = false; $("#fRig").checked = false;
    $("#fSort").value = "tier";
    if (S.cfg) S.cfg.min_vis = 0;
    api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, silent: true,
      body: JSON.stringify({ min_vis: 0 }) }).catch(() => {});
    resetPage(); renderTargets();
  });
  $("#btnCatalogProvenance").addEventListener("click", downloadCatalogProvenance);
  $("#pagerPrev").addEventListener("click", () => {
    if (_page > 0) { _page--; renderTargets(true); }
  });
  $("#pagerNext").addEventListener("click", () => {
    const n = Math.ceil((S.lastRows ? S.lastRows.length : 0) / PAGE_SIZE);
    if (_page < n - 1) { _page++; renderTargets(true); }
  });
  $("#targetRows").addEventListener("click", e => {
    const star = e.target.closest("[data-fav]");
    if (star) {
      // toggle in place first - instant visual feedback, then persist
      e.preventDefault();
      e.stopPropagation();
      const id = star.dataset.fav;
      const cur = new Set(S.cfg.favorites || []);
      const on = !cur.has(id);
      on ? cur.add(id) : cur.delete(id);
      S.cfg.favorites = [...cur];
      star.classList.toggle("on", on);
      api("/api/settings", { method:"POST",
        headers:{"Content-Type":"application/json"}, silent:true,
        body: JSON.stringify({ favorites:S.cfg.favorites })}).catch(()=>{})
        .finally(() => {
          renderTargets();
          document.dispatchEvent(new CustomEvent("favs:changed"));
        });
      return;
    }
    const tr = e.target.closest("tr[data-id]");
    if (tr) openDetail(tr.dataset.id);
  });
  $("#targetRows").addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    e.preventDefault();
    openDetail(tr.dataset.id);
  });

  // late import to avoid a circular dependency at module init
  async function openDetail(id) {
    const mod = await import("./detail.js");
    mod.openDetail(id);
  }
}

function downloadCatalogProvenance() {
  const payload = S.catalogProvenance && typeof S.catalogProvenance === "object"
    ? S.catalogProvenance : { schema_version: 1, objects: {} };
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"],
                        { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "nightseam-catalog-provenance.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function passesFilters(m, o) {
  const q = $("#fSearch").value.trim().toLowerCase();
  if (q && !(m.search || "").toLowerCase().includes(q)) return false;
  const types = [...$("#fType").selectedOptions].map((option) => option.value);
  if (types.length && !types.includes(m.otype)) return false;
  const magMax = +$("#fMag").value;
  if (m.vmag != null && m.vmag > magMax + 0.001) return false;
  if ($("#fUnblocked").checked && !(o.best && o.best[1]-o.best[0] >= 3)) return false;
  if ($("#fFav") && $("#fFav").checked &&
      !(S.cfg.favorites || []).includes(m.id)) return false;
  const visibility = document.querySelector("input[name='fVisibility']:checked")?.value || "all";
  const visibleNow = statusOf(o, S.tIdx).code === "vis";
  const risesLater = !visibleNow && !!(o.best && o.best[0] > S.tIdx);
  if (visibility === "now" && !visibleNow) return false;
  if (visibility === "later" && !risesLater) return false;
  if (visibility === "none" && (visibleNow || risesLater)) return false;
  if ($("#fRig")?.checked) {
    const coverage = frameCoverage(m);
    if (!coverage || !coverage.fits) return false;
  }
  const fc = $("#fConst").value;
  if (fc && m.constellation !== fc) return false;
  return true;
}

/* populate the constellation dropdown once the catalog is loaded */
export function populateConstFilter() {
  const sel = $("#fConst");
  if (!sel || sel.dataset.done) return;
  const names = new Set();
  for (const o of S.catalog) if (o.constellation) names.add(o.constellation);
  if (!names.size) return;
  sel.dataset.done = "1";
  sel.insertAdjacentHTML("beforeend",
    [...names].sort().map(c => `<option value="${esc(c)}">${esc(constName(c))}</option>`).join(""));
}

function sortObjects(list) {
  const mode = $("#fSort").value;
  // The popularity SCORE is never a sort mode of its own - it only breaks ties
  // inside "imaging quality" and inside the tier ordering (see derive.js), so it
  // stays invisible in the UI. The TIER is the ordering the UI may expose.
  const cmp = {
    tier:    (a,b) => tierIdx(a.m) - tierIdx(b.m) ||
                      popularityFactor(b.m) - popularityFactor(a.m),
    score:   (a,b) => {
      const d = objScore(b.o) - objScore(a.o);
      return d !== 0 ? d : popularityFactor(b.m) - popularityFactor(a.m);
    },
    pct:     (a,b) => (b.o.obs_pct||0) - (a.o.obs_pct||0),
    maxalt:  (a,b) => b.o.max_alt - a.o.max_alt,
    beststart:(a,b) => (a.o.best?a.o.best[0]:999) - (b.o.best?b.o.best[0]:999),
    mag:     (a,b) => (a.m.vmag??99) - (b.m.vmag??99),
    name:    (a,b) => a.m.primary_name.localeCompare(b.m.primary_name),
  }[mode];
  return list.sort(cmp);
}

export function renderTargets(fromPager = false) {
  if (!S.night) return;
  let rows;
  if (fromPager && S.lastRows) {
    rows = S.lastRows;
  } else {
    rows = [];
    let hiddenVis = 0;
    for (const o of S.night.objects) {
      const m = S.meta[o.id];
      if (!m) continue;
      if (!passesFilters(m, o)) continue;
      if (minVisHides(o)) { hiddenVis++; continue; }
      rows.push({o, m});
    }
    sortObjects(rows);
    S.filteredIds = rows.map(r => r.m.id);
    S.lastRows = rows;
    S.hiddenVis = hiddenVis;
    // NB: the page is NOT reset here - scrub re-renders must keep it.
    // resetPage() runs in the filter/sort handlers instead.
  }
  $("#resultCount").textContent =
    `${rows.length} of ${S.catalog.length} objects` +
    (rows.length < S.catalog.length ? " \u00B7 filtered" : "") +
    (S.hiddenVis ? ` \u00B7 ${S.hiddenVis} below ${minVisPct()}%` : "");

  const activeSite = (S.cfg?.sites || []).find(
    (site) => String(site.id) === String(S.activeSiteId)) || (S.cfg?.sites || [])[0];
  const context = $("#catalogContext");
  if (context) context.textContent =
    `${activeSite?.name || "Active site"} · ${S.night.date_local || "selected date"} · Min altitude ${S.minAlt.toFixed(0)}°`;

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (_page >= pages) _page = pages - 1;
  const pageRows = rows.slice(_page * PAGE_SIZE, (_page + 1) * PAGE_SIZE);

  const stClass = {vis:"st-vis", blk:"st-blk", low:"st-low", hor:"st-hor"};
  const unit = (S.cfg && S.cfg.size_unit) || "arcmin";
  const favs = new Set(S.cfg.favorites || []);

  $("#targetRows").innerHTML = pageRows.map(({o,m}, index) => {
    const st = statusOf(o, S.tIdx);
    const adv = filterAdvice(m);
    const fav = favs.has(m.id);
    const alt = Number(o.alt?.[S.tIdx]);
    const designation = desigLine(m) || m.id;
    const statusText = st.code === "vis" ? "Visible now"
      : o.best && o.best[0] > S.tIdx ? "Rises later" : "Not visible";
    return `<tr class="${S.currentId === m.id ? "selected" : ""}" data-id="${esc(m.id)}" tabindex="0" role="button" aria-label="Open details for ${esc(m.primary_name)}">
      <td class="mono rankcell">${String(_page * PAGE_SIZE + index + 1).padStart(2, "0")}</td>
      <td class="designation-cell mono" title="${esc(designation)}"><button class="starbtn${fav?" on":""}" data-fav="${esc(m.id)}" title="mark for session" aria-label="Toggle ${esc(m.primary_name)} session target">&#9733;</button><span>${esc(designation)}</span></td>
      <td class="common-cell${adv ? " has-advice" : ""}" title="${esc(m.primary_name)}"><span class="common-name">${esc(m.primary_name)}</span>${adv ? " " + adv.pill : ""}</td>
      <td class="type-cell" title="${esc(TYPE_LABEL[m.otype]||m.otype)}"><span class="typebadge" style="color:${TYPE_COLOR[m.otype] || "#9ca5b5"};border-color:${(TYPE_COLOR[m.otype] || "#9ca5b5")}55">${esc(TYPE_LABEL[m.otype]||m.otype)}</span></td>
      <td class="mono num">${m.vmag!=null?m.vmag.toFixed(1):"—"}</td>
      <td class="mono num">${Number.isFinite(alt)?`${alt.toFixed(0)}°`:"—"}</td>
      <td class="mono num" title="share of the dark window above ${S.minAlt}° and unobstructed">${o.obs_pct!=null?`${Math.round(o.obs_pct)}%`:"—"}</td>
      <td class="mono" title="${esc(compactWindowText(o))}">${esc(compactWindowText(o))}</td>
      <td title="${esc(st.text)}"><span class="statuspill ${stClass[st.code]}">${statusText}</span></td>
    </tr>`;
  }).join("");

  const pager = $("#targetPager");
  pager.classList.toggle("hidden", pages <= 1);
  $("#pagerInfo").textContent =
    `Page ${_page + 1} / ${pages} \u00B7 ${pageRows.length} shown`;
  $("#pagerPrev").disabled = _page === 0;
  $("#pagerNext").disabled = _page >= pages - 1;
}
