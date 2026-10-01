"""core/panohistory.js - the panorama editor's shared snapshot/undo model.

The web shell uses the full model (step list + baseline + redo + dirty); the
desktop uses the SAME snapshot/apply contract from its pre-gesture stack. The
contracts pinned here are the ones that silently broke before:

  * a stroke-y snapshot does NOT carry the stored profile (the mask reproduces
    it, and the field set is what keeps a 60-step stack small), while a
    stroke-less snapshot MUST carry it (ruling C, DE-03/WE-02: an empty mask
    cannot re-derive an authoritative profile, so undo used to leave a discarded
    derived ceiling live in the record planning reads);
  * the snapshot covers the EDITABLE set only - zoom is presentation-only;
  * one gesture is one entry, a new edit drops the redo branch, and the stack is
    bounded;
  * the two shells' field names both work (`keys`).

The engine is pure ES, so a node subprocess is the honest way to exercise it -
the same shape as tests/test_js_behaviour.py.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DRIVER = r"""
const H = await import(new URL('file:///__REPO__/core/panohistory.js').href);
const out = {};

// ---- web field names ------------------------------------------------------
const web = () => ({
  azOffset: 10, altBottom: 0, altTop: 45, fov: 360, paintMode: 'obstacle',
  blockAbove: false, strokes: [], profile: [1, 2], profileTop: [3, 4],
});
const KEYS = H.HISTORY_KEYS;

// 1) a stroke-less snapshot carries the authoritative profile
const w0 = web();
const s0 = H.snapshot(w0, KEYS);
out.strokeLessHasProfile = JSON.parse(s0).pr !== undefined;

// 2) a stroke-y snapshot does NOT (the mask reproduces it)
const w1 = web();
w1.strokes = [{ m: 'b', x: 0.5, y: 0.5, r: 0.1 }];
const s1 = H.snapshot(w1, KEYS);
out.strokeYHasProfile = JSON.parse(s1).pr !== undefined;

// 3) a Clear is dirty; committing it (the shell does that at the end of the
//    gesture, `historyCommit` on pointer up) makes it undoable, and the undo
//    brings the strokes back so the PROFILE is re-derivable from them again
//    (`recomputeProfile` owns that, shell-side - this only checks the state the
//    history hands back).
const h = H.newHistory(w1, KEYS);
w1.strokes = [];
w1.profile = [];            // clearMask blanks it explicitly
out.dirtyAfterClear = H.historyDirty(h, w1);
out.clearCommitted = H.historyCommit(h, w1, 60);
H.historyUndo(h, w1);
out.undoRestoredStrokes = w1.strokes.length === 1;
out.cleanAfterUndo = !H.historyDirty(h, w1);

// 3b) RULING C (DE-03/WE-02), the case the snapshot rule exists for: a record
//     with NO strokes and an AUTHORITATIVE profile (an imported horizon), a
//     deliberate paint-mode switch blanking it, then undo - the stored profile
//     must come back, because an empty mask can never re-derive it.
const bare = { azOffset: 0, altBottom: 0, altTop: 45, fov: 360,
               paintMode: 'obstacle', blockAbove: false, strokes: [],
               profile: [7, 8], profileTop: [5, 6] };
const h6 = H.newHistory(bare, KEYS);
bare.paintMode = 'sky';
bare.profile = [];          // setEditableMode blanks for the new mode
bare.profileTop = [];
out.strokeLessDirty = H.historyDirty(h6, bare);
out.strokeLessCommitted = H.historyCommit(h6, bare, 60);
H.historyUndo(h6, bare);
out.strokeLessProfileRestored = JSON.stringify(bare.profile) === JSON.stringify([7, 8]);
out.strokeLessModeRestored = bare.paintMode === 'obstacle';
out.strokeLessTopRestored = JSON.stringify(bare.profileTop) === JSON.stringify([5, 6]);

// 4) one commit per change; a repeat commit is a no-op
const h2 = H.newHistory(w0, KEYS);
out.commitChanged = H.historyCommit(h2, { ...w0, fov: 200 }, 60);
out.commitRepeat = H.historyCommit(h2, { ...w0, fov: 200 }, 60);

// 5) a new edit drops the redo branch
H.historyUndo(h2, w0);
out.canRedoAfterUndo = H.historyCanRedo(h2);
H.historyCommit(h2, { ...w0, fov: 100 }, 60);
out.canRedoAfterNewEdit = H.historyCanRedo(h2);

// 6) redo replays the undone state
const h3 = H.newHistory(w0, KEYS);
H.historyCommit(h3, { ...w0, fov: 90 }, 60);
H.historyUndo(h3, w0);
const undone = H.historyRedo(h3, w0);
out.redoReplayed = undone && w0.fov === 90;

// 7) the stack is bounded
const h4 = H.newHistory(w0, KEYS);
for (let i = 1; i <= 80; i++) H.historyCommit(h4, { ...w0, fov: i }, 60);
out.boundedEntries = h4.entries.length;

// 8) historyMarkSaved moves the baseline, so a saved state is clean
const h5 = H.newHistory(w0, KEYS);
H.historyCommit(h5, { ...w0, fov: 180 }, 60);
w0.fov = 180;
H.historyMarkSaved(h5, w0);
out.cleanAfterMarkSaved = !H.historyDirty(h5, w0);

