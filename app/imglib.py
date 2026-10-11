"""Image-library logic: resolving captures to catalog objects, grouping
ingested frames per object, and computing imaging sessions.

Sessions use exact sunset-to-sunrise or sunrise-to-sunset intervals at the
measured observing site. Missing locations stay visibly unresolved by UTC
date. Explicit calibration attachments copy selected interval membership
without replacing DATE-OBS or measured site coordinates.
"""

from __future__ import annotations

import re
import math
from datetime import UTC, datetime

from .fits import extract_object_tokens

MATCH_RADIUS_DEG = 0.75     # max angular distance for coordinate matching


def public_image_record(record: dict) -> dict:
    """Portable image metadata without machine-local fingerprint hints."""
    return {key: value for key, value in record.items()
            if key not in ("content_sha256", "file_size", "file_mtime_ns")}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").lower())


def name_key(s: str) -> str:
    """Canonical key for matching unresolved-object names (whitespace removed,
    lowercased). Library grouping and rename/delete lookups share this, so they
    can never disagree about which rows belong to one object."""
    return _norm(s)


def normalize_catalog(catalog: list[dict]) -> list[dict]:
    """Map catalog rows to one shape.

    Both catalog tables (`objects` and `advanced_objects`) already carry the
    `primary_name` / `otype` / `size_am` names; the `primary` / `type` / `size`
    alternatives are accepted so a caller holding an older shape still works.
    """
    out = []
    for o in catalog:
        out.append({
            "id": o.get("id"),
            "primary_name": o.get("primary_name") or o.get("primary"),
            "messier": o.get("messier"),
            "ngcic": o.get("ngcic"),
            "other": o.get("other"),
            "otype": o.get("otype") or o.get("type"),
            "size_am": o.get("size_am") or o.get("size"),
            "ra_hours": o.get("ra_hours"),
            "dec_deg": o.get("dec_deg"),
            "search": o.get("search"),
        })
    return out


def _index_normalized_catalog(rows: list[dict]) -> dict:
    """Build the name index from rows already returned by normalize_catalog."""
    idx: dict[str, str] = {}
    for o in rows:
        keys = [o["messier"], o["ngcic"], o["other"],
                o["primary_name"], o["search"], o["id"]]
        for k in keys:
            if not k:
                continue
            for token in re.split(r"[|/,]", str(k)):
                tok = _norm(token)
                if len(tok) >= 2:
                    idx.setdefault(tok, o["id"])
    return idx


def build_catalog_index(catalog: list[dict]) -> dict:
    """Normalized (lowercase, no-space) designation/name -> object id.

    'M101', 'NGC 5457' and 'Pinwheel' all land on 'm101'; 'ngc7000' matches
    the spaced 'NGC 7000' because spaces are stripped on both sides.
    """
    return _index_normalized_catalog(normalize_catalog(catalog))


class CatalogResolver:
    """Reusable catalog normalization and name/ID indexes for image ingest.

    The first occurrence wins for duplicate names and IDs, matching the prior
    `build_catalog_index` + `_by_id` lookup behavior.
    """

    def __init__(self, catalog: list[dict]):
        self.rows = normalize_catalog(catalog)
        self.name_to_id = _index_normalized_catalog(self.rows)
        self.row_by_id: dict[str, dict] = {}
        for row in self.rows:
            self.row_by_id.setdefault(row["id"], row)

    def resolve(self, name_hint: str | None = None,
                ra_hours: float | None = None,
                dec_deg: float | None = None) -> dict | None:
        """Resolve name tokens first, then coordinates, else return None."""
        if not self.rows:
            return None
        if name_hint:
            tokens = extract_object_tokens(name_hint)
            for token in tokens:
                oid = self.name_to_id.get(_norm(token))
                if oid:
                    return self.row_by_id.get(oid)
            # prefix fallback: 'andromeda' -> 'andromedagalaxy' (M31)
            for token in tokens:
                tok = _norm(token)
                if len(tok) < 3:
                    continue
                hits = [k for k in self.name_to_id if k.startswith(tok)]
                if hits:
                    best = max(hits, key=len)
                    return self.row_by_id.get(self.name_to_id[best])
        if ra_hours is not None and dec_deg is not None:
            best, best_d = None, None
            for o in self.rows:
                r = o["ra_hours"]
                d = o["dec_deg"]
                if r is None or d is None:
                    continue
                dist = angular_dist(ra_hours, dec_deg, r, d)
                if dist <= MATCH_RADIUS_DEG and (best_d is None or dist < best_d):
                    best, best_d = o, dist
            if best:
                return best
        return None


