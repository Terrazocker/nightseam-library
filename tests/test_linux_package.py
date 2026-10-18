"""Portable package extraction refuses profile data and unsafe archive entries."""
import io
from pathlib import Path
import tarfile

import pytest

from scripts.check_linux_package import extract


def package(path, bad=None):
    with tarfile.open(path, 'w:gz') as archive:
        root = tarfile.TarInfo('NightSeam-test')
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        for name in ('NightSeam', 'README.txt', 'build-info.json', 'LICENSE', 'NOTICE',
                     'CFITSIO-LICENSE.txt', 'ZLIB-LICENSE.txt'):
            raw = b'private test package'
            entry = tarfile.TarInfo('NightSeam-test/' + name)
            entry.size = len(raw)
            entry.mode = 0o755 if name == 'NightSeam' else 0o644
            archive.addfile(entry, io.BytesIO(raw))
        if bad:
            entry = tarfile.TarInfo({'traversal': '../escape', 'absolute': '/escape',
                'profile': 'NightSeam-test/data/nightseam.db', 'duplicate': 'NightSeam-test/README.txt',
                'link': 'NightSeam-test/link'}[bad])
            if bad == 'link':
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/tmp/outside'
            archive.addfile(entry)


def test_portable_package_preserves_executable_and_only_public_members(tmp_path):
    bundle = tmp_path / 'bundle.tar.gz'
    package(bundle)
    output = tmp_path / 'extract'
    output.mkdir()
    root = extract(bundle, output)
    assert root == output / 'NightSeam-test'
    assert (root / 'NightSeam').read_bytes() == b'private test package'
    assert not (root / 'data').exists() and len(list(root.iterdir())) == 7
    if __import__('os').name != 'nt':
        assert (root / 'NightSeam').stat().st_mode & 0o111


@pytest.mark.parametrize('bad', ['traversal', 'absolute', 'profile', 'duplicate', 'link'])
def test_package_validation_refuses_bad_members_before_writing(tmp_path, bad):
    bundle = tmp_path / 'bundle.tar.gz'
    package(bundle, bad)
    output = tmp_path / 'extract'
    output.mkdir()
    with pytest.raises(RuntimeError):
        extract(bundle, output)
    assert list(output.iterdir()) == [] and not (tmp_path / 'escape').exists()
