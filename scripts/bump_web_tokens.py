"""Bump the cache token of every shipped web module whose BYTES changed since
the last VERIFIED baseline, in one pass (AGENTS.md: "bump the token of every
module whose BEHAVIOUR changed in the SAME commit, and bump BOTH the entry point
and the dependency").

The baseline lives inside scripts/check_js_tokens.py; the guard refuses `--update`
while a missed bump exists, so this script is the missing half of the documented
remedy. It rewrites, for each stale module:

  * `web/index.html`            (entry points: app.js, style.css, themes.css)
  * every `./name.js?v=N` reference in `web/app.js`
  * the `core.js` stub token, taken from core/core.manifest.json

Run it, then `python scripts/check_js_tokens.py --update`, then the guards.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
GUARD = ROOT / 'scripts' / 'check_js_tokens.py'

START = '# --- BEGIN GENERATED VERIFIED BASELINE ---'
END = '# --- END GENERATED VERIFIED BASELINE ---'
_HTML_TOKEN_RE = re.compile(r'(src|href)="([\w./-]+\.(?:js|css))\?v=(\d+)"')
_IMPORT_TOKEN_RE = re.compile(r"(['\"])(\./[\w./-]+\.js)\?v=(\d+)(['\"])")


def norm(data: bytes) -> bytes:
    return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def sha(data: bytes) -> str:
    return hashlib.sha256(norm(data)).hexdigest()


def baseline() -> dict:
    """The generated baseline block from the guard (single unambiguous match -
    the marker TEXT also appears in the guard's constants, so splitting on it
    would pick the wrong occurrence)."""
    text = GUARD.read_text(encoding='utf-8')
    m = re.search(r'_VERIFIED_BASELINE_JSON\s*=\s*r"""(.*?)"""', text, re.S)
    if not m:
        raise SystemExit('could not find the verified baseline in check_js_tokens.py')
    return json.loads(m.group(1))


def declared_tokens() -> dict[str, str]:
    tokens: dict[str, str] = {}
    index = (WEB / 'index.html').read_text(encoding='utf-8')
    for _attr, ref, tok in _HTML_TOKEN_RE.findall(index):
        tokens[ref.lstrip('./')] = tok
    app = (WEB / 'app.js').read_text(encoding='utf-8')
    for _q, ref, tok, _q2 in _IMPORT_TOKEN_RE.findall(app):
        tokens.setdefault(ref[2:].lstrip('./'), tok)
    return tokens


def main() -> int:
    base = baseline()['modules']
    declared = declared_tokens()
    man = json.loads((ROOT / 'core' / 'core.manifest.json').read_text(encoding='utf-8'))

    stale: dict[str, tuple[str, str]] = {}
    for name, spec in sorted(base.items()):
        path = WEB / name
        if not path.is_file():
            continue
        now_tok = str(man['token']) if name == 'core.js' else declared.get(name)
        if now_tok is None:
            print(f'  {name}: no declared token - skipped')
            continue
        if sha(path.read_bytes()) != spec['sha256'] and now_tok == spec['token']:
            stale[name] = (spec['token'], str(int(spec['token']) + 1))
        elif sha(path.read_bytes()) != spec['sha256']:
            print(f'  {name}: bytes changed, token {spec["token"]} -> {now_tok} (already bumped)')
    if not stale:
        print('no missed bump: every changed module already moved its token')
        return 0

    print('stale pairing (bytes changed, token unmoved) -> bumping:')
    for name, (old, new) in stale.items():
        print(f'  {name}: ?v={old} -> ?v={new}')

    index_p = WEB / 'index.html'
    index = index_p.read_text(encoding='utf-8')

    def bump_html(m: re.Match) -> str:
        attr, ref, tok = m.group(1), m.group(2), m.group(3)
        name = ref.lstrip('./')
        return (f'{attr}="{ref}?v={stale[name][1]}"' if name in stale else m.group(0))

    index_p.write_text(_HTML_TOKEN_RE.sub(bump_html, index), encoding='utf-8', newline='')

    app_p = WEB / 'app.js'
    app = app_p.read_text(encoding='utf-8')

    def bump_import(m: re.Match) -> str:
        q, ref, _tok, q2 = m.groups()
        name = ref[2:].lstrip('./')
        if name not in stale:
            return m.group(0)
        return f'{q}{ref}?v={stale[name][1]}{q2}'

    app_p.write_text(_IMPORT_TOKEN_RE.sub(bump_import, app), encoding='utf-8', newline='')
    print('rewrote web/index.html and web/app.js references')

    # SECOND ORDER: rewriting app.js changed ITS bytes, and app.js's own token
    # is declared in index.html - so the entry point moves too (bumping a
    # dependency must bump the importer, per AGENTS.md).
    index2 = index_p.read_text(encoding='utf-8')
    app_tok = base.get('app.js', {}).get('token')
    if app_tok is not None and sha(app_p.read_bytes()) != base['app.js']['sha256']:
        new_app = str(int(declared.get('app.js', app_tok)) + 1)
        index2b = re.sub(r'app\.js\?v=\d+', f'app.js?v={new_app}', index2)
        if index2b != index2:
            index_p.write_text(index2b, encoding='utf-8', newline='')
            print(f'  app.js: ?v={declared.get("app.js")} -> ?v={new_app} '
                  f'(its bytes changed while bumping a dependency)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
