"""Build the public release package for the desktop app.

Run AFTER build.ps1 (which produced dist\\NightSeam.exe with the baked
version), from the repo root:

    python scripts/make_release.py [--allow-dirty]

Stages a CLEAN package (exe + LICENSE + NOTICE — never the
developer's own dist\\data) into dist/release/ and writes:

  dist/release/NightSeam-<version>.zip     the distributable
  dist/release/version.json                  version + url + sha256
  dist/release/ns-version.js                 JSONP "window.__NS_RELEASE=..."

The website serves release/ content under /downloads/; the landing page
reads version.json (same origin) and the desktop app's update checker loads
ns-version.js (script tag — CORS-free).

Version integrity: <version> is `git describe --tags --always --dirty` —
the same source build.ps1 bakes into the exe and web/build_web.py bakes into
web/version.json.  Before anything is staged, scripts/check_release.py
verifies that the exe's baked version, web/version.json, landing/changelog.json
and the staged artifacts all agree with it; the run aborts on any ERROR, so a
stale exe can no longer be published under a fresh version.  --allow-dirty
forwards to that check and only forgives a dirty tree / off-tag HEAD (the
published version then carries '-dirty' or a commit suffix, never a clean tag).
"""
import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ -> check_release

from check_release import (  # noqa: E402  (needs the path insert above)
    ERROR, ROOT as CHECK_ROOT, check_core_findings, check_source_module_findings,
    collect, evaluate, git_describe, normalize_version,
)

DIST = ROOT / "dist"
EXE = DIST / "NightSeam.exe"
STAGE = DIST / "release"
ZIP_NAME_PREFIX = "NightSeam-"
RELEASE_JS = "ns-version.js"

DOCS = ["LICENSE", "NOTICE"]

assert CHECK_ROOT == ROOT, "check_release.py must live in this repo's scripts/"


def describe():
    """The ONE authoritative version: `git describe --tags --always --dirty`."""
    raw = git_describe(ROOT)
    if not raw:
        raise SystemExit(
            "`git describe --tags --always --dirty` produced nothing - run "
            "make_release.py from the repo root on a machine with git and at "
            "least one tag.")
    return normalize_version(raw)


def run_check(version, allow_dirty, include_stage, when):
    """Run the release integrity check; abort on the first ERROR."""
    findings = evaluate(collect(ROOT), allow_dirty=allow_dirty,
                        include_stage=include_stage)
    # build.ps1 bundles static/ and would ship a stale core bake, so the core
    # drift guard must block STAGING too (check_release.run() already did this,
    # make_release's own "ok" could not see it - FIX-9, review TL1).
    findings += check_core_findings(ROOT)
    findings += check_source_module_findings(ROOT)
    errors = [f for f in findings if f.level == ERROR]
    for finding in findings:
        print("  " + finding.render())
    if errors:
        raise SystemExit(
            f"\nrelease check failed {when} ({len(errors)} error(s)) - nothing "
            f"was staged. Fix the above, or see `python scripts/check_release.py`.")
    print(f"release check {when}: ok (version {version})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--allow-dirty", action="store_true",
                        help="accept a dirty tree / HEAD not on a tag (the "
                             "published version then carries -dirty or a "
                             "commit suffix)")
    args = parser.parse_args(argv)

    if not EXE.is_file():
        raise SystemExit(f"missing {EXE} - run build.ps1 first")
    version = describe()

    # fail BEFORE staging anything: the exe's bake, web/version.json and
    # landing/changelog.json must all agree with this version
    print(f"checking release integrity for {version} ...")
    run_check(version, args.allow_dirty, include_stage=False,
              when="before staging")

    # fresh stage
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    # copy the executable + docs
    shutil.copy2(EXE, STAGE / "NightSeam.exe")
    for doc in DOCS:
        src = ROOT / doc
        if src.is_file():
            shutil.copy2(src, STAGE / src.name)

    # zip (exe + docs only — the app creates its own data/ on first run)
    zip_name = f"{ZIP_NAME_PREFIX}{version}.zip"
    zip_path = STAGE / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(STAGE.iterdir()):
            if f.name == zip_name:
                continue
            zf.write(f, arcname=f.name)

    # stable name for the landing page's no-JS fallback link (the versioned zip
    # alone cannot be linked from static HTML)
    shutil.copy2(zip_path, STAGE / "NightSeam-latest.zip")

    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size
    url = f"/downloads/{zip_name}"

    (STAGE / "version.json").write_text(
        json.dumps({"version": version, "url": url, "sha256": sha,
                    "size": size}, indent=2) + "\n", encoding="utf-8")

    payload = json.dumps({"version": version, "url": url})
    (STAGE / RELEASE_JS).write_text(
        f"window.__NS_RELEASE={payload};\n", encoding="utf-8")

    # verify what was just written (zip name, version.json, ns-version.js and
    # the latest-zip copy must all carry the same version); the tree state is
    # already settled, so only real contradictions can fail here
    run_check(version, True, include_stage=True, when="after staging")

    print(f"release: {zip_path} ({size/1e6:.1f} MB)")
    print(f"version: {version}")
    print(f"sha256:  {sha}")
    print(f"stage:   {STAGE}")


if __name__ == "__main__":
    main()
