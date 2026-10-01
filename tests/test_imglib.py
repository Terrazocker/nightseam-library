"""Tests for library logic: object resolution, grouping, sessions."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import imglib

CSV = Path(__file__).resolve().parents[1] / "app" / "data" / "openned.csv"
COLS = ("id", "primary_name", "messier", "ngcic", "other", "otype", "ra_hours",
        "dec_deg", "vmag", "size_am", "constellation", "wiki",
        "blurb", "search")


def _catalog() -> list[dict]:
    """The shipped CSV in the shape the DB hands to imglib: real numbers, not the
    strings the file holds."""
    out = []
    with open(CSV, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            d = dict(zip(COLS, line.rstrip("\n").split(";")))
            for k in ("ra_hours", "dec_deg", "vmag"):
                d[k] = float(d[k]) if d[k] else None
            out.append(d)
    return out


CATALOG = _catalog()


def img(**kw):
    d = {"file": "x.fit", "orig_name": "x.fit", "object_id": None,
         "object_name": "Unknown", "ra_hours": None, "dec_deg": None,
         "exposure": 300.0, "date_obs": None, "filter": None,
         "telescope": None, "ingested_at": "2026-08-22T00:00:00"}
    d.update(kw)
    return d


# ---------------------------------------------------------------- resolution

def test_resolve_by_messier_designation():
    r = imglib.resolve_object(CATALOG, "M31_300s_L_001")
    assert r and r["id"] == "ngc224"          # M31 as OpenNGC names it
    assert r["primary_name"] == "Andromeda Galaxy"


def test_resolve_by_ngc_designation():
    assert imglib.resolve_object(CATALOG, "NGC 7000_Ha_120s")["id"] == "ngc7000"
    assert imglib.resolve_object(CATALOG, "NGC7000_Ha_120s")["id"] == "ngc7000"


def test_resolve_by_common_name():
    assert imglib.resolve_object(CATALOG, "Andromeda_180s")["id"] == "ngc224"
    # OpenNGC gives 'Eagle Nebula' to TWO rows (IC 4703 the nebula and NGC 6611
    # the cluster, M16), so a common-name hint resolves to whichever the index
    # saw first; the designations stay unambiguous.
    assert imglib.resolve_object(CATALOG, "Eagle Nebula")["id"] in ("ic4703", "ngc6611")
    assert imglib.resolve_object(CATALOG, "M16")["id"] == "ngc6611"
    # OpenNGC carries no common name for NGC 5457, so a FITS header that only
    # says "Pinwheel Galaxy" cannot be resolved any more (it says M101/NGC 5457)
    assert imglib.resolve_object(CATALOG, "Pinwheel Galaxy") is None
    assert imglib.resolve_object(CATALOG, "M101")["id"] == "ngc5457"


def test_resolve_by_coordinates():
    # near M31 (RA 0.712h, Dec +41.27); the hint carries no matching name token,
    # so the coordinate path decides
    r = imglib.resolve_object(CATALOG, "lights_0042", 0.70, 41.2)
    assert r and r["id"] == "ngc224"
    # a dense 12k catalog always has SOMETHING nearby, so the contract that
    # matters is that the match is LOCAL (inside MATCH_RADIUS_DEG)
    other = imglib.resolve_object(CATALOG, "lights_0042", 12.0, 0.0)
    assert other is not None
    assert imglib.angular_dist(12.0, 0.0, other["ra_hours"],
                               other["dec_deg"]) <= imglib.MATCH_RADIUS_DEG
    # nothing to go on -> no match
    assert imglib.resolve_object(CATALOG, "lights_0042") is None


def test_resolve_unmatched_returns_none():
    assert imglib.resolve_object(CATALOG, "My Secret Object") is None
    assert imglib.resolve_object([], "M31") is None


def _resolver_row(oid, name, ra_hours=1.0, dec_deg=2.0, search=""):
    return {
        "id": oid, "primary_name": name, "messier": None, "ngcic": None,
        "other": None, "otype": "galaxy", "size_am": "", "ra_hours": ra_hours,
        "dec_deg": dec_deg, "search": search,
    }


def test_catalog_resolver_normalizes_once_and_preserves_match_order(monkeypatch):
    rows = [
        _resolver_row("same", "Atlas Alpha", search="Shared Alias"),
        _resolver_row("second", "Atlas Bravo", search="Shared Alias"),
        # The old resolver found this name token, then returned the first row
        # for the duplicate ID by linearly scanning from the beginning.
        _resolver_row("same", "Later Alias"),
    ]
    calls = 0
    original = imglib.normalize_catalog

    def counted_normalize(catalog):
        nonlocal calls
        calls += 1
        return original(catalog)

    monkeypatch.setattr(imglib, "normalize_catalog", counted_normalize)
    resolver = imglib.CatalogResolver(rows)
    assert calls == 1

    # Equal-length prefix hits retain insertion-order tie resolution.
    assert resolver.resolve("Atlas")["primary_name"] == "Atlas Alpha"
    # A duplicate exact name keeps the first catalog row.
    assert resolver.resolve("Shared Alias")["id"] == "same"
    # Duplicate IDs resolve to their first row, as _by_id did previously.
    assert resolver.resolve("Later Alias")["primary_name"] == "Atlas Alpha"


def test_catalog_resolver_uses_coordinates_then_preserves_unresolved_result():
    resolver = imglib.CatalogResolver([_resolver_row("near", "Nearby Object")])
    assert resolver.resolve("unmatched_capture", 1.0, 2.0)["id"] == "near"
    assert resolver.resolve("unmatched_capture", 15.0, -20.0) is None
    assert imglib.CatalogResolver([]).resolve("M31") is None


# ---------------------------------------------------------------- sessions

def test_sessions_merge_across_midnight():
    imgs = [
        img(date_obs="2026-08-22T23:00:00", exposure=300),
        img(date_obs="2026-08-23T01:00:00", exposure=300),   # 2 h later
    ]
    s = imglib.compute_sessions(imgs)
    assert len(s) == 1
    assert s[0]["n_images"] == 2
    assert s[0]["exposure"] == 600.0


def test_sessions_split_on_long_gap():
    imgs = [
        img(date_obs="2026-08-22T23:00:00"),
        img(date_obs="2026-08-25T22:00:00"),                 # 3 nights later
    ]
    s = imglib.compute_sessions(imgs)
    assert len(s) == 2
    assert [x["n_images"] for x in s] == [1, 1]


def test_undated_frames_one_session():
    imgs = [img(date_obs=None), img(date_obs=None)]
    s = imglib.compute_sessions(imgs)
    assert len(s) == 1
    assert s[0].get("undated") is True
    assert s[0]["n_images"] == 2


def test_explicit_calibration_joins_selected_dated_session_without_changing_capture_dates():
    frames = [
        img(id=1, date_obs="2026-08-22T23:00:00Z", imagetyp="light"),
        img(id=2, date_obs="2026-08-23T01:00:00Z", imagetyp="light"),
        img(id=3, date_obs="2026-08-25T22:00:00Z", imagetyp="light"),
    ]
    calibration = img(id=4, date_obs="2026-08-25T23:00:00Z", imagetyp="dark",
                      session_date_obs="2026-08-22T23:00:00Z", exposure=120)
    sessions = imglib.compute_sessions(frames + [calibration])
    assert len(sessions) == 2
    chosen = imglib.session_for_anchor(frames + [calibration], 2)
    assert set(chosen["image_ids"]) == {1, 2, 4}
    assert chosen["start"] == "2026-08-22T23:00:00Z"
    assert chosen["end"] == "2026-08-23T01:00:00Z"
    assert chosen["types"] == {"light": {"n": 2, "exposure": 600.0},
                               "dark": {"n": 1, "exposure": 120.0}}
    assert imglib.session_for_anchor(frames + [calibration], 4) == chosen
    assert imglib.session_for_image(frames, calibration) == chosen
    assert imglib.session_for_anchor(frames, 999) is None
    assert calibration["date_obs"] == "2026-08-25T23:00:00Z"
    # The same FITS timestamp follows ordinary grouping when no attachment was requested.
    ordinary = dict(calibration, session_date_obs=None)
    assert set(imglib.session_for_image(frames, ordinary)["image_ids"]) == {3, 4}


def test_explicit_undated_calibration_stays_undated_despite_its_real_date():
    frames = [img(id=1, imagetyp="light"),
              img(id=2, date_obs="2026-08-25T22:00:00Z", imagetyp="light")]
    calibration = img(id=3, date_obs="2026-08-25T22:10:00Z", imagetyp="flat",
                      session_date_obs="")
    chosen = imglib.session_for_image(frames, calibration)
    assert chosen["undated"] is True
    assert chosen["start"] is None and chosen["end"] is None
    assert set(chosen["image_ids"]) == {1, 3}
    assert chosen["types"]["flat"]["n"] == 1
    assert calibration["date_obs"] == "2026-08-25T22:10:00Z"
    assert imglib.session_for_anchor(frames + [calibration], 3) == chosen
    ordinary = dict(calibration, session_date_obs=None)
    assert set(imglib.session_for_image(frames, ordinary)["image_ids"]) == {2, 3}


def test_session_anchor_null_fallback_and_explicit_empty_are_distinct():
    assert imglib.effective_session_date(img(date_obs="2026-08-22T23:00:00Z")) == "2026-08-22T23:00:00Z"
    assert imglib.effective_session_date(img(date_obs="2026-08-22T23:00:00Z",
                                            session_date_obs=None)) == "2026-08-22T23:00:00Z"
    assert imglib.effective_session_date(img(date_obs="2026-08-22T23:00:00Z",
                                            session_date_obs="")) == ""


def test_session_membership_does_not_guess_by_nearest_session_end():
    base = datetime(2026, 8, 23)

    def at(hours):
        return (base + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    previous = img(id=1, date_obs=at(-24))
    run = [img(id=index + 2, date_obs=at(hours)) for index, hours in enumerate(range(0, 81, 10))]
    early = img(id=20, date_obs=at(1))
    chosen = imglib.session_for_image([previous] + run, early)
    # A long connected run's end can be farther away than another session's end.
    assert chosen["start"] == at(0) and chosen["end"] == at(80)
    assert set(chosen["image_ids"]) == {frame["id"] for frame in run} | {20}


def test_ordinary_session_gap_boundary_and_utc_offsets_remain_truthful():
    frames = [img(id=1, date_obs="2026-08-22T23:00:00+02:00"),
              img(id=2, date_obs="2026-08-23T09:00:00Z"),
              img(id=3, date_obs="2026-08-23T10:00:00Z")]
    sessions = imglib.compute_sessions(frames)
    assert [session["image_ids"] for session in sessions] == [[1], [2, 3]]
    attached = img(id=4, date_obs="2026-09-20T00:00:00Z", imagetyp="bias",
                   session_date_obs="2026-08-22T21:00:00Z")
    chosen = imglib.session_for_image(frames, attached)
    assert set(chosen["image_ids"]) == {1, 4}
    assert chosen["start"] == chosen["end"] == "2026-08-22T23:00:00+02:00"
    # Removing the original frame still leaves the explicitly anchored calibration group intact.
    remaining = imglib.compute_sessions([attached])[0]
    assert remaining["start"] == remaining["end"] == "2026-08-22T21:00:00Z"
    assert remaining["image_ids"] == [4]


def test_library_payload_preserves_measurement_and_anchor_but_uses_logical_ranges():
    measured = "2026-10-01T00:00:00Z"
    capture = "2026-08-22T23:00:00Z"
    frames = [img(id=1, object_name="Legacy capture", date_obs=capture),
              img(id=2, object_name="Legacy capture", date_obs=measured,
                  session_date_obs=capture, imagetyp="dark", content_sha256="private",
                  file_size=123, file_mtime_ns=456)]
    original = [dict(frame) for frame in frames]
    library = imglib.group_library(frames, [])
    assert len(library) == 1
    group = library[0]
    assert group["n_sessions"] == 1
    assert group["first"] == group["last"] == capture
    assert set(group["sessions"][0]["image_ids"]) == {1, 2}
    public = next(frame for frame in group["images"] if frame["id"] == 2)
    assert public["date_obs"] == measured and public["session_date_obs"] == capture
    assert not {"content_sha256", "file_size", "file_mtime_ns"} & public.keys()
    assert frames == original


def test_fractional_utc_anchor_does_not_bridge_exact_twelve_hour_sessions(monkeypatch):
    earlier = img(id=1, date_obs="2026-08-22T08:00:00.900000")
    target = img(id=2, date_obs="2026-08-22T20:00:00.900000")
    calibration = img(id=3, date_obs="2026-09-01T00:00:00", imagetyp="dark",
                      session_date_obs="2026-08-22T20:00:00.900000Z")
    for offset_hours in (-7, 2, 5.5):
        # Simulate the existing filename clock in different host zones. A UTC
        # anchor between local-interpreted captures would otherwise bridge them.
        host_zone = timezone(timedelta(hours=offset_hours))

        def filename_epoch(value):
            if not value:
                return None
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (stamp if stamp.tzinfo is not None else
                    stamp.replace(tzinfo=host_zone)).timestamp()

        monkeypatch.setattr(imglib, "date_epoch", filename_epoch)
        sessions = imglib.compute_sessions([earlier, target, calibration])
        assert [session["image_ids"] for session in sessions] == [[1], [2, 3]]
        chosen = imglib.session_for_image([earlier, target], calibration)
        assert chosen["start"] == chosen["end"] == target["date_obs"]
        assert imglib.session_for_anchor([earlier, target, calibration], 3) == chosen
        group = imglib.group_library([calibration, target, earlier], [])[0]
        assert group["n_sessions"] == 2
        assert group["first"] == earlier["date_obs"] and group["last"] == target["date_obs"]


# ---------------------------------------------------------------- grouping

def test_group_library_totals_and_designation():
    """`object_id='m31'` is a STALE curated id: the group must still show the
    object it names (re-resolved through the capture's own OBJECT header)."""
    imgs = [
        img(object_id="m31", object_name="M31", date_obs="2026-08-22T23:00:00",
            exposure=300, ra_hours=0.7, dec_deg=41.2),
        img(object_id="m31", object_name="M31", date_obs="2026-08-23T01:00:00",
            exposure=300),
        img(object_id="ngc7000", object_name="NGC 7000",
            date_obs="2026-08-24T22:00:00", exposure=120),
    ]
    objs = imglib.group_library(imgs, CATALOG)
    assert len(objs) == 2
    m31 = next(o for o in objs if o["object_id"] == "m31")
    assert m31["name"] == "Andromeda Galaxy"
    assert m31["designation"] == "M31"
    assert m31["otype"] == "galaxy"
    assert m31["size_am"] == "177.83x69.66"      # OpenNGC's MajAx x MinAx
    assert m31["n_images"] == 2
    assert m31["total_exposure"] == 600.0
    assert m31["n_sessions"] == 1
    assert m31["first"] == "2026-08-22T23:00:00"
    assert m31["last"] == "2026-08-23T01:00:00"
    n7000 = next(o for o in objs if o["object_id"] == "ngc7000")
    assert n7000["designation"] == "NGC 7000"


def test_group_library_unresolved_keeps_name_and_coords():
    imgs = [img(object_id=None, object_name="My Secret Nebula",
                ra_hours=12.5, dec_deg=-30.0)]
    objs = imglib.group_library(imgs, CATALOG)
    assert len(objs) == 1
    o = objs[0]
    assert o["object_id"] is None
    assert o["name"] == "My Secret Nebula"
    assert o["designation"] == ""
    assert o["otype"] is None
    assert o["ra_hours"] == 12.5
    assert o["dec_deg"] == -30.0


def test_group_library_unresolved_user_type():
    imgs = [img(object_id=None, object_name="My Thing", otype="nebula")]
    objs = imglib.group_library(imgs, CATALOG)
    assert objs[0]["otype"] == "nebula"


def test_rename_images_updates_rows_and_paths(tmp_path):
    from app.database import Database
    db = Database(tmp_path / "t.db")
    db.add_image({
        "file": "My Secret/lights/a.fit", "orig_name": "a.fit",
        "object_id": None, "object_name": "My Secret",
        "ra_hours": 5.0, "dec_deg": -20.0, "exposure": 240.0,
        "date_obs": None, "filter": None, "telescope": None,
        "ingested_at": "2026-08-26T00:10:00",
    })
    db.rename_images("My Secret", "New Name", "nebula", "New Name")
    imgs = db.images()
    assert len(imgs) == 1
    assert imgs[0]["object_name"] == "New Name"
    assert imgs[0]["otype"] == "nebula"
    assert imgs[0]["file"] == "New Name/lights/a.fit"
