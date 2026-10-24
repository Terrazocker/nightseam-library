# Independent catalog repository and release workflow

The default development branch is main. The v0.1.0 commit preserves the
extracted release baseline. VERSION owns catalog numbering. The
website's release history, source and deployment are independent.

## Existing 0.1.0 release

v0.1.0 remains at ffed475b9cb06faa62b873ec7cce1dad6f6d9787. The existing executable
and ZIP were compiled/staged at that clean tag. Documentation commits afterward
are development snapshots; do not move the tag or label a later dirty/off-tag
build a clean 0.1.0 release. Source validation can use check_release.py with
--allow-dirty --source-only --no-stage while work is in progress.

## Prepare the next release

1. Work on a feature branch; open a pull request and merge into main once CI
   passes.
2. Update VERSION deliberately for the next release, curate release notes and
   record actual validation in RELEASES.md.
3. Run relevant tests while editing, the full suite at completion, check_js.py
   and the source release guard. Tests/scratch stay in ignored logs/.
4. Merge the reviewed changes into main, then push a new vVERSION tag at that
   merged commit. Never retag. The release workflow below compiles and gates
   both platforms; local builds with the platform builders remain possible.
   Existing executable replacement requires its verified backup and must
   respect an open application.
5. Run packaged check_browser.py --desktop on a private executable/data copy.
   Complete the platform-specific runtime and distribution gates below. Keep
   checksums/version/member validation and the private-data exclusion intact.

## GitHub release automation

`.github/workflows/release.yml` runs when a `v*` tag is pushed. It refuses a
tag that differs from `v` + VERSION or that is not on main, then on fresh
runners:

- Windows (windows-2022): the full suite, `build.ps1` and `make_release.py`
  without `-AllowDirty`, the packaged browser and native-window gates, and a
  final `check_release.py` proving the ZIP still holds the gated executable.
- Linux (ubuntu-22.04): `build_linux.py`, the restricted-PATH smoke, the three
  pinned clean offline systems and the packaged browser gate.

It then creates a **draft** GitHub release named after the tag. Its body is
the RELEASES.md section headed `## VERSION — date`; a tag without that
section is refused. The draft carries these files, each version producing its
own names:

- `NightSeam-Catalog-VERSION-windows-x64.zip` and its `.sha256`
- `NightSeam-Catalog-VERSION-linux-x86_64.tar.gz` and its `.sha256`

Gate evidence and `build-info.json` stay as workflow artifacts on the run.
A draft is visible only to repository collaborators. Complete the manual
Windows and Linux acceptance on the drafted archives, then publish the draft
from the GitHub Releases page; that click is the explicit publication
authorization. Pull requests touching the release path, and manual runs from
the Actions tab, are dry runs that build and gate the same way with
`-g<commit>` versions and keep only workflow artifacts.

## Windows distribution

Build with `build.ps1`, run `check_release.py --no-stage`, then
`make_release.py` (which stages `NightSeam-Catalog-VERSION-windows-x64.zip`)
and the full release guard. These executable/ZIP gates are
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

The source is public at https://github.com/Terrazocker/nightseam-library since October 18, 2026.
main is the default development branch; feature work uses short-lived branches
merged by pull request. CI (`.github/workflows/windows-validation.yml`) runs the
Linux and Windows suites, both packaged builds and the browser gates on every
pull request and push to main, plus a weekly scheduled run with the packaged
import benchmark. Optional benchmarks are started manually from the Actions tab.
Pushed release tags create draft releases through `.github/workflows/release.yml` (above).
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
