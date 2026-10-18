"""Check an unchanged Linux bundle in an offline, runtime-only Docker image.

Python drives Docker on the host. The application container has only the base
OS and the package; a separate curl container probes its isolated loopback.
Neither container receives the checkout, developer environment or host data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/browser'))
from fits_fixtures import compressed_gate_image
from scripts.check_linux_package import extract
from scripts.clean_workspace import remove_tree

CURL_IMAGE = 'curlimages/curl:8.12.1@sha256:94e9e444bcba979c2ea12e27ae39bee4cd10bc7041a472c4727a558e213744e6'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Docker:
    def __init__(self):
        self.environment = dict(os.environ)
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            self.environment.pop(key, None)
        self.command = ['docker', '--host=unix:///var/run/docker.sock']

    def run(self, *args, check=True, timeout=120, input_data=None):
        result = subprocess.run([*self.command, *args], env=self.environment, input=input_data,
            check=check, capture_output=True, text=input_data is None, timeout=timeout)
        if input_data is not None:
            result.stdout = result.stdout.decode('utf-8', errors='replace')
            result.stderr = result.stderr.decode('utf-8', errors='replace')
        return result


def run(bundle, image, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / 'logs').mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='linux-clean-system-', dir=ROOT / 'logs'))
    (scratch / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
    name = 'nightseam-acceptance-' + uuid.uuid4().hex
    docker, process, created = Docker(), None, False
    report = dict(status='running', image=image, bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
                  physical_desktop_tested=False, actual_browser_or_file_manager_tested=False)
    stream = None
    try:
        package = extract(bundle, scratch)
        manifest = json.loads((package / 'build-info.json').read_text(encoding='utf-8'))
        require(hashlib.sha256((package / 'NightSeam').read_bytes()).hexdigest() == manifest['executable_sha256'],
                'Executable differs from the bundle manifest')
        # Fetch before runtime isolation; never install anything in the app OS.
        for ref in (image, CURL_IMAGE):
            docker.run('pull', ref, timeout=300)
        image_info = json.loads(docker.run('image', 'inspect', image).stdout)[0]
        report.update(image_id=image_info['Id'], image_digests=image_info.get('RepoDigests', []),
                      version=manifest['version'], source_commit=manifest['source_commit'],
                      executable_sha256=manifest['executable_sha256'], curl_probe_image=CURL_IMAGE)
        docker.run('create', '--name', name, '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128',
            '--memory', '768m', '--cpus', '2', '--user', '1000:1000',
            '--tmpfs', '/work:rw,exec,uid=1000,gid=1000,mode=0700',
            '--tmpfs', '/tmp:rw,exec,uid=1000,gid=1000,mode=0700',
            '--entrypoint', '/bin/sleep', image, 'infinity')
        created = True
        docker.run('start', name)
        info = json.loads(docker.run('inspect', name).stdout)[0]
        require(info['HostConfig']['NetworkMode'] == 'none' and not info['Mounts'],
                'App container must have no external network or host mounts')
        report['os_release'] = docker.run('exec', name, '/bin/cat', '/etc/os-release').stdout
        report['system_packages'] = docker.run('exec', name, '/usr/bin/dpkg-query', '-W',
            '-f=${binary:Package}\t${Version}\n').stdout.splitlines()
        docker.run('exec', name, '/bin/sh', '-c',
            'for tool in python python3 node npm pip pip3; do '
            'if command -v "$tool" >/dev/null 2>&1; then exit 1; fi; done')
        docker.run('exec', name, '/bin/mkdir', '/work/catalog', '/work/openers', '/work/archive $(literal); with spaces')
        # Extract as the app user, including on root-owned GitHub runners.
        # Docker's archive-copy API cannot chown under cap-drop ALL.
        import tarfile
        stub = scratch / 'xdg-open'
        stub.write_text('#!/bin/sh\nprintf "%s\\n%s\\n" "$1" "${LD_LIBRARY_PATH-unset}" >> /work/desktop-calls\n', encoding='utf-8')
        stub.chmod(0o755)
        transfer = scratch / 'transfer.tar'
        with tarfile.open(transfer, 'w') as packed:
            for path in package.iterdir():
                packed.add(path, arcname='catalog/' + path.name)
            packed.add(stub, arcname='openers/xdg-open')
        with transfer.open('rb') as source:
            subprocess.run([*docker.command, 'exec', '-i', name, '/bin/tar', '--no-same-owner', '-xf', '-', '-C', '/work'],
                stdin=source, env=docker.environment, check=True, capture_output=True, timeout=60)
        # The capture script is a test double, never a desktop-success claim.
        environment = ['-e', 'PATH=/work/openers', '-e', 'HOME=/work', '-e', 'TMPDIR=/tmp', '-e', 'DISPLAY=:99',
                       '-e', 'LD_LIBRARY_PATH=/system-library-sentinel']
        prefix = ['exec', '--user', '1000:1000', *environment, name, '/work/catalog/NightSeam']
        version = docker.run(*prefix, '--version').stdout.strip()
        require(version == 'NightSeam ' + manifest['version'], 'Frozen version differs')
        stream = output.with_suffix('.app.log').open('w', encoding='utf-8')
        process = subprocess.Popen([*docker.command, *prefix, '--browser', '--console', '--port', '8407',
            '--idle-exit-hours', '0'], env=docker.environment, stdout=stream, stderr=subprocess.STDOUT)
        original = compressed_gate_image(False)

        def request(path, *options, expected=200, raw=False):
            upload = any(option.startswith('files=@-') for option in options)
            result = docker.run('run', '--rm', '--name', name + '-probe', '--interactive',
                '--network', 'container:' + name, '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                CURL_IMAGE, '--silent', '--show-error', '--noproxy', '*', '--max-time', '60',
                '--write-out', '\n%{http_code}', *options, 'http://127.0.0.1:8407/' + path, check=False,
                input_data=original if upload else None)
            require(result.returncode == 0, 'Loopback probe failed: ' + result.stderr[-500:])
            body, status = result.stdout.rsplit('\n', 1)
            require(int(status) == expected, f'{path}: expected HTTP {expected}, got {status}: {body[:500]}')
            return body if raw else json.loads(body)

        deadline = time.monotonic() + 60
        while True:
            try:
                state = request('api/state')
                break
            except RuntimeError:
                require(process.poll() is None and time.monotonic() < deadline,
                        'Offline first launch did not become ready; see app log')
                time.sleep(.3)
        require(state['version'] == manifest['version'], 'API version differs')
        require('catalog' in request('catalog.html', raw=True).lower(), 'Catalog document missing')
        require(len(request('themes.css', raw=True)) > 1000, 'Styles missing')
        def desktop_calls(count):
            deadline = time.monotonic() + 10
            while True:
                result = docker.run('exec', name, '/bin/cat', '/work/desktop-calls', check=False)
                lines = result.stdout.splitlines()
                if result.returncode == 0 and len(lines) >= count:
                    return lines
                require(time.monotonic() < deadline, 'Desktop opener capture missing')
                time.sleep(.1)
        require(desktop_calls(2) ==
                ['http://127.0.0.1:8407/', '/system-library-sentinel'], 'First browser handoff environment differs')
        docker.run(*prefix, '--browser')
        calls = desktop_calls(4)
        require(calls == ['http://127.0.0.1:8407/', '/system-library-sentinel',
                         'http://127.0.0.1:8407/catalog.html', '/system-library-sentinel'],
                'Repeat launch did not hand off to the existing instance with clean system libraries')
        archive = '/work/archive $(literal); with spaces'
        request('api/settings', '-H', 'Content-Type: application/json', '-d', json.dumps({'image_dir': archive}))
        require(request('api/state')['image_dir'] == archive, 'Private archive was not configured')
        request('api/library/open_folder', '-H', 'Content-Type: application/json', '-d', '{"rel":"."}')
        calls = desktop_calls(6)
        require(calls[-2:] == [archive, '/system-library-sentinel'], 'Folder path/environment not literal')
        uploaded = request('api/images/ingest', '-F', 'compress=true', '-F', 'compression_mode=safe',
                           '-F', 'files=@-;filename=capture.fits;type=application/fits')['results']
        item, = uploaded
        require(item.get('ok') and not item.get('skipped') and item['stored'].endswith('.fz'),
                f'Native Rice ingest failed: {item}')
        job = request('api/library/compression', '-H', 'Content-Type: application/json', '-d',
            json.dumps({'mode': 'uncompress', 'anchor_id': item['id'], 'image_ids': [item['id']]}), expected=202)
        deadline = time.monotonic() + 60
        while job['status'] not in ('completed', 'failed', 'cancelled'):
            require(time.monotonic() < deadline, 'Native uncompress timed out')
            time.sleep(.1)
            job = request('api/library/compression/' + job['id'])
        require(job['status'] == 'completed', 'Native uncompress failed')
        library = request('api/library')
        require(library['total_images'] == 1, 'Unexpected image count')
        record, = [row for obj in library['objects'] for row in obj['images']]
        relative = PurePosixPath(record['file'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe stored test image path')
        restored = scratch / 'restored.fits'
        def copy_out(source, destination):
            with destination.open('wb') as stream:
                subprocess.run([*docker.command, 'exec', name, '/bin/cat', source],
                    stdout=stream, stderr=subprocess.PIPE, env=docker.environment, check=True, timeout=30)
        copy_out(archive + '/' + str(relative), restored)
        require(record['id'] == item['id'] and restored.read_bytes() == original, 'Rice changed identity/bytes')
        duplicate, = request('api/images/ingest', '-F', 'compress=true', '-F', 'frame=flat',
                            '-F', 'files=@-;filename=capture.fits;type=application/fits')['results']
        require(duplicate.get('skipped') and duplicate['id'] == item['id'], 'Duplicate changed identity')
        again = request('api/library')
        records = [row for obj in again['objects'] for row in obj['images']]
        require(again['total_images'] == 1 and records == [record], 'Duplicate changed stored metadata')
        # Signal only the owned onefile application child; the bootloader then
        # observes normal shutdown and removes its executable temp directory.
        docker.run('exec', name, '/bin/sh', '-c',
            'for entry in /proc/[0-9]*/cmdline; do '
            'pid=${entry#/proc/}; pid=${pid%/cmdline}; '
            '[ "$(readlink /proc/$pid/exe)" = /work/catalog/NightSeam ] || continue; '
            'parent=$(sed -n "s/^PPid:[[:space:]]*//p" /proc/$pid/status); '
            '[ "$(readlink /proc/$parent/exe)" = /work/catalog/NightSeam ] || continue; '
            'kill -INT "$pid"; exit; done; exit 1')
        require(process.wait(timeout=30) == 0, 'Application did not exit cleanly')
        docker.run('exec', name, '/bin/sh', '-c',
            'test ! -e /work/catalog/data/instance.json && '
            'test -z "$(find /tmp -maxdepth 1 -name \'_MEI*\' -print)" && '
            'test -z "$(find /work -name \'*.part\' -o -name \'.nightseam-*\')"')
        docker.run('exec', name, '/bin/sh', '-c',
            'for entry in /proc/[0-9]*/exe; do '
            '[ "$(readlink "$entry")" != /work/catalog/NightSeam ] || exit 1; done')
        try:
            request('api/state')
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Listener remains after shutdown')
        database = scratch / 'catalog.db'
        copy_out('/work/catalog/data/nightseam.db', database)
        with sqlite3.connect(database) as db:
            require(db.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 1, 'Duplicate publication count differs')
            require(db.execute('SELECT SUM(n_files) FROM import_batches').fetchone()[0] == 1, 'Import history differs')
            require(db.execute('SELECT COUNT(*) FROM compression_journal').fetchone()[0] == 0, 'Compression journal remains')
        report.update(status='passed', runtime_user=1000, external_network='disabled (Docker network none)',
            developer_tools_absent=True, app_path_contains_only_capture_stub=True, source_or_profile_mounted=False,
            offline_first_launch=True, safe_rice_ingest=True, exact_native_restoration=True,
            duplicate_preserved=True, browser_first_and_repeat_handoff=True, literal_folder_handoff=True,
            system_library_environment_restored=True, graceful_exit=True, listener_closed=True,
            onefile_extraction_cleaned=True, staging_leftovers=0)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        cleanup_errors = []
        def cleanup(label, action):
            try:
                action()
            except BaseException as exc:
                cleanup_errors.append(f'{label}: {type(exc).__name__}: {exc}')
        def remove_probe():
            probe = docker.run('container', 'inspect', name + '-probe', check=False)
            if probe.returncode == 0:
                docker.run('rm', '--force', name + '-probe')
            else:
                require('No such container' in probe.stderr or 'No such object' in probe.stderr,
                        'Could not confirm probe cleanup: ' + probe.stderr[-500:])
        try:
            cleanup('probe container', remove_probe)
            if created:
                cleanup('app container', lambda: docker.run('rm', '--force', name))
            if process is not None and process.poll() is None:
                cleanup('app controller', lambda: process.wait(timeout=15))
                if process.poll() is None:
                    cleanup('stop owned Docker client', process.kill)
                    cleanup('reap owned Docker client', lambda: process.wait(timeout=5))
            if stream is not None:
                cleanup('app log', stream.close)
            cleanup('owned scratch', lambda: remove_tree(scratch))
            require(not cleanup_errors, '; '.join(cleanup_errors))
            report['owned_container_and_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Clean Linux runtime passed: {image} ({manifest["version"]})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--image', default='ubuntu:22.04')
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/linux-package-clean-system.json')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Clean Linux checks require Linux and a local Docker daemon')
    run(args.bundle.resolve(), args.image, args.output.resolve())


if __name__ == '__main__':
    main()
