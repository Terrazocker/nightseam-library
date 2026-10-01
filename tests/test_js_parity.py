"""Cross-engine behaviour: static/js/astro.js vs web/astro.js vs core/.

The desktop and the web each own a JS astronomy layer (the web recomputes
everything client-side; the desktop builds its tracks in a worker). The Python
oracle (app/astro.py) was retired in Phase C, so this module no longer compares
live Python: it drives the node engines (tests/js/astro_parity.mjs) and asserts
their contracts, with ABSOLUTE values frozen from the oracle where one number is
the point (the fixture-based oracle checks live in tests/test_core_fixtures.py).
"""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js" / "astro_parity.mjs"


def _run_node(engine: str, cases):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - JS parity cannot be checked here")
    try:
        proc = subprocess.run(
            [node, str(HARNESS), engine],
            input=json.dumps(cases), capture_output=True, text=True,
            encoding="utf-8", timeout=120, cwd=str(ROOT),
            # a fixed zone keeps the harness deterministic (the web builds its
            # scan window from local-noon getters)
            env={**os.environ, "TZ": "UTC"})
    except OSError as exc:          # sandboxed environments deny pipe creation
        pytest.fail(f"cannot spawn node here: {exc}")
    assert proc.returncode == 0, f"harness failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


# ------------------------------------------------- sun/moon visibility anchors
# Frozen from the retired Python oracle (app/astro.py) right before Phase C
# deleted it: (jd, lat, lon, expected apparent altitude). The node engines must
# reproduce them; the visible/invisible decision is the shared aboveHorizon rule.
_SUN_ANCHORS = [
    # 2026-06-21 local noon, Berlin: definitively up
    (2461212.9166666665, 52.52, 13.40, 58.152360),
    # 2026-09-21 after sunset, Berlin: deep night
    (2461305.4166666665, 52.52, 13.40, -35.612917),
    # 2026-09-22 before sunrise, Berlin: below but near the horizon
    (2461305.6666666665, 52.52, 13.40, -8.681207),
    # 2026-06-21 midnight sun, Svalbard
    (2461213.4166666665, 78.22, 15.65, 12.095180),
    # 2026-12-21 polar-night noon, Svalbard
    (2461395.9583333335, 78.22, 15.65, -11.656560),
]
_MOON_ANCHORS = [
    (2461305.4166666665, 52.52, 13.40, 8.599908),      # up at night, Berlin
    (2461305.5833333335, 52.52, 13.40, -20.732772),    # down at night, Berlin
    (2461304.9166666665, 52.52, 13.40, -45.316577),    # daytime Moon, below
    (2461305.0833333335, 78.22, 15.65, -19.270697),    # Svalbard daytime
    (2461396.0, 78.22, 15.65, 17.202490),              # polar-night Moon, up
]


def _anchor_cases(anchors):
    return [{"jd": jd, "ra": 0.0, "dec": 0.0, "lat": lat, "lon": lon}
            for jd, lat, lon, _alt in anchors]


def test_web_sun_visibility_is_astronomical_not_clock_based():
    """Sun must be a visible sky object ONLY while above the horizon at the
    selected time - day, night, polar day/night and around a crossing.

    The original map bug: web/app.js passed `sunRa * 15` (sunRaDec returns
    DEGREES) to altaz, so the Sun sat high in the sky all night. The frozen
    oracle altitudes pin the true altitude and the visible/invisible decision
    at each state."""
    for (jd, lat, lon, want), res in zip(
            _SUN_ANCHORS, _run_node("web", _anchor_cases(_SUN_ANCHORS))):
        assert res["sun_alt"] == pytest.approx(want, abs=1e-6), (jd, lat, lon)
        assert (res["sun_alt"] > 0) is (want > 0), (jd, res["sun_alt"])
        # away from the horizon, so this is a real day/night decision
        assert abs(want) > 2, (jd, want)


def test_web_sun_below_horizon_at_night_reproduces_the_original_bug():
    """The regression in one assertion: on an August night the Sun is ~-21 deg
    (invisible); the OLD `ra * 15` composition put it well above the horizon,
    which is exactly what the map drew (verified before the fix)."""
    jd = 2461265.4166666665           # 2026-08-12T22:00Z
    res = _run_node("web", [{"jd": jd, "ra": 0.0, "dec": 0.0,
                             "lat": 52.52, "lon": 13.40}])[0]
    assert res["sun_alt"] < -5


def test_web_moon_visibility_is_astronomical():
    """Moon: above/below the horizon, a daytime Moon, and a polar site - the
    visible/invisible decision must follow the true altitude in every case
    (frozen oracle values) and discriminate above AND below."""
    above = below = 0
    for (jd, lat, lon, want), res in zip(
            _MOON_ANCHORS, _run_node("web", _anchor_cases(_MOON_ANCHORS))):
        assert res["moon_alt"] == pytest.approx(want, abs=1e-6), (jd, lat, lon)
        if res["moon_alt"] > 0:
            above += 1
        else:
            below += 1
    assert above >= 1 and below >= 1, "the Moon rule must discriminate above/below"


def test_above_horizon_rule_identical_and_astronomical():
    """The ONE selected-time visibility predicate (web/astro.js vs
    derive.js::aboveHorizon): apparent altitude > 0, nothing else. -6 deg (the
    old desktop chart floor) is explicitly NOT visible."""
    alts = [-90, -18.5, -6, -0.01, 0, 0.01, 5, 45, 89.9]
    res = _run_node("abovehorizon", [{"alt": a} for a in alts])
    for a, r in zip(alts, res):
        assert r["desktop"] is r["web"], (a, r)
        assert r["desktop"] is (a > 0), (a, r)
    assert _run_node("abovehorizon", [{"alt": -6}])[0]["desktop"] is False


def test_desktop_and_web_magnitude_rule_identical():
    """The chart magnitude limit is ONE rule (derive.js::magVisible =
    web/format.js::magVisible): a known magnitude shows at <= limit, an object
    without a magnitude shows only at the slider maximum."""
    cases = []
    for vmag in [None, 0, 4.9, 5, 10, 10.5, 14, 20]:
        for limit in [0, 5, 10, 14, 20]:
            for mx in [14, 20]:
                cases.append({"vmag": vmag, "limit": limit, "max": mx})
    for c, r in zip(cases, _run_node("mag", cases)):
        assert r["desktop"] is r["web"], c
    # absolute anchors
    assert _run_node("mag", [
        {"vmag": 5, "limit": 10, "max": 14},
        {"vmag": 12, "limit": 10, "max": 14},
        {"vmag": None, "limit": 10, "max": 14},
        {"vmag": None, "limit": 14, "max": 14},
        {"vmag": 10, "limit": 10, "max": 14},
        {"vmag": 10.5, "limit": 10, "max": 14},
    ]) == [
        {"vmag": 5, "limit": 10, "max": 14, "desktop": True, "web": True},
        {"vmag": 12, "limit": 10, "max": 14, "desktop": False, "web": False},
        {"vmag": None, "limit": 10, "max": 14, "desktop": False, "web": False},
        {"vmag": None, "limit": 14, "max": 14, "desktop": True, "web": True},
        {"vmag": 10, "limit": 10, "max": 14, "desktop": True, "web": True},
        {"vmag": 10.5, "limit": 10, "max": 14, "desktop": False, "web": False},
    ]


