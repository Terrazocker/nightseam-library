"""BUG-003: the production smoke must judge the DOCUMENT response only.

`python scripts/check_browser.py --prod-url ...` used to search `version.json` /
`catalog.manifest.json` for a `Content-Security-Policy` when the HTML carried
none, and counted a hit as a pass. A CSP protects only the response it is
attached to - so a deployment whose pages had NO policy still reported "the host
applies the policy we ship", which is false assurance for exactly the property
the check exists to prove. These are the offline halves: the rule table and the
shape of the check itself.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "browser" / "harness.py"
_HARNESS_NAME = "_nightseam_local_browser_harness"
_HARNESS_SPEC = importlib.util.spec_from_file_location(_HARNESS_NAME, HARNESS)
assert _HARNESS_SPEC and _HARNESS_SPEC.loader
_HARNESS = importlib.util.module_from_spec(_HARNESS_SPEC)
sys.modules[_HARNESS_NAME] = _HARNESS
_HARNESS_SPEC.loader.exec_module(_HARNESS)
_csp_problems = _HARNESS._csp_problems


def _problems(policy, document=False):
    return _csp_problems(policy, document=document)


# The shipped web policy (web/.htaccess) with the hash of the current bootstrap.
GOOD_WEB = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' "
            "data:; connect-src 'self'; base-uri 'self'")


def test_the_document_check_is_the_one_the_smoke_uses():
    """The smoke must pass `document=True`, i.e. it must NOT require the web
    shell's inline-bootstrap hash (the entry point may be the static desktop
    shell, whose index.html has a different one) - but it must keep every policy
    rule, and it must not consult another path for the policy.

    It must also accept the meta carrier: this host serves /astro from nginx and
    exposes no directive field (measured 2026-09-17), so the enforced policy ships
    inside web/index.html. A header still wins when both are present."""
    src = HARNESS.read_text(encoding="utf-8")
    smoke = src.split("def _prod_smoke(", 1)[1]
    assert "document=True" in smoke
    assert "from_meta=from_meta" in smoke, (
        "the smoke must evaluate the meta policy under the meta rules")
    assert 'meta[http-equiv="Content-Security-Policy" i]' in smoke, (
        "the smoke no longer reads the document's meta policy")
    # Look at the CODE, not the comment that explains the removed fallback.
    code = "\n".join(ln for ln in smoke.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "catalog.manifest.json" not in code, (
        "the production smoke still accepts a CSP from another path")
    assert "for probe in" not in code
    assert "urllib.request.urlopen" not in code, (
        "the smoke must read the policy from the page response only")


def test_a_missing_document_policy_is_a_hard_failure():
    """The regression itself: `None` (no header on the HTML) must produce a
    problem, never an empty list that the caller reads as a pass."""
    assert _problems(None, document=True), "a missing document CSP passed"
    assert _problems(None, document=False)


def test_the_document_check_keeps_every_policy_rule():
    for bad in ("default-src 'self'",                       # no script-src
                "default-src 'self'; script-src 'unsafe-inline'",
                "default-src 'self'; script-src 'self' 'unsafe-eval'",
                "default-src 'self'; script-src 'self'"):    # no connect-src
        assert _problems(bad, document=True), bad


def test_a_good_document_policy_passes():
    """No false positives: the policy web/.htaccess ships passes as a document
    policy."""
    assert _problems(GOOD_WEB, document=True) == []


def test_the_document_check_does_not_require_a_removed_bootstrap_hash():
    """The current web shell has no inline bootstrap, so neither document nor
    header checks should require a script hash that no longer exists."""
    without_hash = GOOD_WEB.replace(
        " 'sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='", "")
    assert _problems(without_hash, document=False) == []
    assert _problems(without_hash, document=True) == []


def test_the_shipped_web_policy_is_usable_as_a_document_policy():
    """The policy the repo actually ships (web/.htaccess) passes the document
    rule once it reaches the HTML - so the fix required at the host layer is
    "apply this", not "change this"."""
    text = (ROOT / "web" / ".htaccess").read_text(encoding="utf-8")
    m = re.search(r'Header\s+\S+\s+set\s+Content-Security-Policy\s+"([^"]+)"',
                  text)
    assert m, "web/.htaccess must set a Content-Security-Policy header"
    assert _problems(m.group(1), document=True) == []
