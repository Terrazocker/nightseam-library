"""Integrity checks/resolutions protect originals, baselines and stale findings."""
import hashlib
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import archive_health
from app.database import Database
from app.server import create_app


@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path / "health.db")
    root = tmp_path / "archive"
    db.set_setting("image_dir", str(root))
    return db, root


def add_record(db, root, relative="M31/session/lights/a.fit", data=b"original exposure", baseline=True):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    info = target.stat()
    image_id = db.add_image(dict(file=relative, orig_name=target.name, object_name="M31",
        ingested_at="today", content_sha256=hashlib.sha256(data).hexdigest() if baseline else None,
        file_size=info.st_size, file_mtime_ns=info.st_mtime_ns))
    return image_id, target


def snapshot(db):
    with db.lock: return tuple(db.conn.iterdump())


def audit(db, mode="quick", cancel=None, publish=None):
    return archive_health.scan_archive(db, mode, cancel or threading.Event(), publish or (lambda report: None))


def wait_for_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/library/health/{job_id}")
        assert response.status_code == 200
        result = response.json()
        if result["status"] not in ("running", "cancelling"): return result
        time.sleep(.01)
    pytest.fail("Archive health worker did not finish")


def scan(client, mode="deep"):
    response = client.post("/api/library/health", json={"mode": mode})
    assert response.status_code == 202
    return wait_for_job(client, response.json()["id"])


def acknowledge(client, report, image_id, action):
    return client.post(f"/api/library/health/{report['id']}/acknowledge", json={
        "image_id": image_id, "action": action, "confirmed": True})


def test_session_and_all_deletions_preserve_present_members_and_changed_contents(library):
    db, root = library
    ids = []
    for index in range(4):
        image_id, path = add_record(db, root, f'M31/session/lights/{index}.fit')
        ids.append(image_id)
        if index < 3:
            path.unlink()
        else:
            path.write_bytes(b'changed contents')
    # Explicit calibration membership wins over its unrelated capture time.
    db.conn.execute("UPDATE images SET session_key='night:A',session_kind='night',session_start='2026-08-22T20:00:00' WHERE id IN (?,?,?)", ids[:3])
    db.conn.execute("UPDATE images SET date_obs='2030-01-01T12:00:00',imagetyp='dark',session_date_obs='2026-08-22T22:00:00' WHERE id=?", (ids[2],))
    other, path = add_record(db, root, 'M42/session/lights/other.fit')
    path.unlink()
    db.conn.execute("UPDATE images SET object_name='M42',session_key='night:A',session_kind='night' WHERE id=?", (other,))
    present, _ = add_record(db, root, 'M31/session/lights/healthy.fit')
    db.conn.execute("UPDATE images SET session_key='night:A',session_kind='night',session_start='2026-08-22T20:00:00' WHERE id IN (?,?)", (present, ids[3]))
    db.conn.commit()
    with TestClient(create_app(db)) as client:
        report = scan(client)
        assert len(report['deletion_sessions']) == 2
        session = next(group for group in report['deletion_sessions'] if group['object_name'] == 'M31')
        assert session['pending_count'] == 3
        payload = dict(scope='session', session_id=session['id'], action='forget_missing', confirmed=True)
        result = client.post(f"/api/library/health/{report['id']}/acknowledge", json=payload)
        assert result.status_code == 200 and result.json()['pending_deletions'] == 1
        assert {rec['id'] for rec in db.images()} == {ids[3], present, other}
        assert client.post(f"/api/library/health/{report['id']}/acknowledge", json=payload).status_code == 200
        result = client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(
            scope='all', action='forget_missing', confirmed=True))
        assert result.status_code == 200 and result.json()['pending_deletions'] == 0
        assert {rec['id'] for rec in db.images()} == {ids[3], present}
        assert db.conn.execute('SELECT COUNT(*) FROM archive_health_actions').fetchone()[0] == 4
        assert client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(
            scope='all', action='accept_contents', confirmed=True)).status_code == 409


