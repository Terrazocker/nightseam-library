# Builds NightSeam.exe with PyInstaller in an isolated venv.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1 [-AllowDirty]
#
# The exe's version comes from ONE authoritative source:
#   git describe --tags --always --dirty
# A dirty tree or a HEAD that is not exactly a tag makes the build REFUSE to
# bake a version (it would claim a clean release for uncommitted work); pass
# -AllowDirty to build such a snapshot on purpose - the baked version then
# keeps its '-dirty' suffix / commit suffix.
param(
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv = ".venv-build"
$py = "$venv\Scripts\python.exe"
$devReqs = "requirements-dev.txt"

function Invoke-Native {
    # Runs a native command and fails LOUDLY on a non-zero exit code. The pip
    # steps used to be unchecked, so a failed install produced a build from a
    # stale/partial venv without any message.
    param([string]$Label, [string]$Exe, [string[]]$ExeArgs)
    & $Exe @ExeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (exit code $LASTEXITCODE)"
    }
}

# --- build venv (reused, but verified) ------------------------------------
if (-not (Test-Path $py)) {
    if (Test-Path $venv) {
        # half-created, half-deleted or copied from another checkout: it is
        # gitignored and fully regenerable, so start over instead of limping
        Write-Host "WARNING: $venv has no usable interpreter - recreating it." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venv
    }
    Write-Host "Creating $venv ..."
    Invoke-Native "python -m venv $venv" "python" @("-m", "venv", $venv)
    if (-not (Test-Path $py)) { throw "python -m venv produced no $py" }
}

$cfg = Join-Path $venv "pyvenv.cfg"
if (Test-Path $cfg) {
    $cfgText = Get-Content $cfg -Raw
    if ($cfgText -match "(?m)^\s*command\s*=\s*(.+)$") {
        # an absolute path token that names this venv but is NOT this checkout
        # means the venv came from somewhere else (e.g. the deprecated
        # D:\OpenCode\Object-Astro\.venv-build) and is probably incomplete
        foreach ($tok in ($Matches[1].Trim() -split '\s+')) {
            if ($tok -like '*:*' -and $tok -like "*$venv*" -and $tok -notlike "$PSScriptRoot*") {
                Write-Host "WARNING: $venv was created elsewhere ($tok)." -ForegroundColor Yellow
                Write-Host "         A venv moved from another checkout often misses packages;" -ForegroundColor Yellow
                Write-Host "         the dependency check below repairs it, otherwise delete $venv." -ForegroundColor Yellow
            }
        }
    }
}

# --- dependencies: install when incomplete or when the requirement files moved
$reqSig = ""
foreach ($f in @("requirements.txt", $devReqs)) {
    $item = Get-Item $f
    $reqSig += ("{0}={1}/{2};" -f $item.Name, $item.Length, $item.LastWriteTimeUtc.Ticks)
}
$sigFile = Join-Path $venv ".deps.sig"
$installedSig = ""
if (Test-Path $sigFile) { $installedSig = (Get-Content $sigFile -Raw).Trim() }

# import names the requirement files must provide (package name != import name):
#   fastapi uvicorn python-multipart pywebview  -> requirements.txt
#   pytest httpx pyinstaller Pillow             -> requirements-dev.txt
$depsProbe = @'
import importlib.util, sys
mods = ["fastapi", "uvicorn", "multipart", "webview",
        "PyInstaller", "PIL", "pytest", "httpx"]
missing = []
for name in mods:
    try:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    except Exception:
        missing.append(name)
print("MISSING:" + ",".join(missing))
sys.exit(1 if missing else 0)
'@
# written to a file instead of `python -c`: no command-line quoting to get wrong
$probeFile = Join-Path $venv ".deps_probe.py"
Set-Content -Path $probeFile -Value $depsProbe -Encoding ascii

function Get-MissingModules {
    # stdout is captured into a variable - never piped into a cmdlet (pipe
    # creation is denied under the DSH sandbox)
    $out = & $py $probeFile
    $rc = $LASTEXITCODE
    if ($rc -ne 0 -and $rc -ne 1) { return @("dependency probe failed (exit $rc)") }
    $line = @($out)[0]
    if (-not $line) { return @("dependency probe printed nothing (exit $rc)") }
    $text = "$line".Trim()
    if (-not $text.StartsWith("MISSING:")) { return @("unreadable probe output: $text") }
    $rest = $text.Substring("MISSING:".Length)
    if (-not $rest) { return @() }
    return @($rest.Split(","))
}

$missing = @(Get-MissingModules)
if ($missing.Count -gt 0 -or $reqSig -ne $installedSig) {
    if ($missing.Count -gt 0) {
        Write-Host "Build venv is missing: $($missing -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host "Requirements changed since the last install." -ForegroundColor Yellow
    }
    Write-Host "Installing dependencies into $venv (this can take minutes; with no"
    Write-Host "network access pip may stall - Ctrl+C and fix the venv by hand then)."
    Invoke-Native "pip install --upgrade pip wheel" $py @("-m", "pip", "install", "--upgrade", "pip", "wheel", "--quiet")
    Invoke-Native "pip install -r $devReqs" $py @("-m", "pip", "install", "-r", $devReqs, "--quiet")

    $missing = @(Get-MissingModules)
    if ($missing.Count -gt 0) {
        throw ("dependencies are STILL missing after 'pip install -r $devReqs': " +
               ($missing -join ", ") + ". The build would run against a partial " +
               "venv, so it is stopped here. Delete $venv and re-run once the " +
               "network is available.")
    }
    Set-Content -Path $sigFile -Value $reqSig -Encoding ascii
} else {
    Write-Host "Build venv OK - dependencies present and unchanged."
}

