"""F-19 revival: the scoring-characterization and D1 contract drivers.

The goldens (tests/fixtures/scoring_goldens.json) were captured from the
pre-change per-sample-precession `nightStats` for 697 objects x 2 sites. The
batched engine must keep every DISPLAYED field and the transit index
bit-identical; continuous scores and max altitude retain the existing
characterization tolerances (scores <=2e-5, altitude <=1e-5 deg). The driver reads its INPUT
objects from the goldens, so a catalog edit can never look like a scoring
regression. A missing node is a HARD failure (never a skip) - the same rule
the other Node-backed suites enforce.

The goldens are FROZEN: nothing regenerates them (the Python oracle was
retired in Phase C). Never update them to hide a scoring regression.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "tests" / "fixtures" / "scoring_goldens.json"
RUNNER = ROOT / "tests" / "js" / "scoring_parity.mjs"
D1 = ROOT / "tests" / "js" / "core_d1.mjs"


def _node(argv, timeout):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - core parity cannot run")
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, cwd=str(ROOT))
    except OSError as exc:          # sandboxed environments deny pipe creation
        pytest.fail(f"cannot spawn node here: {exc}")


def _run_scoring(core_dir=None, goldens=GOLDENS):
    argv = ["node", str(RUNNER), str(goldens)]
    if core_dir is not None:
        argv.append(str(core_dir))
    return _node(argv, timeout=300)


def test_batched_scoring_matches_the_pre_change_goldens():
    proc = _run_scoring()
    assert proc.returncode == 0, f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert "scoring batched parity held" in proc.stdout


def test_perturbed_scoring_coefficient_is_detected(tmp_path):
    """Mutation proof on a COPY of core/: a perturbed altitude weight must
    break the parity - the driver cannot silently pass a formula change."""
    core = tmp_path / "core"
    shutil.copytree(ROOT / "core", core)
    target = core / "scoring.js"
    text = target.read_text(encoding="utf-8")
    old = "let w = Math.sin(Math.max(0, Math.min(90, alt)) * Math.PI / 180);"
    new = "let w = Math.sin(Math.max(0, Math.min(90, alt)) * Math.PI / 180) * 1.001;"
    assert old in text
    target.write_text(text.replace(old, new), encoding="utf-8")
    proc = _run_scoring(core)
    assert proc.returncode != 0, \
        "a perturbed altitude weight must break the scoring parity"


@pytest.mark.parametrize("export,expression,label,missing", [
    ("nightStatsBatch", "[]", "batch", "all"),
    ("nightStatsBatch", "original.nightStatsBatch(...args).slice(0, -1)",
     "batch", 1),
    ("scoreNightTracks", "[]", "split", "all"),
    ("scoreNightTracks", "original.scoreNightTracks(...args).slice(0, -1)",
     "split", 1),
])
def test_incomplete_scoring_output_is_rejected(tmp_path, export, expression, label, missing):
    core = _overridden_core(tmp_path, export, expression)
    proc = _run_scoring(core)
    count = len(json.loads(GOLDENS.read_text(encoding="utf-8"))["cases"][0]["stats"])
    actual_count = 0 if missing == "all" else count - missing
    diagnostic = f"{label}: expected {count} rows, got {actual_count}"
    _assert_rejected(proc, diagnostic)


def _overridden_core(tmp_path, export, expression):
    """Override one public export on a private core copy; original formulas stay intact."""
    core = tmp_path / "core"
    shutil.copytree(ROOT / "core", core)
    index = core / "index.js"
    index.rename(core / "original-index.js")
    index.write_text(
        'export * from "./original-index.js";\n'
        'import * as original from "./original-index.js";\n'
        f'export function {export}(...args) {{ return {expression}; }}\n',
        encoding="utf-8")
    return core


def _assert_rejected(proc, diagnostic):
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, output
    assert diagnostic in output, output[-1500:]
    assert "scoring batched parity held" not in output


@pytest.mark.parametrize("export,label", [
    ("nightStatsBatch", "batch"), ("scoreNightTracks", "split"),
])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "undefined"])
def test_nonfinite_or_missing_score_is_rejected(tmp_path, export, label, value):
    expression = (f"original.{export}(...args).map(row => "
                  f"({{...row, sClassic: {value}}}))")
    core = _overridden_core(tmp_path, export, expression)
    proc = _run_scoring(core)
    _assert_rejected(proc, f"{label}[0].sClassic: expected a finite number")


@pytest.mark.parametrize("mutation,diagnostic", [
    ("empty_cases", "no nonempty cases array"),
    ("empty_stats", "nonempty stats array"),
    ("null_score", "expected[0].sClassic: expected a finite number"),
    ("missing_score", "expected[0].sClassic: expected a finite number"),
])
def test_invalid_golden_shape_cannot_make_parity_vacuous(tmp_path, mutation, diagnostic):
    fixture = json.loads(GOLDENS.read_text(encoding="utf-8"))
    if mutation == "empty_cases":
        fixture["cases"] = []
    elif mutation == "empty_stats":
        fixture["cases"][0]["stats"] = []
    elif mutation == "null_score":
        fixture["cases"][0]["stats"][0]["sClassic"] = None
    else:
        del fixture["cases"][0]["stats"][0]["sClassic"]
    path = tmp_path / "invalid-goldens.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    proc = _run_scoring(goldens=path)
    _assert_rejected(proc, diagnostic)


def test_d1_world_bin_contract_holds():
    """D1: panorama profiles are WORLD-AZIMUTH bins (bin i = azimuth i*0.5
    deg). The planner/editor routes and UI are retired; this retained pure-core
    contract still drives core/horizon.js through the original driver."""
    proc = _node(["node", str(D1)], timeout=120)
    assert proc.returncode == 0, f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert "core D1 contract held" in proc.stdout