@pytest.mark.parametrize('change', ['restored', 'catalog', 'transaction'])
def test_batch_deletions_are_atomic_when_any_member_changes(library, change):
    db, root = library
    first, path = add_record(db, root); path.unlink()
    second, path = add_record(db, root, 'M31/session/lights/b.fit'); path.unlink()
    with TestClient(create_app(db)) as client:
        report = scan(client)
        if change == 'restored':
            path.write_bytes(b'restored')
        elif change == 'catalog':
            db.conn.execute('UPDATE images SET orig_name=? WHERE id=?', ('edited.fit', second)); db.conn.commit()
        else:
            db.conn.execute(f"CREATE TRIGGER reject_second BEFORE DELETE ON images WHEN OLD.id={second} BEGIN SELECT RAISE(ABORT,'batch failure'); END"); db.conn.commit()
        before = snapshot(db)
        if change == 'transaction':
            with pytest.raises(Exception, match='batch failure'):
                client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(scope='all', action='forget_missing', confirmed=True))
        else:
            assert client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(scope='all', action='forget_missing', confirmed=True)).status_code == 409
        assert snapshot(db) == before
        assert len(db.images()) == 2


def test_accept_all_includes_findings_beyond_display_limit(library):
    db, root = library
    for index in range(205):
        _, path = add_record(db, root, f'M31/session/lights/{index}.fit'); path.unlink()
    with TestClient(create_app(db)) as client:
        report = scan(client, 'quick')
        assert len(report['findings']) == 200 and report['pending_deletions'] == 205
        result = client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(scope='all', action='forget_missing', confirmed=True))
        assert result.status_code == 200 and result.json()['acknowledged_count'] == 205
        assert not db.images()


def test_accept_all_leaves_omitted_findings_for_next_scan(library, monkeypatch):
    db, root = library
    monkeypatch.setattr(archive_health, 'MAX_FINDINGS', 2)
    for index in range(3):
        _, path = add_record(db, root, f'M31/session/lights/{index}.fit'); path.unlink()
    with TestClient(create_app(db)) as client:
        report = scan(client, 'quick')
        assert report['omitted_findings'] == 1 and report['pending_deletions'] == 2
        result = client.post(f"/api/library/health/{report['id']}/acknowledge", json=dict(scope='all', action='forget_missing', confirmed=True))
        assert result.status_code == 200 and result.json()['acknowledged_count'] == 2
        assert len(db.images()) == 1
        assert scan(client, 'quick')['pending_deletions'] == 1


def test_quick_metadata_is_not_content_verification_and_deep_is_read_only(library):
    db, root = library
    _, target = add_record(db, root)
    before = snapshot(db), target.read_bytes(), target.stat().st_mtime_ns
    quick, deep = audit(db), audit(db, "deep")
    assert quick["metadata_unchanged"] == 1 and quick["verified"] == 0 and quick["unverified"] == 1
    assert quick["bytes_read"] == 0 and not quick["findings"]
    assert deep["verified"] == 1 and deep["bytes_read"] == len(before[1]) and not deep["findings"]
    assert before == (snapshot(db), target.read_bytes(), target.stat().st_mtime_ns)


