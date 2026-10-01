"""BUG-005: the Tonight render marker and the offline half of its check.

The defect itself is a TIMING race (the browser check slept a nominal 120 ms
while the app debounced the same 120 ms and then deferred its ranking), so its
end-to-end proof is the browser gate check
`web-tonight-render-marker-converges`. What can be pinned without a browser is
the CONTRACT that check depends on:

  * the view publishes a marker AFTER the DOM swap, and clears it the moment the
    selection moves (otherwise a stale paint would satisfy the wait);
  * the check uses that marker instead of a fixed sleep;
  * the two sides agree on the attribute names, so a rename cannot silently
    downgrade the check back to a timing guess.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "web" / "app.js"
HARNESS = ROOT / "tests" / "browser" / "harness.py"
SUITE = ROOT / "tests" / "browser" / "web_suite.py"


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def test_the_tonight_view_publishes_a_render_marker():
    """`#view[data-ns-gen]`/`[data-ns-sel]` are written after the swap, and a
    paint that does not belong to the selected instant publishes a value that can
    never be mistaken for the selection."""
    src = _app()
    assert "root.dataset.nsGen" in src and "root.dataset.nsSel" in src
    # the swap is followed by the marker, inside the deferred ranking callback
    render = src.split("function renderTonight(", 1)[1]
    assert "stampTonightRender(box, selTime)" in render
    assert "applyTonightMarker()" in render
    # the freshness rule: a stale paint must not publish the selection. It
    # publishes `Date.now()` - a LARGE moving value - because the wait helper
    # requires `nsSel === <requested instant>` (a past timestamp can never equal
    # it), while an EMPTY marker would make a waiting check hang instead of
    # reporting the staleness.
    marker = src.split("function applyTonightMarker(", 1)[1].split("\n}\n", 1)[0]
    assert "tonightRenderedMs" in marker
    assert re.search(r"fresh\s*=", marker), marker
    assert "scrubTimeMs(" in marker, (
        "the freshness test must compare on the scrubber's own grid")
    assert "String(Date.now())" in marker and "String(sel)" in marker, (
        "a stale paint must publish a value the wait can never match, and a fresh "
        "one must publish the selection")
    # the published value is the scrubber's snapped instant, not raw ms
    stamp = src.split("function stampTonightRender(", 1)[1].split("\n}\n", 1)[0]
    assert "scrubTimeMs(" in stamp


def test_the_marker_is_cleared_the_moment_the_selection_moves():
    """Between the scrub and the debounced paint the cards describe the PREVIOUS
    time; a wait that read the marker there would sample stale rows."""
    src = _app()
    listener = src.split("window.addEventListener('sky:time'", 1)[1] \
                  .split("}, 120);", 1)[0]
    assert "applyTonightMarker()" in listener, (
        "the sky:time listener must invalidate the marker for the old selection")
    assert "tonightScrubTimer = setTimeout" in listener


def test_sync_only_time_notification_does_not_schedule_a_second_repaint():
    """A refresh renders its active view directly but must still resync both
    scrubbers. The event therefore carries a sync-only mode: it invalidates a
    Tonight marker but does not queue a second ranking pass or Sky canvas draw.
    """
    src = _app()
    clamp = src.split("function clampSkyTime(", 1)[1].split("\n}\n", 1)[0]
    assert "{ repaint = false }" in clamp
    assert "announceSkyTime({ repaint })" in clamp
    sky_listener = src.split("skyTimeHandler = (ev)", 1)[1] \
                      .split("window.addEventListener('sky:time', skyTimeHandler)", 1)[0]
    assert "ev.detail?.repaint !== false" in sky_listener
    tonight_listener = src.split("window.addEventListener('sky:time', (ev)", 1)[1] \
                        .split("}, 120);", 1)[0]
    assert "applyTonightMarker()" in tonight_listener
    assert "ev.detail?.repaint === false" in tonight_listener
    assert "if (ev.detail?.repaint === false) return;" in tonight_listener


def test_the_check_waits_for_the_marker_not_a_sleep():
    """The regression is a sleep, so the check must not carry one on the Tonight
    scrub path any more."""
    src = SUITE.read_text(encoding="utf-8")
    check = src.split("def web_tonight_pool_includes_unmagnituded_standard_rows",
                      1)[1].split("\n@check(", 1)[0]
    assert "wait_tonight_after" in check
    assert "wait_for_timeout(120)" not in check, (
        "the Tonight check still guesses the debounce with a fixed sleep")


def test_harness_and_app_agree_on_the_marker_attributes():
    src = HARNESS.read_text(encoding="utf-8")
    for attr in ("dataset.nsGen", "dataset.nsSel"):
        assert attr in src, f"the harness no longer reads {attr}"
    app = _app()
    for attr in ("dataset.nsGen", "dataset.nsSel"):
        assert attr in app, f"the app no longer writes {attr}"
    # the wait helper exists, is bounded by an explicit timeout, and its success
    # condition requires a NEWER generation than the caller passed in
    body = src.split("def wait_tonight_after(", 1)[1].split("def tonight_gen(", 1)[0]
    assert "timeout" in body
    assert re.search(r">\s*\bg\b", body), (
        "the wait must require a generation NEWER than the one passed in")
    assert "nsSel" in body


def test_the_marker_check_exists_and_drives_a_scrub():
    src = SUITE.read_text(encoding="utf-8")
    assert '@check("web-tonight-render-marker-converges", "web")' in src
    body = src.split("def web_tonight_render_marker_converges", 1)[1] \
              .split("\n@check(", 1)[0]
    # it must compare the published instant with the scrubber's own, or a lagging
    # render would still pass
    assert "__ns_scrub_sel" in body
    assert "wait_tonight_after" in body
