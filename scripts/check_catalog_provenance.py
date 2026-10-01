"""Guard the catalog provenance sidecar and its cache-bust pairing.

The sidecar is generated data, not a JavaScript module.  Both shells fetch or
load it as a complete JSON document, so the web copy, desktop copy, catalog
membership and the named ``CATALOG_PROVENANCE_TOKEN`` must stay in lockstep.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
WEB = ROOT / "web" / "catalog-provenance.json"
DESKTOP = ROOT / "app" / "data" / "catalog_provenance.json"
MANIFEST = ROOT / "web" / "catalog-provenance.manifest.json"
CATALOG = ROOT / "web" / "catalog.json"
CSV = ROOT / "app" / "data" / "openned.csv"
SELF = Path(__file__).resolve()

_TOKEN_RE = re.compile(r"const CATALOG_PROVENANCE_TOKEN = '(\d+)'")
_FETCH_RE = re.compile(
    r"fetch\('catalog-provenance\.json\?v=' \+ CATALOG_PROVENANCE_TOKEN\)")
_BASELINE_START = "# --- BEGIN GENERATED VERIFIED PROVENANCE BASELINE ---"
_BASELINE_END = "# --- END GENERATED VERIFIED PROVENANCE BASELINE ---"
# --- BEGIN GENERATED VERIFIED PROVENANCE BASELINE ---
_VERIFIED_BASELINE_JSON = r"""
{
 "sha256": "0d960875c28d2fefd9a1ca4449d1b28c242d0440d3fdb97067228f8247b0f180",
 "token": "1"
}
"""

# --- END GENERATED VERIFIED PROVENANCE BASELINE ---


def _git(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30)
    except Exception:  # noqa: BLE001 - git is an optional hardening check
        return None


def _git_ok() -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"])
    return bool(r and r.returncode == 0 and r.stdout.strip() == "true")


def token_of(text: str) -> str | None:
    m = _TOKEN_RE.search(text)
    return m.group(1) if m else None


def load_baseline(src: str | None = None) -> dict:
    text = SELF.read_text(encoding="utf-8") if src is None else src
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    if start not in text or end not in text:
        return {}
    block = text.split(start, 1)[1].split(end, 1)[0]
    m = re.search(r'_VERIFIED_BASELINE_JSON\s*=\s*r?"""(.*?)"""', block, re.S)
    if not m:
        return {}
    try:
        value = json.loads(m.group(1))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline_problems(token: str | None, digest: str,
                      baseline: dict) -> list[str]:
    base_token = str(baseline.get("token") or "")
    base_sha = str(baseline.get("sha256") or "")
    if not base_token or not base_sha:
        return [
            "no verified provenance baseline embedded in "
            "scripts/check_catalog_provenance.py - run `python "
            "scripts/check_catalog_provenance.py --update` and commit it"]
    if digest != base_sha and token == base_token:
        return [
            "catalog-provenance.json bytes differ from the verified baseline "
            f"but CATALOG_PROVENANCE_TOKEN is still '{token}' - bump the token "
            "and refresh the baseline"]
    if digest != base_sha or token != base_token:
        return [
            "the verified catalog provenance baseline is stale (run `python "
            "scripts/check_catalog_provenance.py --update` and commit it with "
            "this change)"]
    return []


def write_baseline() -> Path | None:
    src = SELF.read_text(encoding="utf-8")
    token = token_of(APP.read_text(encoding="utf-8"))
    digest = _sha(WEB)
    baseline = load_baseline(src)
    if (baseline.get("sha256") and baseline.get("token") == token
            and baseline.get("sha256") != digest):
        return None
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    block = (f'{start}_VERIFIED_BASELINE_JSON = r"""\n'
             + json.dumps({"sha256": digest, "token": token}, indent=1,
                          sort_keys=True)
             + f'\n"""\n{end}')
    SELF.write_text(src.split(start, 1)[0] + block + src.split(end, 1)[1],
                    encoding="utf-8")
    return SELF


def scan() -> list[str]:
    problems: list[str] = []
    app_text = APP.read_text(encoding="utf-8")
    token = token_of(app_text)
    if token is None:
        problems.append("web/app.js has no CATALOG_PROVENANCE_TOKEN")
    if len(_TOKEN_RE.findall(app_text)) != 1:
        problems.append("web/app.js must declare exactly one CATALOG_PROVENANCE_TOKEN")
    if len(_FETCH_RE.findall(app_text)) != 1:
        problems.append(
            "web/app.js must fetch catalog-provenance.json through the named token")

    if not WEB.is_file() or not DESKTOP.is_file():
        problems.append("desktop and web catalog provenance sidecars are both required")
        return problems
    if WEB.read_bytes() != DESKTOP.read_bytes():
        problems.append("desktop and web catalog provenance sidecars differ")
    digest = _sha(WEB)

    if not MANIFEST.is_file():
        problems.append("web/catalog-provenance.manifest.json is missing")
    else:
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"catalog provenance manifest is not valid JSON: {exc}")
            manifest = {}
        if token is not None and str(manifest.get("token")) != token:
            problems.append("catalog provenance manifest token does not match app token")
        if manifest.get("sha256") != digest:
            problems.append("catalog provenance manifest sha256 does not match sidecar")

    try:
        payload = json.loads(WEB.read_text(encoding="utf-8"))
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"catalog provenance or catalog JSON is invalid: {exc}")
        return problems
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        problems.append("catalog provenance schema_version must be 1")
        return problems
    objects = payload.get("objects")
    if not isinstance(objects, dict):
        problems.append("catalog provenance objects must be an object")
    elif isinstance(catalog, list):
        ids = {str(row.get("id")) for row in catalog if isinstance(row, dict)}
        if set(objects) != ids:
            problems.append("catalog provenance ids do not match web/catalog.json")
        if payload.get("catalog", {}).get("row_count") != len(ids):
            problems.append("catalog provenance row_count does not match catalog")
    if payload.get("catalog", {}).get("sha256") != _sha(CSV):
        problems.append("catalog provenance catalog.sha256 does not match openned.csv")

    problems += baseline_problems(token, digest, load_baseline())

    if _git_ok():
        changed = _git(["diff", "--quiet", "HEAD", "--", str(WEB.relative_to(ROOT))])
        if changed is not None and changed.returncode != 0:
            diff = _git(["diff", "HEAD", "--", "web/app.js"])
            if diff is not None and "CATALOG_PROVENANCE_TOKEN" not in (diff.stdout or ""):
                problems.append(
                    "catalog-provenance.json differs from HEAD but its app token "
                    "does not (bump CATALOG_PROVENANCE_TOKEN)")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if "--update" in args:
        if write_baseline() is None:
            print("check_catalog_provenance: REFUSED - bump the token first")
            return 1
        print("check_catalog_provenance: wrote the verified baseline")
        return 0
    problems = scan()
    for problem in problems:
        print("  catalog-provenance: " + problem)
    print("check_catalog_provenance:", "FAIL (%d)" % len(problems)
          if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
