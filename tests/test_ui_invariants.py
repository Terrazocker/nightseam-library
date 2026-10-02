"""UI invariants: popularity is a scoring input, never a primary table column.

The catalog may retain popularity as a secondary ordering option, but its
primary table follows the compact Direction B columns and never exposes tier
letters or the underlying the popularity source number. These text assertions fail loudly
if either shell reintroduces those fields into the visible UI.

The same file also pins the `hidden` handling, because that broke once: an
author `display` beats the UA's `[hidden] { display:none }`, so a panel hidden
with the ATTRIBUTE shows up unless an author rule restores it.

And it pins the web's content width, because a width CAP there silently killed
the "Free space per side" slider (test_web_content_width_is_edge_driven).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts) -> str:
    """Read a subject file, FAILING when it is missing.

    TR-07 (0.6.32 review): this used to `pytest.skip`, so renaming a subject file
    (e.g. `web/app.js`) silently retired ~25 text invariants - including "the
    popularity score is never rendered" - with a GREEN run. The project rule
    (FIX-9b/TS6) is that a green run must never mean "not checked", so a missing
    subject is a failure that names the file."""
    path = ROOT.joinpath(*parts)
    if not path.is_file():
        pytest.fail(f"{path} is missing - this invariant cannot be checked "
                    f"(a missing subject file is a FAILURE, never a skip)")
    return path.read_text(encoding="utf-8")














def test_desktop_robustness_invariants():
    """Zero-site recovery and pano-save safety are one-line-critical: a
    substring assertion is enough to catch a revert.

    - main.js must reopen the wizard whenever there are no sites (the old
      `&& !wizard_done` guard left a permanent 0-site dead-end).
    - pano.js select() must flush a pending explicit save (it belongs to the
      previous panorama) and must not blank a stored profile for a stroke-less
      record.
    - detail.js must escape the constellation and the catalogue chips."""
    mainjs = _read("static", "js", "main.js")
    assert "if (!S.cfg.sites.length) {" in mainjs
    assert "!S.cfg.wizard_done" not in mainjs
    pano = _read("static", "js", "pano.js")
    # The desktop editor has an explicit-save boundary: select() FLUSHES the
    # captured payload, and the cancel-only path is gone.
    assert "await flushSave();" in pano
    assert "if (Pano.saveTimer) { clearTimeout(Pano.saveTimer); Pano.saveTimer = null; }" not in pano
    assert "function requestSave()" in pano
    assert "setTimeout(() => { void flushSave();" not in pano
    assert "confirmDialog({" in pano
    assert "panoSaveStatus" in _read("static", "index.html")
    # recomputeProfile must keep a stroke-less record's stored profile. Since
    # PY-03 the test is the shared strokeList() guard, so a NON-ARRAY stored
    # `strokes` also counts as stroke-less instead of crashing the editor - and
    # both iteration sites must go through it.
    assert "function strokeList(rec)" in pano
    assert "if (strokeList(Pano.cur).length === 0) {" in pano
    assert pano.count("for (const s of strokeList(Pano.cur)) {") == 2
    detail = _read("static", "js", "detail.js")
    assert "esc(constName(m.constellation))" in detail
    assert "esc(m.ngcic)" in detail


def _js_body(src: str, header: str) -> str:
    """Body of a top-level JS function, up to its first closing brace."""
    start = src.index(header)
    return src[start:src.index("\n}", start)]


def test_desktop_ownership_invariants():
    """DE-02 / DE-07 / DE-08: three ownership contracts, each a single statement.

    A handler or a cache belonged to the WRONG owner: the mask cache survived a
    panorama switch, the dismiss button bubbled into the notice's own action, and
    the wizard's typed values survived the run that should have replaced them.
    The pin slices the function BODY (a whole-file grep would pass on an
    unrelated copy elsewhere)."""
    pano = _read("static", "js", "pano.js")
    select_body = _js_body(pano, "async function select(rec) {")
    assert "invalidateMask();" in select_body, (
        "DE-02: select() must drop the previous panorama's mask cache")
    assert "await flushSave();" in select_body, (
        "a panorama switch must FLUSH the pending explicit save, not drop it")
    assert "clearTimeout(Pano.saveTimer)" not in select_body, (
        "DE-04: the cancel-only path is what silently lost the edit")
    flush_body = _js_body(pano, "async function flushSave() {")
    assert "persistSave(job)" in flush_body, (
        "the flush wrapper must delegate the captured job to the persistence helper")
    persist_body = _js_body(pano, "async function persistSave(job) {")
    assert "S.panos[idx] = upd;" in persist_body, (
        "DE-05: refresh ONLY the record that was saved, never the whole array")
    assert "if (Pano.cur === job.rec)" in persist_body, (
        "DE-05: never rebind a record the user has already left")
    assert "const stillEditing = Pano.cur === job.rec;" in persist_body, (
        "DE-05: response ownership must be checked against the captured record")
    assert "if (stillEditing && !Pano.pending)" in persist_body, (
        "DE-05: a response must preserve newer unsaved edits")
    assert 'addEventListener("pagehide"' in pano and "keepalive: true" in pano, (
        "DE-04: the unload guard must survive the window close")
    util = _read("static", "js", "util.js")
    toast_body = _js_body(util, "export function toast(msg")
    assert "stopPropagation()" in toast_body, (
        "DE-08: the toast's dismiss button must not bubble to the toast body")
    wiz = _read("static", "js", "wizard.js")
    assert "_formState = {};" in _js_body(wiz, "export function openWizard() {"), (
        "DE-07: a wizard replay must not reuse the first run's coordinates")












def test_desktop_weather_drops_a_superseded_response():
    """DE-06: the weather panel is labelled with the CURRENT site, so a response
    that lands after the user changed the site must be dropped. It used to be
    written unconditionally - one site's name over another site's numbers - and a
    late failure nulled a newer success."""
    body = _js_body(_read("static", "js", "weather.js"),
                    "async function loadWeather() {")
    assert "const gen = ++_wxGen;" in body
    assert body.count("if (gen !== _wxGen) return;") >= 2, (
        "DE-06: the success AND the failure path must drop a stale response")
    assert "if (gen === _wxGen) WX_STATE.loading = false;" in body, (
        "DE-06: a superseded request must not clear the newer one's spinner")


def test_desktop_pref_handlers_survive_a_failed_settings_post():
    """DE-09: `api()` toasts and RETHROWS. A preference handler that mutates
    `S.cfg` and then awaits the POST without catching skipped its own re-render,
    so the visible ranking/list kept the OLD preference while the control showed
    the new one. Every await preceded by an `S.cfg.` write must be guarded."""
    for parts in (("static", "js", "header.js"), ("static", "js", "setup.js")):
        lines = _read(*parts).splitlines()
        for i, line in enumerate(lines):
            if 'await api("/api/settings"' not in line:
                continue
            mutates = any(prev.strip().startswith("S.cfg.")
                          for prev in lines[max(0, i - 10):i])
            if not mutates:
                continue
            window = "\n".join(lines[i:i + 8])
            assert "catch" in window, (
                f"DE-09: {parts[-1]}:{i + 1} mutates S.cfg then awaits "
                "/api/settings with no catch - a failed save would skip the "
                "re-render")


def test_desktop_loupe_and_chart_hover_invariants():
    """DE-10 / DE-11 / DE-12: three "looks right, is wrong" one-liners.

    The loupe mapped the cursor into the magnifier by the sample SCALE instead of
    its DESTINATION size (so the marker never marked the sampled pixel); both
    charts compared the hover by OBJECT identity against a freshly rebuilt point
    array, which is always true (a full repaint - and a canvas reallocation - per
    mousemove); and each legend's chips were rendered once, so a type toggled in
    the other chart's legend was never reflected."""
    pano = _read("static", "js", "pano.js")
    loupe = _js_body(pano, "function updateLoupe() {")
    assert "lcv.width / sw" in loupe and "lcv.height / sw" in loupe, (
        "DE-10: the loupe marker must map by the destination size")
    assert "/ Z;" not in loupe, "DE-10: the old sample-scale mapping is back"
    sky = _read("static", "js", "skyview.js")
    skymap = _read("static", "js", "skymap.js")
    assert "if (key !== prev) { SKY_HOVER = best; renderSky(); }" in sky, (
        "DE-11: the sky hover guard must compare by value, not object identity")
    assert "if (key !== prev) { SM_HOVER = best; renderSkyMap(); }" in skymap, (
        "DE-11: the sky-map hover guard must compare by value")
    assert "syncTypeLegend();" in _js_body(sky, "export function renderSky() {"), (
        "DE-12: the Sky View legend must resync on render")
    assert "syncTypeLegend();" in _js_body(skymap, "export function renderSkyMap() {"), (
        "DE-12: the Sky Map legend must resync on render")


