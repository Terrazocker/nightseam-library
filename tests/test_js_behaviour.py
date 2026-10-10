"""JavaScript behaviour tests that do not fit the Python mirrors.

These drive the real browser modules through Node with a stub DOM/fetch, which
is the smallest practical harness for async UI invariants. A missing Node is a
HARD failure (never a skip) - the same rule the syntax checker enforces.
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


def test_polar_fallback_never_reads_the_wall_clock():
    """FIX-6: the polar-day fallback window needs the site's local noon (or an
    explicit day anchor); with neither it must refuse, not use `new Date()`."""
    out = run_node_harness("noon_fallback.mjs")
    assert "noon fallback contract held" in out


def test_scrub_window_degeneracy_policy():
    """The scrub/detail range must be strictly increasing for every twilight
    geometry, including the Tromso 2026-01-15 grazing polar night where the
    sunset/sunrise pair is degenerate (sunset after the next dawn)."""
    out = run_node_harness("scrub_degenerate.mjs")
    assert "scrub degeneracy policy held" in out


def test_horizon_entry_points_degrade_on_malformed_records():
    """CR2 (carried over, closed in 0.6.35): every `core/horizon.js` entry point
    must return its EMPTY answer for a missing or malformed record instead of
    throwing mid-render. `visibleSkyBands` was fixed earlier (CO-04); this pins
    the whole family, including the element-level case (`ceilFor` returning
    `NaN`: `alt > NaN` is false, so every object would read as blocked).
    Mutation-proven for both guards this pass added."""
    out = run_node_harness("horizon_degrade.mjs")
    assert "CR2 horizon degrade contract held" in out
