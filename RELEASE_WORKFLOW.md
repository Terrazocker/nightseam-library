# Independent catalog repository and release workflow

The default local development branch is main. codex/direction-b-ui and v0.1.0
preserve the extracted release baseline. VERSION owns catalog numbering. The
website's release history, source and deployment are independent.

## Existing 0.1.0 release

v0.1.0 remains at 97d840dabe260f2cf3dd85720f37c98c2610cf76. The existing executable
and ZIP were compiled/staged at that clean tag. Documentation commits afterward
are development snapshots; do not move the tag or label a later dirty/off-tag
build a clean 0.1.0 release. Source validation can use check_release.py with
--allow-dirty --source-only --no-stage while work is in progress.

## Prepare the next release

1. Work on a codex/ feature branch; review and integrate into main.
2. Update VERSION deliberately for the next release, curate release notes and
   record actual validation in RELEASES.md.
3. Run relevant tests while editing, the full suite at completion, check_js.py
   and the source release guard. Tests/scratch stay in ignored logs/.
4. Commit the reviewed changes, create a new vVERSION tag at the clean commit,
   and compile with the platform builder below. Never retag. Existing executable replacement
   requires its verified backup and must respect an open application.
5. Run packaged check_browser.py --desktop on a private executable/data copy.
   Complete the platform-specific runtime and distribution gates below. Keep
   checksums/version/member validation and the private-data exclusion intact.

## Windows distribution

Build with `build.ps1`, run `check_release.py --no-stage`, then
`make_release.py` and the full release guard. These executable/ZIP gates are
Windows-specific. Follow [Windows manual acceptance](docs/windows-manual-acceptance.md)
for clean disconnected VM and physical display checks; CI cannot complete all
of those interactive checks.

## Linux distribution

Use glibc Linux x86_64, CPython 3.12, Node and binutils with the independent
`requirements-build-linux-lock.txt` closure. Follow [LINUX.md](LINUX.md) to
prepare `.venv-build`, then run:

```sh
.venv-build/bin/python scripts/build_linux.py
.venv-build/bin/python scripts/check_linux_package.py \
  --bundle dist/NightSeam-Catalog-VERSION-linux-x86_64.tar.gz
.venv-build/bin/python scripts/check_linux_clean_system.py \
  --bundle dist/NightSeam-Catalog-VERSION-linux-x86_64.tar.gz \
  --image ubuntu:22.04
```

Replace VERSION with the actual frozen build version. Use the separate browser
environment from CI to run `scripts/check_browser.py --desktop --channel chromium
--exe dist/NightSeam-Catalog-VERSION-linux-x86_64/NightSeam`. The required CI
gate additionally tests the same tarball on pinned Ubuntu 22.04/24.04 and Debian
12 minimal images with external networking disabled. Retain their reports,
packaged browser results, tarball, checksum and `build-info.json` together.
The manifest must record clean source provenance, the tested executable hash
and actual GLIBC requirements. Do not use `--allow-dirty` for a release.

The Linux builder produces the tarball/checksum directly; do not pass it through
the Windows-only `make_release.py` or claim its ZIP checks validated a tarball.
Publish only a reviewed, tested archive with its checksum and build metadata
after release publication is explicitly authorized. Complete
[Linux desktop acceptance](docs/linux-manual-acceptance.md) before claiming
interactive desktop support. ARM, musl and GTK/Qt native mode remain outside
the current Linux target.

## Remote and publication

The October 5, 2026 decision kept the catalog local initially. On October 11,
the user authorized origin setup and source publication to
https://github.com/Terrazocker/nightseam-catalog.git. main remains the default
development branch. Feature work uses codex/ branches.
A GitHub repository and executable release publication are distinct choices.
Upload only verified platform archives and their checksums/manifests when authorized.
Never commit source data/, dist/data/, logs/, executable backups or credentials.
The retired combined updater stays disabled until an independent update policy
is designed and tested. Public releases are not implied by a local build.

## Build environment

Use Windows x64 CPython 3.12 and requirements-build-lock.txt for tested desktop
artifacts. build.ps1 validates exact package versions and dependency closure
before modifying build outputs. It does not create environments or install
packages silently. The initial manual setup is:

```powershell
py -3.12 -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r requirements-build-lock.txt
.venv-build\Scripts\python.exe scripts/check_build_dependencies.py
```

Update the lock deliberately with dependency review, full source validation and
a freshly compiled private-data browser gate. A package-version lock does not
claim identical bytes across operating-system or Python patch changes.
