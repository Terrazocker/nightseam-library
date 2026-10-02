# Compile this independent catalog. VERSION is the project version authority.
param([switch]$AllowDirty)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$catalogPython = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $catalogPython)) {
    & python -m venv (Join-Path $PSScriptRoot ".venv-build")
    if ($LASTEXITCODE -ne 0) { throw "Cannot create build environment" }
}
& $catalogPython -c "import fastapi, uvicorn, multipart, webview, PyInstaller, PIL, pytest, httpx"
if ($LASTEXITCODE -ne 0) {
    & $catalogPython -m pip install -r (Join-Path $PSScriptRoot "requirements-dev.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "Cannot install build dependencies" }
}
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
    --add-data "$PSScriptRoot\static;static" --add-data "$PSScriptRoot\app\data;app\data" `
    --collect-all uvicorn --collect-all webview --hidden-import python_multipart "$PSScriptRoot\main.py"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $catalogExe)) { throw "Compilation failed" }
Write-Host "Built $catalogExe ($catalogVersion)"
