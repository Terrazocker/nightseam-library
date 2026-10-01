"""JavaScript behaviour tests that do not fit the Python mirrors.

These drive the real browser modules through Node with a stub DOM/fetch, which
is the smallest practical harness for async UI invariants. They skip cleanly
when Node is unavailable or cannot be spawned (restricted sandboxes deny the
pipe that captures its output).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_node_harness(script: str):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - JS behaviour cannot be checked here")
    try:
        proc = subprocess.run([node, str(ROOT / "tests" / "js" / script)],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=180, cwd=str(ROOT))
    except OSError as exc:          # sandboxed environments deny pipe creation
        pytest.fail(f"cannot spawn node here: {exc}")
    assert proc.returncode == 0, (
        f"{script} failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return proc.stdout


def test_night_load_supersession_invariant():
    """A superseded night load must not commit its payload or clear busy."""
    out = run_node_harness("night_race.mjs")
    assert "supersession invariant held" in out


def test_shared_planner_state_revision_contract():
    """The panorama invalidation event and content revision are one pure core
    contract, so desktop and web cannot silently invent different cache keys."""
    out = run_node_harness("plannerstate.mjs")
    assert "planner state contract held" in out


def test_horizon_metadata_normalization_and_comparison():
    """Horizon annotations use one bounded shape in both shells and profile
    comparison reports a reproducible world-bin difference."""
    out = run_node_harness("horizon_metadata.mjs")
    assert "horizon metadata contract held" in out


def test_visibility_explainability_and_culmination_contract():
    """Core target results expose stable reason codes, usable boundaries and
    culmination/rise/set facts for both shells to format."""
    out = run_node_harness("visibility_explain.mjs")
    assert "visibility explainability contract held" in out


def test_core_exports_that_no_other_test_named():
    """TO-09 (carry-over TS4): eight exported core functions were referenced
    nowhere under tests/; this drives each through a contract property."""
    out = run_node_harness("core_exports.mjs")
    assert "core exports contract held" in out


def test_worker_lifecycle_prefs_during_build():
    """FIX-1: a preference change during an in-flight worker build must not
    cancel the build; the finished plan carries the new preference and the busy
    chip clears (real planning_worker.js under Node via worker_bridge.mjs)."""
    out = run_node_harness("worker_lifecycle.mjs")
    assert "worker lifecycle contract held" in out


def test_catalog_identity_across_worker_boundary():
    """FIX-2: a changed catalog must invalidate the worker + client caches, and
    a worker response whose ids do not match the current catalog must never be
    bound by position (real planning_worker.js under Node)."""
    out = run_node_harness("catalog_identity.mjs")
    assert "catalog identity contract held" in out


def test_polar_fallback_never_reads_the_wall_clock():
    """FIX-6: the polar-day fallback window needs the site's local noon (or an
    explicit day anchor); with neither it must refuse, not use `new Date()`."""
    out = run_node_harness("noon_fallback.mjs")
    assert "noon fallback contract held" in out


def test_sync_cache_key_covers_tz_and_profile_content():
    """FIX-3: on the synchronous fallback a timezone change or a horizon
    repaint (same pano id, same 720 bins) must rebuild the payload."""
    out = run_node_harness("sync_cache_key.mjs")
    assert "sync cache key contract held" in out


def test_desktop_window_contract():
    """The clear window / % of night / score are measured inside dusk..dawn.

    Regression: the derivation counted the payload's twilight margins, so the
    targets table advertised clear windows that included daylight (measured on
    a real payload: 164/197 objects, worst 690 min for a 590-min night)."""
    out = run_node_harness("derive_window.mjs")
    assert "derive window contract held" in out


def test_desktop_preference_and_scrub_handlers():
    """E5/E8/E9 + DE-01: the real header.js Bortle/min-alt handlers re-derive the
    Bortle default live and let an explicit value win, the real main.js keydown
    handler scrubs by one 6-min sample (Shift = 60 min), clamped, and the real
    setup.js custom-target add rebuilds the night plan (review 0.6.24, DE-01:
    it dropped the planning caches but never re-ran the build, so the new target
    was missing from Targets/Tonight/Sky). Driven through a stub DOM because
    headless browsers cannot run in this sandbox; the pure helpers are pinned
    separately in tests/test_js_parity.py."""
    out = run_node_harness("dom_handlers.mjs")
    assert "E5 handler: bortle re-derives min-alt held" in out
    assert "E5 handler: explicit min-alt survives bortle held" in out
    assert "E8 handler: 6/60-min steps + clamp held" in out
    assert "E9 handler: scrub window (sunset..sunrise) + scoring window held" in out
    assert "DE-01 handler: custom target add rebuilds the plan held" in out


def test_web_panorama_geometry():
    """The web panorama editor's coordinate system (web/horizon.js): the
    row<->altitude and azimuth->x transforms used by the guides are the SAME
    ones the calibration grid, ceilFor and the profile use; obstacle/sky profile
    derivation, the sky-mode open-above sentinel and the 360 deg seam wrap match
    the desktop's semantics; a save/load round trip preserves them."""
    out = run_node_harness("pano_geom.mjs")
    assert "pano geometry contract held" in out


