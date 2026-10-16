"""Build pinned official Windows x64 fpack/funpack with a static C runtime.

Requires Visual Studio C++ Build Tools (vswhere) and its bundled CMake.
Source downloads are hash checked; build evidence remains in ignored logs/.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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


def main():
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

    for name, url, digest in PACKAGES:
        archive = WORK / (name + '.zip')
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'{name} source checksum failed')
        with zipfile.ZipFile(archive) as packed:
            for item in packed.infolist():
                path = (WORK / 'sources' / item.filename).resolve()
                if not path.is_relative_to((WORK / 'sources').resolve()) or item.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError('Unsafe source archive entry')
            packed.extractall(WORK / 'sources')
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
        shutil.copyfile(WORK / 'cfitsio-build' / target.name, target)
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
