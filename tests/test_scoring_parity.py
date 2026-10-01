"""Phase 3b: batched scoring parity + mutation (owner-authorized semantics).

The goldens (tests/fixtures/scoring_goldens.json) were captured from the
pre-change per-sample-precession `nightStats`. The batched engine must keep
every DISPLAYED field and the transit index bit-identical; only the continuous
score doubles may drift by the authorized precess-once amount (<=2e-5).

The fixture also carries the 697 INPUT objects the stats were captured for, and
the engine reads them from there: the parity proof must not change when
`web/catalog.json` does (it used to rebuild its inputs from the shipped catalog,
so removing the curated list looked like a scoring regression).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "tests" / "fixtures" / "scoring_goldens.json"
RUNNER = ROOT / "tests" / "js" / "scoring_parity.mjs"


def _run(core_dir=None):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - scoring parity cannot run")
    argv = [node, str(RUNNER), str(GOLDENS)]
    if core_dir is not None:
        argv.append(str(core_dir))
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=300, cwd=str(ROOT))
    except OSError as exc:
        pytest.fail(f"cannot spawn node here: {exc}")


def test_batched_scoring_matches_the_pre_change_goldens():
    proc = _run()
    assert proc.returncode == 0, f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert "scoring batched parity held" in proc.stdout


def test_perturbed_scoring_coefficient_is_detected(tmp_path):
    core = tmp_path / "core"
    shutil.copytree(ROOT / "core", core)
    target = core / "scoring.js"
    text = target.read_text(encoding="utf-8")
    old = "let w = Math.sin(Math.max(0, Math.min(90, alt)) * Math.PI / 180);"
    new = "let w = Math.sin(Math.max(0, Math.min(90, alt)) * Math.PI / 180) * 1.001;"
    assert old in text
    target.write_text(text.replace(old, new), encoding="utf-8")
    proc = _run(core)
    assert proc.returncode != 0, \
        "a perturbed altitude weight must break the scoring parity"
