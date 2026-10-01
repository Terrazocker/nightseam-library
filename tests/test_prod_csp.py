"""The production-CSP check for /astro (read-only, run by the browser gate).

`python scripts/check_browser.py --prod-url https://nightseam.de/astro/` is the
only place the DEPLOYED policy can be inspected from this repo, so the parsing
and the rules live in `tests/browser/harness.py::_csp_problems` and are pinned
here with absolute cases (the gate itself needs a live URL).

Why a policy at all: the web shell is the PUBLIC origin and it loads third-party
images while holding the user's sites, rigs, targets and panoramas in IndexedDB
and localStorage - the desktop shell ships a strict CSP in `app/server.py` and
the static shell had none.
"""

import importlib.util
import sys
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "browser" / "harness.py"
_HARNESS_NAME = "_nightseam_local_browser_harness"
_HARNESS_SPEC = importlib.util.spec_from_file_location(_HARNESS_NAME, HARNESS)
assert _HARNESS_SPEC and _HARNESS_SPEC.loader
_HARNESS = importlib.util.module_from_spec(_HARNESS_SPEC)
sys.modules[_HARNESS_NAME] = _HARNESS
_HARNESS_SPEC.loader.exec_module(_HARNESS)
_csp_problems = _HARNESS._csp_problems
_inline_script_hashes = _HARNESS._inline_script_hashes

# The deployed policy, copied from web/.htaccess.
GOOD = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: "
    "https://upload.wikimedia.org; connect-src 'self' https://en.wikipedia.org "
    "https://api.open-meteo.com https://air-quality-api.open-meteo.com; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
    "object-src 'none'"
)


def test_the_repo_ships_a_policy_and_it_passes_its_own_check():
    """The header the host applies must satisfy the rules, and its inline-script
    hash must be the CURRENT one - an edited index.html bootstrap would silently
    stop applying the saved theme before paint."""
    htaccess = ROOT / "web" / ".htaccess"
    assert htaccess.is_file(), (
        "web/.htaccess is missing - the static shell then has no CSP at all")
    text = htaccess.read_text(encoding="utf-8")
    # only a real directive: prose that merely NAMES the header must not be
    # parsed (a placeholder in a comment once hijacked this assertion)
    m = re.search(r'^\s*Header\s+\S+\s+set\s+Content-Security-Policy\s+"([^"]+)"',
                  text, re.M)
    assert m, ".htaccess must set a Content-Security-Policy header"
    policy = m.group(1)
    assert _csp_problems(policy) == []
    # no 'unsafe-inline' for scripts: the shell has no inline handler
    script_src = policy.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src
    assert "unsafe-eval" not in policy


def test_the_web_shell_has_no_inline_theme_bootstrap():
    """The one-theme shell no longer needs pre-paint theme selection code."""
    hashes = _inline_script_hashes()
    assert hashes == []
    text = (ROOT / "web" / ".htaccess").read_text(encoding="utf-8")
    assert "sha256-" not in text
    # the computed hash must come from the SHIPPED html, not a literal in a test
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-theme="workbench"' in html
    assert "nightseam_theme" not in html


def test_the_weather_provider_is_allowlisted_in_both_csp_carriers():
    """The browser-side weather fetch must be allowed by either carrier."""
    text = (ROOT / "web" / ".htaccess").read_text(encoding="utf-8")
    header = re.search(
        r'^\s*Header\s+\S+\s+set\s+Content-Security-Policy\s+"([^"]+)"',
        text, re.M).group(1)
    meta = _meta_policy()
    expected = "connect-src 'self' https://en.wikipedia.org https://api.open-meteo.com https://air-quality-api.open-meteo.com"
    assert expected in header
    assert meta and expected in meta


