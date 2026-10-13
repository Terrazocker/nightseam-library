# Compile this independent catalog. VERSION is the project version authority.
param([switch]$AllowDirty)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$catalogPython = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $catalogPython)) {
    throw "Prepare the build environment manually: py -3.12 -m venv .venv-build; .venv-build\Scripts\python.exe -m pip install -r requirements-build-lock.txt"
}
& $catalogPython (Join-Path $PSScriptRoot "scripts\check_build_dependencies.py")
if ($LASTEXITCODE -ne 0) { throw "Build dependency verification failed; follow the manual setup instructions above" }
$catalogVersion = (Get-Content -LiteralPath (Join-Path $PSScriptRoot "VERSION") -Raw).Trim()
if ($catalogVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid VERSION" }
$catalogStatus = @(& git status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "Build requires the independent Git repository" }
$catalogTags = @(& git tag --points-at HEAD)
if (-not $AllowDirty -and ($catalogStatus.Count -gt 0 -or $catalogTags -notcontains "v$catalogVersion")) {
    throw "Release build requires a clean tree tagged v$catalogVersion; use -AllowDirty for development"
}
$catalogVersion = (& $catalogPython -c "from app.version import build_version; print(build_version())").Trim()
if ($LASTEXITCODE -ne 0) { throw "Cannot determine catalog build version" }
& $catalogPython (Join-Path $PSScriptRoot "scripts\check_js.py")
if ($LASTEXITCODE -ne 0) { throw "Source checks failed" }
$catalogExe = Join-Path $PSScriptRoot "dist\NightSeam.exe"
$catalogRunning = @(Get-CimInstance Win32_Process -Filter "Name='NightSeam.exe'" | Where-Object { $_.ExecutablePath -eq $catalogExe })
if ($catalogRunning.Count -gt 0) { throw "Close this catalog instance before replacing its executable" }
$catalogBuildDir = Join-Path $PSScriptRoot "build"
New-Item -ItemType Directory -Path $catalogBuildDir -Force | Out-Null
$catalogBuildOwner = Join-Path $catalogBuildDir ".owner-pid"
if (Test-Path -LiteralPath $catalogBuildOwner) {
    $catalogOwnerId = (Get-Content -LiteralPath $catalogBuildOwner -Raw).Trim()
    if ($catalogOwnerId -notmatch '^\d+$') { throw "Unknown build owner; inspect build/.owner-pid" }
    if (Get-Process -Id ([int]$catalogOwnerId) -ErrorAction SilentlyContinue) { throw "Another build is active" }
}
# Keep an exclusive writer open so simultaneous builds cannot both claim it.
$catalogOwnerStream = [System.IO.File]::Open($catalogBuildOwner, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::Read)
try {
$catalogOwnerBytes = [System.Text.Encoding]::UTF8.GetBytes([string]$PID)
$catalogOwnerStream.SetLength(0)
$catalogOwnerStream.Write($catalogOwnerBytes, 0, $catalogOwnerBytes.Length)
$catalogOwnerStream.Flush()
# Preserve the previous installed executable before a successful replacement.
if (Test-Path -LiteralPath $catalogExe) {
    $backupName = "NightSeam.before-" + $catalogVersion + "-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".exe"
    $backupExe = Join-Path (Join-Path $PSScriptRoot "dist") $backupName
    Copy-Item -LiteralPath $catalogExe -Destination $backupExe
    if ((Get-FileHash -LiteralPath $catalogExe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $backupExe -Algorithm SHA256).Hash) {
        throw "Executable backup verification failed"
    }
}
$catalogBake = "BAKED_VERSION = '$catalogVersion'`n"
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "app\_version_baked.py"), $catalogBake, [System.Text.UTF8Encoding]::new($false))
& $catalogPython -m PyInstaller --clean --noconfirm --onefile --noconsole `
    --specpath "$PSScriptRoot\dist" --distpath "$PSScriptRoot\dist" --workpath "$PSScriptRoot\build" `
    --name NightSeam --icon "$PSScriptRoot\assets\app.ico" `
    --additional-hooks-dir "$PSScriptRoot\scripts\pyinstaller_hooks" `
    --add-data "$PSScriptRoot\static;static" --add-data "$PSScriptRoot\app\data;app\data" `
    --add-data "$PSScriptRoot\NOTICE;." --add-data "$PSScriptRoot\LICENSE;." `
    --collect-all uvicorn --collect-all webview --collect-all py_mini_racer --hidden-import python_multipart "$PSScriptRoot\main.py"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $catalogExe)) { throw "Compilation failed" }
} finally {
    $catalogOwnerStream.Dispose()
    Remove-Item -LiteralPath $catalogBuildOwner -ErrorAction Stop
}
# After successful compilation, retain three recovery executables and remove
# regenerable build scratch. A failed build retains its diagnostics/backups.
& $catalogPython (Join-Path $PSScriptRoot "scripts\clean_workspace.py") --apply --build-output --keep-backups 3
if ($LASTEXITCODE -ne 0) { throw "Build succeeded but workspace cleanup failed" }
Write-Host "Built $catalogExe ($catalogVersion)"
