"""Mutation proofs for the Class P core modules (Phase 2, concepts 6-9).

Each case copies core/ to a temp dir, perturbs one load-bearing constant or
branch, runs the committed oracle harness against the copy and compares the
output with the oracle HERE: the perturbation must produce a mismatch. The
real core matches. Mirrors the D1 mutation pattern.
"""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "core_fixtures.json"
FX = json.loads(FIXTURES.read_text(encoding="utf-8"))
TOL = FX["manifest"]["tolerances"]


def _node():
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - mutations cannot run")
    return node


def _run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=300, cwd=str(ROOT))
    except OSError as exc:
        pytest.fail(f"cannot spawn node here: {exc}")


def _near(a, b, tol):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def _epoch(iso):
    return None if iso is None else datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _mismatch(core_dir) -> bool:
    """True when the core copy deviates from the committed oracle fixtures."""
    proc = _run([_node(), str(ROOT / "tests" / "js" / "core_fixtures.mjs"),
                 str(FIXTURES), str(core_dir)])
    assert proc.returncode == 0, proc.stderr[-1500:]
    got = json.loads(proc.stdout)
    for c, h in zip(FX["jd"], got.get("jd", [])):
        if not _near(c["jd"], h, TOL["jd"]):
            return True
    for c, h in zip(FX["gmst"], got.get("gmst", [])):
        if not _near(c["deg"] % 360, h, TOL["gmst"]):
            return True
    for key, fields in (("precession", ("ra_out", "dec_out")),
                        ("altaz", ("alt", "az")),
                        ("sun", ("ra", "dec"))):
        for c, res in zip(FX[key], got.get(key, [])):
            if not all(_near(c[f], res[i], TOL[key]) for i, f in enumerate(fields)):
                return True
    for c, res in zip(FX["moon"], got.get("moon", [])):
        if not all(_near(c[f], res[i], TOL["moon"])
                   for i, f in enumerate(("ra", "dec", "illum", "age"))):
            return True
    for c, res in zip(FX["twilight"], got.get("twilight", [])):
        if res["level"] != c["level"]:
            return True
        for key in ("sunset", "sunrise", "dusk", "dawn"):
            a, b = _epoch(c[key]), _epoch(res[key])
            if (a is None) != (b is None):
                return True
            if a is not None and abs((a - b).total_seconds()) > TOL["twilight_s"]:
                return True
    return False


def _mutated_core(tmp_path, rel, old, new):
    dest = tmp_path / "core"
    shutil.copytree(ROOT / "core", dest)
    target = dest / rel
    text = target.read_text(encoding="utf-8")
    assert old in text, f"mutation anchor moved in {rel}: {old!r}"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return dest


def test_real_core_matches_the_oracle():
    assert not _mismatch(ROOT / "core"), "the real core deviates from the oracle"


def test_precession_axis_mutation_is_detected(tmp_path):
    """Middle rotation sign = the 0.6.9 half-degree bug class."""
    core = _mutated_core(tmp_path, "astro.js", "* R2D - zeta", "* R2D + zeta")
    assert _mismatch(core), "a perturbed precession rotation must be detected"


def test_sun_model_mutation_is_detected(tmp_path):
    core = _mutated_core(tmp_path, "astro.js",
                         "280.460 + 0.9856474 * n",
                         "280.461 + 0.9856474 * n")
    assert _mismatch(core), "a perturbed solar model must be detected"


def test_twilight_pairing_mutation_is_detected(tmp_path):
    """The pre-fix 'any ascent is a morning' pairing must be detected."""
    core = _mutated_core(tmp_path, "planning.js",
                         "else if (evening) { morning = dt; break; }",
                         "else { morning = dt; }")
    assert _mismatch(core), "the old sunrise pairing must be detected"


def test_scrub_window_mutation_is_detected(tmp_path):
    core = _mutated_core(tmp_path, "planning.js",
                         "&& (dawnMs == null || sunsetMs < dawnMs)", "&& true")
    proc = _run([_node(), str(ROOT / "tests" / "js" / "scrub_degenerate.mjs"),
                 str(core / "planning.js")])
    assert proc.returncode != 0, \
        "accepting the degenerate sunset pair must fail the policy matrix"
