"""Fetch object example images with on-disk caching.

Resolution chain per object:
  1. local cache hit
  2. Wikidata P18 image via structured search ("Messier 29" -> Q18823 -> commons file)
  3. Wikipedia REST summary thumbnails for exact titles
  4. DSS2 colour survey cutout at the object's coordinates (hips2fits service)
  5. negative cache (3 days) -> caller renders a placeholder instead

The chain deliberately avoids free-text search: querying plain "M29" used to
return unrelated pages (actors, weapons, routes).
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .paths import data_dir

CACHE_DIR = data_dir() / "imgcache"
UA = {"User-Agent": "NightSeam/1.0 (amateur astronomy planner)"}
NEG_TTL = 24 * 3600          # negative cache: 1 day
NEG_VERSION = 2              # bump to invalidate failure markers from older logic
CACHE_VERSION = 2            # bump to invalidate stored IMAGES (e.g. wrongly
                             # resolved ones cached by earlier resolver logic)

# ---- retention (SP-10, 0.6.32 review) --------------------------------------
# CACHE_DIR used to grow forever: every detail view (and the boot prefetch)
# leaves a `<slug>.bin` + `<slug>.json` pair and nothing ever evicted one
# (measured 440 files / 24.8 MB; the advanced catalog is 12,176 objects, so
# browsing it reaches ~1.3 GB). Policy: an LRU-ish BYTE cap over the cached
# payloads, enforced opportunistically ON CACHE WRITE only - the request path
# pays one integer increment and nothing else.
CACHE_MAX_BYTES = 384 * 1024 * 1024   # ceiling for .bin payloads in CACHE_DIR
CACHE_MAX_AGE = 180 * 24 * 3600       # plus: drop payloads untouched for 180 d
CACHE_MIN_AGE = 300                   # never evict a file younger than 5 min:
                                      # it may be the one the current request is
                                      # about to serve (and the prefetch wrote it)
CACHE_CHECK_EVERY = 64                # prune at most once per N cache writes

# ---- offline handling -------------------------------------------------------
# After repeated network failures we stop trying for a while so an offline
# machine does not sit through timeout after timeout.
_offline_until = 0.0
_consec_fail = 0


def is_offline() -> bool:
    return time.time() < _offline_until


def _note_success():
    global _consec_fail
    _consec_fail = 0


def _note_failure():
    global _consec_fail, _offline_until
    _consec_fail += 1
    if _consec_fail >= 4:
        _offline_until = time.time() + 120     # pause all lookups for 2 min

# the CDS survey server ships an incomplete cert chain on some systems;
# allow it without verification (public GET-only astronomy data service).
# EXACT host names only: a substring test also matched hostile look-alikes such
# as "cds.unistra.example.com", which would have handed them the bypass.
_INSECURE_CTX = ssl._create_unverified_context()
_INSECURE_HOSTS = {"alasky.cds.unistra.fr", "cds.unistra.fr"}


def _context_for(url: str):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return _INSECURE_CTX if host in _INSECURE_HOSTS else None


class _AllowListRedirects(urllib.request.HTTPRedirectHandler):
    """PY6: refuse a redirect hop that leaves the exact allow-list.

    The unverified TLS context is attached to the OPENER, and urllib follows
    redirects on that same opener without re-checking the host - so one of the
    allow-listed CDS hosts could hand the relaxed context to any third party,
    whose bytes we would then cache and serve as the object's image. The
    documented control is the hostname check, and a redirect walked around it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nxt = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nxt is None:
            return None
        host = (urllib.parse.urlsplit(nxt.full_url).hostname or "").lower()
        if host not in _INSECURE_HOSTS:
            raise urllib.error.HTTPError(
                newurl, code,
                f"redirect left the CDS allow-list ({host or 'no host'})",
                headers, fp)
        return nxt


def _urlopen(req, timeout: float):
    """Open `req`, installing the redirect guard for the allow-listed hosts whose
    TLS chain is deliberately unverified (PY6). Every other host keeps the normal,
    certificate-verifying opener (a redirect there is fine - the hop is verified
    like any other HTTPS request)."""
    host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
    if host in _INSECURE_HOSTS:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_INSECURE_CTX),
            _AllowListRedirects())
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


class Temporary(Exception):
    """Rate limits / server hiccups / network issues -> must NOT be cached."""