def test_desktop_dialog_and_worker_invariants():
    """DE-13 / DE-14 / DE-15.

    The library-edit dialog was the only modal with no Escape path; the detail
    modal's "closed while fetching" guard compared against an identity no close
    path ever cleared (so a full-size blob stayed decoded for the session); and
    the worker allocated and filled a flat `ceil` buffer that was never posted or
    transferred (~23 MB per advanced-mode build)."""
    arch = _read("static", "js", "archive.js")
    bind = _js_body(arch, "function bindArchive() {")
    assert 'addEventListener("keydown"' in bind and "Escape" in bind, (
        "DE-13: the library-edit dialog must close on Escape like every other one")
    detail = _read("static", "js", "detail.js")
    assert "S.currentId = null;" in _js_body(detail, "function closeDetail() {"), (
        "DE-14: closing must DROP the identity so an in-flight fetch sees it")
    assert detail.count("closeDetail();") >= 3, (
        "DE-14: every close path must go through closeDetail()")
    worker = _read("static", "js", "planning_worker.js")
    assert "const ceil = new Float64Array" not in worker, (
        "DE-15: the dead flat ceiling buffer is back")
    assert "ceil[base + i]" not in worker
    assert "ceil: ceils" in worker, (
        "DE-15: the per-object ceilings (prefs rescore) must stay")