// ---- desktop field names (the same contract through `keys`) ---------------
const KEYS_D = H.HISTORY_KEYS_DESKTOP;
const desk = () => ({
  az_offset: 20, alt_bottom: -5, alt_top: 40, fov: 300,
  paint_mode: 'sky', block_above: true, strokes: [],
  profile: [9], profile_top: [8],
});
const d0 = desk();
const sd = H.snapshot(d0, KEYS_D);
const od = JSON.parse(sd);
out.desktopEncodes = od.a === 20 && od.b === -5 && od.t === 40 && od.f === 300
  && od.m === 'sky' && od.o === 1 && od.pr !== undefined;
H.applySnapshot(d0, sd, KEYS_D);
out.desktopRoundTrip = d0.az_offset === 20 && d0.fov === 300
  && d0.paint_mode === 'sky' && d0.block_above === true;

// 9) the two shells must produce the SAME snapshot for equivalent records
const wEq = { ...web(), strokes: [{ m: 'b', x: 1, y: 2, r: 3 }] };
const dEq = { az_offset: 10, alt_bottom: 0, alt_top: 45, fov: 360,
              paint_mode: 'obstacle', block_above: false,
              strokes: [{ m: 'b', x: 1, y: 2, r: 3 }] };
out.crossShellIdentical = H.snapshot(wEq, KEYS) === H.snapshot(dEq, KEYS_D);

// 10) zoom is NOT part of the snapshot
const z0 = { ...web(), editorZoom: 4 };
out.zoomIgnored = H.snapshot(z0, KEYS) === H.snapshot(web(), KEYS);

process.stdout.write(JSON.stringify(out));
"""


def _run() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.fail("node is not installed - the history engine cannot run")
    driver = ROOT / "logs" / ".pano_history_probe.mjs"
    driver.parent.mkdir(exist_ok=True)
    # absolute file URL: the driver sits in logs/, so a relative specifier would
    # resolve against logs/ and miss core/
    driver.write_text(DRIVER.replace("__REPO__", ROOT.as_posix()),
                      encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run([node, str(driver)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120,
                              cwd=str(ROOT))
    finally:
        driver.unlink(missing_ok=True)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def probe() -> dict:
    return _run()


def test_the_snapshot_covers_exactly_the_editable_state(probe):
    assert probe["strokeLessHasProfile"] is True, (
        "a stroke-less snapshot must carry the AUTHORITATIVE profile")
    assert probe["strokeYHasProfile"] is False, (
        "a stroke-y snapshot must stay profile-free (the mask reproduces it)")
    assert probe["zoomIgnored"] is True, "editor zoom is presentation-only"


def test_undo_restores_the_authoritative_profile_and_clears_dirty(probe):
    """Ruling C (DE-03/WE-02) as behaviour. Two shapes, because they differ:

    * a stroke-y record: undo brings the STROKES back, and the shell re-derives
      the profile from the mask (`recomputeProfile` owns that);
    * a STROKE-LESS record with a stored profile: undo must bring the stored
      profile back verbatim - nothing can re-derive it, so without this a
      discarded derived ceiling would stay live in the record planning reads.
    """
    assert probe["dirtyAfterClear"] is True
    assert probe["undoRestoredStrokes"] is True
    assert probe["cleanAfterUndo"] is True

    assert probe["strokeLessDirty"] is True
    assert probe["strokeLessCommitted"] is True
    assert probe["strokeLessProfileRestored"] is True, (
        "a stroke-less undo must restore the AUTHORITATIVE stored profile")
    assert probe["strokeLessTopRestored"] is True
    assert probe["strokeLessModeRestored"] is True


def test_one_gesture_is_one_entry_and_a_new_edit_drops_redo(probe):
    assert probe["commitChanged"] is True
    assert probe["commitRepeat"] is False, "an unchanged state is not a step"
    assert probe["canRedoAfterUndo"] is True
    assert probe["canRedoAfterNewEdit"] is False
    assert probe["redoReplayed"] is True


def test_the_history_is_bounded_and_the_baseline_tracks_saves(probe):
    assert probe["boundedEntries"] == 60
    assert probe["cleanAfterMarkSaved"] is True


def test_both_shells_encode_the_same_state_through_their_own_keys(probe):
    """The whole point of the unification: the desktop's snake_case record and
    the web's camelCase record produce the SAME snapshot, so the two editors
    cannot disagree about what "the editable state" is."""
    assert probe["desktopEncodes"] is True
    assert probe["desktopRoundTrip"] is True
    assert probe["crossShellIdentical"] is True


def test_the_desktop_delegates_instead_of_re_implementing():
    """The shells must CALL the core model, not carry a copy: the desktop's undo
    helpers go through core, and the web's history functions are delegates."""
    pano = (ROOT / "static" / "js" / "pano.js").read_text(encoding="utf-8")
    assert "coreSnapshotPano(rec, HISTORY_KEYS_DESKTOP)" in pano
    assert "coreApplySnapshot(rec, stack.pop(), HISTORY_KEYS_DESKTOP)" in pano
    assert "JSON.stringify({\n    strokes:" not in pano, (
        "the desktop is building its own snapshot again")
    horizon = (ROOT / "web" / "horizon.js").read_text(encoding="utf-8")
    for call in ("coreSnapshotPano(p, HISTORY_KEYS)", "coreApplySnapshot(p, snap, HISTORY_KEYS)",
                 "coreHistoryCommit(h, p, limit)", "coreHistoryDirty(h, p)"):
        assert call in horizon, f"web/horizon.js must delegate: {call}"
