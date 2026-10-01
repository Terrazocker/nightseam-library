"""D1 contract: core panorama profiles are WORLD-AZIMUTH bins.

The web editor's FOV-relative mask converts through the core adapter at the
storage boundary. This proves the node contract holds on the real core, that a
perturbed world-bin mapping fails it (mutation), and that both shells wire the
canonical representation and the D2 arcmin FOV unit.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_contract(module: Path) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - D1 contract cannot be checked")
    try:
        return subprocess.run(
            [node, str(ROOT / "tests" / "js" / "core_d1.mjs"), str(module)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=180, cwd=str(ROOT))
    except OSError as exc:          # sandboxed environments deny pipe creation
        pytest.fail(f"cannot spawn node here: {exc}")


def test_core_d1_contract_holds():
    proc = _run_contract(ROOT / "core" / "horizon.js")
    assert proc.returncode == 0, (
        f"D1 contract failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-1500:]}")
    assert "core D1 contract held" in proc.stdout


def test_perturbed_world_bin_mapping_fails_the_contract(tmp_path):
    """Mutation proof: the world-bin mapping is load-bearing."""
    src = (ROOT / "core" / "horizon.js").read_text(encoding="utf-8")
    mutated = src.replace("Math.floor(a / BIN_DEG)",
                          "Math.floor(a / (BIN_DEG * 2))")
    assert mutated != src, "mutation anchor moved - update the mutation"
    target = tmp_path / "horizon.js"
    target.write_text(mutated, encoding="utf-8")
    proc = _run_contract(target)
    assert proc.returncode != 0, \
        "a perturbed world-bin mapping must fail the D1 contract"
    assert "FAILED" in proc.stdout


def test_web_shell_wires_the_d1_adapter():
    hz = (ROOT / "web" / "horizon.js").read_text(encoding="utf-8")
    assert "normalizeProfileBins" in hz, "the web boundary must convert bins"
    assert "d.panos.forEach(normalizeProfileBins)" in hz, \
        "stored legacy profiles must be converted on load"
    assert "normalizeProfileBins(rec)" in hz, \
        "imported backup records must be converted"
    assert "profileBins: WORLD_BINS" in hz, "new records are born canonical"


def test_web_rig_fov_unit_is_arcmin_with_a_legacy_adapter():
    ws = (ROOT / "web" / "webstore.js").read_text(encoding="utf-8")
    assert "export function fovArcmin" in ws
    assert "export function fovDeg" in ws
    assert "fovFrom" not in ws, "the old degree-valued helper must be gone"
    assert "r.fov_unit !== 'arcmin'" in ws, "legacy degree rigs must convert"
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "fovArcmin" in app and "fov_unit: 'arcmin'" in app
    assert "rig.fov_w * 60" not in app, \
        "framePct must take arcmin directly (no hidden degree conversion)"
