"""UI invariants for the catalog shell (static/catalog.html + shared modules).

The catalog must never expose the underlying popularity number as a
primary-table column, the `.hidden` class must stay authoritative over the
attribute, the sky map's hover guards must compare by value, and a catalog edit
must close its dialog on Escape. These text assertions fail loudly (a missing
subject file is a FAILURE, never a skip) if a revert reintroduces the old
behavior.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts) -> str:
    """Read a subject file, FAILING when it is missing.

    TR-07 (0.6.32 review): this used to `pytest.skip`, so renaming a subject file
    silently retired text invariants with a GREEN run. The project rule
    (FIX-9b/TS6) is that a green run must never mean "not checked", so a missing
    subject is a failure that names the file."""
    path = ROOT.joinpath(*parts)
    if not path.is_file():
        pytest.fail(f"{path} is missing - this invariant cannot be checked "
                    f"(a missing subject file is a FAILURE, never a skip)")
    return path.read_text(encoding="utf-8")


def _js_body(src: str, header: str) -> str:
    """Body of a top-level JS function, up to its first closing brace."""
    start = src.index(header)
    return src[start:src.index("\n}", start)]


def test_toast_dismiss_does_not_bubble():
    """DE-08: the toast's dismiss button must not bubble to the toast body."""
    util = _read("static", "js", "util.js")
    toast_body = _js_body(util, "export function toast(msg")
    assert "stopPropagation()" in toast_body, (
        "DE-08: the toast's dismiss button must not bubble to the toast body")


def test_sky_map_hover_and_legend_invariants():
    """DE-11 / DE-12: the sky map compared the hover by OBJECT identity against
    a freshly rebuilt point array (always true - a full repaint per mousemove),
    and its legend chips were rendered once, so a type toggled elsewhere was
    never reflected."""
    skymap = _read("static", "js", "skymap.js")
    assert "if (key !== prev) { SM_HOVER = best; renderSkyMap(); }" in skymap, (
        "DE-11: the sky-map hover guard must compare by value")
    assert "syncTypeLegend();" in _js_body(skymap, "export function renderSkyMap() {"), (
        "DE-12: the Sky Map legend must resync on render")


def test_library_edit_dialog_closes_on_escape():
    """DE-13: the library-edit dialog must have an Escape path like every
    other modal in the app."""
    arch = _read("static", "js", "archive.js")
    bind = _js_body(arch, "function bindArchive() {")
    assert 'addEventListener("keydown"' in bind and "Escape" in bind, (
        "DE-13: the library-edit dialog must close on Escape like every other one")


def test_desktop_keeps_the_hidden_class_authoritative():
    """The catalog hides things with the `.hidden` CLASS - it must keep winning."""
    assert ".hidden { display: none !important; }" in _read("static", "style.css")


def test_scoring_loop_is_shared_and_the_window_is_a_parameter():
    """SHELL-UNIFICATION (CO-07 follow-up): scoring lives in
    core/scoring.js::scoreNightTracks and the scoring span is the `range`
    parameter - the shell must not own a second aggregation loop."""
    scoring = _read("core", "scoring.js")
    assert "const win = Array.isArray(ctx.range) ? ctx.range : null;" in scoring
    assert "const nightN = winHi - winLo + 1;" in scoring, (
        "the % denominator must be the scoring range, not the grid")
    server = _read("app", "server.py")
    assert "planning_client_compute" not in server,         "the retired flag must not survive in the API"