@pytest.mark.parametrize("W,H", [(800, 800), (1600, 800), (800, 1600), (2560, 1440)])
def test_desktop_and_web_square_zoom_math_identical(W, H):
    """The "square zoom" geometry is identical in both apps and is a uniform
    (equal-axis) scale about a focus point: matrix.a == matrix.d, the disc
    centre maps to the canvas centre, screen<->base round-trips, and zoom/focus
    are clamped. Wide and tall viewports included - the desktop must stay
    usable at any aspect ratio."""
    cases = []
    for zoom in [1, 2.5, 8, 12, 0.5]:
        for fx, fy in [(None, None), (W / 2 + 40, H / 2 - 60)]:
            cases.append({"zoom": zoom, "W": W, "H": H, "fx": fx, "fy": fy,
                          "sx": 0, "sy": 0})
            cases.append({"zoom": zoom, "W": W, "H": H, "fx": fx, "fy": fy,
                          "sx": W * 0.3, "sy": H * 0.7})
    for c, r in zip(cases, _run_node("skyzoom", cases)):
        d, w = r["desktop"], r["web"]
        assert d == w, c
        z = d["zoom"]
        assert z == pytest.approx(min(8, max(1, c["zoom"]))), c
        # uniform scale on both axes (no distortion)
        assert d["matrix"]["a"] == d["matrix"]["d"]
        # the focus point maps to the canvas centre
        cx = (c["fx"] if c["fx"] is not None else W / 2)
        cy = (c["fy"] if c["fy"] is not None else H / 2)
        assert d["matrix"]["a"] * cx + d["matrix"]["e"] == pytest.approx(W / 2, abs=1e-9)
        assert d["matrix"]["d"] * cy + d["matrix"]["f"] == pytest.approx(H / 2, abs=1e-9)
        # screen -> base round-trips through the matrix
        bx, by = d["base"]
        assert d["matrix"]["a"] * bx + d["matrix"]["e"] == pytest.approx(c["sx"], abs=1e-9)
        assert d["matrix"]["d"] * by + d["matrix"]["f"] == pytest.approx(c["sy"], abs=1e-9)
    assert _run_node("skyzoom", [{"zoom": 1, "W": W, "H": H, "fx": None, "fy": None,
                                  "sx": W / 2, "sy": H / 2}])[0]["desktop"]["zoom"] == 1
    assert _run_node("skyzoom", [{"zoom": 99, "W": W, "H": H, "fx": None, "fy": None,
                                  "sx": 0, "sy": 0}])[0]["desktop"]["zoom"] == 8


# ------------------------------------------------------- clear-window contract
# The client derives its own best window from the 6-min night grid; the detail
# modal uses the server's 4-min grid. Both follow the same half-open contract
# the retired app/nightcalc.py::longest_run() established, and the end boundary
# must always resolve to a REAL timestamp (the "--:--" / "NaNh NaNm" regression).

_STEP = 6


def _times(n: int) -> list[str]:
    """A 6-minute UTC grid starting at 18:00, long enough for `n` samples."""
    return [f"2026-09-10T{(18 * 60 + i * _STEP) // 60:02d}:"
            f"{(18 * 60 + i * _STEP) % 60:02d}:00" for i in range(n)]


def test_js_window_end_is_never_undefined_for_a_full_grid_run():
    """The exact regression: a run ending at the last sample made the clients
    index times_iso[n] (undefined) and render "--:--"/"NaNh NaNm"."""
    res = _run_node("window", [{"flags": [True] * 12, "times": _times(12),
                                "step": _STEP}])[0]
    assert res["best"] == [0, 12]
    assert res["endISO"] == "2026-09-10T19:12:00"   # 18:00 + 12 * 6 min


# ------------------------------------------------- twilight-window selection
# The web used to score over the CIVIL window while the desktop prefers the
# astronomical one, so "% of night" and the ranking disagreed between the two
# apps for the same site and date. Both now follow the shared core cascade:
# astronomical -> nautical -> civil -> sunset/sunrise.

def test_web_twilight_choice_rule():
    """Deterministic unit test of the cascade + polar fallback."""
    cases = [
        ({"astro": True, "naut": True, "civil": True, "sun": True}, "astronomical"),
        ({"naut": True, "civil": True, "sun": True}, "nautical"),
        ({"civil": True, "sun": True}, "civil"),
        ({"sun": True}, "fallback"),
        ({}, "fallback"),
        # a missing dawn still fills the pair from the same fallback rule
        ({"civil": True, "sun": True, "astro": False, "naut": False}, "civil"),
    ]
    got = _run_node("pick", [c[0] for c in cases])
    for (flags, want), res in zip(cases, got):
        assert res["level"] == want, flags
    # polar day: no crossing at all -> 20:00 -> 04:00 next day, like planning.py
    polar = got[4]
    assert polar["level"] == "fallback", polar
    assert polar["dusk"].endswith("T20:00:00.000Z"), polar
    assert polar["dawn"].endswith("T04:00:00.000Z"), polar
    # E1: even when sunset/sunrise EXIST but no twilight level is reached
    # (polar-twilight season), the fallback is the FIXED 20:00-04:00 window -
    # never sunset..sunrise, which is what the web used to return here (a
    # different "fallback" window than app/planning.py for the same night)
    twilight = got[3]
    assert twilight["level"] == "fallback", twilight
    assert twilight["dusk"].endswith("T20:00:00.000Z"), twilight
    assert twilight["dawn"].endswith("T04:00:00.000Z"), twilight
    # E2: the level the planner picks must be the level the "Dark time" card
    # reports - a civil/nautical night is never shown as an astronomical one
    assert got[2]["level"] == "civil" and got[2]["dusk"].endswith("T20:00:00.000Z")
    assert got[1]["level"] == "nautical" and got[1]["dusk"].endswith("T21:00:00.000Z")


# The python-side twilight tests (and the live Py<->JS dark-window comparison)
# were removed in Phase C with app/planning.py. The web's own side of the
# contract is pinned above (pick engine) and by the freeze-level assertions in
# test_web_dark_window_levels_are_absolute below.


# ------------------------------------------------------ scoring-model parity
# The web used to score with a civil twilight window, tonight's live moon
# illumination, a dead filter branch and a 10-minute grid, so the same night
# ranked differently in the two apps. The curves themselves now live in ONE
# place per app and this test compares them number by number.

def test_desktop_and_web_scoring_model_are_identical():
    res = _run_node("scoring", [])

    for otype, (desk, web) in res["MOON_TYPE_FACTOR"].items():
        assert desk == web, f"MOON_TYPE_FACTOR[{otype}]"
    for name, desk, web in res["sensitivity"]:
        assert desk == web, f"moonSensitivity({name})"
    for f, age, desk, web in res["minDist"]:
        assert desk == pytest.approx(web, abs=1e-12), (f, age)
    for f, age, illum, sep, desk, web in res["glow"]:
        assert desk == pytest.approx(web, abs=1e-12), (f, age, illum, sep)
    for g, t, b, desk, web in res["penalty"]:
        assert desk == pytest.approx(web, abs=1e-12), (g, t, b)
    for bortle, desk, web in res["bortle"]:
        assert desk == pytest.approx(web, abs=1e-12), bortle

    # sanity: the curves behave as documented (largest threshold at full moon,
    # continuous Bortle junction)
    dist = {(f, a): web for f, a, _desk, web in res["minDist"]}
    for f in ("narrowband", "oiii", "lrgb"):
        assert dist[(f, 14.76)] > dist[(f, 0)], f
    b = {x[0]: x[1] for x in res["bortle"]}
    assert abs(b[4] - b[3]) < 0.13 and b[9] == pytest.approx(1.12, abs=1e-9)

    # ... and the values themselves are anchored against the DOCUMENTED model
    _assert_scoring_anchors(res)