# make sure no running instance locks the output file
Get-Process NightSeam -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# --- bake the authoritative version ---------------------------------------
# git output is captured with `cmd /c ... 2>nul` into a VARIABLE. Never pipe a
# native command's stdout into a PowerShell cmdlet here: '| Measure-Object' /
# '| Select-Object' dies with a pipe-denied error under the DSH sandbox, and the
# build then silently baked the fallback version 'dev'.
$describeLines = @(& cmd /c "git describe --tags --always --dirty 2>nul")
if ($LASTEXITCODE -ne 0 -or $describeLines.Count -lt 1) {
    throw "git describe --tags --always --dirty produced no version - refusing to bake a guessed one (run the build from a git checkout)."
}
$describe = "$($describeLines[0])".Trim()
if (-not $describe) {
    throw "git describe --tags --always --dirty produced an empty version - refusing to bake a guessed one."
}

$changed = @(& cmd /c "git status --porcelain 2>nul")
$dirtyTree = $describe.EndsWith("-dirty")
& cmd /c "git describe --tags --exact-match HEAD >nul 2>nul"
$onTag = ($LASTEXITCODE -eq 0)

# TR-08 (0.6.32 review): `git describe --dirty` only sees TRACKED modifications,
# so an UNTRACKED file under static/ or app/data/ was compiled into the exe by
# `--add-data` while the exe still claimed a clean tag (`check_release.py`
# compares versions, not contents). `git status --porcelain` DOES list untracked,
# non-ignored paths, so that list is the gate. The gitignored bakery is filtered
# out because this script writes part of it (`app\_version_baked.py`) and the
# rest is local state (data/, logs/) that must never block a build.
$bakery = @('app/_version_baked.py', 'web/version.json', 'data', 'logs', '.pytest-tmp')
$bundled = @($changed | Where-Object {
    $line = "$_".Trim()
    if (-not $line) { return $false }
    $path = ($line -replace '^..\s+', '').Trim('"').TrimEnd('/')
    foreach ($b in $bakery) {
        if ($path -eq $b -or $path.StartsWith("$b/")) { return $false }
    }
    return $true
})

if ($dirtyTree -or (-not $onTag) -or ($bundled.Count -gt 0)) {
    $reasons = @()
    if ($dirtyTree) { $reasons += "the working tree is dirty ($($changed.Count) changed file(s))" }
    if ($bundled.Count -gt 0) {
        $shown = ($bundled | Select-Object -First 5) -join ", "
        $reasons += "untracked/modified path(s) would be BUNDLED ($($bundled.Count)): $shown"
    }
    if (-not $onTag) { $reasons += "HEAD is not exactly a git tag" }
    $why = $reasons -join " and "
    $ver = $describe.TrimStart("v")
    if (-not $AllowDirty) {
        throw ("refusing to bake version '$ver': $why. Commit and tag first " +
               "(AGENTS.md: 'Tag BEFORE rebuilding'), or re-run with " +
               "-AllowDirty to build a snapshot - it stays labelled '$ver', " +
               "never a clean tag.")
    }
    Write-Host "WARNING: $why - baking '$ver' anyway (-AllowDirty)." -ForegroundColor Yellow
}

$ver = $describe.TrimStart("v")
Set-Content -Encoding utf8 "app\_version_baked.py" "BAKED_VERSION = '$ver'"
$bakedText = Get-Content "app\_version_baked.py" -Raw
if (-not $bakedText.Contains("BAKED_VERSION = '$ver'")) {
    throw "the version bake did not stick - app\_version_baked.py does not contain '$ver'"
}
Write-Host "Baking version '$ver' into the build" -ForegroundColor Green

New-Item -ItemType Directory -Force -Path dist | Out-Null
# TO-08 follow-up (found by actually building 0.6.25): with --specpath the spec
# lives in dist/, and PyInstaller resolves --add-data / --icon sources RELATIVE TO
# THE SPEC DIRECTORY - so a relative "static" looked for dist\static and the build
# died with "Unable to find ...dist\static". Every source path is absolute now
# ($PSScriptRoot), and dist/work/spec are pinned so the build no longer depends on
# the caller's working directory.
& $py -m PyInstaller --clean --noconfirm --onefile --noconsole `
    --specpath "$PSScriptRoot\dist" `
    --distpath "$PSScriptRoot\dist" `
    --workpath "$PSScriptRoot\build" `
    --name NightSeam `
    --icon "$PSScriptRoot\assets\app.ico" `
    --add-data "$PSScriptRoot\static;static" `
    --add-data "$PSScriptRoot\app\data;app\data" `
    --collect-all uvicorn `
    --collect-all webview `
    --hidden-import python_multipart `
    "$PSScriptRoot\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $PSScriptRoot "dist\NightSeam.exe"
if (-not (Test-Path $exe)) { throw "Build produced no exe" }

# the exe must be NEWER than the bake, otherwise it cannot carry it
$bakedItem = Get-Item "app\_version_baked.py"
$exeItem = Get-Item $exe
if ($bakedItem.LastWriteTimeUtc -gt $exeItem.LastWriteTimeUtc) {
    throw "dist\NightSeam.exe ($($exeItem.LastWriteTime)) is older than the bake ($($bakedItem.LastWriteTime)) - the exe does not contain version '$ver'."
}

Write-Host ""
Write-Host ("Build finished -> dist\NightSeam.exe  (" +
    "{0:N1} MB, {1}, version {2})" -f ($exeItem.Length / 1MB),
    $exeItem.LastWriteTime, $ver)
Write-Host "Next: python scripts/check_release.py, then python scripts/make_release.py"