@pytest.mark.parametrize("mode", ["quick", "deep"])
@pytest.mark.parametrize("malformed", [
    hashlib.sha256(b"original exposure").hexdigest().encode("ascii"),
    b"\xff\x00invalid hash",
], ids=["ascii-blob", "non-utf8-blob"])
def test_blob_baseline_is_unverified_without_aborting_other_records(library, monkeypatch, mode, malformed):
    from app import discovery_spool

    db, root = library
    image_id, damaged = add_record(db, root)
    healthy_id, healthy = add_record(db, root, "M31/session/lights/healthy.fit")
    db.conn.execute("UPDATE image_integrity SET sha256=? WHERE image_id=?", (malformed, image_id))
    db.conn.commit()
    before = snapshot(db), damaged.read_bytes(), healthy.read_bytes()
    scratch = root.parent / "health-scratch"
    scratch.mkdir()
    monkeypatch.setattr(discovery_spool, "logs_dir", lambda: scratch)

    with TestClient(create_app(db)) as client:
        report = scan(client, mode)
        assert report["status"] == "completed"
        assert report["total"] == report["processed"] == 2
        assert report["finding_counts"] == {"no_baseline": 1}
        assert report["verified"] == (mode == "deep")
        assert report["metadata_unchanged"] == (mode == "quick")
        assert report["unverified"] == 2 - report["verified"]
        finding, = report["findings"]
        assert finding["image_id"] == image_id != healthy_id
        assert finding["code"] == "no_baseline"
        observed = hashlib.sha256(damaged.read_bytes()).hexdigest() if mode == "deep" else None
        assert finding["observed_sha256"] == observed
        assert "_record" not in finding and "expected_sha256" not in finding
        assert report["pending_deletions"] == 0
        assert acknowledge(client, report, image_id, "accept_contents").status_code == 409
    assert before == (snapshot(db), damaged.read_bytes(), healthy.read_bytes())
    stored = db.conn.execute("SELECT sha256 FROM image_integrity WHERE image_id=?", (image_id,)).fetchone()
    assert stored[0] == malformed
    assert not list(scratch.iterdir())
    assert db.archive_lock.acquire(blocking=False)
    db.archive_lock.release()


def test_deep_detects_changes_with_same_size_and_time(library):
    db, root = library
    _, target = add_record(db, root, data=b"AAAA")
    info = target.stat()
    target.write_bytes(b"BBBB")
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    before = snapshot(db)
    assert audit(db)["metadata_unchanged"] == 1
    deep = audit(db, "deep")
    assert deep["finding_counts"] == {"content_changed": 1} and deep["verified"] == 0
    assert deep["findings"][0]["expected_sha256"] == hashlib.sha256(b"AAAA").hexdigest()
    assert snapshot(db) == before


def test_metadata_change_with_same_contents_still_verifies_deep(library):
    db, root = library
    _, target = add_record(db, root)
    info = target.stat()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    assert audit(db)["finding_counts"] == {"metadata_changed": 1}
    assert audit(db, "deep")["verified"] == 1


def test_fingerprint_cache_refresh_preserves_baseline_across_reopen(library):
    db, root = library
    image_id, target = add_record(db, root)
    expected = db.integrity_snapshot()[1][0]["expected_sha256"]
    target.write_bytes(b"external edit")
    info = target.stat()
    db.set_image_fingerprint(image_id, hashlib.sha256(target.read_bytes()).hexdigest(), info.st_size, info.st_mtime_ns)
    reopened = Database(db.path)
    assert reopened.integrity_snapshot()[1][0]["expected_sha256"] == expected
    assert audit(reopened, "deep")["finding_counts"] == {"content_changed": 1}


def test_migration_labels_historical_fingerprint(library):
    db, root = library
    image_id, _ = add_record(db, root)
    db.conn.execute("DELETE FROM image_integrity WHERE image_id=?", (image_id,)); db.conn.commit()
    reopened = Database(db.path)
    deep = audit(reopened, "deep")
    assert deep["verified"] == 1 and deep["baseline_sources"] == {"existing_fingerprint": 1}


def test_reindex_cannot_accept_changed_contents(library):
    from _util import m31_fits
    db, root = library
    with TestClient(create_app(db)) as client:
        data = m31_fits()
        result = client.post("/api/images/ingest", files={"files": ("a.fit", data, "application/fits")}).json()["results"][0]
        assert result["ok"]
        (root / result["stored"]).write_bytes(data + b"external edit")
        assert client.post("/api/library/reindex").json()["skipped"] == 1
        row = db.integrity_snapshot()[1][0]
        assert row["expected_sha256"] == hashlib.sha256(data).hexdigest() != row["content_sha256"]
        assert audit(db, "deep")["finding_counts"] == {"content_changed": 1}