# ------------------------------------------- ABSOLUTE scoring-model anchors
# The parity loops above only prove the two copies agree. They cannot see a
# COORDINATED edit (the same constant changed in static/js/derive.js AND
# web/astro.js), and nothing in them says which numbers are the right ones.
# The values below are derived BY HAND from the model as documented in both
# files - not read out of either implementation - so one changed constant
# breaks them even when both copies are changed together.
#
#   moonMinDist(f, age) = BASE / (1 + ((0.5 - age/29.53)/0.333)^2), BASE 60/90/120
#     age 0 (new):   (0.5/0.333)^2 = 1.501502^2 = 2.254507 -> BASE / 3.254507,
#                    i.e. the "smallest at new = BASE/3.25" the source comment
#                    states:  60 -> 18.4360   90 -> 27.6540   120 -> 36.8719
#     age 14.76 (full): frac 14.76/29.53 = 0.49983068, so (0.00016932/0.333)^2
#                    = 2.585e-7 -> BASE / 1.00000026, i.e. 60 / 90 / 120
#   effectiveMoonGlow(illum, sep, f, age) = illum * max(0, 1 - max(0, sep-minDist)/180)
#     lrgb at full moon (minDist 120), sep 179.9, illum 1:
#                    1 - (179.9-120)/180 = 1 - 0.3327778 = 0.6672222
#     narrowband at new moon (minDist 18.4360), sep 45, illum 1:
#                    1 - (45-18.4360)/180 = 1 - 0.1475778 = 0.8524222
#     oiii at new moon, sep 0 (inside the threshold) -> distDeg 0 -> glow = illum
#   moonPenalty(glow, typeF, bortleF) = max(0.05, 1 - 0.95*glow*typeF*bortleF)
#     (1, 1.6, 1.12): 0.95*1.792 = 1.7024 -> 1-1.7024 < 0 -> the 0.05 FLOOR
#     (0.3, 0.5, 0.3): 0.95*0.045 = 0.04275 -> 0.95725
#     (1, 1, 0.64):   0.95*0.64 = 0.608    -> 0.392
#   bortleMoonFactor: 1..3 = 0.25 + 0.05*bortle, then 4..9 = 0.40 + 0.12*(b-3)
#     -> 0.30 0.35 0.40 | 0.52 0.64 0.76 0.88 1.00 1.12   (junctions: 1->2 and
#        the CONTINUOUS 3/4 step 0.40 -> 0.52)
_ANCHOR_MIN_DIST = {
    ("narrowband", 0): 18.4360, ("oiii", 0): 27.6540, ("lrgb", 0): 36.8719,
    ("narrowband", 14.76): 60.0, ("oiii", 14.76): 90.0, ("lrgb", 14.76): 120.0,
}
_ANCHOR_GLOW = {
    ("lrgb", 14.76, 1.0, 179.9): 0.6672222,
    ("narrowband", 0, 1.0, 45.0): 0.8524222,
    ("oiii", 0, 0.62, 0.0): 0.62,
}
_ANCHOR_PENALTY = {
    (1.0, 1.6, 1.12): 0.05,          # the documented floor
    (0.3, 0.5, 0.3): 0.95725,
    (1.0, 1.0, 0.64): 0.392,
}
_ANCHOR_BORTLE = {1: 0.30, 2: 0.35, 3: 0.40, 4: 0.52, 5: 0.64,
                  6: 0.76, 7: 0.88, 8: 1.00, 9: 1.12}
_ANCHOR_TYPE_FACTOR = {
    "nebula": 1.6, "supernova_remnant": 1.6, "planetary": 1.3,
    "star_cloud": 1.1, "galaxy": 0.8, "open_cluster": 0.55,
    "globular": 0.5, "asterism": 0.55,
}


def _assert_scoring_anchors(res: dict) -> None:
    """Assert the harness output against the hand-derived values above, for
    BOTH engines (a coordinated change breaks either side)."""
    min_dist = {(r[0], r[1]): (r[2], r[3]) for r in res["minDist"]}
    glow = {(r[0], r[1], r[2], r[3]): (r[4], r[5]) for r in res["glow"]}
    penalty = {(r[0], r[1], r[2]): (r[3], r[4]) for r in res["penalty"]}
    bortle = {r[0]: (r[1], r[2]) for r in res["bortle"]}

    for (f, age), want in _ANCHOR_MIN_DIST.items():
        for got, side in zip(min_dist[(f, age)], ("desktop", "web")):
            assert got == pytest.approx(want, abs=1e-3), \
                f"{side} moonMinDist({f}, {age}) = {got}, model says {want}"
    for (f, age, illum, sep), want in _ANCHOR_GLOW.items():
        for got, side in zip(glow[(f, age, illum, sep)], ("desktop", "web")):
            assert got == pytest.approx(want, abs=1e-6), \
                f"{side} effectiveMoonGlow({illum}, {sep}, {f}, {age}) = {got}"
    for (g, t, b), want in _ANCHOR_PENALTY.items():
        for got, side in zip(penalty[(g, t, b)], ("desktop", "web")):
            assert got == pytest.approx(want, abs=1e-9), \
                f"{side} moonPenalty({g}, {t}, {b}) = {got}"
    for bortle_n, want in _ANCHOR_BORTLE.items():
        for got, side in zip(bortle[bortle_n], ("desktop", "web")):
            assert got == pytest.approx(want, abs=1e-12), \
                f"{side} bortleMoonFactor({bortle_n}) = {got}"
    # the floor is reached by a real (glow, type, bortle) combination, not only
    # asserted at the arithmetic boundary
    assert min(min(v) for v in penalty.values()) == pytest.approx(0.05, abs=1e-12)
    for otype, want in _ANCHOR_TYPE_FACTOR.items():
        for got, side in zip(res["MOON_TYPE_FACTOR"][otype], ("desktop", "web")):
            assert got == pytest.approx(want, abs=1e-12), \
                f"{side} MOON_TYPE_FACTOR[{otype}] = {got}"
    # an unknown type falls back to 1.0 in both engines
    unknown = {r[0]: r[1:] for r in res["sensitivity"]}["unknown"]
    assert list(unknown) == [1.0, 1.0]


# ------------------------------------------------------ cross-timezone planning
# The open question from the audit: the web anchored its 48 h scan at
# BROWSER-local midnight while the site lived in another zone. The scan is now
# anchored at the SITE's local noon (+30 h, the shared core policy), and the
# cases below assert that ABSOLUTELY: the site's own night must resolve in both
# directions (astronomical level, a < 20 h span), and the superseded
# browser-anchored scan stays in the harness as the documented counter-example
# for a site far EAST of the browser.

def _crosszone(case, browser_zone):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - JS parity cannot be checked here")
    try:
        proc = subprocess.run(
            [node, str(HARNESS), "crosszone"],
            input=json.dumps([case]), capture_output=True, text=True,
            encoding="utf-8", timeout=120, cwd=str(ROOT),
            env={**os.environ, "TZ": browser_zone})
    except OSError as exc:
        pytest.fail(f"cannot spawn node here: {exc}")
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout)[0]


