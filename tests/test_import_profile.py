import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from scripts import profile_import as profile
from scripts import profile_populated_import as populated
from test_ingest_performance import durable_versions


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
    # With durable file versions, same-size files this import published are
    # known from the bytes written and none is read back. Without them (no
    # change journal on the volume) each is read once, as before.
    profile.harness.SCRATCH.mkdir(parents=True, exist_ok=True)
    durable = durable_versions(profile.harness.SCRATCH) or os.name != 'nt'
    assert (counters.get('archive_hashes_completed', 0) == 0) == durable
    assert counters.get('archive_sha256_bytes', 0) == counters.get('completed_archive_bytes', 0)
    assert result['profile']['phases']['database_commit']['calls'] == 5


def test_populated_profile_audits_mixed_representations_and_error_boundaries(tmp_path):
    output = tmp_path / 'populated.json'
    populated.run(SimpleNamespace(count=24, output=output))
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'passed' and result['owned_process_and_scratch_cleanup']
    assert result['archived_files_verified'] == 36
    assert result['new_images'] == result['history_units'] == 12
    assert result['seed']['rice'] == 3 and len(result['seed']['sizes']) >= 3
    assert result['seed_metadata_preserved'] and result['restored_mtime_edit_detected']
    assert result['missing_original_restored'] and result['malformed_input_isolated']
    assert result['exact_rice_restoration'] and result['staging_leftovers'] == 0
    rice = result['waves']['new-safe-rice']['profile']
    assert rice['phases']['rice_encode_verify']['calls'] == 3
    assert rice['phases']['native_tool']['calls'] == 6
    assert rice['phases']['positive_preflight']['calls'] == 3
    assert result['restoration_profile']['phases']['native_tool']['calls'] == 1
