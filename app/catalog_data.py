"""Catalog policy: the object-type vocabulary and the standard-mode selection.

The catalog DATA is OpenNGC - `app/data/openned.csv`, built by
`scripts/build_adv_catalog.py` from `database_files/NGC.csv` + `addendum.csv`.
There is NO hand-curated object list any more (the 197-object list that used to
live here was removed): every row in either mode is an OpenNGC object.

What this module owns is the policy both shells share:

  * `TYPE_LABELS` - the object-type vocabulary an OpenNGC type is mapped into
    (`core/catalog.js` documents the same palette), and
  * `STANDARD_TOP_N` / `standard_ids()` - standard mode is the N MOST POPULAR
    objects (`app/popularity_data.py`), advanced mode is the whole catalog.
    Popularity itself stays INVISIBLE in both UIs (it is a silent ranking
    tiebreaker, pinned by `tests/test_ui_invariants.py`) - it only decides
    WHICH objects the standard list contains.
"""

TYPE_LABELS = {
    "globular": "Globular cluster",
    "open_cluster": "Open cluster",
    "nebula": "Nebula",
    "planetary": "Planetary nebula",
    "galaxy": "Galaxy",
    "supernova_remnant": "Supernova remnant",
    "asterism": "Asterism / double star",
    "star_cloud": "Star cloud",
}

# Standard mode = this many objects, by popularity. Advanced mode
# adds every other OpenNGC object; the two tables are disjoint, so the catalog
# is exactly `openned.csv` either way.
STANDARD_TOP_N = 200


def standard_ids(ids, popularity=None, top_n: int = STANDARD_TOP_N) -> set[str]:
    """The standard-mode id set: the `top_n` most popular objects.

    Deterministic (score descending, then id ascending) and conservative: an
    object with no score is not "popular" and never becomes standard, so a
    catalog with fewer than `top_n` scored objects simply has a smaller standard
    list rather than a list padded with unscored rows.

    `popularity` defaults to the shipped table - pass a dict to test the rule.
    """
    if popularity is None:
        from .popularity_data import POPULARITY as popularity
    ranked = sorted(((popularity.get(i, 0), i) for i in ids),
                    key=lambda pair: (-pair[0], pair[1]))
    return {oid for score, oid in ranked[:top_n] if score}

# Ids the catalog used BEFORE 0.6.26 (the hand-curated 197-object list) -> the
# OpenNGC id that names the same object. A user's image library stores the id a
# capture resolved to at INGEST time, so without this an old 'm42' would stop
# resolving after the catalogue switch (`Database.migrate_legacy_object_ids()`
# rewrites the stored ids once; `app/imglib.py` also re-resolves by name/coords).
# Curated-only objects OpenNGC has no entry for (Abell 21, Jones-Emberson 1,
# Barnard's Loop, ...) are deliberately ABSENT: there is nothing to point at.
LEGACY_ID_MAP = {
    'cr399': 'cl399',
    'lmc': 'eso56-115',
    'm1': 'ngc1952',
    'm10': 'ngc6254',
    'm100': 'ngc4321',
    'm101': 'ngc5457',
    'm102': 'ngc5866',
    'm103': 'ngc581',
    'm104': 'ngc4594',
    'm105': 'ngc3379',
    'm106': 'ngc4258',
    'm107': 'ngc6171',
    'm108': 'ngc3556',
    'm109': 'ngc3992',
    'm11': 'ngc6705',
    'm110': 'ngc205',
    'm12': 'ngc6218',
    'm13': 'ngc6205',
    'm14': 'ngc6402',
    'm15': 'ngc7078',
    'm16': 'ngc6611',
    'm17': 'ngc6618',
    'm18': 'ngc6613',
    'm19': 'ngc6273',
    'm2': 'ngc7089',
    'm20': 'ngc6514',
    'm21': 'ngc6531',
    'm22': 'ngc6656',
    'm23': 'ngc6494',
    'm24': 'ic4715',
    'm25': 'ic4725',
    'm26': 'ngc6694',
    'm27': 'ngc6853',
    'm28': 'ngc6626',
    'm29': 'ngc6913',
    'm3': 'ngc5272',
    'm30': 'ngc7099',
    'm31': 'ngc224',
    'm32': 'ngc221',
    'm33': 'ngc598',
    'm34': 'ngc1039',
    'm35': 'ngc2168',
    'm36': 'ngc1960',
    'm37': 'ngc2099',
    'm38': 'ngc1912',
    'm39': 'ngc7092',
    'm4': 'ngc6121',
    'm41': 'ngc2287',
    'm42': 'ngc1976',
    'm43': 'ngc1982',
    'm44': 'ngc2632',
    'm45': 'mel22',
    'm46': 'ngc2437',
    'm47': 'ngc2422',
    'm48': 'ngc2548',
    'm49': 'ngc4472',
    'm5': 'ngc5904',
    'm50': 'ngc2323',
    'm51': 'ngc5194',
    'm52': 'ngc7654',
    'm53': 'ngc5024',
    'm54': 'ngc6715',
    'm55': 'ngc6809',
    'm56': 'ngc6779',
    'm57': 'ngc6720',
    'm58': 'ngc4579',
    'm59': 'ngc4621',
    'm6': 'ngc6405',
    'm60': 'ngc4649',
    'm61': 'ngc4303',
    'm62': 'ngc6266',
    'm63': 'ngc5055',
    'm64': 'ngc4826',
    'm65': 'ngc3623',
    'm66': 'ngc3627',
    'm67': 'ngc2682',
    'm68': 'ngc4590',
    'm69': 'ngc6637',
    'm7': 'ngc6475',
    'm70': 'ngc6681',
    'm71': 'ngc6838',
    'm72': 'ngc6981',
    'm74': 'ngc628',
    'm75': 'ngc6864',
    'm76': 'ngc650',
    'm77': 'ngc1068',
    'm78': 'ngc2068',
    'm79': 'ngc1904',
    'm8': 'ngc6523',
    'm80': 'ngc6093',
    'm81': 'ngc3031',
    'm82': 'ngc3034',
    'm83': 'ngc5236',
    'm84': 'ngc4374',
    'm85': 'ngc4382',
    'm86': 'ngc4406',
    'm87': 'ngc4486',
    'm88': 'ngc4501',
    'm89': 'ngc4552',
    'm9': 'ngc6333',
    'm90': 'ngc4569',
    'm91': 'ngc4548',
    'm92': 'ngc6341',
    'm93': 'ngc2447',
    'm94': 'ngc4736',
    'm95': 'ngc3351',
    'm96': 'ngc3368',
    'm97': 'ngc3587',
    'm98': 'ngc4192',
    'm99': 'ngc4254',
    'mel25': 'c41',
    'ngc2244': 'ngc2238',
    'sh2-155': 'c9',
    'smc': 'ngc292',
}