def test_missing_untracked_and_no_baseline_are_distinct_and_read_only(library):
    db, root = library
    _, target = add_record(db, root); target.unlink()
    add_record(db, root, "legacy.fit", baseline=False)
    (root / "unindexed.FITS").write_bytes(b"new")
    before = snapshot(db)
    deep = audit(db, "deep")
    assert deep["finding_counts"] == {"missing": 1, "no_baseline": 1, "untracked": 1}
    assert deep["processed"] == deep["total"] == deep["unverified"] == 3 and snapshot(db) == before


def test_unavailable_archive_never_creates_it_or_reports_mass_missing(library):
    db, root = library
    db.add_image(dict(file="missing.fit", orig_name="missing.fit", object_name="M31", ingested_at="today"))
    result = audit(db)
    assert result["status"] == "unavailable" and result["finding_counts"] == {"archive_unavailable": 1}
    assert result["processed"] == 0 and not root.exists()


def test_unsafe_paths_and_links_are_not_opened(library, tmp_path, monkeypatch):
    db, root = library; root.mkdir()
    outside = tmp_path / "outside.fit"; outside.write_bytes(b"private")
    (root / "linked.fit").symlink_to(outside)
    for name in ("../outside.fit", "linked.fit"):
        db.conn.execute("INSERT INTO images(file,orig_name,object_name,ingested_at) VALUES(?,?,?,?)", (name, "bad", "M31", "today"))
    db.conn.commit()
    original = os.open
    def guarded(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).suffix == ".fit":
            assert Path(path).resolve() != outside
        return original(path, *args, **kwargs)
    monkeypatch.setattr(archive_health.os, "open", guarded)
    assert audit(db, "deep")["finding_counts"] == {"unsafe_path": 2}


def test_permission_failure_is_a_finding(library, monkeypatch):
    db, root = library
    _, target = add_record(db, root)
    original = os.open
    def refused(path, *args, **kwargs):
        if path == target: raise PermissionError("refused")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(archive_health.os, "open", refused)
    assert audit(db, "deep")["finding_counts"] == {"unreadable": 1}


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptor regression")
def test_discovery_is_anchored_during_directory_swap(library, tmp_path, monkeypatch):
    db, root = library
    child = root / "child"; child.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "private.fit").write_bytes(b"private")
    inode = child.stat().st_ino
    original = os.scandir
    swapped = False
    def swapped_scandir(fd):
        nonlocal swapped
        if isinstance(fd, int) and os.fstat(fd).st_ino == inode and not swapped:
            swapped = True
            child.rename(root / "old-child"); child.symlink_to(outside, target_is_directory=True)
        return original(fd)
    monkeypatch.setattr(archive_health.os, "scandir", swapped_scandir)
    result = audit(db, "deep")
    assert swapped and result["bytes_read"] == 0
    assert all("private.fit" not in item["path"] for item in result["findings"])


def test_root_replacement_during_scan_is_unavailable(library):
    db, root = library; add_record(db, root)
    def replace(report):
        if report["phase"] == "checking":
            root.rename(root.parent / "old-archive"); root.mkdir()
    result = audit(db, "deep", publish=replace)
    assert result["status"] == "unavailable" and result["finding_counts"] == {"archive_unavailable": 1}


def test_cancellation_and_mutation_during_hash_are_not_verified(library, monkeypatch):
    db, root = library
    _, target = add_record(db, root, data=b"x" * (3 * 1024 * 1024))
    event = threading.Event(); original = hashlib.sha256
    class CancelAfterChunk:
        def __init__(self): self.hash = original()
        def update(self, data): self.hash.update(data); event.set()
        def hexdigest(self): return self.hash.hexdigest()
    before = snapshot(db)
    monkeypatch.setattr(archive_health.digests, "sha256", CancelAfterChunk)
    result = audit(db, "deep", event)
    assert result["status"] == "cancelled" and result["verified"] == result["processed"] == 0
    assert result["bytes_read"] == 1024 * 1024 and snapshot(db) == before
    class MutatingHash:
        def __init__(self): self.hash = original()
        def update(self, data): self.hash.update(data); target.write_bytes(b"changed")
        def hexdigest(self): return self.hash.hexdigest()
    monkeypatch.setattr(archive_health.digests, "sha256", MutatingHash)
    assert audit(db, "deep")["finding_counts"] == {"unstable": 1}


