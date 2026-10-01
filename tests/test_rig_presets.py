"""Rig presets are a PICKER, never defaults.

0.6.1/0.6.2 auto-seeded the smart telescopes into every database; that is gone.
`remove_seeded_once` undoes those leftovers exactly once, and only for rigs that
still match a preset - a rig the user edited or renamed is their data.
"""

import pytest
from fastapi.testclient import TestClient

from app import rig_presets
from app.database import Database
from app.server import create_app, fov_arcmin


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "nightseam.db")


def _fake_old_seed(db, names=None):
    """Reproduce what 0.6.1/0.6.2 wrote: the presets + the seeded flag."""
    for name, focal, sw, sh in rig_presets.DEFAULT_RIGS:
        if names is not None and name not in names:
            continue
        db.add_rig(name, focal, sw, sh,
                   rig_presets.fov_arcmin(focal, sw),
                   rig_presets.fov_arcmin(focal, sh))
    db.set_setting(rig_presets.SEEDED_FLAG, 1)


def test_fov_twin_matches_server():
    """rig_presets.fov_arcmin must stay identical to server.fov_arcmin."""
    for _name, focal, sw, sh in rig_presets.DEFAULT_RIGS:
        assert rig_presets.fov_arcmin(focal, sw) == fov_arcmin(focal, sw)
        assert rig_presets.fov_arcmin(focal, sh) == fov_arcmin(focal, sh)


def test_presets_cover_zwo_and_dwarflab():
    names = [n for n, *_ in rig_presets.DEFAULT_RIGS]
    assert any("Seestar" in n for n in names)
    assert any("DWARF" in n for n in names)
    assert len(names) == len(set(names)) >= 4


def test_golden_fov_values():
    # published FOV of the Seestar S50 is ~1.28 deg -> 76.6 arcmin
    assert rig_presets.fov_arcmin(250.0, 5.57) == pytest.approx(76.6, abs=0.2)
    # DWARF 3: 150 mm / 7.68 mm sensor -> ~2.93 deg
    assert rig_presets.fov_arcmin(150.0, 7.68) == pytest.approx(176.0, abs=0.4)


def test_fresh_setup_has_no_rigs(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as c:
        state = c.get("/api/state").json()
    assert state["rigs"] == []                      # nothing added by default
    assert state["active_rig_id"] is None
    presets = state["rig_presets"]                  # ... but the picker is filled
    assert [p["name"] for p in presets] == [n for n, *_ in rig_presets.DEFAULT_RIGS]
    for p in presets:
        assert p["focal_mm"] > 0 and p["sensor_w"] > p["sensor_h"] > 0
        assert p["fov_w"] > p["fov_h"] > 0
        assert p["fov_w"] == fov_arcmin(p["focal_mm"], p["sensor_w"])


def test_picker_values_can_be_added_as_a_rig(tmp_path):
    """A preset is just data: posting it must produce a normal rig row."""
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as c:
        preset = c.get("/api/state").json()["rig_presets"][0]
        r = c.post("/api/rigs", json={k: preset[k] for k in
                                      ("name", "focal_mm", "sensor_w", "sensor_h")})
        assert r.status_code == 200
        assert r.json()["fov_w"] == preset["fov_w"]
        rigs = c.get("/api/state").json()["rigs"]
    assert [x["name"] for x in rigs] == [preset["name"]]


def test_auto_seeded_leftovers_are_removed_once(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    _fake_old_seed(db)
    assert len(db.rigs()) == len(rig_presets.DEFAULT_RIGS)
    with TestClient(create_app(db, prefetch=False)) as c:
        assert c.get("/api/state").json()["rigs"] == []


def test_cleanup_keeps_edited_and_renamed_rigs(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    _fake_old_seed(db)
    # the user tweaked the S50 for a reducer -> different sensor, keep it
    db.add_rig("ZWO Seestar S50 (reduced)", 250.0, 5.57, 3.13, 76.6, 43.0)
    # ... and re-typed one preset's numbers under their own name
    db.add_rig("My scope", 150.0, 5.57, 3.13, 127.9, 71.8)
    with TestClient(create_app(db, prefetch=False)) as c:
        names = [r["name"] for r in c.get("/api/state").json()["rigs"]]
    assert names == ["ZWO Seestar S50 (reduced)", "My scope"]


def test_a_preset_added_after_the_cleanup_stays(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    _fake_old_seed(db)
    with TestClient(create_app(db, prefetch=False)) as c:
        preset = c.get("/api/state").json()["rig_presets"][0]
        c.post("/api/rigs", json={k: preset[k] for k in
                                  ("name", "focal_mm", "sensor_w", "sensor_h")})
    # next start must not treat the user's own pick as a leftover
    with TestClient(create_app(db, prefetch=False)) as c:
        assert [r["name"] for r in c.get("/api/state").json()["rigs"]] == [preset["name"]]


def test_remove_seeded_once_is_idempotent(db):
    _fake_old_seed(db)
    assert rig_presets.remove_seeded_once(db) == len(rig_presets.DEFAULT_RIGS)
    assert db.rigs() == []
    assert rig_presets.remove_seeded_once(db) == 0


def test_refill_route_is_gone(tmp_path):
    """The old bulk "Add smart telescopes" endpoint must not come back silently."""
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as c:
        # 405: the path now only matches DELETE /api/rigs/{rid} ("presets" is
        # not an int, but the shape matches, so FastAPI answers Method Not
        # Allowed instead of Not Found). Either way there is no refill route.
        assert c.post("/api/rigs/presets").status_code in (404, 405)
