"""Visibility predicates + min-alt rule live in core/ (Phase 2, concept 4).

Behavioural anchors are the `status`, `minalt` and `abovehorizon` engines of
tests/js/astro_parity.mjs (absolute cases in tests/test_js_parity.py); this
test pins that the shells delegate instead of re-implementing, and that the two
inline web predicate copies are gone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_desktop_shell_delegates_visibility_to_core():
    derive = _read("static", "js", "derive.js")
    assert "statusOf as coreStatusOf" in derive
    assert "function statusOf" not in derive, "desktop kept a statusOf copy"
    assert "effectiveMinAlt as coreMinAlt" in derive
    assert "function effectiveMinAlt" not in derive, \
        "desktop kept a min-alt rule copy"


def test_web_shells_delegate_visibility_to_core():
    horizon = _read("web", "horizon.js")
    assert "effectiveMinAlt as coreMinAlt" in horizon
    assert "function effectiveMinAlt" not in horizon, \
        "web horizon kept a min-alt rule copy"
    nr = _read("web", "nightrank.js")
    assert "sampleVisible" in nr
    assert "function sampleVisible" not in nr, "nightrank kept the predicate"
    app = _read("web", "app.js")
    assert "sampleVisible(altNow, minAlt" in app, \
        "Tonight must use the shared predicate"
    assert "sampleVisible(alt, minAlt" in app, \
        "the detail loop must use the shared predicate"
    assert "altNow > ceilNow.ceil" not in app, "inline predicate copy survived"