def test_web_backup_integrity():
    """web/webstore.js + web/horizon.js backup/restore through a fake
    IndexedDB: truthful hasImage, the profile/strokes invariant, staged
    validation before any write, and rollback to the pre-restore snapshot when
    a write fails."""
    out = run_node_harness("web_backup.mjs")
    assert "web backup integrity held" in out


def test_scrub_window_degeneracy_policy():
    """The scrub/detail range must be strictly increasing for every twilight
    geometry, including the Tromso 2026-01-15 grazing polar night where the
    sunset/sunrise pair is degenerate (sunset after the next dawn)."""
    out = run_node_harness("scrub_degenerate.mjs")
    assert "scrub degeneracy policy held" in out


def test_web_panorama_editor_history_and_view():
    """The panorama editor's pure UI logic (web/horizon.js): undo/redo history
    with a bounded stack and redo invalidation, the dirty/saved baseline, and
    the presentation-only zoom transform (uniform scale, screen<->base
    round-trip, focus clamping, DPR-independent geometry)."""
    out = run_node_harness("pano_ui.mjs")
    assert "pano ui contract held" in out


def test_desktop_attribute_sinks_escape_imported_data():
    """BUG-002: an imported custom id / image type must never become MARKUP.

    The real desktop renderers (targets.js, tonight.js) run under Node with a
    stub DOM and a hostile id is pushed through them: every attribute sink has to
    emit an entity-escaped value, no inline event handler may remain (that is
    what forced `script-src 'unsafe-inline'`, which made the injection
    executable), and the listener that replaced the thumbnail `onerror=` must
    actually run. Mutation-proven: dropping one `esc()` makes it fail."""
    out = run_node_harness("dom_escape.mjs")
    assert "desktop attribute sinks escape hostile values" in out


def test_web_sky_band_complement_geometry():
    """ITEM C (0.6.13): the visible-sky band is the exact complement of the
    blocked band - open-above (90.5) extends to the zenith, unpainted columns
    are fully blocked, the preview covers the full width, splits at the blocked
    gaps and never self-intersects; and the shared edge geometry yields
    identical vertices on web + desktop."""
    out = run_node_harness("sky_band.mjs")
    assert "sky-band contract held" in out


def test_pano_mode_semantics_ruling_c():
    """DE-03/WE-02 under ruling C: a DELIBERATE paint-mode switch re-derives the
    canonical blank for a stroke-less record in BOTH shells and is ONE undoable
    step that restores the previous mode AND the authoritative profile;
    open/load/save never blank it, and the mask-derived (stroke-y) path is left
    to the strokes."""
    out = run_node_harness("pano_mode_semantics.mjs")
    assert "pano mode semantics contract held" in out


def test_horizon_entry_points_degrade_on_malformed_records():
    """CR2 (carried over, closed in 0.6.35): every `core/horizon.js` entry point
    must return its EMPTY answer for a missing or malformed record instead of
    throwing mid-render. `visibleSkyBands` was fixed earlier (CO-04); this pins
    the whole family, including the element-level case (`ceilFor` returning
    `NaN`: `alt > NaN` is false, so every object would read as blocked).
    Mutation-proven for both guards this pass added."""
    out = run_node_harness("horizon_degrade.mjs")
    assert "CR2 horizon degrade contract held" in out


def test_worker_failure_disposes_the_worker_and_aligns_the_detail_peak():
    """DS4 + DS5 (carried over, closed in 0.6.35).

    DS5: `_worker.onerror` set the broken flag but kept the DEAD Worker, so the
    revive hook handed the corpse back to `postMessage` - the worker path could
    not recover. DS4: the detail payload took its peak from the 2-dp-ROUNDED
    array while the night payload used the raw argmax, so the two could name
    different samples. Drives the real planning_client.js with a counting Worker
    shim."""
    out = run_node_harness("worker_failure.mjs")
    assert "DS4/DS5 worker contract held" in out
