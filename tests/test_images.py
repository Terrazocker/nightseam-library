"""Tests for the example-image chain (app/images.py) - network fully stubbed.

These exercise the caching, negative-cache and offline-circuit-breaker logic
without ever touching the network: the source resolvers and the downloader
are monkeypatched, and CACHE_DIR points at a tmp dir.
"""

import json
import time
import urllib.error

import pytest

import app.images as img

OBJ = {"id": "m31", "wiki": "Andromeda Galaxy", "messier": "M 31",
       "ngcic": "NGC 224", "ra_hours": 0.712, "dec_deg": 41.269,
       "size_am": "190x60"}
JPEG = (b"\xff\xd8" + b"x" * 598)          # 600 bytes, "image/jpeg"
SVG = b"<svg/>"                             # rejected: too small / svg


@pytest.fixture()
def nw(tmp_path, monkeypatch):
    """Stubbed network layer + isolated cache dir; returns call counters."""
    monkeypatch.setattr("app.images.CACHE_DIR", tmp_path / "imgcache")
    # isolate the offline circuit-breaker globals per test (monkeypatch
    # restores the imported values on teardown, so no state leaks between
    # tests even if a test trips the failure counters)
    monkeypatch.setattr("app.images._consec_fail", 0)
    monkeypatch.setattr("app.images._offline_until", 0.0)
    calls = {"download": 0, "wikidata": 0, "wikipedia": 0, "dss": 0}

    def stub_download(url, timeout=10.0):
        calls["download"] += 1
        return JPEG, "image/jpeg"

    monkeypatch.setattr("app.images._download", stub_download)
    monkeypatch.setattr("app.images._wikidata_thumb",
                        lambda title: (calls.__setitem__("wikidata", calls["wikidata"] + 1) or "https://example.invalid/w.jpg"))
    monkeypatch.setattr("app.images._wikipedia_thumb",
                        lambda title: (calls.__setitem__("wikipedia", calls["wikipedia"] + 1) or None))
    monkeypatch.setattr("app.images._dss_cutout",
                        lambda ra, dec, size, pixels=512: None)
    return calls


def test_fetch_cached_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.images.CACHE_DIR", tmp_path / "imgcache")
    assert img.fetch_image_cached(OBJ) == (None, None)


def test_fetch_image_wikidata_hit_and_cache(nw):
    blob, ctype = img.fetch_image(OBJ)
    assert blob == JPEG
    assert ctype == "image/jpeg"
    assert nw["wikidata"] == 1            # only the first (best) title tried
    assert nw["download"] == 1

    # second call: served from the on-disk cache, no network at all
    blob2, ctype2 = img.fetch_image(OBJ)
    assert blob2 == JPEG and ctype2 == "image/jpeg"
    assert nw["download"] == 1


def test_fetch_image_negative_cache(nw, monkeypatch):
    # no source resolves -> negative marker written; TTL suppresses retries
    monkeypatch.setattr("app.images._wikidata_thumb", lambda title: None)
    blob, ctype = img.fetch_image(OBJ)
    assert blob is None and ctype is None
    assert nw["download"] == 0

    blob2, _ = img.fetch_image(OBJ)
    assert blob2 is None
    assert nw["download"] == 0             # still no network attempt


def test_fetch_image_ignore_negative_retries(nw, monkeypatch):
    def no_thumb(title):
        nw["wikidata"] += 1
        return None

    monkeypatch.setattr("app.images._wikidata_thumb", no_thumb)
    img.fetch_image(OBJ)                    # writes negative marker
    # ignore_negative=True forces a re-attempt despite the marker
    blob, _ = img.fetch_image(OBJ, ignore_negative=True)
    assert blob is None
    # 2 title candidates ("Andromeda Galaxy", "NGC 224") x 2 attempts
    assert nw["wikidata"] == 4


def test_fetch_image_rejects_small_and_svg(nw, monkeypatch):
    # all three wikidata title candidates yield SVG (rejected); Wikipedia
    # and DSS return nothing -> negative cache; one download per title
    def svg_download(url, timeout=10.0):
        nw["download"] += 1
        return SVG, "image/svg+xml"

    monkeypatch.setattr("app.images._download", svg_download)
    blob, ctype = img.fetch_image(OBJ)
    assert blob is None
    # one download per title candidate: wiki "Andromeda Galaxy" + "NGC 224"
    assert nw["download"] == 2


def test_fetch_image_offline_breaker(nw, monkeypatch):
    monkeypatch.setattr("app.images._offline_until", time.time() + 300)
    blob, ctype = img.fetch_image(OBJ)
    assert blob is None and ctype is None
    assert nw["download"] == 0 and nw["wikidata"] == 0


def test_fetch_image_dss_fallback(nw, monkeypatch):
    """Wikidata and Wikipedia find nothing -> the DSS2 survey cutout is
    used and cached like any other source."""
    monkeypatch.setattr("app.images._wikidata_thumb", lambda title: None)
    monkeypatch.setattr("app.images._wikipedia_thumb", lambda title: None)

    def dss_cutout(ra, dec, size, pixels=512):
        nw["dss"] += 1
        return "https://alasky.cds.unistra.fr/hips-image-services/hips2fits?x=1"

    monkeypatch.setattr("app.images._dss_cutout", dss_cutout)
    blob, ctype = img.fetch_image(OBJ)
    assert blob == JPEG
    assert ctype == "image/jpeg"
    assert nw["dss"] == 1                   # first (512px) attempt succeeded
    assert nw["download"] == 1

    # cached now - no second DSS call or download
    blob2, _ = img.fetch_image(OBJ)
    assert blob2 == JPEG
    assert nw["dss"] == 1 and nw["download"] == 1