def test_mutation_during_hash_with_restored_size_and_mtime_is_unstable(library, monkeypatch):
    db, root = library
    _, target = add_record(db, root, data=b"AAAA")
    info = target.stat()
    original = hashlib.sha256

    class MutatingHash:
        def __init__(self): self.hash = original()
        def update(self, data):
            self.hash.update(data)
            time.sleep(.01)
            target.write_bytes(b"BBBB")
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        def hexdigest(self): return self.hash.hexdigest()

    monkeypatch.setattr(archive_health.digests, "sha256", MutatingHash)
    report = audit(db, "deep")
    assert report["verified"] == 0 and report["finding_counts"] == {"unstable": 1}


def test_retention_limit_counts_omitted_findings(library, monkeypatch):
    db, root = library; root.mkdir()
    for i in range(3): (root / f"untracked-{i}.fit").write_bytes(b"x")
    monkeypatch.setattr(archive_health, "MAX_FINDINGS", 2)
    report = audit(db)
    assert report["finding_counts"] == {"untracked": 3} and len(report["findings"]) == 2 and report["omitted_findings"] == 1


def test_api_cancel_conflict_report_and_origin_guard(library):
    db, root = library; add_record(db, root)
    app = create_app(db)
    with TestClient(app) as client:
        assert client.get("/api/library/health").json() is None
        assert client.post("/api/library/health", json={"mode": "bad"}).status_code == 422
        assert client.post("/api/library/health", json={"mode": "quick"}, headers={"Origin": "https://example.com"}).status_code == 403
        with db.archive_lock:
            job = client.post("/api/library/health", json={"mode": "deep"}).json()
            assert client.post("/api/library/health", json={"mode": "quick"}).status_code == 409
            assert client.get("/api/state").status_code == 200
            assert client.get(f"/api/library/health/{job['id']}/report").status_code == 409
            client.post(f"/api/library/health/{job['id']}/cancel")
            result = wait_for_job(client, job["id"])
        assert result["status"] == "cancelled" and result["processed"] == 0
        report = client.get(f"/api/library/health/{job['id']}/report")
        assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
        assert "attachment" in report.headers["content-disposition"]
        assert scan(client)["verified"] == 1
        assert client.get(f"/api/library/health/{job['id']}").status_code == 404


@pytest.mark.parametrize("action", ["forget_missing", "accept_contents"])
def test_acknowledgement_resolves_only_selected_record_and_persists(library, action):
    db, root = library
    image_id, target = add_record(db, root)
    other_id, other = add_record(db, root, "other.fit")
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    if action == "forget_missing": target.unlink()
    else: target.write_bytes(b"intentional edit")
    with TestClient(create_app(db)) as client:
        report = scan(client)
        assert "_record" not in report["findings"][0]
        before_bytes = target.read_bytes() if target.exists() else None
        response = acknowledge(client, report, image_id, action)
        assert response.status_code == 200 and response.json()["acknowledged_count"] == 1
        assert not response.json()["acknowledging"]
        saved = snapshot(db)
        assert acknowledge(client, report, image_id, action).status_code == 200 and saved == snapshot(db)
        downloaded = client.get(f"/api/library/health/{report['id']}/report").json()
        assert downloaded["findings"][0]["acknowledged_at"] and "_record" not in downloaded["findings"][0]
        assert (target.read_bytes() if target.exists() else None) == before_bytes
        assert db.image(other_id) is not None and other.read_bytes() == b"original exposure"
        assert scan(client, "quick")["finding_counts"] == {} and scan(client)["finding_counts"] == {}
    history = dict(db.conn.execute("SELECT * FROM archive_health_actions").fetchone())
    assert history["previous_sha256"] == original
    reopened = Database(db.path)
    if action == "forget_missing":
        assert reopened.image(image_id) is None and history["accepted_sha256"] is None
    else:
        row = next(row for row in reopened.integrity_snapshot()[1] if row["id"] == image_id)
        assert row["expected_sha256"] == row["content_sha256"] == history["accepted_sha256"]
        assert row["baseline_source"] == "user_accepted"