def test_web_cross_timezone_site_resolves_its_own_night():
    """Site far EAST of the browser (Honolulu browser, Berlin site)."""
    case = {"date": "2026-12-05", "lat": 52.52, "lon": 13.40,
            "tz": "Europe/Berlin"}
    res = _crosszone(case, "Pacific/Honolulu")

    assert res["site"]["level"] == "astronomical", res
    assert res["site"]["spanH"] < 20, res["site"]

    # ... and the old browser-anchored scan is demonstrably broken here: with
    # the pairing rule it no longer finds a pair at all inside its off-by-12h
    # window, so it degrades to the fixed 'fallback' span - still not the
    # site's night (the pre-fix scan instead paired the neighbouring night's
    # dawn before dusk).
    assert (res["browserAnchored"]["level"] == "fallback"
            or res["browserAnchored"]["dawn"] < res["browserAnchored"]["dusk"]), \
        res["browserAnchored"]


@pytest.mark.parametrize("browser_zone,case", [
    # site EAST of the browser
    ("Pacific/Honolulu", {"date": "2026-12-05", "lat": 52.52, "lon": 13.40,
                          "tz": "Europe/Berlin"}),
    # site WEST of the browser
    ("Europe/Berlin", {"date": "2026-12-05", "lat": 40.71, "lon": -74.01,
                       "tz": "America/New_York"}),
])
def test_web_cross_timezone_anchoring_is_site_based(browser_zone, case):
    """Cross-zone planning must resolve the SITE's night in both directions.

    Measured outcome (resolving the reviewers' disagreement about the sign):
    the old browser-anchored scan produced a >24 h "night" for a site EAST of
    the browser AND for a site WEST of it - the direction only changes *how
    far* the window edge misses, because the 48 h window started at
    browser-midnight, which in site-local time lands somewhere inside the
    neighbouring night. The scan is now anchored at the SITE's local noon
    (+ 30 h), so the counter-example only survives in the east direction
    (browser noon ~12 h off the site's -> the neighbouring night's
    dawn-before-dusk pair); a site WEST of the browser (noon offset < ~2 h)
    accidentally resolves correctly, which is why the site-anchored anchor is
    the only stable rule.
    """
    res = _crosszone(case, browser_zone)
    assert res["site"]["level"] == "astronomical", (case, res["site"])
    assert res["site"]["spanH"] < 20, (case, res["site"])
    # the superseded browser-anchored behaviour, kept in the harness as the
    # documented counter-example: still wrong for a site EAST of the browser
    # (after the pairing fix it degrades to the fixed fallback span)
    if "Honolulu" in browser_zone:
        assert (res["browserAnchored"]["level"] == "fallback"
                or res["browserAnchored"]["dawn"] < res["browserAnchored"]["dusk"]), \
            (case, res["browserAnchored"])


# --------------------------------------------------- web sinks stay escaped
# TEXT-LEVEL on purpose: both sinks below build HTML from template literals in
# a browser-only view, and there is no browser test harness for web/ (headless
# Chrome cannot start under this environment - see AGENTS.md). The project
# already pins this class of invariant textually in tests/test_ui_invariants.py;
# the point is that an object type or a panorama id can arrive from an imported
# setup ZIP and is therefore untrusted, so the esc() wrapper must not be
# dropped or bypassed by a NEW interpolation.

def _template_interpolations(js: str, needle: str) -> list[str]:
    """Bodies of `${...}` interpolations whose body matches `needle` (the
    templates here carry no nested braces inside those bodies)."""
    return [m.group(1).strip() for m in
            re.finditer(r"\$\{([^{}]*" + needle + r"[^{}]*)\}", js)]


def test_web_type_and_panorama_id_sinks_stay_escaped():
    _assert_web_sinks_escaped(
        (ROOT / "web" / "app.js").read_text(encoding="utf-8"),
        (ROOT / "web" / "horizon.js").read_text(encoding="utf-8"))


def _assert_web_sinks_escaped(app_js: str, hz: str) -> None:
    otype = _template_interpolations(app_js, r"TYPE_LABELS\[[^{}]*\.otype\]")
    escaped = [e for e in otype if e.startswith("esc(")]
    # targets-table type cell, detail-dialog type row, custom-target tag
    assert len(escaped) >= 3, otype
    # the one interpolation without an inline esc() is the sky-map tooltip,
    # which escapes its whole composed string at the sink
    assert [e for e in otype if not e.startswith("esc(")] == \
        ["TYPE_LABELS[best.o.otype] || best.o.otype"], otype
    assert "tip.innerHTML = `<b>${esc(extra)}</b>" in app_js

    pano_ids = _template_interpolations(hz, r"p\.id")
    # `${p.id === active ? ...}` only decides `checked`; it emits no id
    emitted = [e for e in pano_ids if not e.startswith("p.id ===")]
    assert emitted, "the panorama-id render path vanished - update this pin"
    assert all(e == "esc(p.id)" for e in emitted), emitted
    # ... and both id-bearing attributes are still the escaped form
    assert 'data-id="${esc(p.id)}"' in hz
    assert 'data-del="${esc(p.id)}"' in hz

_SIZE_CASES = [
    [size, unit]
    for unit in ("arcmin", "arcsec", "deg")
    for size in (None, "", "330", "110", "50", "1.4", "9.3", "0.05",
                 "645x550", "120x80.0", "6x4", "1000", "0.5")
]

# Absolute expected strings (TS1, review: the desktop/web "parity" pairs both
# re-export the same core function, so a COORDINATED change - a re-baked
# core/format.js with a wrong unit or rounding - passed the old equality check).
# Provenance: format documented in core/format.js - arcmin/arcsec/deg are ONE
# user unit, unknown -> em dash, pairs joined with U+00D7, arcsec = x60,
# deg = x/60 rounded to 2 decimals (1 dp below 1').
_SIZE_EXPECTED = {
    (None, "arcmin"): "\u2014", ("", "arcmin"): "\u2014",
    ("330", "arcmin"): "330\u2032", ("110", "arcmin"): "110\u2032",
    ("50", "arcmin"): "50\u2032", ("1.4", "arcmin"): "1.4\u2032",
    ("9.3", "arcmin"): "9.3\u2032", ("0.05", "arcmin"): "0.1\u2032",
    ("645x550", "arcmin"): "645\u2032\u00D7550\u2032",
    ("120x80.0", "arcmin"): "120\u2032\u00D780\u2032",
    ("6x4", "arcmin"): "6\u2032\u00D74\u2032",
    ("1000", "arcmin"): "1000\u2032", ("0.5", "arcmin"): "0.5\u2032",
    (None, "arcsec"): "\u2014", ("", "arcsec"): "\u2014",
    ("330", "arcsec"): "19800\u2033", ("110", "arcsec"): "6600\u2033",
    ("50", "arcsec"): "3000\u2033", ("1.4", "arcsec"): "84\u2033",
    ("9.3", "arcsec"): "558\u2033", ("0.05", "arcsec"): "3\u2033",
    ("645x550", "arcsec"): "38700\u2033\u00D733000\u2033",
    ("120x80.0", "arcsec"): "7200\u2033\u00D74800\u2033",
    ("6x4", "arcsec"): "360\u2033\u00D7240\u2033",
    ("1000", "arcsec"): "60000\u2033", ("0.5", "arcsec"): "30\u2033",
    (None, "deg"): "\u2014", ("", "deg"): "\u2014",
    ("330", "deg"): "5.5\u00B0", ("110", "deg"): "1.83\u00B0",
    ("50", "deg"): "0.83\u00B0", ("1.4", "deg"): "0.02\u00B0",
    ("9.3", "deg"): "0.15\u00B0", ("0.05", "deg"): "0\u00B0",
    ("645x550", "deg"): "10.75\u00B0\u00D79.17\u00B0",
    ("120x80.0", "deg"): "2\u00B0\u00D71.33\u00B0",
    ("6x4", "deg"): "0.1\u00B0\u00D70.07\u00B0",
    ("1000", "deg"): "16.67\u00B0", ("0.5", "deg"): "0.01\u00B0",
}