# errors that are environmental and worth a retry. HTTPError is a subclass of
# URLError/OSError, so membership here would mark a permanent 404 as temporary
# and keep re-fetching it forever - `_is_temporary` classifies HTTP codes
# explicitly instead.
TEMP_EXC = (Temporary, urllib.error.URLError, TimeoutError, OSError)

MAX_IMAGE_BYTES = 12 * 1024 * 1024      # a thumbnail/cutout is far smaller


def _is_temporary(exc: BaseException) -> bool:
    """True when the failure is environmental (retry later), False when the
    answer is permanent (e.g. HTTP 404 -> this object has no image there)."""
    if isinstance(exc, urllib.error.HTTPError):
        # same set `_http_bytes` converts into Temporary: rate limits, blocks
        # and server hiccups - everything else (404/410/...) is an answer
        return exc.code in (403, 408, 429, 500, 502, 503, 504)
    if isinstance(exc, json.JSONDecodeError):
        return True                      # truncated/hostile response
    return isinstance(exc, TEMP_EXC)


def _http_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 500, 502, 503):
            raise Temporary(str(e.code)) from None
        raise


def _http_bytes(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=UA)
    try:
        with _urlopen(req, timeout) as r:
            ctype = r.headers.get("Content-Type", "image/jpeg")
            # read with a cap: these are third-party hosts and the bytes are
            # cached on disk, so an oversized/hostile response must not be
            # buffered whole (nor written into the cache)
            chunks = []
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise Temporary(
                        f"image larger than {MAX_IMAGE_BYTES // (1 << 20)} MB")
                chunks.append(chunk)
            return b"".join(chunks), ctype.split(";")[0]
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 500, 502, 503):
            raise Temporary(str(e.code)) from None
        raise


def _download(url: str, timeout: float = 10.0):
    """Download with one retry after a short pause on TRANSIENT errors.
    A permanent answer (e.g. 404) is raised immediately so `fetch_image` can
    write the negative marker instead of retrying the object forever."""
    try:
        return _http_bytes(url, timeout=timeout)
    except Exception as exc:                # noqa: BLE001 - classified below
        if not _is_temporary(exc):
            raise
        time.sleep(1.5)
        return _http_bytes(url, timeout=timeout)


def _slug(*parts) -> str:
    s = "-".join(p for p in parts if p).lower()
    return re.sub(r"[^a-z0-9]+", "-", s)[:60] or "x"


# ------------------------------------------------------------------ sources

def _wikidata_thumb(title: str):
    """Image (P18) of the Wikidata entity best matching `title`."""
    try:
        s = _http_json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbsearchentities", "search": title, "language": "en",
            "format": "json", "limit": 1, "type": "item"}))
        hits = s.get("search") or []
        if not hits:
            return None
        qid = hits[0]["id"]
        c = _http_json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbgetclaims", "entity": qid, "property": "P18",
            "format": "json"}))
        claims = (c.get("claims") or {}).get("P18") or []
        if not claims:
            return None
        val = claims[0]["mainsnak"].get("datavalue", {}).get("value")
        if isinstance(val, dict):            # newer datatypes wrap the name
            val = val.get("time") or val.get("id") or val.get("text")
        if not val:
            return None
        url = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
               + urllib.parse.quote(str(val)) + "?width=640")
        return url
    except TEMP_EXC:
        raise                                  # transient - let callers retry/flag
    except Exception:
        return None


def _wikipedia_thumb(title: str):
    """Summary-thumbnail URL, or None when the title has no usable image.

    A DISAMBIGUATION page is never an image of the object (0.6.32): "M101" is a
    disambiguation page for motorways and highways, and its summary carries a
    picture of none of them. Skipping it lets the caller try the next candidate
    ("Messier 101", then "NGC 5457") instead of falling straight through to the
    survey cutout.

    Like `_wikidata_thumb`, a TEMPORARY failure propagates: swallowing it here
    made a pure network outage look like "this object has no image", which then
    poisoned the 24 h negative cache."""
    try:
        data = _http_json(
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title.replace(" ", "_")))
        if (data.get("type") or "") == "disambiguation":
            return None
        thumb = data.get("thumbnail", {}).get("source")
        if thumb and thumb.startswith("http"):
            big = thumb.replace("/330px-", "/500px-")
            return big if "/thumb/" in thumb else thumb
    except TEMP_EXC:
        raise
    except Exception:
        return None
    return None


