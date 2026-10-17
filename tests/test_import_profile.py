import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from scripts import profile_import as profile


def test_profile_measures_workers_nested_cpu_and_actual_hash_bytes():
    measurements = profile.Measurements()
    proxy = profile.HashProxy(measurements)
    original = hashlib.sha256
    measurements.start()
    def worker(_):
        digest = proxy.sha256()  # Constructed outside its measured copy scope.
        with measurements.phase('publication'):
            with measurements.phase('incoming_copy_hash'):
                digest.update(b'a' * 100_000)
                digest.update(b'b')
            with measurements.phase('archive_hash'):
                archived = proxy.sha256(b'a' * 100_000 + b'b')
            assert digest.digest() == archived.digest()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, range(6)))
    proxy.sha256(b'session identity')
    result = measurements.finish()
    assert hashlib.sha256 is original
    assert result['counters']['incoming_sha256_bytes'] == 600_006
    assert result['counters']['archive_sha256_bytes'] == 600_006
    assert result['counters']['incoming_sha256_streams'] == 6
    assert result['counters']['other_sha256_bytes'] == 16
    phases = result['phases']
    assert phases['publication']['calls'] == 6
    inclusive = phases['publication']['thread_cpu_seconds']
    exclusive = sum(value['exclusive_thread_cpu_seconds'] for value in phases.values())
    assert abs(inclusive - exclusive) < 1e-6
    measurements.add(incoming_sha256_bytes=999)
    assert measurements.finish()['counters'] == result['counters']


def test_profile_real_private_http_import_excludes_audits_and_cleans_up(tmp_path):
    output = tmp_path / 'profile.json'
    profile.run(SimpleNamespace(count=5, batch_size=3, output=output))
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'passed'
    assert result['owned_process_and_scratch_cleanup']
    assert result['import']['same_size_restored_mtime_edit_detected']
    assert result['import']['source_and_archive_hashes_verified'] == 5
    counters = result['profile']['counters']
    assert counters['incoming_sha256_bytes'] == 5 * profile.bench.FILE_BYTES
    assert counters['incoming_sha256_streams'] == counters['sql_commit'] == 5
    assert counters['archive_hashes_completed'] > 0
    assert counters['archive_sha256_bytes'] == counters['completed_archive_bytes']
    assert result['profile']['phases']['database_commit']['calls'] == 5
