"""Retired actions stay unavailable; previously retained originals stay recoverable."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, UTC

import pytest
from fastapi.testclient import TestClient

from app import archive_trash as recovery
from app.archive_health import scan_archive
from app.database import Database
from app.ingest import reindex_library
from app.server import create_app
import threading


@pytest.fixture
def retained(tmp_path):
    db=Database(tmp_path/'catalog.db')
    root=tmp_path/'archive'; root.mkdir()
    db.set_setting('image_dir',str(root))
    data=b'previously retained original exposure bytes'
    relative='M31/2026-10-13/lights/a.fit'
    path=root/relative; path.parent.mkdir(parents=True); path.write_bytes(data)
    info=path.stat()
    image_id=db.add_image(dict(file=relative,orig_name='a.fit',object_name='M31',ingested_at='2026-10-13',
        date_obs='2026-10-13T01:10:00',session_date_obs='2026-10-13T01:15:00Z',imagetyp='light',
        content_sha256=hashlib.sha256(data).hexdigest(),file_size=len(data),file_mtime_ns=info.st_mtime_ns))
    image,baseline=recovery._snapshot(db,image_id)
    root_info=root.stat()
    entry=dict(token='retained-one',archive=str(root),root_dev=str(root_info.st_dev),root_ino=str(root_info.st_ino),
        image_json=json.dumps(image),integrity_json=json.dumps(baseline),payload_sha256=hashlib.sha256(data).hexdigest(),
        payload_size=len(data),state='trashed',trashed_at=datetime.now(UTC).isoformat())
    folder=root/recovery.TRASH_FOLDER/entry['token']; folder.mkdir(parents=True)
    (folder.parent/'format.json').write_bytes(recovery.MARKER)
    manifest=dict(format='nightseam-exposure-trash',version=1,**entry); manifest['state']='pending_trash'
    (folder/'metadata.json').write_text(json.dumps(manifest),encoding='utf-8')
    path.rename(folder/'payload.bin')
    with db.lock:
        db.conn.execute('DELETE FROM image_integrity WHERE image_id=?',(image_id,))
        db.conn.execute('DELETE FROM images WHERE id=?',(image_id,))
        db.conn.execute(f"INSERT INTO image_trash ({','.join(entry)}) VALUES ({','.join('?' for _ in entry)})",tuple(entry.values()))
        db.conn.commit()
    return db,root,path,data,image,baseline,entry


def test_retired_application_endpoints_preserve_retained_files(retained):
    db,root,path,data,image,baseline,entry=retained
    with TestClient(create_app(db)) as client:
        for method,url in [('get',f'/api/images/{image["id"]}/preview'),('get','/api/library/trash'),
                           ('post',f'/api/images/{image["id"]}/trash'),('post',f'/api/library/trash/{entry["token"]}/restore')]:
            response=getattr(client,method)(url,**({'json':{'confirmed':True}} if method=='post' else {}))
            assert response.status_code in (404,405)  # unregistered route/static mount
    assert not path.exists() and (root/recovery._payload(entry)).read_bytes()==data
    assert db.image(image['id']) is None and recovery._entries(db)[0]['state']=='trashed'
    assert not hasattr(recovery,'trash_image')


def test_explicit_maintenance_restore_preserves_metadata_id_and_bytes(retained):
    db,root,path,data,image,baseline,entry=retained
    assert recovery.list_trash(db)['items'][0]['can_restore']
    recovery.restore_image(db,entry['token'])
    assert path.read_bytes()==data and recovery._snapshot(db,image['id'])==(image,baseline)
    assert recovery.list_trash(db)['items']==[]


@pytest.mark.parametrize('tamper',['payload','manifest','occupied'])
def test_legacy_recovery_refuses_conflicts_without_discarding_bytes(retained,tamper):
    db,root,path,data,image,baseline,entry=retained
    target=root/recovery._payload(entry)
    if tamper=='payload': target.write_bytes(b'changed retained file')
    elif tamper=='manifest': target.with_name('metadata.json').write_bytes(b'invalid metadata')
    else: path.write_bytes(b'unrelated current file')
    before=target.read_bytes()
    with pytest.raises(ValueError): recovery.restore_image(db,entry['token'])
    assert target.read_bytes()==before and db.image(image['id']) is None
    if tamper=='occupied': assert path.read_bytes()==b'unrelated current file'


def test_failed_legacy_restore_rolls_back_file(retained,monkeypatch):
    db,root,path,data,image,baseline,entry=retained
    def refused(*args): raise sqlite3.OperationalError('injected publication failure')
    monkeypatch.setattr(recovery,'_publish_restore',refused)
    with pytest.raises(sqlite3.OperationalError): recovery.restore_image(db,entry['token'])
    assert not path.exists() and (root/recovery._payload(entry)).read_bytes()==data
    assert recovery._entries(db)[0]['state']=='trashed'


@pytest.mark.parametrize('state',['pending_trash','pending_restore'])
def test_pending_legacy_operations_reconcile_on_startup(retained,state):
    db,root,path,data,image,baseline,entry=retained
    with db.lock:
        if state=='pending_trash':
            recovery._publish_restore(db,entry,image,baseline)
        else:
            (root/recovery._payload(entry)).rename(path)
        db.conn.execute('UPDATE image_trash SET state=?',(state,)); db.conn.commit()
    with TestClient(create_app(db)):
        if state=='pending_trash':
            assert path.read_bytes()==data and recovery._snapshot(db,image['id'])==(image,baseline)
            assert recovery._entries(db)==[]
        else:
            assert not path.exists() and (root/recovery._payload(entry)).read_bytes()==data
            assert recovery._entries(db)[0]['state']=='trashed'


def test_archive_switch_never_restores_into_another_archive(retained,tmp_path):
    db,root,path,data,image,baseline,entry=retained
    other=tmp_path/'other'; other.mkdir(); db.set_setting('image_dir',str(other))
    with pytest.raises(ValueError): recovery.restore_image(db,entry['token'])
    assert not list(other.iterdir()) and (root/recovery._payload(entry)).read_bytes()==data


def test_legacy_payloads_remain_excluded_from_scans(retained):
    db,root,path,data,image,baseline,entry=retained
    assert reindex_library(db,root)['found']==0
    report=scan_archive(db,'deep',threading.Event(),lambda _:None)
    assert report['total']==0 and not report['findings']
    assert (root/recovery._payload(entry)).read_bytes()==data


def test_maintenance_command_lists_only_explicit_database(retained):
    db,root,path,data,image,baseline,entry=retained
    result=subprocess.run([sys.executable,'scripts/restore_legacy_trash.py','--database',str(db.path)],
                          capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['items'][0]['token']==entry['token']
    assert not path.exists() and (root/recovery._payload(entry)).read_bytes()==data