def _dss_cutout(ra_hours: float, dec_deg: float, size_arcmin: float | None,
                pixels: int = 512):
    """Real-sky colour cutout from the DSS2 survey around the object's position."""
    try:
        ra_deg = (ra_hours % 24.0) * 15.0
        fov = 0.35
        if size_arcmin:
            fov = max(0.12, min(1.4, float(size_arcmin) * 2.2 / 60.0))
        url = ("https://alasky.cds.unistra.fr/hips-image-services/hips2fits?"
               + urllib.parse.urlencode({
                   "hips": "CDS/P/DSS2/color",
                   "ra": ra_deg, "dec": dec_deg,
                   "width": pixels, "height": pixels,
                   "projection": "TAN", "fov": round(fov, 3), "format": "jpg"}))
        return url
    except Exception:
        return None


# ------------------------------------------------------------------ helpers

def _full_messier(m: str | None):
    if m and m[:1].upper() == "M" and m[1:].isdigit():
        return f"Messier {int(m[1:])}"
    return None


def _first_ngc(n: str | None):
    return n.split("/")[0].strip() if n else None


def _parse_size(size_str: str | None):
    if not size_str:
        return None
    nums = re.findall(r"\d+\.?\d*", str(size_str))
    return max(float(n) for n in nums) if nums else None


# ------------------------------------------------------------------ main entry

_fetch_lock = __import__("threading").Lock()

# SP-10: cheap write counter for the opportunistic retention check. One integer
# increment per cache write; the directory scan happens at most every
# CACHE_CHECK_EVERY writes.
_cache_writes_since_check = 0


def _cache_files() -> list:
    """Every regular file in CACHE_DIR as (path, size, mtime)."""
    try:
        entries = list(CACHE_DIR.iterdir())
    except OSError:
        return []
    found = []
    for p in entries:
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:
            continue
        found.append((p, st.st_size, st.st_mtime))
    return found