def test_web_size_column_absolute_values():
    """Catalog sizes are arcminutes shown in ONE unit - single-axis too.

    The web used to reformat only "NxM" strings and echo everything else
    verbatim, so the 961 single-axis entries (Hyades 330, Pleiades 110, Ring
    Nebula 1.4, IC planetaries ...) lost their unit AND ignored the
    arcmin/arcsec/deg setting, while the desktop showed "330'"; the same
    strict regex also left the Frame % column empty for those objects.
    web/format.js is the web twin of static/js/util.js::fmtSize.

    TS1 (review): the old check compared the two shells, which now re-export
    the same core function - a coordinated formatting change (or the frozen
    values being wrong) passed. These absolute strings are the reference.
    """
    got = _run_node("size", _SIZE_CASES)
    assert len(got) == len(_SIZE_CASES)
    for (size, unit), res in zip(_SIZE_CASES, got):
        assert res["web"] == _SIZE_EXPECTED[(size, unit)], (size, unit, res)


# ================================================== E1/E2 remediation pins
# E1: the polar fallback must be the FIXED 20:00-04:00 window (in the site's
# zone), never sunset..sunrise - the web used to return the latter whenever the
# sun still crossed the horizon in the polar-twilight season, producing a
# different "fallback" window than app/planning.py for the same night.
# E2: the level the planner picks (astronomical/nautical/civil/fallback) is the
# window the web "Dark time" card must display - at nautical/civil latitudes
# the astronomical pair is empty, and the card used to say "no full dark night"
# while scoring ran on the nautical/civil window.

_POLAR_CASES = [
    # (date, tz_min_east, lat, lon, browser_zone, site_tz or None, level)
    ("2026-05-15", 120, 69.65, 18.96, "Europe/Oslo", None, "fallback"),
    ("2026-07-30", 120, 69.65, 18.96, "Europe/Oslo", None, "fallback"),
    ("2026-05-25", -480, 64.80, -147.70, "Europe/Oslo", "America/Anchorage",
     "fallback"),   # polar twilight, site EAST of the browser zone
    ("2026-06-21", 120, 78.22, 15.63, "Europe/Oslo", None, "fallback"),
    ("2026-06-21", 120, 59.50, 5.30, "Europe/Oslo", None, "civil"),
    ("2026-06-21", 120, 52.52, 13.40, "Europe/Oslo", None, "nautical"),
    ("2026-09-10", 120, 52.52, 13.40, "Europe/Oslo", None, "astronomical"),
    ("2026-12-21", 660, -33.87, 151.21, "Australia/Sydney", None,
     "astronomical"),
]


def _darkwindow(case, browser_zone):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - JS parity cannot be checked here")
    try:
        proc = subprocess.run(
            [node, str(HARNESS), "darkwindow"],
            input=json.dumps([case]), capture_output=True, text=True,
            encoding="utf-8", timeout=120, cwd=str(ROOT),
            env={**os.environ, "TZ": browser_zone})
    except OSError as exc:
        pytest.fail(f"cannot spawn node here: {exc}")
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout)["windows"][0]


@pytest.mark.parametrize("date,tz,lat,lon,bzone,site_tz,level", _POLAR_CASES)
def test_web_dark_window_levels_are_absolute(
        date, tz, lat, lon, bzone, site_tz, level):
    """E1+E2: for polar day, polar twilight, civil/nautical/astronomical nights
    the web resolves the LEVEL the hardcoded table names (the table is the
    frozen oracle output, kept after app/planning.py was deleted). The fallback
    is the fixed 20:00-04:00 span, i.e. an 8 h window; crossing cases must be
    ordered with a sane span. The window the planner scores on is the window the
    "Dark time" card must display, so the level is asserted too."""
    case = {"date": date, "lat": lat, "lon": lon}
    if site_tz:
        case["tz"] = site_tz
    res = _darkwindow(case, bzone)

    assert res["level"] == level, (date, res)
    dusk = datetime.fromisoformat(res["dusk"].replace("Z", "+00:00"))
    dawn = datetime.fromisoformat(res["dawn"].replace("Z", "+00:00"))
    span = (dawn - dusk).total_seconds()
    assert 0 < span <= 24 * 3600, (date, res)
    if level == "fallback":
        assert abs(span - 8 * 3600) < 120, (date, res)   # 20:00 -> 04:00


# ================================================== E3 frame coverage
# "Frame coverage" means: how much of the frame the OBJECT spans (object/field,
# like the desktop tooltip "object spans X% of the frame"). The web used to
# invert the ratio (field/object, clamped 0-100), so M42 in a Seestar rig read
# 85% there and 139% on the desktop - same boolean, opposite number meaning.

_FRAME_WEB_CASES = [
    {"side": "web", "sizeAm": 90, "fovArcmin": 76.8},    # larger than frame
    {"side": "web", "sizeAm": 30, "fovArcmin": 76.8},    # smaller than frame
    {"side": "web", "sizeAm": 76.8, "fovArcmin": 76.8},  # exactly equal
    {"side": "web", "sizeAm": 0, "fovArcmin": 76.8},     # invalid size
    {"side": "web", "sizeAm": 90, "fovArcmin": 0},       # invalid fov
]
_FRAME_DESK_CASES = [
    {"side": "desktop", "sizeAm": "90x60", "fovW": 76.6, "fovH": 43.0},
    {"side": "desktop", "sizeAm": "30", "fovW": 76.6, "fovH": 43.0},
    {"side": "desktop", "sizeAm": "645x550", "fovW": 76.6, "fovH": 43.0},
    {"side": "desktop", "sizeAm": "76.6x43", "fovW": 76.6, "fovH": 43.0},
    {"side": "desktop", "sizeAm": "0", "fovW": 76.6, "fovH": 43.0},
]


def test_web_frame_coverage_is_object_over_field():
    """Absolute values, not just desktop/web equality: object span as % of the
    frame width, values above 100 = does not fit, null for invalid inputs."""
    res = _run_node("frame", _FRAME_WEB_CASES)
    assert [r["pct"] for r in res] == [117, 39, 100, None, None]


def test_desktop_frame_coverage_absolute():
    """Same definition, worse of BOTH axes: max(ow/fw, oh/fh) in arcminutes."""
    res = _run_node("frame", _FRAME_DESK_CASES)
    got = [(r["pct"], r["fits"]) for r in res]
    assert got == [(140, False), (70, True), (1279, False), (100, True),
                   (0, True)], got


