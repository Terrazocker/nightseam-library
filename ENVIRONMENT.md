# ENVIRONMENT.md - measured facts about the agent sandbox

This file records environment behavior only. Current product and test-harness
defects are tracked in `BUGS.md`; do not reclassify them as sandbox limitations.

Moved out of `AGENTS.md` (which had grown past the workspace instruction budget
and was being truncated). These are measurements of THIS machine's sandbox
token, not properties of the app - re-measure before trusting them if the
environment changes. `AGENTS.md` links here from its Commands section.

- Sandbox shell gotchas (RE-MEASURED 2026-09-13, workspace-write mode):
  - Plain pipes are FINE here: `subprocess.run(..., capture_output=True)` works,
    PowerShell `... | Select-Object` works, and `python web/build_web.py version`
    exits 0 (it wrote web/version.json). The blanket "piped stdio dies" notes below
    were true of the older read-only mode - MEASURE before working around a
    restriction.
  - What IS denied: NAMED pipes and asyncio's OVERLAPPED pipes
    (`_winapi.CreatePipe` WinError 5). The first stops Chromium (mojo IPC) and the
    second stops the Playwright DRIVER - even though plain subprocess pipes work.
    Anything that needs a browser or an asyncio subprocess must run UNSANDBOXED
    (see the browser note below).
  - If a release step does fail from `_winapi.CreatePipe`, bake `web/version.json`
    by hand (`{"version","describe","built"}`, `version = describe.lstrip("v")`,
    `built` = ISO-8601 UTC) or re-run it with full access.
  - Heredocs / inline `python -c` with quotes break - write probe scripts to
    `%USERPROFILE%\AppData\Local\Temp\opencode\` and run them.
  - **Scratch dirs in the REPO ROOT are a trap:** a pytest tmp dir or review
    scratch created there can get an owner-SID-only DACL that the SANDBOXED token
    cannot even list, and `icacls`/`takeown`/`robocopy`/`rd` cannot repair it
    (no WRITE_DAC, not admin). An UNSANDBOXED delete removes them normally
    (measured: all 20 `pt-*`/`.rv-*` dirs gone in one escalated command), but the
    durable rule is to keep scratch under the gitignored `logs/`, whose dirs the
    sandbox can create AND delete.
- `git commit`/`git tag` work, but git's background auto-maintenance then prints
  pack-objects pipe errors AND pops a harmless Windows dialog on the user's
  desktop (a detached `git maintenance run --auto` child cannot init under the
  restricted token). This repo's LOCAL config sets `maintenance.auto=false` +
  `gc.auto=0` to stop it (invisible to `git status`, costs no release window).
  Never run `git gc` from the sandboxed shell.
- **Browser automation needs an UNSANDBOXED command (measured, 2026-09-13).**
  Under the workspace-write sandbox token NO Chromium can start: its mojo IPC
  creates NAMED PIPES, so every variant (`--headless=new`, `--headless`,
  `--no-sandbox`, `--single-process`, `--in-process-gpu`) dies with
  `FATAL:mojo\public\cpp\platform\platform_channel.cc: Check failed: . :
  Zugriff verweigert (0x5)`. Playwright's own driver cannot even spawn - asyncio
  needs OVERLAPPED pipes (`_winapi.CreatePipe` WinError 5), while plain pipes are
  fine (that is why `subprocess.run(..., capture_output=True)` works). Under an
  unsandboxed command the gate runs: `python scripts/check_browser.py --web` =>
  **12/12 passed, exit 0** on the 0.6.24 tree. Firefox stays alive headless but
  never opens its remote-debugging port. So run the gate with a one-shot
  escalation or from a normal shell; `--desktop` additionally needs a FRESH
  `dist\NightSeam.exe` (`build.ps1`). For a manual look, the DEPLOYED app is
  verified via FTP download + `Get-FileHash` against the local file, plus an HTTP
  fetch of `/astro/version.json` and `/astro/index.html`. If driving CDP by hand:
  Chrome needs `--remote-allow-origins=*`, `Network.setCacheDisabled` + a `?v=`
  query, an absolute `--user-data-dir` inside the workspace, and `S` (state.js)
  is not global - probe the DOM/API.

## Agent sessions in this environment (measured 2026-09-13, workspace-write)

Commands an agent runs go through a sandbox TOKEN. That is what decides whether a
tool works here, so measure before working around anything:

- **Works sandboxed:** `python -m pytest`, every `scripts/check_*.py` guard, git,
  node, PowerShell pipelines (`... | Select-Object`), `subprocess` with
  `capture_output=True`, `python web/build_web.py version`, FTPS deploy, and
  scratch dirs under the gitignored `logs/` (created AND deleted freely).
- **Needs ONE unsandboxed command** (`sandbox_permissions: danger-full-access`,
  which raises an approval prompt for the user):
  * **the browser gate.** Chromium's mojo IPC needs NAMED PIPES, which the sandbox
    token refuses (`FATAL platform_channel.cc ... Zugriff verweigert (0x5)`), so no
    Chromium flag combination helps. Measured unsandboxed:
    `python scripts/check_browser.py --web` => **12/12 passed, exit 0**. Sandboxed,
    `check_browser.py` reports exit 2 (infrastructure) honestly, because
    `run_gate()` converts the driver failure - see TO-02.
  * **removing repo-root scratch dirs that an EARLIER sandbox token created.**
    They deny even listing, and `takeown` needs admin. `logs/` never has this
    problem, which is why the rule is "scratch under `logs/`, never the repo root".
- **The Playwright driver cannot spawn sandboxed:** asyncio needs OVERLAPPED pipes
  (`_winapi.CreatePipe`, WinError 5) even though plain pipes work. Never "probe"
  it with a throw-away start - `check_browser.run_gate()` owns the exit-2
  conversion and the harness starts the driver exactly ONCE
  (`tests/test_browser_gate.py` pins both).
- **Long commands:** run them as background jobs and read the output later; the
  browser gate is ~20 s for `--web`, a few minutes for `--all` (which also needs a
  fresh `dist\NightSeam.exe`).
- **Release work from an agent session**, in order: add the curated
  `CURATED[X.Y.Z]` entry and commit it with the release -> `python
  scripts/pre_tag_check.py X.Y.Z` -> `git tag vX.Y.Z` -> `scripts/make_release.py`
  + `web/build_web.py version` (at the tag, clean tree) -> `scripts/check_release.py`
  -> `python scripts/check_browser.py --all` (unsandboxed) -> FTPS uploads
  (`uploadtree web /astro`, the `/downloads/` artifacts, `landing` last) ->
  regenerate and commit `landing/changelog.json`.
