import hashlib
import json
import zipfile

import pytest

from scripts.make_acceptance_kit import create


def test_acceptance_kit_contains_verifiable_private_inputs_and_unrun_checklist(tmp_path):
    executable = tmp_path / 'owned-test.exe'
    executable.write_bytes(b'owned synthetic executable')
    output = tmp_path / 'kit.zip'
    result = create(executable, output, commit='test-revision')
    assert result['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as kit:
        manifest = json.loads(kit.read('manifest.json'))
        assert manifest['source_commit'] == 'test-revision'
        assert manifest['acceptance_status'] == 'not_run'
        assert len(manifest['files']) == 4
        for entry in manifest['files']:
            raw = kit.read(entry['path'])
            assert len(raw) == entry['bytes']
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        results = json.loads(kit.read('results.template.json'))
        assert set(results['checks'].values()) == {'not_run'}
        assert results['windows_build'] is None
        assert {'Verify-Kit.ps1', 'acceptance.md', 'LICENSE', 'NOTICE'} <= set(kit.namelist())
    with pytest.raises(RuntimeError, match='existing acceptance kit'):
        create(executable, output)
    assert not list(tmp_path.glob('*.part'))