@pytest.mark.parametrize("action", ["forget_missing", "accept_contents"])
@pytest.mark.parametrize("change", ["file", "record", "baseline", "archive_setting", "archive_replaced", "archive_gone", "link"])
def test_stale_or_unsafe_acknowledgement_is_refused(library, tmp_path, action, change):
    db, root = library
    image_id, target = add_record(db, root)
    if action == "forget_missing": target.unlink()
    else: target.write_bytes(b"expected edit")
    with TestClient(create_app(db)) as client:
        report = scan(client)
        if change == "file": target.write_bytes(b"restored or changed again")
        elif change == "record": db.conn.execute("UPDATE images SET file='renamed.fit' WHERE id=?", (image_id,)); db.conn.commit()
        elif change == "baseline": db.conn.execute("UPDATE image_integrity SET sha256=? WHERE image_id=?", ("a" * 64, image_id)); db.conn.commit()
        elif change == "archive_setting":
            other = tmp_path / "other"; other.mkdir(); db.set_setting("image_dir", str(other))
        elif change in ("archive_replaced", "archive_gone"):
            root.rename(tmp_path / "old-root")
            if change == "archive_replaced": root.mkdir()
        elif change == "link":
            if target.exists(): target.unlink()
            outside = tmp_path / "private.fit"; outside.write_bytes(b"private"); target.symlink_to(outside)
        before = snapshot(db)
        response = acknowledge(client, report, image_id, action)
        assert response.status_code == 409, response.text
        assert snapshot(db) == before and not client.get(f"/api/library/health/{report['id']}").json()["acknowledging"]


@pytest.mark.parametrize("action", ["forget_missing", "accept_contents"])
def test_resolution_and_audit_history_roll_back_together(library, action):
    db, root = library
    image_id, target = add_record(db, root)
    if action == "forget_missing": target.unlink()
    else: target.write_bytes(b"expected edit")
    with TestClient(create_app(db), raise_server_exceptions=False) as client:
        report = scan(client)
        db.conn.execute("CREATE TRIGGER refuse_resolution BEFORE INSERT ON archive_health_actions "
                        "BEGIN SELECT RAISE(ABORT,'injected failure'); END"); db.conn.commit()
        before = snapshot(db)
        assert acknowledge(client, report, image_id, action).status_code == 500
        assert snapshot(db) == before
        assert client.get(f"/api/library/health/{report['id']}").json()["acknowledged_count"] == 0


def test_confirmation_and_operation_conflicts(library):
    db, root = library
    image_id, target = add_record(db, root); target.unlink()
    app = create_app(db)
    with TestClient(app) as client:
        report = scan(client)
        path = f"/api/library/health/{report['id']}/acknowledge"
        body = {"image_id": image_id, "action": "forget_missing", "confirmed": True}
        assert client.post(path, json={**body, "confirmed": False}).status_code == 422
        assert client.post(path, json={"image_id": image_id, "action": "forget_missing"}).status_code == 422
        assert client.post(path, json=body, headers={"Origin": "https://example.com"}).status_code == 403
        assert acknowledge(client, {"id": "expired"}, image_id, "forget_missing").status_code == 404
        with db.archive_lock:
            assert acknowledge(client, report, image_id, "forget_missing").status_code == 409
        with app.state.archive_health.operation_lock:
            assert client.post("/api/library/health", json={"mode": "quick"}).status_code == 409
            assert acknowledge(client, report, image_id, "forget_missing").status_code == 409
        assert db.image(image_id) is not None


def test_shutdown_cancels_waiting_worker(library):
    db, root = library; add_record(db, root)
    app = create_app(db)
    with db.archive_lock:
        with TestClient(app) as client:
            started = client.post("/api/library/health", json={"mode": "deep"}).json()
            assert acknowledge(client, started, 1, "forget_missing").status_code == 409
        assert app.state.archive_health.snapshot()["status"] == "cancelled"
        assert not app.state.archive_health.thread.is_alive()
