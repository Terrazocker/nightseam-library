"""Exercise the extracted Linux bundle with private data and no developer PATH."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench
from scripts.clean_workspace import remove_tree
from scripts.build_linux import sha256


def extract(bundle, directory):
    with tarfile.open(bundle, 'r:gz') as archive:
        members = archive.getmembers()
        roots = {Path(m.name).parts[0] for m in members if Path(m.name).parts}
        bench.require(len(roots) == 1, 'Package must have one portable root directory')
        root = roots.pop()
        expected = {'NightSeam', 'README.txt', 'build-info.json', 'LICENSE', 'NOTICE',
                    'CFITSIO-LICENSE.txt', 'ZLIB-LICENSE.txt'}
        files = set()
        for member in members:
            path = Path(member.name)
            bench.require(not path.is_absolute() and '..' not in path.parts, 'Unsafe package path')
            if member.isdir():
                bench.require(path.parts == (root,), 'Unexpected package subdirectory')
            else:
                bench.require(member.isfile() and len(path.parts) == 2, 'Unexpected linked or nested package entry')
                bench.require(path.name not in files, 'Repeated package member')
                files.add(path.name)
        bench.require(files == expected, 'Package contents differ; private data must never be included')
        archive.extractall(directory, filter='data')
    return directory / root


def graceful_stop(server):
    # Signal the actual onefile app once. Signalling its whole process group
    # would also make the bootloader forward SIGINT a second time.
    pid = server.proc.pid
    children = []
    # Some kernels omit /proc/PID/task/PID/children. PPID in stat is portable
    # across the supported Linux runners; comm may itself contain spaces/').'
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) == pid:
                children.append(int(entry.name))
        except (FileNotFoundError, PermissionError):
            continue
    bench.require(len(children) == 1, 'Expected one owned PyInstaller application child')
    child = children[0]
    bench.require(Path(f'/proc/{child}/exe').resolve() == (server.tmp / 'NightSeam.exe').resolve(),
                  'Refusing to signal a process outside the owned executable')
    os.kill(child, signal.SIGINT)
    bench.require(server.proc.wait(timeout=30) == 0, 'Linux package did not exit cleanly')
    bench.harness._wait_listener_closed(server.port)
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            break
        bench.require(time.monotonic() < deadline, 'Owned process-group descendants remain after shutdown')
        time.sleep(.05)
    bench.require(not list(server.tmp.glob('_MEI*')), 'Onefile extraction remains after graceful shutdown')
    bench.require(not (server.tmp / 'data/instance.json').exists(), 'Instance marker remains after shutdown')


def run(bundle, output):
    import httpx
    from fits_fixtures import compressed_gate_image
    output.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / 'logs').mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='linux-package-check-', dir=ROOT / 'logs'))
    (scratch / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
    report, server = dict(status='running', bundle_sha256=sha256(bundle)), None
    try:
        package = extract(bundle, scratch)
        manifest = json.loads((package / 'build-info.json').read_text(encoding='utf-8'))
        executable = package / 'NightSeam'
        bench.require(sha256(executable) == manifest['executable_sha256'] and os.access(executable, os.X_OK),
                      'Packaged executable identity/permission differs')
        empty = scratch / 'empty-path'
        empty.mkdir()
        environment = dict(os.environ, PATH=str(empty), TMPDIR=str(scratch))
        for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'NODE_PATH'):
            environment.pop(name, None)
        version = subprocess.check_output([str(executable), '--version'], cwd=scratch,
            env=environment, timeout=30, text=True).strip()
        bench.require(version == 'NightSeam ' + manifest['version'], 'Frozen version bake differs')
        server = bench.harness.DesktopServer(executable, seed=False, restricted_runtime=True)
        server.start(timeout=60)
        archive = server.tmp / 'archive'
        database = server.tmp / 'data/nightseam.db'
        with httpx.Client(base_url=server.base, timeout=120, trust_env=False) as client:
            bench.require(bench.payload(client.get('/api/state'))['version'] == manifest['version'], 'API version differs')
            client.get('/catalog.html').raise_for_status()
            client.get('/themes.css').raise_for_status()
            report['rice'] = bench.rice_smoke(client, archive)
            before = bench.snapshot(database)
            result = bench.payload(client.post('/api/images/ingest', data={'frame': 'flat', 'compress': 'true'},
                files={'files': ('duplicate.fits', compressed_gate_image(False), 'application/fits')}))['results']
            bench.require(len(result) == 1 and result[0].get('skipped') and result[0]['id'] == before[0][0][0],
                          'Duplicate lost its original identity')
            bench.require(bench.snapshot(database) == before, 'Duplicate changed metadata/integrity')
            with sqlite3.connect(database) as connection:
                units = connection.execute('SELECT SUM(n_files) FROM import_batches').fetchone()[0]
            bench.require(units == 1 and before[1] == 1 and before[2] == 0, 'Image/history/integrity publication differs')
            # The deliberately empty PATH has no desktop opener. Refuse cleanly
            # rather than calling Windows-only os.startfile or opening a GUI.
            opened = client.post('/api/library/open_folder', json={'rel': '.'})
            bench.require(opened.status_code == 500 and 'xdg-open' in opened.json().get('detail', ''),
                          'Missing desktop opener did not return its explanatory error')
            bench.require(not any(p.suffix == '.part' or p.name.startswith('.nightseam-') for p in archive.rglob('*')),
                          'Staging leftovers remain')
        graceful_stop(server)
        report.update(status='passed', version=manifest['version'], source_commit=manifest['source_commit'],
            executable_sha256=manifest['executable_sha256'], glibc=manifest['glibc'], developer_path_removed=True,
            duplicate_identity_and_metadata_preserved=True, committed_images=1, history_units=1,
            graceful_exit=True, listener_closed=True, extraction_cleaned=True, staging_leftovers=0,
            native_gtk_qt_window_tested=False)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if server is not None and server.tmp is not None:
            for log in (server.tmp / 'logs').glob('nightseam.log*'):
                shutil.copyfile(log, output.parent / ('linux-package-' + log.name))
        raise
    finally:
        try:
            try:
                if server is not None:
                    server.stop()
            finally:
                remove_tree(scratch)
            report['owned_process_and_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Linux package: version, assets, Rice, duplicate, graceful exit and cleanup passed ({version})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/linux-package-smoke.json')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Linux package checks require Linux')
    run(args.bundle.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
