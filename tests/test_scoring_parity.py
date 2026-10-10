"""F-19 revival: the scoring-characterization and D1 contract drivers.

The goldens (tests/fixtures/scoring_goldens.json) were captured from the
pre-change per-sample-precession `nightStats` for 697 objects x 2 sites. The
batched engine must keep every DISPLAYED field and the transit index
bit-identical; only the continuous score doubles may drift by the
owner-authorized precess-once amount (<=2e-5). The driver reads its INPUT
objects from the goldens, so a catalog edit can never look like a scoring
regression. A missing node is a HARD failure (never a skip) - the same rule
the other Node-backed suites enforce.

The goldens are FROZEN: nothing regenerates them (the Python oracle was
retired in Phase C). Never update them to hide a scoring regression.
"""

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


def _run_scoring(core_dir=None):
    argv = ["node", str(RUNNER), str(GOLDENS)]
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


def test_d1_world_bin_contract_holds():
    """D1: panorama profiles are WORLD-AZIMUTH bins (bin i = azimuth i*0.5
    deg) - the storage contract the desktop editor, the server and the core
    adapter all share. Drives core/horizon.js through the retained driver."""
    proc = _node(["node", str(D1)], timeout=120)
    assert proc.returncode == 0, f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert "core D1 contract held" in proc.stdout