@pytest.mark.parametrize("policy,expectation", [
    (None, "no Content-Security-Policy"),
    ("default-src 'self'", "no script-src"),
    (GOOD.replace("script-src 'self'", "script-src 'unsafe-inline'"),
     "unsafe-inline"),
    (GOOD.replace("connect-src 'self' https://en.wikipedia.org "
                  "https://api.open-meteo.com https://air-quality-api.open-meteo.com; ", ""),
     "no connect-src"),
    (GOOD.replace("script-src 'self'", "script-src 'self' 'unsafe-eval'"),
     "unsafe-eval"),
])
def test_a_broken_policy_is_rejected(policy, expectation):
    problems = _csp_problems(policy)
    assert any(expectation in p for p in problems), (expectation, problems)


def test_unsafe_inline_is_flagged_in_script_src():
    """The current shell has no inline scripts, so script-src must not allow
    inline code regardless of whether a hash happens to accompany it."""
    policy = GOOD.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
    assert any("unsafe-inline" in p for p in _csp_problems(policy))


# ---------------------------------------------------------------- meta carrier
# BUG-003 workaround: the deployed host answers /astro from nginx and exposes no
# directive field, so the policy also ships INSIDE web/index.html. Measured
# 2026-09-17: `server: nginx`, `content-type: text/html`, no policy header, no
# meta policy - so the document was unprotected.

def _meta_policy() -> str | None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    m = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"',
        html, re.I)
    return m.group(1) if m else None


def test_the_document_ships_a_meta_policy_that_passes():
    """The meta policy must satisfy the SAME rules as the header form, so a
    visitor is protected regardless of which carrier the host ends up serving."""
    policy = _meta_policy()
    assert policy, ("web/index.html must carry the meta CSP - nginx serves the "
                    "HTML on this host, so web/.htaccess cannot reach it")
    assert _csp_problems(policy, document=True, from_meta=True) == []
    # This one-theme shell has no inline script bootstrap.
    script_src = policy.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src
    assert "sha256-" not in script_src
    assert "unsafe-eval" not in policy


def test_the_meta_policy_carries_no_header_only_directive():
    """`frame-ancestors` (and report-uri/sandbox) are IGNORED in a meta policy.
    Naming one there would look like protection while providing none - the exact
    false-assurance shape BUG-003 is about - so the check must report it."""
    assert "frame-ancestors" not in (_meta_policy() or "")
    problems = _csp_problems("default-src 'self'; script-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'",
                             document=True, from_meta=True)
    assert any("frame-ancestors is IGNORED" in p for p in problems), problems
    # ... and the header form is allowed to keep it
    assert _csp_problems(GOOD, document=True, from_meta=False) == []


def test_both_carriers_agree_on_the_directives_they_share():
    """The meta policy and web/.htaccess must not drift: same sources, same hash.

    Two differences are documented and asserted here, so neither carrier can
    change silently:
      * the header form carries `frame-ancestors 'none'`; the meta form cannot
        (browsers ignore it there), and
      * BOTH must carry `style-src 'unsafe-inline'`. Measured 2026-09-17: the
        shell styles several controls with inline `style=` attributes, and
        `style-src 'self'` blocks them with NO console message - the page still
        boots, only the sliders and theme swatches lose their styling (the
        `web-prod` gate check now measures that at runtime). The header form
        shipped `style-src 'self'` until this was found."""
    text = (ROOT / "web" / ".htaccess").read_text(encoding="utf-8")
    header = re.search(
        r'^\s*Header\s+\S+\s+set\s+Content-Security-Policy\s+"([^"]+)"',
        text, re.M).group(1)
    meta = _meta_policy()

    def directives(policy):
        out = {}
        for part in policy.split(";"):
            part = part.strip()
            if part:
                name, _, value = part.partition(" ")
                out[name] = " ".join(value.split())
        return out

    h, m = directives(header), directives(meta)
    assert set(h) - set(m) == {"frame-ancestors"}, (
        f"the header form carries directives the meta form cannot: "
        f"{set(h) ^ set(m)}")
    for key in m:
        assert h[key] == m[key], f"{key} differs: {h[key]!r} vs {m[key]!r}"
    assert m["style-src"] == "'self' 'unsafe-inline'", (
        "inline style= attributes in index.html need this (see the docstring)")
    assert not _inline_script_hashes()
    assert "sha256-" not in meta
    assert "sha256-" not in header
