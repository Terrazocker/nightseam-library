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

Decision recorded October 5, 2026: keep the catalog local. No GitHub repository
or origin remote is configured. main remains the default local development
branch; future remote setup requires a new user instruction.
A GitHub repository and executable release publication are distinct choices.
Upload only the verified release ZIP and its checksum/manifest when authorized.
Never commit source data/, dist/data/, logs/, executable backups or credentials.
The retired combined updater stays disabled until an independent update policy
is designed and tested. Public releases are not implied by a local build.