def test_desktop_hides_popularity_from_the_primary_table():
    """The desktop keeps the secondary sort but follows the compact columns."""
    html = _read("static", "index.html")
    js = _read("static", "js", "targets.js")
    assert "<th>#</th><th>Designation</th><th>Common name</th>" in html
    assert 'value="tier"' in html, "the desktop must offer the tier sort"
    assert html.index('value="tier"') < html.index('value="score"'), (
        "the tier sort must be the FIRST option, i.e. the default")
    assert "<th>Tier</th>" not in html
    assert "<th>Frame</th>" not in html
    assert "assignPopularityTiers" in js, "tiers must come from core, not a copy"
    assert "tierbadge" not in js
    assert 'value="popularity"' not in html
    assert "popularity:" not in js                   # no score comparator key
    for forbidden in ("${m.popularity", "${o.popularity", "<dt>Popularity</dt>"):
        assert forbidden not in js, f"targets.js still shows popularity: {forbidden!r}"


def test_desktop_frame_cell_is_green_until_it_needs_a_mosaic():
    """Frame coverage advice keeps its green/amber semantics for secondary
    filters and inspector content, without adding a Frame table column."""
    derive = _read("static", "js", "derive.js")
    assert 'cov.pct > 100 ? "st-low" : "st-vis"' in derive
    assert "cov.pct > 85" not in derive, "the 85% warning band is gone"








def test_the_scoring_loop_is_shared_and_the_window_is_a_parameter():
    """SHELL-UNIFICATION (CO-07 follow-up): both shells score through
    core/scoring.js::scoreNightTracks, and the scoring span is the `range`
    parameter. The desktop keeps its 241-sample noon->noon payload the charts
    draw and clips the numbers to dusk..dawn by passing `range`; the web's grid
    already IS the dark window and passes none. Before this, the desktop owned a
    second aggregation loop (`derive.js::recomputeNightDerived`) that could drift
    from core - the drift was CO-07."""
    scoring = _read("core", "scoring.js")
    assert "const win = Array.isArray(ctx.range) ? ctx.range : null;" in scoring
    assert "const nightN = winHi - winLo + 1;" in scoring, (
        "the % denominator must be the scoring range, not the grid")
    derive = _read("static", "js", "derive.js")
    assert "scoreNightTracks(tracks, N.objects, jd0, step, minAlt, pano, ctx)" in derive
    assert "range: [i0, i1]" in derive, "the desktop must pass its dusk..dawn range"
    assert "range: null" not in derive, (
        "one scoring pass per recompute - no second, unclipped pass")
    # the shell's local aggregation must be gone, not kept beside the core call
    body = _js_body(derive, "export function recomputeNightDerived() {")
    assert "effectiveMoonGlow(" not in body and "moonPenalty(" not in body, (
        "the desktop still aggregates the moon penalty itself")