def _unlink_quietly(path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _cache_total_bytes() -> int:
    return sum(size for _p, size, _t in _cache_files())


def prune_cache(max_bytes: int | None = None, now: float | None = None) -> int:
    """Enforce the cache cap; returns the number of removed files.

    Oldest-first (by mtime, i.e. least recently written) and only over files
    older than `CACHE_MIN_AGE`, so a file the app has just written - or is about
    to serve for a request that resolved its slug before the prune - is never
    deleted. Stale negative markers (a `.json` with no `.bin` beside it) are
    dropped as housekeeping whenever they are in the eviction set: the marker
    itself is re-created on the next failure, and once it is past NEG_TTL it no
    longer blocks a retry anyway.
    """
    cap = CACHE_MAX_BYTES if max_bytes is None else max_bytes
    now = time.time() if now is None else now
    files = _cache_files()
    if not files:
        return 0
    total = sum(size for _p, size, _t in files)
    removed = 0
    done = set()
    if total > cap:
        evictable = sorted((f for f in files if now - f[2] > CACHE_MIN_AGE),
                           key=lambda f: f[2])       # oldest first
        for path, size, _mtime in evictable:
            if total <= cap:
                break
            _unlink_quietly(path)
            done.add(path)
            total -= size
            removed += 1
    # age cap: a payload nobody asked for in half a year is dead weight, cap or
    # no cap (this is what actually keeps a long-lived install bounded)
    for path, size, mtime in files:
        if path in done:
            continue
        if now - mtime > CACHE_MAX_AGE and now - mtime > CACHE_MIN_AGE:
            _unlink_quietly(path)
            total -= size
            removed += 1
    return removed


def clear_cache() -> int:
    """Delete the whole image cache (the `clear cached images` API action).

    Returns the number of bytes removed. Bounded retention is automatic; this is
    the explicit user-triggered path. Callers must not hold `_fetch_lock`.
    """
    files = _cache_files()
    freed = sum(size for _p, size, _t in files)
    for path, _size, _mtime in files:
        try:
            path.unlink()
        except OSError:
            pass
    return freed


def _prune_cache_opportunistically() -> None:
    """Called after a cache write; does the real work at most every N writes."""
    global _cache_writes_since_check
    _cache_writes_since_check += 1
    if _cache_writes_since_check < CACHE_CHECK_EVERY:
        return
    _cache_writes_since_check = 0
    try:
        prune_cache()
    except Exception:                       # noqa: BLE001 - never break a fetch
        pass


def fetch_image_cached(obj: dict):
    """Cache-only lookup for table thumbnails - never touches the network.

    Returns (bytes|None, content_type|None); None means the caller should
    render a placeholder immediately. This keeps a 250-row targets table
    instant instead of queueing a Wikipedia/DSS lookup per row behind the
    prefetch thread's lock."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(obj["id"], obj.get("wiki") or "", obj.get("messier") or "")
    cache_path = CACHE_DIR / f"{slug}.bin"
    meta_path = CACHE_DIR / f"{slug}.json"
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        return None, None
    if meta.get("none") or meta.get("v") != CACHE_VERSION:
        return None, None
    try:
        return cache_path.read_bytes(), meta["ctype"]
    except Exception:
        return None, None


def fetch_image(obj: dict, ignore_negative: bool = False):
    """Return (bytes|None, content_type|None). Cached under data/imgcache.

    Network fetching is serialized so interactive requests and the background
    warm-up never burst the remote APIs simultaneously."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    oid = obj["id"]
    wiki = obj.get("wiki")
    messier = obj.get("messier")
    ngcic = obj.get("ngcic")

    slug = _slug(oid, wiki or "", messier or "")
    cache_path = CACHE_DIR / f"{slug}.bin"
    meta_path = CACHE_DIR / f"{slug}.json"

    def cached():
        # The failure MARKER lives in the .json and has no .bin beside it, so
        # the marker must be read first: requiring the image file made every
        # permanent failure (a 404 for an object with no Wikipedia/Wikidata/DSS
        # image) invisible, and the object was re-fetched on every detail view
        # and every prefetch pass, forever.
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            return None
        if meta.get("none"):
            if (not ignore_negative and meta.get("v") == NEG_VERSION
                    and time.time() - meta.get("ts", 0) < NEG_TTL):
                return (None, None)        # recent, deliberate failure
            return None                    # stale marker -> retry below
        if not cache_path.exists():
            return None
        if meta.get("v") != CACHE_VERSION:
            return None                    # image from older resolver logic -> refetch
        return cache_path.read_bytes(), meta["ctype"]

    hit = cached()
    if hit is not None:
        return hit

    if is_offline():
        return None, None                  # in an outage window - placeholder now

    with _fetch_lock:
        hit = cached()                     # someone may have filled it meanwhile
        if hit is not None:
            return hit

        # structured, unambiguous title candidates (best first)
        titles = []
        if wiki:
            titles.append(wiki)
        fm = _full_messier(messier)
        if fm:
            titles.append(fm)
        fn = _first_ngc(ngcic)
        if fn:
            titles.append(fn)

        blob = ctype = None
        temporary_failure = False

        # 1) Wikidata P18 photos - keyed by unambiguous designations
        for t in titles:
            try:
                url = _wikidata_thumb(t)
                if url:
                    blob, ctype = _download(url)
                    if blob and len(blob) > 500 and ctype and ctype != "image/svg+xml":
                        break
                    blob = ctype = None
            except Exception as exc:        # noqa: BLE001
                if _is_temporary(exc):
                    temporary_failure = True

        # 2) Wikipedia summary thumbnails for exact page titles
        if blob is None:
            for t in titles:
                try:
                    url = _wikipedia_thumb(t)
                    if url:
                        blob, ctype = _download(url)
                        if blob and len(blob) > 500:
                            break
                        blob = ctype = None
                except Exception as exc:    # noqa: BLE001
                    if _is_temporary(exc):
                        temporary_failure = True

        # 3) coordinate-based survey cutout - always the right patch of sky
        if blob is None:
            size_am = _parse_size(obj.get("size_am"))
            for pixels in (512, 384):
                url = _dss_cutout(obj.get("ra_hours", 0.0),
                                  obj.get("dec_deg", 0.0), size_am, pixels)
                if not url:
                    continue
                try:
                    blob, ctype = _download(url, timeout=12.0)
                    if blob and len(blob) > 500 and "image" in (ctype or ""):
                        break
                    blob = ctype = None
                except Exception as exc:    # noqa: BLE001
                    if _is_temporary(exc):
                        temporary_failure = True

        if blob is not None:
            cache_path.write_bytes(blob)
            meta_path.write_text(json.dumps({"ctype": ctype, "v": CACHE_VERSION}))
            _note_success()
            _prune_cache_opportunistically()   # SP-10: cheap counter + rare prune
            return blob, ctype

        # only remember failures that are the object's fault; network hiccups retry
        if not temporary_failure:
            meta_path.write_text(json.dumps({"none": True, "ts": time.time(),
                                             "v": NEG_VERSION}))
            _prune_cache_opportunistically()   # negative markers count too
        else:
            _note_failure()
        return None, None
