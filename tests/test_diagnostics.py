import hashlib
import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app import compression, diagnostics, server
from test_compression import library


def test_error_summaries_are_bounded_and_never_retain_raw_log_fields():
    collector = diagnostics.ErrorCollector(limit=2)
    logger = logging.Logger('app.server')
    logger.addHandler(collector)
    private = r'C:\Users\PRIVATE_MARKER\private-image.fits'
    for index in range(3):
        logger.error('PRIVATE_MARKER %s', private,
                     exc_info=(ValueError, ValueError(private), None),
                     extra={'private_extra': private})
    events = collector.snapshot()
    assert len(events) == 2
    assert all(event['code'] == 'catalog_operation' and
               event['exception_kind'] == 'ValueError' for event in events)
    assert 'PRIVATE_MARKER' not in json.dumps(events)
    assert 'private-image' not in json.dumps(events)
    events[0]['summary'] = 'caller modification'
    assert collector.snapshot()[0]['summary'] != 'caller modification'


def test_error_collector_installation_is_idempotent():
    logger = logging.Logger('isolated-diagnostics-test')
    first = diagnostics.install_error_collector(logger)
    assert diagnostics.install_error_collector(logger) is first
    assert len(logger.handlers) == 1


def test_tool_report_verifies_actual_hashes_and_never_exports_provenance_paths(tmp_path, monkeypatch):
    root = tmp_path / 'vendor/fits-tools/linux-x64'
    root.mkdir(parents=True)
    provenance = {'cfitsio': '4.7.0', 'zlib': '1.3.2',
                  'private_build_path': str(tmp_path), 'binaries': {}}
    for name in ('fpack', 'funpack'):
        raw = name.encode('ascii')
        (root / name).write_bytes(raw)
        provenance['binaries'][name] = {'sha256': hashlib.sha256(raw).hexdigest()}
    (root / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
    monkeypatch.setattr(diagnostics, 'resource_dir', lambda: tmp_path)
    monkeypatch.setattr(compression, 'resource_dir', lambda: tmp_path)
    monkeypatch.setattr(compression, 'tool_bundle', lambda: ('linux-x64', ''))
    monkeypatch.setattr(compression, '_TOOLS_CACHE', None)
    monkeypatch.setattr(compression, 'native', lambda *args: (_ for _ in ()).throw(AssertionError('must not execute')))
    result = diagnostics.fits_tools_report()
    assert result == {'bundle': 'linux-x64', 'integrity': 'verified',
                      'cfitsio_version': '4.7.0', 'zlib_version': '1.3.2'}
    assert str(tmp_path) not in json.dumps(result)
    (root / 'fpack').write_bytes(b'changed executable')
    assert diagnostics.fits_tools_report()['integrity'] == 'verification_failed'
    (root / 'funpack').unlink()
    assert diagnostics.fits_tools_report()['integrity'] == 'missing'
    def unsupported():
        raise ValueError('PRIVATE_MARKER unsupported system')
    monkeypatch.setattr(compression, 'tool_bundle', unsupported)
    assert diagnostics.fits_tools_report() == {'bundle': None, 'integrity': 'unsupported'}


def test_report_filters_versions_and_source_frozen_state(monkeypatch):
    monkeypatch.setattr(diagnostics, 'app_version', lambda: '0.1.0-g1234abcd')
    monkeypatch.setattr(diagnostics.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(diagnostics.metadata, 'version', lambda name: '/PRIVATE_MARKER/install')
    monkeypatch.setattr(diagnostics, 'fits_tools_report', lambda: {'integrity': 'missing'})
    report = diagnostics.report()
    assert report['application'] == {'version': '0.1.0-g1234abcd', 'frozen': True}
    assert set(report['runtime']['packages'].values()) == {'unavailable'}
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_diagnostics_download_is_guarded_uncached_and_independent_of_archive(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    with TestClient(server.create_app(db)) as client:
        with monkeypatch.context() as context:
            def forbidden(*args, **kwargs):
                raise AssertionError('Diagnostics must not inspect library or archive settings')
            context.setattr(db, 'image_dir', forbidden)
            context.setattr(db, 'images', forbidden)
            response = client.get('/api/diagnostics')
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'nightseam-support-report.json' in response.headers['Content-Disposition']
        assert response.json()['fits_tools']['integrity'] == 'verified'
        assert str(archive) not in response.text and str(tmp_path) not in response.text
        assert client.get('/api/diagnostics', headers={'Host': 'evil.invalid'}).status_code == 403
        assert client.get('/api/diagnostics', headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert not list(archive.iterdir())
