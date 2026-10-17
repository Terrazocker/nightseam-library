"""Prepare a local Windows acceptance kit; packaging is not an acceptance pass."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench


VERIFY = r'''$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $root 'manifest.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $path = Join-Path $root $entry.path
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw "Hash mismatch: $($entry.path)"
    }
    Write-Host "Verified $($entry.path)"
}
Write-Host 'File verification passed. Manual acceptance checks are still required.'
'''


def create(executable, output, commit=None):
    bench.require(executable.is_file(), 'Build the Windows executable before preparing acceptance')
    bench.require(not output.exists(), 'Preserve the existing acceptance kit; choose a new output')
    files = []
    results = dict(source_commit=commit, executable_sha256=bench.digest(executable),
        windows_build=None, dotnet_version=None, webview2_version=None,
        external_network_disconnected_from=None, external_network_disconnected_until=None,
        display_configuration=None, evidence_notes=None,
        checks={name: 'not_run' for name in ('clean_vm_native_startup', 'first_offline_launch',
            'safe_rice_exact_restoration', 'duplicate_identity_preservation',
            'native_support_save_and_cancel', 'normal_close_and_reopen',
            'physical_scaling_100_125_150', 'mixed_dpi_monitor_transition',
            'pointer_and_keyboard_snap', 'disconnected_monitor_recovery')})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.part')
    # Exclusive creation preserves other runs' partial/recovery evidence.
    with temporary.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as kit:
                kit.write(executable, 'NightSeam.exe')
                files.append(dict(path='NightSeam.exe', sha256=results['executable_sha256'],
                    bytes=executable.stat().st_size))
                for index in range(3):
                    raw = bench.exposure(index)
                    path = f'fixtures/exposure-{index:04d}.fits'
                    kit.writestr(path, raw)
                    files.append(dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
                for name, source in (('acceptance.md', 'docs/windows-manual-acceptance.md'),
                                     ('LICENSE', 'LICENSE'), ('NOTICE', 'NOTICE')):
                    kit.write(ROOT / source, name)
                kit.writestr('Verify-Kit.ps1', VERIFY)
                kit.writestr('results.template.json', json.dumps(results, indent=2) + '\n')
                kit.writestr('manifest.json', json.dumps(dict(source_commit=commit, files=files,
                    acceptance_status='not_run'), indent=2) + '\n')
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    temporary.rename(output)
    return dict(path=str(output), sha256=bench.digest(output), files=len(files), acceptance_status='not_run')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=bench.harness.DEFAULT_EXE)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-acceptance-kit.zip')
    parser.add_argument('--commit', default=os.environ.get('GITHUB_SHA'))
    args = parser.parse_args()
    print(json.dumps(create(args.exe, args.output, args.commit), indent=2))


if __name__ == '__main__':
    main()