def test_desktop_keeps_the_hidden_class_authoritative():
    """The desktop hides things with the `.hidden` CLASS - it must keep winning."""
    assert ".hidden { display: none !important; }" in _read("static", "style.css")
    html = _read("static", "index.html")
    assert 'id="rigPicker" class="hidden"' in html
    assert 'id="rigForm" class="formrow hidden"' in html













def test_desktop_sky_view_respects_block_above_and_hatches_open_above():
    """Desktop parity for the sky band: the sky view must treat the band top as
    a ceiling only with block_above, and hatch (never shade) the assumed-open
    area above the band by default."""
    derive = _read("static", "js", "derive.js")
    assert "act.block_above && act.profile_top" in derive, \
        "activeTopProfile must require block_above"
    assert "export function activeBandTop" in derive, "raw band top helper missing"
    sky = _read("static", "js", "skyview.js")
    assert "activeBandTop" in sky, "sky view must read the raw band top"
    assert "hatchPoly" in sky, "the assumed-open area must be hatched on the desktop"
    # TR-11 (0.6.32 review): `assert "open above" in sky` was satisfied by a
    # COMMENT in skyview.js. Pin the legend's real condition instead: the hatch
    # legend is emitted only in sky mode with a band top and WITHOUT block-above.
    assert 'const openAbove = mode === "sky" && !!activeBandTop() ' \
           '&& !activeTopProfile();' in sky, \
        "the assumed-open legend must require sky mode and no block-above"
    assert "activePaintMode() !== \"sky\"" in sky, \
        "sky mode must not stroke the fully-blocked 89-deg ring"
    assert "skyPanoLegend" in sky and "blocked by horizon" in sky
    assert "above band: open (assumed)" in sky
    assert 'id="skyPanoLegend"' in _read("static", "index.html")














def test_desktop_planning_is_core_only():
    """Phase C: the desktop builds /api/night AND /api/object/{id} locally from
    the shared core. The old planning_client_compute flag and the server
    fallback are gone - neither consumer may reach for a server compute route,
    and the server may not carry the retired setting."""
    pc = _read("static", "js", "planning_client.js")
    assert "computeNightTracks" in pc
    assert "from \"./core.js\"" in pc
    night = _read("static", "js", "night.js")
    assert "buildClientNight" in night
    assert "planning_client_compute" not in night
    assert "/api/night" not in night, "the server planning route is gone"
    assert "falling back to the " not in night, "no server fallback may return"
    assert "requestAnimationFrame" in night, \
        "the busy chip must paint before the synchronous compute"
    detail = _read("static", "js", "detail.js")
    assert "buildClientDetail" in detail
    assert "planning_client_compute" not in detail
    assert not re.search(r"/api/object/\$\{id\}\?", detail), \
        "detail.js must not call the server planning route"
    assert "/api/object/${id}/image" in detail, \
        "the image route is IO and stays"
    server = _read("app", "server.py")
    assert "planning_client_compute" not in server, \
        "the retired flag must not survive in the API"


def test_catalog_edits_invalidate_the_planning_caches():
    """FIX-2 (review DS2): a custom-target add/delete must drop the planning
    caches - the metadata list + built payload in planning_client.js AND the
    worker's retained catalog - or a same-length change can bind tracks to the
    wrong object. setup.js::refreshCatalogMeta is the one edit path."""
    setup = _read("static", "js", "setup.js")
    assert "resetPlanningCaches()" in setup, \
        "refreshCatalogMeta must invalidate the planning caches"
    pc = _read("static", "js", "planning_client.js")
    assert "export function resetPlanningCaches" in pc
    assert "catalog ids mismatch" in pc, \
        "the worker response must be id-validated before binding"