def test_fetch_image_temporary_failure_not_cached(nw, monkeypatch):
    # transient errors must NOT write a negative marker (retried next time)
    monkeypatch.setattr("app.images._wikidata_thumb",
                        lambda title: "https://example.invalid/w.jpg")

    def flaky_download(url, timeout=10.0):
        nw["download"] += 1
        raise img.Temporary("503")

    monkeypatch.setattr("app.images._download", flaky_download)
    blob, ctype = img.fetch_image(OBJ)
    assert blob is None and ctype is None
    # no negative marker yet: a later call re-attempts
    assert not list(img.CACHE_DIR.glob("*.json")) or all(
        "none" not in json.loads(p.read_text())
        for p in img.CACHE_DIR.glob("*.json"))


def test_slug_and_fields_sanitized():
    assert img._slug("M 31", "Andromeda", "M 31") == "m-31-andromeda-m-31"
    assert img._full_messier("m31") == "Messier 31"
    assert img._full_messier("NGC 224") is None
    assert img._first_ngc("NGC 224/205") == "NGC 224"
    assert img._parse_size("12.5x8") == 12.5


# ------------------------------------------------- transient vs permanent
# HTTPError subclasses URLError/OSError, so membership in TEMP_EXC made a
# permanent 404 look transient: no negative marker was written and the object
# was re-fetched on every detail view and prefetch pass, forever.

def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/x", code, "err",
                                  {}, None)


@pytest.mark.parametrize("code,temporary", [
    (404, False), (403, True), (410, False), (429, True), (500, True),
    (502, True), (503, True),
])
def test_http_status_classification(code, temporary):
    assert img._is_temporary(_http_error(code)) is temporary


def test_network_errors_are_temporary():
    assert img._is_temporary(urllib.error.URLError("offline")) is True
    assert img._is_temporary(TimeoutError()) is True
    assert img._is_temporary(img.Temporary("429")) is True
    # a truncated JSON body must not become a permanent negative marker
    assert img._is_temporary(json.JSONDecodeError("x", "y", 0)) is True


def test_permanent_failure_is_negative_cached(nw, monkeypatch):
    """A 404 must be remembered, so the object is not re-fetched forever."""
    def not_found(url, timeout=10.0):
        nw["download"] += 1
        raise _http_error(404)

    monkeypatch.setattr("app.images._download", not_found)
    blob, ctype = img.fetch_image(OBJ)
    assert blob is None and ctype is None
    markers = list(img.CACHE_DIR.glob("*.json"))
    assert markers and any(json.loads(p.read_text()).get("none") is True
                           for p in markers)
    calls_after_first = nw["download"]
    assert img.fetch_image(OBJ) == (None, None)
    assert nw["download"] == calls_after_first, "a cached 404 was re-fetched"


def test_the_insecure_tls_context_is_limited_to_exact_cds_hosts():
    """A substring test also matched look-alikes such as
    'cds.unistra.example.com', handing a hostile host the verification bypass."""
    assert img._context_for("https://alasky.cds.unistra.fr/hips2fits?x=1") is not None
    assert img._context_for("https://cds.unistra.fr/x") is not None
    assert img._context_for("https://cds.unistra.example.com/x") is None
    assert img._context_for("https://evil.example/cds.unistra.fr") is None
    assert img._context_for("https://commons.wikimedia.org/x") is None


def test_py6_a_redirect_off_the_allow_list_is_refused():
    """PY6: the unverified context is per-OPENER, and urllib follows redirects on
    that opener without re-checking the host - so an allow-listed CDS host could
    hand the relaxed context to any third party, whose bytes we would then cache
    and serve as the object's image. The guard refuses every hop that leaves the
    exact allow-list, while hops that stay on it are still followed."""
    import inspect
    import urllib.request as urlreq

    guard = img._AllowListRedirects()
    req = urlreq.Request("https://alasky.cds.unistra.fr/hips2fits")

    def hop(url):
        return guard.redirect_request(req, None, 302, "Found",
                                      {"Location": url}, url)

    with pytest.raises(urllib.error.HTTPError) as exc:
        hop("https://evil.example/x.jpg")
    assert "allow-list" in str(exc.value)
    assert hop("https://cds.unistra.fr/other").full_url == \
        "https://cds.unistra.fr/other"
    # the guarded opener must actually be wired into the byte fetch
    assert "_urlopen(req, timeout)" in inspect.getsource(img._http_bytes)


def test_image_download_reads_are_capped(monkeypatch):
    """Third-party bytes are cached, so an oversized response must be refused
    instead of buffered whole."""
    class BigResponse:
        headers = {"Content-Type": "image/jpeg"}

        def __init__(self):
            self.served = 0

        def read(self, n=-1):
            self.served += n
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(img.urllib.request, "urlopen",
                        lambda *a, **k: BigResponse())
    with pytest.raises(img.Temporary):
        img._http_bytes("https://example.invalid/huge.jpg")