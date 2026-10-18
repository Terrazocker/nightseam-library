"""Build pinned official fpack/funpack for Windows x64 or Linux x64.

Windows requires Visual Studio C++ Build Tools and its bundled CMake.
Linux requires GCC, Make and CMake (system or the active Python environment).
Source downloads are hash checked; build evidence remains in ignored logs/.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'logs/fits-tools-build'
OUT = ROOT / 'vendor/fits-tools/windows-x64'
PACKAGES = (
    ('cfitsio', 'https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/cfit-4.7.0.zip',
     '63ec1941a239832e90f71dde32422f9c5cbafc161950102b3f2ba5516d864a5f'),
    ('zlib', 'https://codeload.github.com/madler/zlib/zip/refs/tags/v1.3.2',
     '31fd9fee98812abcf147d0e103bc4d2f983c35a8d7a807a328a299f3a74e0050'),
)


def sources(work):
    """Download and unpack the same pinned upstream sources on both hosts."""
    work.mkdir(parents=True, exist_ok=True)
    for name, url, digest in PACKAGES:
        archive = work / (name + '.zip')
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'{name} source checksum failed')
        with zipfile.ZipFile(archive) as packed:
            for item in packed.infolist():
                path = (work / 'sources' / item.filename).resolve()
                if not path.is_relative_to((work / 'sources').resolve()) or item.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError('Unsafe source archive entry')
            packed.extractall(work / 'sources')


def build_linux():
    work = ROOT / 'logs/fits-tools-build-linux-x64'
    out = ROOT / 'vendor/fits-tools/linux-x64'
    cmake = shutil.which('cmake') or str(Path(sys.executable).parent / 'cmake')
    cc = shutil.which('gcc')
    if not cc or not shutil.which('make') or not shutil.which('readelf') or not Path(cmake).is_file():
        raise RuntimeError('Linux builds require GCC, Make, binutils and CMake.')
    sources(work)
    commands = []

    def run(args):
        args = list(map(str, args))
        commands.append(args)
        with (work / 'build.log').open('a', encoding='utf-8') as log:
            subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=True)

    prefix = work / 'prefix'
    common = ['-G', 'Unix Makefiles', '-DCMAKE_BUILD_TYPE=Release',
              f'-DCMAKE_C_COMPILER={cc}']
    run([cmake, '-S', work / 'sources/zlib-1.3.2', '-B', work / 'zlib-build', *common,
         f'-DCMAKE_INSTALL_PREFIX={prefix}', '-DZLIB_BUILD_TESTING=OFF',
         '-DZLIB_BUILD_SHARED=OFF', '-DZLIB_BUILD_STATIC=ON'])
    run([cmake, '--build', work / 'zlib-build', '--target', 'install', '--parallel', '2'])
    libraries = list(prefix.rglob('libz.a')) + list(prefix.rglob('libzs.a'))
    if len(libraries) != 1:
        raise RuntimeError(f'Expected one static zlib library, found {len(libraries)}')
    flags = ['-DBUILD_SHARED_LIBS=OFF', '-DUSE_CURL=OFF', '-DUSE_PTHREADS=OFF',
             '-DUSE_BZIP2=OFF', '-DTESTS=OFF', '-DUTILS=ON',
             f'-DZLIB_INCLUDE_DIR={prefix / "include"}', f'-DZLIB_LIBRARY={libraries[0]}']
    run([cmake, '-S', work / 'sources/cfitsio-4.7.0', '-B', work / 'cfitsio-build',
         *common, *flags])
    run([cmake, '--build', work / 'cfitsio-build', '--target', 'fpack', 'funpack', '--parallel', '2'])
    binaries = {}
    # Stage and validate every artifact before replacing the installed bundle.
    stage = work / 'bundle'
    stage.mkdir(exist_ok=True)
    for name in ('fpack', 'funpack'):
        target = stage / name
        shutil.copyfile(work / 'cfitsio-build' / name, target)
        target.chmod(0o755)
        dependencies = subprocess.check_output(['ldd', str(target)], text=True)
        if 'not found' in dependencies or any(lib in dependencies for lib in ('libcfitsio', 'libz.so', 'libcurl', 'libbz2')):
            raise RuntimeError(f'{name} must bundle CFITSIO and zlib statically')
        versions = re.findall(r'GLIBC_(\d+\.\d+)', subprocess.check_output(
            ['readelf', '--version-info', str(target)], text=True))
        binaries[name] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'version': subprocess.check_output([str(target), '-V'], text=True).strip(),
            'required_glibc': max(versions, key=lambda value: tuple(map(int, value.split('.')))),
            'dynamic_dependencies': [re.sub(r'\s+\(0x[0-9a-f]+\)', '', line.strip())
                                     for line in dependencies.strip().splitlines()]}
    shutil.copyfile(work / 'sources/cfitsio-4.7.0/licenses/License.txt', stage / 'CFITSIO-LICENSE.txt')
    shutil.copyfile(work / 'sources/zlib-1.3.2/LICENSE', stage / 'ZLIB-LICENSE.txt')
    provenance = {'cfitsio': '4.7.0', 'zlib': '1.3.2', 'platform': 'linux-x64',
        'runtime': 'static CFITSIO and zlib; dynamic system libc/libm',
        'compiler': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        'cmake': subprocess.check_output([cmake, '--version'], text=True).splitlines()[0],
        'libc': list(platform.libc_ver()), 'cmake_flags': common + flags,
        'sources': [{'url': u, 'sha256': h} for _, u, h in PACKAGES], 'binaries': binaries}
    (stage / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    out.mkdir(parents=True, exist_ok=True)
    for artifact in stage.iterdir():
        shutil.copy2(artifact, out / artifact.name)
    (work / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(binaries))


def trim_build_paths(data):
    # MSVC embeds assert() __FILE__ as an absolute path, which would publish the
    # builder's checkout location. Make it source-relative and NUL-pad it to the
    # same length so no other byte moves; refuse any remaining build-root path.
    prefix = (str(WORK / 'sources') + '\\').encode()
    while (start := data.find(prefix)) >= 0:
        end = data.index(b'\0', start)
        relative = data[start + len(prefix):end]
        data = data[:start] + relative + b'\0' * (end - start - len(relative)) + data[end:]
    if str(ROOT).encode().lower() in data.lower():
        raise RuntimeError('A built FITS utility still embeds the local build path.')
    return data


def main():
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('FITS tool builds support x64 hosts only.')
    if sys.platform == 'linux':
        return build_linux()
    if sys.platform != 'win32':
        raise RuntimeError('FITS tool builds support Windows and Linux only.')
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    finder = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    vs = Path(subprocess.check_output([str(finder), '-latest', '-products', '*',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip())
    cmake = vs / 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
    vcvars = vs / 'VC/Auxiliary/Build/vcvars64.bat'
    if not cmake.is_file() or not vcvars.is_file():
        raise RuntimeError('Visual Studio C++ Build Tools and CMake are required.')
    initialized = subprocess.check_output(f'call "{vcvars}" >nul && set', shell=True, text=True)
    env = dict(line.split('=', 1) for line in initialized.splitlines() if '=' in line and not line.startswith('='))
    commands = []

    def run(args):
        args = list(map(str, args))
        commands.append(args)
        with (WORK / 'build.log').open('a', encoding='utf-8') as log:
            subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

    sources(WORK)
    common = ['-G', 'NMake Makefiles', '-DCMAKE_BUILD_TYPE=Release',
              '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW', '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded']
    prefix = WORK / 'prefix'
    run([cmake, '-S', WORK / 'sources/zlib-1.3.2', '-B', WORK / 'zlib-build', *common,
         f'-DCMAKE_INSTALL_PREFIX={prefix}', '-DZLIB_BUILD_TESTING=OFF', '-DZLIB_BUILD_SHARED=OFF', '-DZLIB_BUILD_STATIC=ON'])
    run([cmake, '--build', WORK / 'zlib-build', '--target', 'install'])
    library, = (prefix / 'lib').glob('*.lib')
    run([cmake, '-S', WORK / 'sources/cfitsio-4.7.0', '-B', WORK / 'cfitsio-build', *common,
         '-DBUILD_SHARED_LIBS=OFF', '-DUSE_CURL=OFF', '-DUSE_PTHREADS=OFF', '-DUSE_BZIP2=OFF',
         '-DTESTS=OFF', '-DUTILS=ON', f'-DZLIB_INCLUDE_DIR={prefix / "include"}', f'-DZLIB_LIBRARY={library}'])
    run([cmake, '--build', WORK / 'cfitsio-build', '--target', 'fpack', 'funpack'])
    binaries = {}
    for name in ('fpack', 'funpack'):
        target = OUT / (name + '.exe')
        target.write_bytes(trim_build_paths((WORK / 'cfitsio-build' / target.name).read_bytes()))
        binaries[target.name] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'version': subprocess.check_output([str(target), '-V'], text=True).strip()}
    # Distribute the upstream notices verbatim alongside the utilities.
    shutil.copyfile(WORK / 'sources/cfitsio-4.7.0/licenses/License.txt', OUT / 'CFITSIO-LICENSE.txt')
    shutil.copyfile(WORK / 'sources/zlib-1.3.2/LICENSE', OUT / 'ZLIB-LICENSE.txt')
    (OUT / 'provenance.json').write_text(json.dumps({'cfitsio': '4.7.0', 'zlib': '1.3.2',
        'compiler': env.get('VCToolsVersion'), 'sdk': env.get('WindowsSDKVersion'),
        'cmake': subprocess.check_output([str(cmake), '--version'], text=True).splitlines()[0],
        'cmake_flags': common + ['BUILD_SHARED_LIBS=OFF', 'USE_CURL=OFF', 'USE_PTHREADS=OFF', 'USE_BZIP2=OFF'],
        'runtime': 'static MSVC', 'sources': [{'url': u, 'sha256': h} for _, u, h in PACKAGES],
        'binaries': binaries}, indent=2) + '\n', encoding='utf-8')
    (WORK / 'commands.json').write_text(json.dumps(commands, indent=2), encoding='utf-8')
    print(json.dumps(binaries))


if __name__ == '__main__':
    main()
