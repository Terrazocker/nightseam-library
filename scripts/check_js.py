"""One-command JS health check: runs eight HARD guards - the four static/js
checkers (imports, DOM ids, `node --check` syntax) plus the encoding guard
(shipped text must be UTF-8, never double-encoded - see check_encoding.py for
the 0.6.9 mojibake that reached the live web app), the cache-token pair
(catalog.json + every tokenized web module, see check_js_tokens.py for the
v0.6.24 stale-token defect) and check_core.py (baked-core drift, stale hash
dirs, stub/token pairing, core purity, the moved-concept source guard).

Hard failures (broken imports, missing DOM ids, syntax errors, bad encoding,
a missed cache-token bump, baked-core drift) exit non-zero; the defs checker
stays review-only (it is heuristic). A missing node is a HARD failure too
(exit 2, never a skip - TO-03): the syntax checker is the only JS parser in the
bundle, so a node-less run must not report OK. The same rule governs
`check_js_tokens` and `check_catalog_token`, which are embedded guards: their
`main()` never inherits this process's sys.argv (TR-06), so a stray flag here
cannot replace the scan they exist to run.
Usage: python scripts/check_js.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _run(name: str):
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def main() -> int:
    hard = 0
    hard += _run("check_js_imports")
    hard += _run("check_dom_ids")
    hard += _run("check_js_syntax")  # parses the modules (no node = exit 2)
    hard += _run("check_encoding")   # UTF-8 / no cp1252 mojibake in shipped text
    hard += _run("check_catalog_token")  # catalog.json cache-bust pairing
    hard += _run("check_catalog_provenance")  # provenance sidecar pairing
    hard += _run("check_js_tokens")      # per-module ?v= token vs module bytes
    hard += _run("check_core")           # baked core drift + source guard
    _run("check_js_defs")            # heuristic - candidates only
    print("check_js:", "FAIL" if hard else "OK")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