# ================================================== E4 statusOf vs vis[]
def test_desktop_status_of_matches_visibility_predicate():
    """statusOf must say "Blocked" wherever the vis[] predicate blocks - the
    sky-mode ceil_top ceiling included. The old statusOf ignored ceil_top, so a
    balcony/overhang object showed "Observable" in the targets table while the
    very same sample was counted blocked."""
    cases = [
        {"alt": 40, "ceil": -90, "ceilTop": None, "minAlt": 25},   # clear
        {"alt": 50, "ceil": -90, "ceilTop": 30, "minAlt": 25},     # above ceiling
        {"alt": 30, "ceil": -90, "ceilTop": 30, "minAlt": 25},     # exactly at
        {"alt": 30.1, "ceil": -90, "ceilTop": 30, "minAlt": 25},   # just above
        {"alt": 29.9, "ceil": -90, "ceilTop": 30, "minAlt": 25},   # just below
        {"alt": 10, "ceil": 15, "ceilTop": None, "minAlt": 25},    # horizon obs
        {"alt": -5, "ceil": -90, "ceilTop": None, "minAlt": 25},   # below horizon
        {"alt": 20, "ceil": -90, "ceilTop": None, "minAlt": 25},   # too low
        {"alt": 25, "ceil": -90, "ceilTop": None, "minAlt": 25},   # at min alt
    ]
    codes = [r["code"] for r in _run_node("status", cases)]
    assert codes == ["vis", "blk", "blk", "blk", "vis",
                     "blk", "hor", "low", "vis"], codes


# ================================================== E5 min-altitude rule
def test_min_alt_rule_identical_desktop_web():
    """The authoritative threshold: explicit min_useful_alt wins, else
    17 + 2*bortle. Desktop derive.js::effectiveMinAlt and web
    horizon.js::effectiveMinAlt must agree on every state (the desktop used to
    freeze the boot value, the web re-derived live)."""
    cases = [
        {"bortle": 1, "explicit": False}, {"bortle": 4, "explicit": False},
        {"bortle": 7, "explicit": False}, {"bortle": 9, "explicit": False},
        {"bortle": 1, "explicit": True, "value": 20},
        {"bortle": 7, "explicit": True, "value": 30},
        {"bortle": 7, "explicit": True, "value": 5},   # boundary clamp case
    ]
    res = _run_node("minalt", cases)
    for c, r in zip(cases, res):
        assert r["desk"] == r["web"], (c, r)
    assert [r["desk"] for r in res] == [19, 25, 31, 35, 20, 30, 5]


# ================================================== E7 filter advice model
def test_filter_advice_model_identical():
    """Emission objects need narrowband (Ha OR OIII); a broadband-only object
    only warns when the user owns narrowband but no broadband. The web used a
    different type map and only ever showed an ownership badge - the model must
    agree with the desktop's filterAdvice."""
    cases = [
        {"otype": "nebula", "bb": True, "ha": False, "oiii": True},
        {"otype": "nebula", "bb": False, "ha": False, "oiii": False},
        {"otype": "planetary", "bb": False, "ha": True, "oiii": False},
        {"otype": "supernova_remnant", "bb": False, "ha": False, "oiii": False},
        {"otype": "galaxy", "bb": False, "ha": True, "oiii": False},
        {"otype": "galaxy", "bb": True, "ha": True, "oiii": False},
        {"otype": "galaxy", "bb": False, "ha": False, "oiii": False},
        {"otype": "open_cluster", "bb": True, "ha": False, "oiii": False},
        {"otype": "star_cloud", "bb": False, "ha": False, "oiii": False},
        {"otype": "asterism", "bb": False, "ha": False, "oiii": False},
        {"otype": "unknown", "bb": False, "ha": False, "oiii": False},
    ]
    res = _run_node("advice", cases)
    for c, r in zip(cases, res):
        assert r["desk"] == r["web"], (c, r)
    # absolute sanity on the shared model
    assert res[0]["web"] == {"ok": True, "text": "narrowband \u2713"}
    assert res[1]["web"] == {"ok": False, "text": "narrowband"}
    assert res[4]["web"] == {"ok": False, "text": "broadband \u2717"}
    assert res[5]["web"] is None and res[6]["web"] is None


# ================================================== E8 keyboard scrub steps
def test_scrub_step_constants_identical():
    """Plain arrow = 6 min (one night-grid sample), Shift = 60 min - the web
    had 60/10, the desktop 6/60, and the web comment claimed desktop parity."""
    res = _run_node("shortcut", [{}])
    assert res["desk"] == [6, 60], res
    assert res["web"] == [6, 60], res


# ================================================== 11.2 shared web scoring loop
_NIGHTSTATS_CASES = [
    # circumpolar object: clear every 6-min sample of the 2 h grid
    {"obj": {"ra_hours": 0, "dec_deg": 89}, "jd0": 2461294.315972222,
     "jd1": 2461294.3993055554, "step": 6 / 1440, "minAlt": 25,
     "lat": 52.5, "lon": 13.4},
    # never rises at this latitude
    {"obj": {"ra_hours": 0, "dec_deg": -89}, "jd0": 2461294.315972222,
     "jd1": 2461294.3993055554, "step": 6 / 1440, "minAlt": 25,
     "lat": 52.5, "lon": 13.4},
    # rises but never reaches the min altitude: above-horizon samples are
    # "blocked" but not "visible"
    {"obj": {"ra_hours": 0, "dec_deg": -30}, "jd0": 2461294.315972222,
     "jd1": 2461294.3993055554, "step": 6 / 1440, "minAlt": 25,
     "lat": 52.5, "lon": 13.4},
    # moon-aware: full moon, broadband base -> every visible sample penalized
    # by bortleMoonFactor(5) = 0.64 (moon at alt 30 az 180 keeps the separation
    # inside the 120' threshold for this geometry, so the penalty is exactly
    # 1 - 0.95*0.64 = 0.392 on every sample)
    {"obj": {"ra_hours": 0, "dec_deg": 89}, "jd0": 2461294.315972222,
     "jd1": 2461294.3993055554, "step": 6 / 1440, "minAlt": 25,
     "lat": 52.5, "lon": 13.4,
     "ctx": {"moonAware": True, "illum": 1, "moonAgeDays": 14.76,
             "filterType": "lrgb", "typeF": 1, "bortleF": 0.64,
             "moonAlt": [30] * 21, "moonAz": [180] * 21}},
    # a 60 deg obstruction profile blocks the whole circumpolar track
    {"obj": {"ra_hours": 0, "dec_deg": 89}, "jd0": 2461294.315972222,
     "jd1": 2461294.3993055554, "step": 6 / 1440, "minAlt": 25,
     "lat": 52.5, "lon": 13.4,
     "pano": {"paintMode": "obstacle", "fov": 360, "azOffset": 0,
              "profile": [60] * 720}},
]


def test_web_night_stats_absolute_values():
    """The ONE shared scoring loop (web/nightrank.js) behind Tonight, the
    catalog % column and the detail modal. Absolute values pin the loop; the
    E6 gate depends on visN - a target never clear during the night has
    visN == 0 and must not rank in "Best targets tonight"."""
    res = _run_node("nightstats", _NIGHTSTATS_CASES)
    c = res[0]
    assert (c["visN"], c["pct"], c["bestRun"], c["blocked"]) == (21, 100, 21, 0)
    assert c["bestStart"] == _NIGHTSTATS_CASES[0]["jd0"]
    never = res[1]
    assert (never["visN"], never["pct"], never["bestRun"]) == (0, 0, 0)
    low = res[2]
    assert (low["visN"], low["blocked"], low["blockedPct"]) == (0, 7, 33)
    moon = res[3]
    assert moon["visN"] == 21 and moon["pct"] == 100
    assert moon["sMoon"] == pytest.approx(0.392 * moon["sClassic"], abs=0.01)
    assert moon["sClassic"] > 0
    blocked = res[4]
    assert (blocked["visN"], blocked["blocked"], blocked["blockedPct"]) == \
        (0, 21, 100)


