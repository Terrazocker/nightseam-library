"""Real-browser release gate.

    python scripts/check_browser.py [--web|--desktop|--all] [options]

Drives a REAL browser engine (system Chrome by default) through the NightSeam
shells:

  web      https://<local static server over web/> in Chrome
  desktop  the PACKED dist/NightSeam.exe, copied into a throwaway temp dir so
           its data/ never touches the user's install, driven over the local
           API server it starts (--no-browser --planner)
  catalog  the PACKED exe's standard launch, verified as archive-only

If a browser cannot launch the gate FAILS (exit 2), exactly like a missing
node in tests/js/* - there are no silent skips.  Everything runs offline and
deterministically: UTC timezone, en-US locale, dark scheme, a pinned clock,
empty storage per check, and the one external boot fetch (the popularity source) stubbed.

Exit codes: 0 pass, 1 failed check(s), 2 infrastructure could not start
(browser, static server or packed exe).

Typical use:
    python scripts/check_browser.py --web                  # fast, no exe
    python scripts/check_browser.py --all                  # full gate
    python scripts/check_browser.py --all --prod-url https://nightseam.de/astro/
    python scripts/check_browser.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "browser"))

import harness  # noqa: E402
import web_suite  # noqa: E402,F401  (import registers the checks)
import desktop_suite  # noqa: E402,F401
import catalog_suite  # noqa: E402,F401  (default packed executable profile)
import catalog_map_labels  # noqa: E402,F401


def run_gate(**kwargs) -> int:
    """Run the harness, converting an INFRASTRUCTURE failure into exit 2.

    TO-02 (0.6.24 review): the Playwright DRIVER can fail to start before any
    browser exists (restricted token, broken install, missing driver bundle).
    The contract documented at the top of this file defines that as exit 2
    ("infrastructure could not start"), NOT exit 1 ("failed check(s)") - an
    escaping traceback used to report the latter, which is the signal a release
    wrapper keys on. Handled here, at the owner of the exit-code contract."""
    try:
        return harness.run(**kwargs)
    except Exception as exc:                       # noqa: BLE001
        print(f"check_browser FATAL: browser infrastructure could not start "
              f"({type(exc).__name__}: {exc}).")
        return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="NightSeam real-browser gate")
    ap.add_argument("--web", action="store_true", help="only the web shell")
    ap.add_argument("--desktop", action="store_true",
                    help="packed exe legacy planner plus catalog-only launch")
    ap.add_argument("--all", action="store_true",
                    help="both shells (default when neither --web nor --desktop)")
    ap.add_argument("--channel", default="chrome",
                    help="Playwright browser channel (default: chrome; 'chromium' "
                         "uses the bundled engine)")
    ap.add_argument("--exe", default=str(harness.DEFAULT_EXE),
                    help="packed exe to test (default dist/NightSeam.exe)")
    ap.add_argument("--only", default=None,
                    help="run only checks whose name contains this substring")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--prod-url", default=None,
                    help="also run a read-only smoke against this deployed URL "
                         "(e.g. https://nightseam.de/astro/)")
    ap.add_argument("--list", action="store_true", help="list checks and exit")
    args = ap.parse_args()

    if args.list:
        for name, target, _fn in harness.all_checks():
            print(f"{target:7s} {name}")
        return 0

    if args.web:
        targets = ("web",)
    elif args.desktop:
        targets = ("desktop", "catalog")
    else:
        targets = ("web", "desktop", "catalog")

    return run_gate(
        channel=args.channel, targets=targets, exe=Path(args.exe),
        only=args.only, headed=args.headed, prod_url=args.prod_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
