"""Shipped text must stay UTF-8 - the 0.6.9 mojibake accident, pinned.

web/app.js and web/horizon.js were committed after a tool had read them as
cp1252 and written them back as UTF-8: every em dash, degree sign and emoji in
them turned into its multi-character mojibake cluster and the live web app
showed garble until the follow-up commit. scripts/check_encoding.py is the
guard; this test keeps it in the suite (and in the release path).
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = ROOT / "scripts" / "check_encoding.py"
    spec = importlib.util.spec_from_file_location("check_encoding", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_non_utf8_or_double_encoded_text():
    problems = _load().scan()
    assert problems == [], "\n".join("%s: %s" % (p.as_posix(), r) for p, r in problems)


def test_the_review_workspace_is_not_policed(tmp_path):
    """TO-11: `reviews/` is not shipped text (charter 1.1 keeps it outside the
    product), so a BOM or a UTF-16LE PowerShell capture written there by a review
    probe must not turn the release JS guard red - while the same bytes in shipped
    text still must."""
    mod = _load()
    ship = tmp_path / "web"
    ship.mkdir()
    (ship / "bad.js").write_bytes(b"\xef\xbb\xbfconst a = 1;\n")        # UTF-8 BOM
    rev = tmp_path / "reviews" / "0.6.24" / "evidence"
    rev.mkdir(parents=True)
    (rev / "probe.txt").write_bytes(b"\xef\xbb\xbfscratch\n")
    (rev / "capture.txt").write_bytes(b"\xff\xfeh\x00i\x00")            # UTF-16LE

    problems = [p.as_posix() for p, _ in mod.scan(tmp_path)]
    assert "web/bad.js" in problems, "shipped text must still be policed"
    assert not [p for p in problems if p.startswith("reviews/")], problems