# ================================================== 11.1 moon illum/age/JD
def test_web_julian_day_round_trips():
    """julianDay(dateFromJD(jd)) must return the same instant (the frozen jd is
    a textbook oracle value). moonIllumination/moonAge absolute values are
    pinned by the frozen fixtures in tests/test_core_fixtures.py."""
    jd = 2461305.4166666665           # 2026-09-21T22:00Z
    res = _run_node("web", [{"jd": jd, "ra": 0.0, "dec": 0.0,
                             "lat": 52.52, "lon": 13.40}])[0]
    assert res["jd_rt"] == pytest.approx(jd, abs=1e-7)


def test_web_detail_visibility_predicate_cannot_drift():
    """The web detail modal keeps its own sample loop (its 120-sample grid over
    sunset-1h..sunrise+1h is deliberately different from the 6-min dusk..dawn
    grid), but its per-sample visibility rule must stay the ONE shared
    predicate - now core/visibility.js::sampleVisible(), i.e.
    alt >= minAlt && alt > ceil && alt < (ceilTop ?? 90.5) - which both shells
    call (nightrank re-exports it; the detail loop calls it directly). This is
    a text-level pin on purpose: a silently edited inline copy (e.g. dropping
    the ceilTop ceiling) must fail here."""
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    shared = (ROOT / "core" / "visibility.js").read_text(encoding="utf-8")
    inline = "alt >= minAlt && alt > ceil.ceil && alt < (ceil.ceilTop ?? 90.5)"
    assert inline not in app_js, \
        "the detail loop rebuilt its own predicate instead of calling " \
        "core/visibility.js::sampleVisible()"
    assert "sampleVisible(" in app_js
    assert "alt >= minAlt && alt > ceil && alt < (ceilTop ?? 90.5)" in shared, \
        "core/visibility.js::sampleVisible() changed - re-check the detail loop"


# ================================================== scrub-range policy
# (ITEM 1, 0.6.13: the scrub spans the planner's sunset..sunrise by default,
#  the full payload span only with viewFullDay. One shared policy in
#  core/planning.js::scrubWindowMs, called by web/app.js and desktop
#  derive.js::scrubWindowRange.)
#
# The expected windows and grid indices below were FROZEN from the retired
# Python oracle (app/planning.py) before Phase C deleted it. They are goldens,
# NOT derived from the JS engines under test.
# name, night_start, night_end, payload_t0 (site-zoned local noon),
# sunset, sunrise (None for the polar fallback), dusk, dawn,
# exp_lo, exp_hi (sunset..sunrise indices), scoring_lo, scoring_hi (dusk..dawn)
_SCRUB_SCENARIOS = [
    ("astronomical", 1768492103973, 1768546924236, 1768474800000,
     1768492103973, 1768546924236, 1768498864235, 1768540180562,
     49, 200, 67, 181),
    ("nautical", 1782073130919, 1782099900444, 1782039600000,
     1782073130919, 1782099900444, 1782080607186, 1782092430582,
     94, 167, 114, 146),
    ("civil", 1782075343105, 1782097693155, 1782039600000,
     1782075343105, 1782097693155, 1782079616979, 1782093415860,
     100, 161, 112, 149),
    ("polar fallback", 1782064800000, 1782093600000, 1782036000000,
     None, None, 1782064800000, 1782093600000,
     80, 160, 80, 160),
    ("midnight-crossing", 1797862253691, 1797927942275, 1797850800000,
     1797862253691, 1797927942275, 1797872427406, 1797917768519,
     32, 214, 61, 186),
    ("southern summer", 1768468060793, 1768503706128, 1768438800000,
     1768468060793, 1768503706128, 1768474140083, 1768497626744,
     82, 180, 99, 163),
    ("southern winter", 1782024695293, 1782075692034, 1782007200000,
     1782024695293, 1782075692034, 1782030197300, 1782070242748,
     49, 190, 64, 175),
    ("dst night", 1774805538411, 1774846104454, 1774778400000,
     1774805538411, 1774846104454, 1774812965201, 1774838710824,
     76, 188, 97, 167),
]


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).replace(
        tzinfo=None).isoformat()


def test_scrub_window_follows_the_planner_and_matches_between_apps():
    """CORRECTION 2 (0.6.13): the default scrub range is the WHOLE night,
    sunset..sunrise from the planner's own sun-crossings - NOT the twilight-level
    dark window (which stays the separate, unchanged SCORING window). The polar
    fallback is the site-zoned 20:00-04:00 span. viewFullDay ON => the full span.
    Checked for astronomical / nautical / civil / polar fallback /
    midnight-crossing / DST / both hemispheres, at BOTH toggle states, and
    desktop(derive.js) == web(astro.js) for every case. Probes outside the range
    must clamp to the nearest edge (turning the night range back on while sitting
    at 1 pm)."""
    cases = []
    expected = []
    for (name, n0, n1, _t0, _sset, _srise, _dusk, _dawn,
         _elo, _ehi, _slo, _shi) in _SCRUB_SCENARIOS:
        # a deliberately WIDER "full day" span, never equal to the night range
        full_start = n0 - 6 * 3600_000
        full_end = n1 + 6 * 3600_000
        # probes: the exact edges, a mid-night instant, the full-span edges
        # (outside the night range), and 1 h beyond each side
        probes = [n0, n1, (n0 + n1) // 2,
                  full_start, full_end, full_start - 3600_000,
                  full_end + 3600_000]
        for vfd in (False, True):
            cases.append({
                "name": name, "viewFullDay": vfd,
                "nightStart": n0, "nightEnd": n1,
                "fullStart": full_start, "fullEnd": full_end,
                "probes": probes,
            })
            expected.append((n0, n1, full_start, full_end))

    res = _run_node("scrubwindow", cases)
    assert len(res) == len(cases)
    for c, r, exp in zip(cases, res, expected):
        n0, n1, f0, f1 = exp
        assert r["desk"] == r["web"], ("app parity", c["name"], c["viewFullDay"], r)
        it = r["web"]
        if c["viewFullDay"]:
            assert (it["start"], it["end"]) == (f0, f1), (c["name"], it)
        else:
            # exactly the planner's sunset..sunrise - never the dark window or a
            # second window definition
            assert (it["start"], it["end"]) == (n0, n1), (c["name"], it)
        for p, cl in zip(c["probes"], it["clamps"]):
            assert cl == min(max(p, it["start"]), it["end"]), (c["name"], p, cl)
        # explicit: an out-of-range instant clamps to the NEXT edge, so turning
        # the night range back on while at 1 pm lands on sunrise (or sunset)
        if not c["viewFullDay"]:
            assert it["clamps"][3] == n0 and it["clamps"][4] == n1, (c["name"], it)


