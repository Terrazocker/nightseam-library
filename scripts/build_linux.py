"""Build a portable Linux x86_64 browser-mode bundle without private data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_build_dependencies import read_lock, inventory_errors, closure_errors
from scripts.clean_workspace import remove_tree
from app.version import build_version


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def glibc_requirements(executable, work):
    """Inspect the bootloader and every bundled ELF; libc itself stays external."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    requirements = {}
    directory = work / 'elf-inspection'
    directory.mkdir()
    def inspect(path, name):
        result = subprocess.run(['readelf', '-W', '--version-info', str(path)],
            check=True, capture_output=True, text=True)
        versions = sorted(set(re.findall(r'Name: (GLIBC_[A-Za-z0-9_.]+)', result.stdout)))
        if versions:
            requirements[name] = versions
    inspect(executable, 'NightSeam bootloader')
    for index, (name, entry) in enumerate(archive.toc.items()):
        if entry[-1] != 'b':
            continue
        raw = archive.extract(name)
        if not raw.startswith(b'\x7fELF'):
            continue
        path = directory / str(index)
        path.write_bytes(raw)
        inspect(path, name)
        path.unlink()
    numeric = [v.removeprefix('GLIBC_') for versions in requirements.values()
               for v in versions if re.fullmatch(r'GLIBC_[0-9.]+', v)]
    return dict(highest_numeric_requirement=max(numeric, key=lambda v: tuple(map(int, v.split('.')))),
        by_elf=requirements, system_libc_and_loader_not_bundled=True)


def build(args):
    if (sys.platform != 'linux' or platform.libc_ver()[0] != 'glibc'
            or platform.machine().lower() not in ('x86_64', 'amd64')
            or platform.python_implementation() != 'CPython' or sys.version_info[:2] != (3, 12)
            or struct.calcsize('P') != 8):
        raise ValueError('Build on glibc Linux x86_64 using CPython 3.12')
    pins = read_lock(ROOT / 'requirements-build-linux-lock.txt')
    errors = inventory_errors(pins) or closure_errors(pins)
    if errors:
        raise ValueError('\n'.join(errors))
    from app.compression import tools
    tools()  # Check the bundled Linux utilities and their recorded hashes.
    if not shutil.which('readelf'):
        raise ValueError('Install binutils (readelf) before building')
    status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if status and not args.allow_dirty:
        raise ValueError('Commit source changes first or use --allow-dirty for a marked development build')
    version = build_version()
    output = (args.output or ROOT / 'dist' / f'NightSeam-Catalog-{version}-linux-x86_64').resolve()
    bundle = output.with_name(output.name + '.tar.gz')
    if output.exists() or bundle.exists():
        raise ValueError('Output already exists; choose a new --output directory to preserve prior builds/data')
    output.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / 'logs').mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='linux-build-', dir=ROOT / 'logs'))
    (work / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
    log = ROOT / 'logs' / f'linux-build-{version}.log'
    try:
        # Bake only an owned source copy, never the shared source checkout.
        source = work / 'source'
        source.mkdir()
        for name in ('main.py', 'VERSION'):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(ROOT / 'app', source / 'app',
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '_version_baked.py'))
        (source / 'app/_version_baked.py').write_text(f'BAKED_VERSION = {version!r}\n', encoding='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'scripts/check_js.py')], cwd=ROOT, check=True)
        command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--onefile',
            '--name', 'NightSeam', '--specpath', str(work / 'spec'), '--distpath', str(output),
            '--workpath', str(work / 'build'), '--paths', str(source),
            '--additional-hooks-dir', str(ROOT / 'scripts/pyinstaller_hooks'), '--exclude-module', 'webview',
            '--hidden-import', 'python_multipart']
        for name in ('static', 'app/data'):
            command += ['--add-data', f'{ROOT / name}:{name}']
        for name in ('NOTICE', 'LICENSE'):
            command += ['--add-data', f'{ROOT / name}:.']
        vendor = ROOT / 'vendor/fits-tools/linux-x64'
        for name in ('fpack', 'funpack', 'provenance.json', 'CFITSIO-LICENSE.txt', 'ZLIB-LICENSE.txt'):
            command += ['--add-binary' if name in ('fpack', 'funpack') else '--add-data',
                f'{vendor / name}:vendor/fits-tools/linux-x64']
        # The support report records the build's pywebview version even though
        # Linux browser mode deliberately excludes its native backend module.
        for name in ('fastapi', 'uvicorn', 'pywebview', 'mini-racer', 'astropy', 'numpy', 'Pillow'):
            command += ['--copy-metadata', name]
        for name in ('uvicorn', 'py_mini_racer'):
            command += ['--collect-all', name]
        command.append(str(source / 'main.py'))
        with log.open('w', encoding='utf-8') as stream:
            subprocess.run(command, cwd=source, stdout=stream, stderr=subprocess.STDOUT, check=True)
        executable = output / 'NightSeam'
        manifest = dict(version=version,
            source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            source_dirty=bool(status), platform='linux-x86_64', ui='system browser; no bundled GTK/Qt backend',
            python=platform.python_version(), build_host=platform.platform(), build_host_libc=platform.libc_ver(),
            dependencies=pins, executable_sha256=sha256(executable), executable_bytes=executable.stat().st_size,
            glibc=glibc_requirements(executable, work), private_profile_data_included=False)
        (output / 'build-info.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        for name in ('LICENSE', 'NOTICE'):
            shutil.copy2(ROOT / name, output / name)
        for name in ('CFITSIO-LICENSE.txt', 'ZLIB-LICENSE.txt'):
            shutil.copy2(vendor / name, output / name)
        (output / 'README.txt').write_text(
            f'NightSeam Catalog {version} — Linux x86_64 browser build\n\n'
            'Extract into a user-writable folder, then run:\n  ./NightSeam --browser\n\n'
            'Python and Node are not required. Data and logs stay beside the executable.\n'
            'A system browser and an executable temporary filesystem are required.\n'
            'Opening archive folders also uses xdg-open from xdg-utils.\n'
            'GTK/Qt native-window mode is not bundled. Do not install into /usr/bin.\n'
            'See build-info.json for the actual bundled ELF glibc requirements.\n'
            'This development artifact is not a public release.\n', encoding='utf-8')
        with tarfile.open(bundle, 'w:gz') as archive:
            archive.add(output, arcname=output.name)
        bundle.with_name(bundle.name + '.sha256').write_text(f'{sha256(bundle)}  {bundle.name}\n', encoding='utf-8')
        remove_tree(work)
        print(json.dumps(dict(bundle=str(bundle), executable=str(executable),
            glibc=manifest['glibc']['highest_numeric_requirement'], log=str(log)), indent=2))
        return bundle
    except BaseException:
        print(f'Linux build failed; retained diagnostic work at {work} and log {log}', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-dirty', action='store_true')
    parser.add_argument('--output', type=Path)
    build(parser.parse_args())


if __name__ == '__main__':
    main()