def resolve_object(catalog: list[dict], name_hint: str | None = None,
                   ra_hours: float | None = None,
                   dec_deg: float | None = None) -> dict | None:
    """Compatibility wrapper for one-off catalog resolution."""
    return CatalogResolver(catalog).resolve(name_hint, ra_hours, dec_deg)


def _by_id(catalog: list[dict], oid: str) -> dict | None:
    for o in catalog:
        if o["id"] == oid:
            return o
    return None


def angular_dist(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance in degrees."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dra = math.radians((ra1 - ra2) * 15.0)
    return math.degrees(math.acos(max(-1.0, min(1.0,
        math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dra)))))


def designation_of(row: dict | None, image: dict) -> str:
    """Display designation: 'M31', else 'NGC 224', else the stored name."""
    if row:
        if row.get("messier"):
            return str(row["messier"])
        if row.get("ngcic"):
            return str(row["ngcic"]).split("/")[0]
        return row.get("primary_name") or ""
    return image.get("object_name") or ""


def effective_session_date(image: dict) -> str | None:
    """Logical grouping date; an explicit empty anchor means undated.

    DATE-OBS remains the frame's measured timestamp. Only an explicitly
    attached calibration carries a non-NULL session_date_obs override.
    """
    anchor = image.get("session_date_obs")
    return image.get("date_obs") if anchor is None else anchor


def _session_epoch(value: str | None) -> float | None:
    """Compare measured/anchor ranges in UTC, retaining fractions."""
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


SESSION_FIELDS = ("session_key", "session_kind", "session_start", "session_end")


def image_session_metadata(image: dict) -> dict:
    """Stored explicit membership wins; otherwise derive a solar/date bucket."""
    if image.get("session_key"):
        return {key: image.get(key) for key in SESSION_FIELDS}
    from .solar import session_metadata
    return session_metadata(effective_session_date(image), image.get("site_lat"),
                            image.get("site_lon"))


def _session_groups(images: list[dict]) -> list[list[dict]]:
    """Group exact physical night/day intervals, never chronological gaps."""
    groups = {}
    for image in images:
        metadata = image_session_metadata(image)
        groups.setdefault(metadata["session_key"], []).append(image)
    for members in groups.values():
        members.sort(key=lambda image: (_session_epoch(effective_session_date(image))
                                        is None, _session_epoch(effective_session_date(image)) or 0))
    return sorted(groups.values(), key=lambda members: (
        image_session_metadata(members[0]).get("session_start") is None,
        image_session_metadata(members[0]).get("session_start") or "",
        image_session_metadata(members[0])["session_key"]))


def _session_record(members: list[dict]) -> dict:
    # Attached calibration represents the chosen range rather than extending
    # it. Prefer its existing capture endpoints, including their date format;
    # anchors remain sufficient if all ordinary frames have been removed.
    capture_members = [im for im in members if im.get("session_date_obs") is None] or members
    start = effective_session_date(capture_members[0])
    end = effective_session_date(capture_members[-1])
    metadata = image_session_metadata(members[0])
    undated = metadata["session_kind"] == "undated"
    labels = {"night": "Night", "day": "Day", "polar_day": "Polar day",
              "polar_night": "Polar night", "unassigned": "Unassigned location",
              "undated": "Undated"}
    s = {
        **metadata,
        "label": labels.get(metadata["session_kind"], "Unassigned location"),
        "start": None if undated else start,
        "end": None if undated else end,
        "n_images": len(members),
        "exposure": sum(float(im.get("exposure") or 0.0) for im in members),
        "image_ids": [im["id"] for im in members if im.get("id") is not None],
        "types": {}, "filters": {},
    }
    if undated:
        s["undated"] = True
    for im in members:
        fr = im.get("imagetyp") or "light"
        t = s["types"].setdefault(fr, {"n": 0, "exposure": 0.0})
        t["n"] += 1
        t["exposure"] += float(im.get("exposure") or 0.0)
        fl = im.get("filter") or "?"
        f = s["filters"].setdefault(fl, {"n": 0, "exposure": 0.0})
        f["n"] += 1
        f["exposure"] += float(im.get("exposure") or 0.0)
    s["types"] = {key: {"n": value["n"], "exposure": round(value["exposure"], 1)}
                  for key, value in s["types"].items()}
    s["filters"] = {key: {"n": value["n"], "exposure": round(value["exposure"], 1)}
                    for key, value in s["filters"].items()}
    return s


def compute_sessions(images: list[dict]) -> list[dict]:
    """Group exact solar intervals or visibly unresolved UTC date buckets.

    Capture ranges remain measured timestamps. Explicit calibration targets
    copy stored session membership without changing DATE-OBS or site metadata.
    """
    return [_session_record(members) for members in _session_groups(images)]