# ================================================== desktop 24 h grid (ITEM A)
def test_desktop_scrub_grid_maps_the_real_24h_payload():
    """ITEM A: on the REAL desktop 24 h grid (site-zoned local noon -> +24 h at
    the 6-min step), the desktop's scrubWindowRange() selects sunset..sunrise,
    and the WHOLE grid with view_full_day; nightIndexRange() (SCORING) still
    selects the twilight-level dusk..dawn in both states. Every scenario at both
    toggle states, and the grid endpoints are the exact samples bracketing the
    frozen crossings."""
    cases, expected = [], []
    for (name, _n0, _n1, t0, s_set, s_rise, dusk, dawn,
         exp_lo, exp_hi, slo, shi) in _SCRUB_SCENARIOS:
        times = [t0 + 6 * 60_000 * i for i in range(241)]
        assert times[-1] - times[0] == 24 * 3600_000
        base = {"times": [_iso_ms(t) for t in times], "step": 6,
                "dusk": _iso_ms(dusk), "dawn": _iso_ms(dawn),
                "sunset": _iso_ms(s_set) if s_set else None,
                "sunrise": _iso_ms(s_rise) if s_rise else None}
        for vfd in (False, True):
            cases.append(dict(base, viewFullDay=vfd))
            expected.append((exp_lo, exp_hi, slo, shi, vfd))

    res = _run_node("scrubgrid", cases)
    assert len(res) == len(cases)
    for c, r, (elo, ehi, slo, shi, vfd) in zip(cases, res, expected):
        if vfd:
            assert (r["lo"], r["hi"]) == (0, 240), r
        else:
            assert (r["lo"], r["hi"]) == (elo, ehi), (c["sunset"], c["sunrise"], r)
        assert (r["scoringLo"], r["scoringHi"]) == (slo, shi), r
        assert r["lo"] <= r["hi"]


# ======================================== popularity TIERS (0.6.30, web table)
def test_popularity_tiers_are_rank_bands_and_never_the_score():
    """The catalog exposes a TIER letter (A-F) and never the popularity score.

    The bands are RANK bands because the shipped score distribution is far too
    heavy-tailed for score cut-offs: measured on `web/catalog.json`, rank 1 =
    166,350, rank 100 = 9,910, rank 250 = 1,163, rank 1,500 = 19, and the last
    ~4,000 rows all sit at 1-4. Any score threshold would put nearly the whole
    catalog in one bucket.

    The engine imports the SHIPPED core stub (`web/core.js`), so this also proves
    the baked core actually carries the helpers the shell calls.
    """
    edges = [(1, "A"), (100, "A"), (101, "B"), (200, "B"), (250, "B"),
             (251, "C"), (600, "C"), (601, "D"), (1500, "D"), (1501, "E"),
             (3500, "E"), (3501, "F"), (7654, "F"), (0, "F")]
    res = _run_node("tier", [{"rank": n} for n, _ in edges])
    for (rank, want), r in zip(edges, res):
        assert r["tier"] == want, (rank, r)

    # Why B ends at 250: A+B must cover the standard-mode shortlist
    # (`STANDARD_TOP_N` = 200) with a margin, so the default view is A/B only.
    assert _run_node("tier", [{"rank": 200}])[0]["tier"] == "B"
    assert _run_node("tier", [{"rank": 251}])[0]["tier"] == "C"

    # Ranking mirrors the SERVER's standard-mode rule: score DESC, then id ASC.
    # 120 equally-scored objects, so the rank order is purely the id tie-break:
    # o000..o099 are the first hundred (A), o100..o119 the next band (B).
    objs = [{"id": f"o{i:03d}", "popularity": 42} for i in range(120)]
    r = _run_node("tier", [{"objs": objs}])[0]
    assert r["order"] == ["A", "B", "C", "D", "E", "F"]
    for oid, want in (("o000", "A"), ("o099", "A"), ("o100", "B"), ("o119", "B")):
        assert r["tiers"][oid] == want, (oid, r["tiers"][oid])
    assert sum(1 for t in r["tiers"].values() if t == "A") == 100
    assert sum(1 for t in r["tiers"].values() if t == "B") == 20

    # A higher score outranks a lower one regardless of id ...
    r = _run_node("tier", [{"objs": [{"id": "aaa", "popularity": 5},
                                     {"id": "zzz", "popularity": 900}]}])[0]
    assert r["tiers"] == {"aaa": "A", "zzz": "A"}      # only two scored rows
    # ... and BOTH kinds of "no popularity" land in F, so the column always sorts
    # A-F instead of leaving a hole: missing field and explicit zero.
    r = _run_node("tier", [{"objs": [{"id": "nos", "popularity": 100},
                                     {"id": "mis"}, {"id": "zero", "popularity": 0}]}])[0]
    assert r["tiers"] == {"nos": "A", "mis": "F", "zero": "F"}, r


def test_tier_letters_are_mode_independent_on_the_real_catalog():
    """ALIGNMENT (0.6.31). The web ranks the set the current mode shows
    (`assignPopularityTiers(activeCatalog())`) and the desktop ranks the same set
    (`assignPopularityTiers(S.catalog)`), so the two apps cannot disagree about a
    letter. That only works if ranking the standard subset reproduces the global
    ranks - true because standard mode IS the top-N by popularity
    (`catalog_data.standard_ids`, pinned by test_catalog.py). Proven here on the
    REAL shipped payload: every standard row gets the same letter whether it is
    ranked inside the 200 or inside all 12,176."""
    rows = json.loads((ROOT / "web" / "catalog.json").read_text(encoding="utf-8"))
    main = [{"id": r["id"], "popularity": r.get("popularity") or 0}
            for r in rows if not r.get("_adv")]
    whole = [{"id": r["id"], "popularity": r.get("popularity") or 0} for r in rows]
    assert len(main) == 200 and len(whole) > 10000, (len(main), len(whole))
    res = _run_node("tier", [{"objs": main}, {"objs": whole}])
    in_main, in_whole = res[0]["tiers"], res[1]["tiers"]
    mismatch = {k: (v, in_whole.get(k)) for k, v in in_main.items()
                if in_whole.get(k) != v}
    assert not mismatch, f"standard-mode tiers differ from the global ones: {mismatch}"
    # and the standard set really is the most popular slice: A/B only
    assert set(in_main.values()) <= {"A", "B"}, sorted(set(in_main.values()))


def test_wiki_titles_avoid_the_messier_disambiguation_trap():
    """0.6.32, the M101 report: an image lookup keyed on the DISPLAY NAME lands
    on a disambiguation page - "M101" is motorways, highways and a rifle - so the
    full "Messier 101" is tried first, then the NGC/IC designation, then the
    display name. A stored `wiki` title (which the catalogue no longer ships)
    would still win, and every list is deduped and non-empty."""
    cases = [
        {"obj": {"messier": "M101", "ngcic": "NGC 5457", "primary_name": "M101"}},
        {"obj": {"messier": "M31", "ngcic": "NGC 224",
                 "primary_name": "Andromeda Galaxy"}},
        {"obj": {"messier": "", "ngcic": "NGC 7000", "wiki": "",
                 "primary_name": "North America Nebula"}},
        {"obj": {"messier": "M42", "ngcic": "NGC 1976", "wiki": "Orion Nebula",
                 "primary_name": "Great Orion Nebula"}},
        {"obj": {"messier": "", "ngcic": "", "primary_name": "u_my target"}},
    ]
    got = _run_node("wikititles", cases)
    assert got[0] == ["Messier 101", "NGC 5457", "M101"], got[0]
    assert got[1] == ["Messier 31", "NGC 224", "Andromeda Galaxy"], got[1]
    assert got[2] == ["NGC 7000", "North America Nebula"], got[2]
    assert got[3] == ["Orion Nebula", "Messier 42", "NGC 1976",
                      "Great Orion Nebula"], got[3]
    assert got[4] == ["u_my target"], got[4]        # custom targets resolve too
    for row, case in zip(got, cases):
        assert row, f"no candidate for {case['obj']}"
        assert len(row) == len(set(row)), f"duplicate candidate: {row}"
        assert row[0] != "M101", "the disambiguation form must never be first"
