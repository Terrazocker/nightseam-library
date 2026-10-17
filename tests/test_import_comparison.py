from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts import compare_import as compare


def report(seconds=10):
    return dict(status='passed', executable_sha256='verified', owned_process_and_scratch_cleanup=True,
        restricted_runtime_rice=dict(safe_rice_ingest=True, native_uncompress=True, exact_original_bytes=True),
        **{'import': dict(files=3, source_and_archive_hashes_verified=3, batch_size=2,
            bytes=3 * compare.bench.FILE_BYTES, duplicate_class_session_location_preserved=True,
            same_size_restored_mtime_edit_detected=True, pending_or_staging=0,
            elapsed_seconds=seconds, mib_per_second=100 / seconds)})


def test_comparison_balances_order_and_reports_timing_spread():
    assert compare.order(2) == ['baseline', 'candidate', 'candidate', 'baseline']
    runs = [dict(variant=variant, report=report(seconds)) for variant, seconds in
        zip(compare.order(2), [12, 9, 11, 14])]
    result = compare.summarize(runs)
    assert result['baseline']['seconds'] == [12, 14]
    assert result['candidate']['median_seconds'] == 10
    assert result['median_elapsed_reduction_percent'] == pytest.approx(100 * (1 - 10 / 13))


@pytest.mark.parametrize('broken', ['cleanup', 'hash', 'count', 'bytes', 'duplicate', 'edit', 'staging', 'rice'])
def test_comparison_refuses_incomplete_or_different_workload_audits(broken):
    current = deepcopy(report())
    if broken == 'cleanup':
        current['owned_process_and_scratch_cleanup'] = False
    elif broken == 'hash':
        current['executable_sha256'] = 'wrong'
    elif broken == 'rice':
        current['restricted_runtime_rice']['exact_original_bytes'] = False
    else:
        key = {'count': 'source_and_archive_hashes_verified', 'bytes': 'bytes',
            'duplicate': 'duplicate_class_session_location_preserved',
            'edit': 'same_size_restored_mtime_edit_detected', 'staging': 'pending_or_staging'}[broken]
        current['import'][key] = 1 if broken == 'staging' else 0
    with pytest.raises(RuntimeError):
        compare.validate(current, 3, 2, 'verified')


def test_comparison_records_failure_and_stops_owned_controller(tmp_path, monkeypatch):
    executable = tmp_path / 'private.exe'
    executable.write_bytes(b'owned test binary')
    stopped = []
    class Tree:
        def __init__(self, command, **kwargs):
            self.proc = self
        def wait(self, timeout):
            return 1
        def stop(self):
            stopped.append(True)
    monkeypatch.setattr(compare.bench.harness, 'ProcessTree', Tree)
    args = SimpleNamespace(output=tmp_path / 'comparison.json', baseline_exe=executable,
        candidate_exe=executable, baseline_commit='before', candidate_commit='after',
        count=3, batch_size=2, pairs=2)
    with pytest.raises(RuntimeError, match='baseline.*failed'):
        compare.run(args)
    assert stopped == [True]
    assert json.loads(args.output.read_text())['status'] == 'failed'