def session_for_anchor(images: list[dict], anchor_id: int) -> dict | None:
    """The current logical session containing an existing stored frame."""
    return next((session for session in compute_sessions(images)
                 if anchor_id in session["image_ids"]), None)


def session_for_image(images: list[dict], new_im: dict) -> dict | None:
    """The session (as in compute_sessions) that a new frame belongs to when
    appended to `images`, including a session it opens itself. Used by ingest to
    select exact stored/derived membership. Solar session folders use their
    fixed interval boundary, independent of capture order. Existing files are
    not relocated when metadata is backfilled."""
    for members in _session_groups(list(images) + [new_im]):
        if any(member is new_im for member in members):
            return _session_record(members)
    return None


def group_library(images: list[dict], catalog: list[dict]) -> list[dict]:
    """Group ingested frames per object, with totals and sessions."""
    by_key: dict[str, dict] = {}
    for im in images:
        # Fingerprints are local IO bookkeeping, not exported library metadata.
        im = public_image_record(im)
        oid = im.get("object_id")
        key = oid or ("u:" + name_key(im.get("object_name") or ""))
        g = by_key.setdefault(key, {
            "object_id": oid,
            "name": None,
            "designation": "",
            "otype": None,
            "size_am": None,
            "ra_hours": None,
            "dec_deg": None,
            "n_images": 0,
            "total_exposure": 0.0,
            "types": {},              # frame type -> {n, exposure}
            "filters": {},            # filter name -> {n, exposure}
            "first": None,
            "last": None,
            "images": [],
        })
        g["n_images"] += 1
        g["total_exposure"] += float(im.get("exposure") or 0.0)
        fr = im.get("imagetyp") or "light"
        t = g["types"].setdefault(fr, {"n": 0, "exposure": 0.0})
        t["n"] += 1
        t["exposure"] += float(im.get("exposure") or 0.0)
        fl = im.get("filter") or "?"
        f = g["filters"].setdefault(fl, {"n": 0, "exposure": 0.0})
        f["n"] += 1
        f["exposure"] += float(im.get("exposure") or 0.0)
        g["images"].append(im)

    objects = []
    rows = normalize_catalog(catalog)
    for g in by_key.values():
        row = None
        if g["object_id"]:
            row = _by_id(rows, g["object_id"])
            if row is None:
                # A stored id can vanish when the CATALOG changes (the curated
                # list was replaced by OpenNGC ids: an old capture resolved to
                # 'm42', the catalog now names that object 'ngc1976').
                # Re-resolve from the capture's own OBJECT header / coordinates -
                # the same path an ingest takes - before calling a real capture
                # "Unknown object". A capture that was never resolved
                # (object_id is None) stays unresolved: that is user intent.
                first = g["images"][0]
                row = resolve_object(rows, first.get("object_name"),
                                     first.get("ra_hours"), first.get("dec_deg"))
        if row:
            g["name"] = row["primary_name"] or g["object_id"]
            g["designation"] = designation_of(row, g["images"][0])
            g["otype"] = row["otype"]
            g["size_am"] = row["size_am"]
            g["ra_hours"] = row["ra_hours"]
            g["dec_deg"] = row["dec_deg"]
        else:
            first = g["images"][0]
            g["name"] = first.get("object_name") or "Unknown object"
            g["designation"] = ""
            g["otype"] = first.get("otype") or None
            g["size_am"] = None
            # fall back to header coordinates when not in the catalog
            g["ra_hours"] = first.get("ra_hours") or row_coord(g, "ra")
            g["dec_deg"] = first.get("dec_deg") or row_coord(g, "dec")
        g["total_exposure"] = round(g["total_exposure"], 1)
        for t in g["types"].values():
            t["exposure"] = round(t["exposure"], 1)
        for f in g["filters"].values():
            f["exposure"] = round(f["exposure"], 1)
        g["sessions"] = compute_sessions(g["images"])
        dates = [(_session_epoch(session[key]), session[key])
                 for session in g["sessions"] for key in ("start", "end")]
        dates = [(epoch, date) for epoch, date in dates if epoch is not None]
        if dates:
            g["first"] = min(dates, key=lambda pair: pair[0])[1]
            g["last"] = max(dates, key=lambda pair: pair[0])[1]
        g["n_sessions"] = len(g["sessions"])
        objects.append(g)

    objects.sort(key=lambda g: (g["name"] or "").lower())
    return objects


def row_coord(g: dict, kind: str):
    """Coordinates of the first image that actually has one."""
    for i in g.get("images", []):
        v = i.get("ra_hours" if kind == "ra" else "dec_deg")
        if v is not None:
            return v
    return None
