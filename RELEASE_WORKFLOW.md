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
   and compile with build.ps1. Never retag. Existing executable replacement
   requires its verified backup and must respect an open application.
5. Run packaged check_browser.py --desktop on a private executable/data copy.
   Run check_release.py --no-stage, make_release.py and the full release guard.
   Keep checksums/version/member validation and the private-data exclusion intact.

## Remote and publication

The October 5, 2026 decision kept the catalog local initially. On October 11,
the user authorized origin setup and source publication to
https://github.com/Terrazocker/nightseam-catalog.git. main remains the default
development branch. Feature work uses codex/ branches.
A GitHub repository and executable release publication are distinct choices.
Upload only the verified release ZIP and its checksum/manifest when authorized.
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
