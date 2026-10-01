"""Preset rigs offered in the "Add rig" picker: smart telescopes, not defaults.

`focal_mm` is the scope's focal length, `sensor_w`/`sensor_h` are the imaging
sensor's active area in millimetres (resolution x pixel pitch), which is all the
frame-coverage estimate needs. FOV comes from the same formula as POST /api/rigs.

Nothing is ever added automatically: a fresh setup has an empty rig list and the
user picks a telescope in the dialog (or fills in a custom rig). The presets ship
to the UI through `/api/state` (`rig_presets`).

The module still knows how to UNDO the auto-seeding that 0.6.1/0.6.2 did
(`remove_seeded_once`), so a database created by those versions ends up empty
again - it only deletes rigs that still match a preset exactly, never one the
user edited or renamed.

The web app is static and cannot read Python: web/webstore.js carries the same
list as `DEFAULT_RIGS` - keep the two in sync.
"""

from __future__ import annotations

import math

SEEDED_FLAG = "rigs_preset_seeded"      # set by 0.6.1/0.6.2 (and by the 0.6.2 refill)
CLEANED_FLAG = "rigs_preset_cleaned"    # this cleanup already ran

# name, focal_mm, sensor_w, sensor_h
DEFAULT_RIGS: list[tuple[str, float, float, float]] = [
    # ZWO Seestar — Sony IMX462 (S50) / IMX662 (S30): both 1/2.8", 2.9 um pitch
    ("ZWO Seestar S50", 250.0, 5.57, 3.13),
    ("ZWO Seestar S30", 150.0, 5.57, 3.13),
    ("ZWO Seestar S30 Pro", 150.0, 11.14, 6.26),      # IMX585, 1/1.2"
    # DwarfLab DWARF — IMX678 (DWARF 3), IMX662 (mini), IMX415 (DWARF II)
    ("DwarfLab DWARF 3", 150.0, 7.68, 4.32),
    ("DwarfLab DWARF mini", 150.0, 5.57, 3.13),
    ("DwarfLab DWARF II", 100.0, 5.60, 3.18),
]


def fov_arcmin(focal_mm: float, sensor_mm: float) -> float:
    """Field of view in arcminutes.

    Twin of app.server.fov_arcmin (kept separate so this module never imports the
    route module); tests/test_rig_presets.py pins the two together.
    """
    return round(2 * math.degrees(math.atan(sensor_mm / 2 / focal_mm)) * 60, 1)


def presets_payload() -> list[dict]:
    """The picker list for /api/state: name + geometry + FOV in arcminutes."""
    return [
        {"name": name, "focal_mm": focal, "sensor_w": sw, "sensor_h": sh,
         "fov_w": fov_arcmin(focal, sw), "fov_h": fov_arcmin(focal, sh)}
        for name, focal, sw, sh in DEFAULT_RIGS
    ]


def _preset_by_name(name: str):
    want = str(name or "").strip().lower()
    for row in DEFAULT_RIGS:
        if row[0].strip().lower() == want:
            return row
    return None


def remove_seeded_once(db) -> int:
    """Delete the rigs an earlier version auto-seeded, once. Returns the count.

    Only EXACT, untouched presets go: a rig whose focal length/sensor differs
    (the user edited it) or that carries another name is user data and stays.
    """
    if db.setting(CLEANED_FLAG):
        return 0
    removed = 0
    if db.setting(SEEDED_FLAG):
        for r in db.rigs():
            preset = _preset_by_name(r.get("name"))
            if preset and (float(r["focal_mm"]), float(r["sensor_w"]),
                           float(r["sensor_h"])) == preset[1:]:
                db.delete_rig(r["id"])
                removed += 1
    db.set_setting(CLEANED_FLAG, 1)
    return removed
