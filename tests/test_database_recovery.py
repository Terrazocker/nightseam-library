"""Recovery snapshots and atomic reindex use only private test databases."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from app.database import BACKUP_HISTORY_LIMIT, Database


def rec(file="Old/undated/lights/a.fit", **values):
    return {"file": file, "orig_name": "a.fit", "object_name": "Manual identity",
            "ingested_at": "2026-10-11T00:00:00Z", **values}


def read_value(path, query):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(query).fetchone()[0]
    finally:
        connection.close()


def histories(path, phase):
    return sorted(path.parent.glob(path.name + f".backup.{phase}.*"))


def no_staging(path):
    assert not list(path.parent.glob(path.name + ".backup-stage-*"))


def test_historical_good_state_survives_later_opens(tmp_path):
    path = tmp_path / "private.sqlite"
    db = Database(path)
    db.add_image(rec())
    db.make_backup()
    good = histories(path, "open")[-1]
    good_bytes = good.read_bytes()
    db.conn.execute("DELETE FROM images")
    db.conn.commit()
    db.conn.close()
    for _ in range(2):
        reopened = Database(path)
        reopened.conn.close()
    assert read_value(Path(str(path) + ".backup"), "SELECT COUNT(*) FROM images") == 0
    assert good.read_bytes() == good_bytes
    assert read_value(good, "SELECT COUNT(*) FROM images") == 1
    no_staging(path)


def test_pre_migration_preserves_original_schema_and_removed_setting(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);"
                           "INSERT INTO settings VALUES('sky_brightness_provider','\"old\"');"
                           "CREATE TABLE recovery_marker(original TEXT);")
        conn.commit()
    db = Database(path)
    before = histories(path, "pre-migration")[-1]
    assert read_value(before, "SELECT value FROM settings WHERE key='sky_brightness_provider'") == '"old"'
    assert read_value(before, "SELECT COUNT(*) FROM sqlite_master WHERE name='images'") == 0
    assert read_value(path, "SELECT COUNT(*) FROM sqlite_master WHERE name='images'") == 1
    assert db.setting("sky_brightness_provider") is None
    no_staging(path)


def test_backup_contains_committed_wal_records(tmp_path):
    path = tmp_path / "wal.sqlite"
    db = Database(path)
    db.conn.execute("PRAGMA journal_mode=WAL")
    db.conn.execute("PRAGMA wal_autocheckpoint=0")
    db.add_image(rec())
    assert Path(str(path) + "-wal").stat().st_size > 0
    db.make_backup()
    rolling = Path(str(path) + ".backup")
    assert read_value(rolling, "PRAGMA quick_check") == "ok"
    assert read_value(rolling, "SELECT COUNT(*) FROM images") == 1
    no_staging(path)


def test_invalid_snapshot_aborts_before_migration_and_preserves_prior_copy(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);"
                           "INSERT INTO settings VALUES('sky_brightness_provider','\"original\"');")
        conn.commit()
    old_copy = Path(str(path) + ".backup")
    old_copy.write_bytes(b"preserved previous recovery evidence")
    instance = Database.__new__(Database)

    def failed_check(connection):
        raise sqlite3.DatabaseError("injected failed quick_check")

    monkeypatch.setattr(Database, "_validate_backup", staticmethod(failed_check))
    with pytest.raises(sqlite3.DatabaseError, match="quick_check"):
        instance.__init__(path)
    assert read_value(path, "SELECT COUNT(*) FROM sqlite_master WHERE name='images'") == 0
    assert read_value(path, "SELECT value FROM settings") == '"original"'
    assert old_copy.read_bytes() == b"preserved previous recovery evidence"
    assert histories(path, "pre-migration") == []
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        instance.conn.execute("SELECT 1")
    no_staging(path)


def test_failed_snapshot_publication_preserves_old_backups_and_database(tmp_path, monkeypatch):
    path = tmp_path / "private.sqlite"
    db = Database(path)
    db.add_image(rec())
    before = {p.name: p.read_bytes() for p in tmp_path.glob("private.sqlite.backup*")}
    original_replace = Path.replace

    def refused(source, target):
        if source.name.startswith("private.sqlite.backup-stage-"):
            raise PermissionError("injected snapshot publication refusal")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", refused)
    with pytest.raises(PermissionError, match="publication refusal"):
        db.make_backup()
    assert {p.name: p.read_bytes() for p in tmp_path.glob("private.sqlite.backup*")} == before
    assert len(db.images()) == 1
    no_staging(path)


def test_recovery_retention_is_bounded_per_phase(tmp_path):
    path = tmp_path / "private.sqlite"
    for _ in range(BACKUP_HISTORY_LIMIT + 3):
        db = Database(path)
        db.conn.close()
    assert len(histories(path, "open")) == BACKUP_HISTORY_LIMIT
    assert len(histories(path, "pre-migration")) == BACKUP_HISTORY_LIMIT
    assert read_value(Path(str(path) + ".backup"), "PRAGMA quick_check") == "ok"
    no_staging(path)


def test_reindex_repairs_identity_and_metadata_in_one_commit(tmp_path):
    db = Database(tmp_path / "private.sqlite")
    img_id = db.add_image(rec(otype="nebula", imagetyp="flat", date_obs="2026-01-01T01:00:00Z",
                              session_date_obs="2026-01-02T01:00:00Z"))
    # Type edits are an independent library operation, not FITS metadata.
    db.conn.execute("UPDATE images SET otype='nebula' WHERE id=?", (img_id,))
    db.conn.commit()
    before = db.images()
    db.apply_reindex_plan(before, [rec("New/undated/lights/b.fit")],
                          [(img_id, "d" * 64, 4, 1)],
                          [(img_id, "Found/undated/flats/a.fit", "d" * 64, 4, 2)])
    repaired = db.image(img_id)
    for key, value in before[0].items():
        if key not in {"file", "content_sha256", "file_size", "file_mtime_ns"}:
            assert repaired[key] == value
    assert repaired["file"] == "Found/undated/flats/a.fit"
    assert repaired["file_mtime_ns"] == 2
    assert len(db.images()) == 2


def test_reindex_failure_rolls_back_fingerprints_relinks_and_prior_additions(tmp_path):
    db = Database(tmp_path / "private.sqlite")
    img_id = db.add_image(rec())
    db.conn.execute("CREATE TRIGGER refuse_bad BEFORE INSERT ON images WHEN NEW.orig_name='bad.fit' "
                    "BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
    db.conn.commit()
    before = db.images()
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        db.apply_reindex_plan(before, [rec("New/a.fit"), rec("New/b.fit", orig_name="bad.fit")],
                              [(img_id, "d" * 64, 4, 1)],
                              [(img_id, "Found/a.fit", "d" * 64, 4, 2)])
    assert db.images() == before


def test_reindex_refuses_concurrently_changed_metadata(tmp_path):
    db = Database(tmp_path / "private.sqlite")
    img_id = db.add_image(rec())
    scan = db.images()
    db.conn.execute("UPDATE images SET otype='galaxy' WHERE id=?", (img_id,))
    db.conn.commit()
    expected = db.images()
    with pytest.raises(RuntimeError, match="changed during reindex"):
        db.apply_reindex_plan(scan, [rec("New/a.fit")], [], [(img_id, "Found/a.fit", "d" * 64, 4, 1)])
    assert db.images() == expected


@pytest.mark.parametrize("kind", ["addition", "repair"])
def test_reindex_rejects_unsafe_paths_without_any_change(tmp_path, kind):
    db = Database(tmp_path / "private.sqlite")
    img_id = db.add_image(rec())
    before = db.images()
    additions = [rec("../outside.fit")] if kind == "addition" else []
    repairs = [(img_id, "../outside.fit", "d" * 64, 4, 1)] if kind == "repair" else []
    with pytest.raises(ValueError, match="unsafe stored path"):
        db.apply_reindex_plan(before, additions, [(img_id, "d" * 64, 4, 1)], repairs)
    assert db.images() == before


def test_rename_changes_paths_only_for_proven_published_moves(tmp_path):
    db = Database(tmp_path / "private.sqlite")
    moved_id = db.add_image(rec("First/a.fit", object_name="Blob 7"))
    retained_id = db.add_image(rec("Other/b.fit", object_name="Blob7"))
    db.rename_images("Blob 7", "Renamed", "nebula", "Renamed", {"First/a.fit": "Renamed/a.fit"})
    assert db.image(moved_id)["file"] == "Renamed/a.fit"
    assert db.image(retained_id)["file"] == "Other/b.fit"
    assert {row["object_name"] for row in db.images()} == {"Renamed"}


class StorageCancelled(Exception):
    pass


def test_reindex_cancellation_while_waiting_for_connection_lock_prevents_publication(tmp_path):
    db = Database(tmp_path / "private.sqlite")
    db.add_image(rec())
    before = db.images()
    cancel, entering = threading.Event(), threading.Event()

    def check():
        if cancel.is_set():
            raise StorageCancelled()

    def worker():
        entering.set()
        db.apply_reindex_plan(before, [rec("New/a.fit")], [], [], cancel_check=check)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.lock:
            future = pool.submit(worker)
            assert entering.wait(2)
            cancel.set()
        with pytest.raises(StorageCancelled):
            future.result(timeout=2)
    assert db.images() == before
    assert not db.conn.in_transaction


def test_reindex_cancellation_immediately_before_commit_rolls_back(tmp_path, monkeypatch):
    db = Database(tmp_path / "private.sqlite")
    before = db.images()
    cancel = threading.Event()
    insert = db._insert_image

    def insert_and_cancel(record):
        result = insert(record)
        cancel.set()
        return result

    def check():
        if cancel.is_set():
            raise StorageCancelled()

    monkeypatch.setattr(db, "_insert_image", insert_and_cancel)
    with pytest.raises(StorageCancelled):
        db.apply_reindex_plan(before, [rec("New/a.fit")], [], [], cancel_check=check)
    assert db.images() == before
    assert not db.conn.in_transaction


@pytest.mark.parametrize("existing", [False, True])
def test_import_history_failure_rolls_back_only_history_and_keeps_committed_image(tmp_path, existing):
    db = Database(tmp_path / "private.sqlite")
    record = {"created_at": "2026-10-11", "object_name": "Manual identity",
              "slug": "Object", "session": "undated", "frame": "light",
              "n_files": 1, "total_exposure": 20, "image_dir": "private archive"}
    if existing:
        db.merge_import_batch("run", record)
    before = db.import_batches()
    action = "UPDATE" if existing else "INSERT"
    db.conn.execute(f"CREATE TRIGGER reject_history BEFORE {action} ON import_batches "
                    "BEGIN SELECT RAISE(ABORT,'history failure'); END")
    db.conn.commit()
    image_id = db.add_image(rec())
    with pytest.raises(sqlite3.IntegrityError, match="history failure"):
        db.merge_import_batch("run", record)
    assert not db.conn.in_transaction
    assert db.import_batches() == before
    assert db.image(image_id) is not None
    # A following transaction must work without a sacrificial failing call.
    db.apply_reindex_plan(db.images(), [], [], [])



def test_session_fields_round_trip_and_transactional_backfill(tmp_path):
    from app.solar import session_metadata
    db = Database(tmp_path / "sessions.sqlite")
    metadata = session_metadata("2026-08-22T23:00:00Z", 52.52, 13.405)
    identifier = db.add_image(rec(date_obs="2026-08-22T23:00:00", site_lat=52.52, site_lon=13.405, **metadata))
    record = db.image(identifier)
    assert record["site_lat"] == 52.52 and record["site_lon"] == 13.405
    assert all(record[key] == value for key, value in metadata.items())
    before = db.images()
    updated = {"id":identifier, "date_obs":None, "site_lat":None, "site_lon":None,
               **session_metadata(None, None, None)}
    db.apply_reindex_plan(before, [], [], [], session_updates=[updated])
    after = db.image(identifier)
    assert all(after[key] == value for key, value in updated.items())
    assert after["file"] == before[0]["file"]


def test_reindex_session_update_failure_rolls_back_prior_fingerprint_and_relink(tmp_path):
    db = Database(tmp_path / "sessions.sqlite")
    identifier = db.add_image(rec())
    before = db.images()
    with pytest.raises(ValueError, match="unknown image"):
        db.apply_reindex_plan(before, [], [(identifier,"a"*64,10,20)],
                              [(identifier,"Relink/lights/a.fit","a"*64,10,20)],
                              session_updates=[{"id":identifier,"session_key":"undated"},
                                               {"id":identifier+100,"session_key":"undated"}])
    assert db.images() == before


def test_legacy_image_table_gains_nullable_session_columns_without_rewriting(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE images(id INTEGER PRIMARY KEY,file TEXT,orig_name TEXT,object_id TEXT,"
                       "object_name TEXT,ra_hours REAL,dec_deg REAL,exposure REAL,date_obs TEXT,"
                       "filter TEXT,telescope TEXT,ingested_at TEXT)")
    connection.execute("INSERT INTO images VALUES(1,'Original/lights/a.fit','a.fit',NULL,'Target',"
                       "NULL,NULL,60,'2026-08-22T23:00:00',NULL,NULL,'original')")
    connection.commit()
    connection.close()
    db = Database(path)
    record = db.image(1)
    assert record["file"] == "Original/lights/a.fit" and record["date_obs"] == "2026-08-22T23:00:00"
    assert all(record[key] is None for key in ("site_lat","site_lon","session_key","session_kind","session_start","session_end"))


def test_settings_failure_rolls_back_entire_request(tmp_path):
    db = Database(tmp_path / "settings.sqlite")
    with closing(db.conn):
        db.update_settings({"size_unit": "arcmin", "observing_site": None})
        db.conn.execute("CREATE TRIGGER reject_site BEFORE UPDATE ON settings "
                        "WHEN NEW.key='observing_site' "
                        "BEGIN SELECT RAISE(ABORT,'site failure'); END")
        db.conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="site failure"):
            db.update_settings({"size_unit": "deg", "observing_site": {"latitude": 50, "longitude": 8}})
        assert db.setting("size_unit") == "arcmin"
        assert db.setting("observing_site") is None
        assert not db.conn.in_transaction
        db.set_setting("size_unit", "deg")
        assert db.setting("size_unit") == "deg"
