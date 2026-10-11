"""Execute the baked shared solar core in embedded V8, including headless runs.

Only module declarations are adapted. Astronomy formulas remain in core/*.js.
Contexts close after each cache miss: no thread-local native resources or
async callbacks survive the request. The bounded cache contains plain records.
"""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from .paths import resource_dir


def _parse_stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _coordinate(value, limit):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or abs(number) > limit:
        return None
    # Match the shared JS site's Math.round convention, including negative ties.
    return math.floor(number * 1000 + 0.5) / 1000 or 0.0


def _adapt_module(source, dependencies):
    """Accept only explicit named imports and exported function declarations."""
    bindings = []
    import_pattern = re.compile(
        r"^import\s*\{\s*([\w\s,]+)\}\s*from\s*['\"]([^'\"]+)['\"];\s*$", re.M)

    def replace_import(match):
        names, path = match.groups()
        if path not in dependencies:
            raise RuntimeError(f"Unsupported solar core dependency: {path}")
        bindings.append(f"const {{ {names} }} = {dependencies[path]};")
        return ""

    body = import_pattern.sub(replace_import, source)
    exports = re.findall(r"^export function ([A-Za-z_$][\w$]*)\(", body, re.M)
    body = re.sub(r"^export (?=function )", "", body, flags=re.M)
    # Strip comments for validation only; preserve original source for execution.
    significant = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", body)
    if re.search(r"\b(?:import|export)\b", significant) or not exports:
        raise RuntimeError("Unsupported declaration in baked solar core")
    return "(() => {\n" + "\n".join(bindings) + "\n" + body + "\nreturn { " + ", ".join(exports) + " };\n})()"


@lru_cache(maxsize=1)
def _embedded_source():
    root = resource_dir() / "static" / "js"
    stub = (root / "core.js").read_text(encoding="utf-8")
    match = re.search(r"from './core/([0-9a-f]{16})/index\.js';", stub)
    if not match:
        raise RuntimeError("Baked core entry does not identify a valid current tree")
    tree = root / "core" / match.group(1)
    astro = _adapt_module((tree / "astro.js").read_text(encoding="utf-8"), {})
    solar = _adapt_module((tree / "solar_sessions.js").read_text(encoding="utf-8"),
                          {"./astro.js": "__solarAstro"})
    return f"const __solarAstro = {astro};\nconst __solarSession = {solar};"


@lru_cache(maxsize=2048)
def _day_windows(day, latitude, longitude):
    from py_mini_racer import MiniRacer

    midnight = datetime.fromisoformat(day).replace(tzinfo=UTC)
    arguments = json.dumps([int(midnight.timestamp() * 1000), latitude, longitude],
                           allow_nan=False)
    with MiniRacer() as context:
        context.eval(_embedded_source())
        windows = json.loads(context.eval(
            f"JSON.stringify(__solarSession.solarSessionWindows(...{arguments}))",
            timeout=10000))
    # Serialize the cached value so callers can never modify the cache itself.
    return json.dumps(windows)


def session_metadata(date_obs, latitude=None, longitude=None):
    """Stable solar membership; naive capture dates are UTC, never machine local.

    Missing coordinates remain visibly unassigned by UTC capture day. Ordinary
    windows use conventional geometric -0.833-degree sunrise/set. Continuous
    polar phases split at UTC midnight, with explicit polar_day/polar_night.
    """
    stamp = _parse_stamp(date_obs)
    if stamp is None:
        return {"session_key": "undated", "session_kind": "undated",
                "session_start": None, "session_end": None}
    day = stamp.date().isoformat()
    lat, lon = _coordinate(latitude, 90), _coordinate(longitude, 180)
    if lat is None or lon is None:
        begin = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"session_key": f"unassigned:{day}", "session_kind": "unassigned",
                "session_start": begin.replace(tzinfo=None).isoformat(),
                "session_end": (begin + timedelta(days=1)).replace(tzinfo=None).isoformat()}
    for window in json.loads(_day_windows(day, lat, lon)):
        if _parse_stamp(window["session_start"]) <= stamp < _parse_stamp(window["session_end"]):
            return window
    raise RuntimeError("Shared solar core did not cover the capture timestamp")
