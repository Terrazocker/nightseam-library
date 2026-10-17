import io

from astropy.io import fits
import pytest

from app import ingest
from scripts import stress_dedup as stress


@pytest.mark.parametrize('count', [7, 8, 9])
def test_stress_exercises_real_handles_boundary_fallback_and_cancellation(tmp_path, monkeypatch, count):
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 8)
    result = stress.exercise(tmp_path / 'owned-stress', count, cycles=2, files_per_directory=2)
    assert result['all_archive_hashes_verified'] == count
    assert result['file_bytes'] == 5760
    assert result['cancellation']['worker_drained']
    assert result['cancellation']['lease_released']
    for cycle in result['cycles']:
        assert cycle['pinned_files'] == min(count, 8)
        assert cycle['archive_hashes'] == min(count, 8) + 2 * max(0, count - 8) + 1
        assert cycle['after']['handles'] <= cycle['before']['handles'] + 2
    for path in (tmp_path / 'owned-stress/archive').rglob('*.fits'):
        with fits.open(io.BytesIO(path.read_bytes())) as hdus:
            hdus.verify('exception')
            assert hdus[0].data.shape == (8, 8)


def test_stress_refuses_existing_directory(tmp_path):
    with pytest.raises(RuntimeError, match='new directory'):
        stress.exercise(tmp_path, 1)
