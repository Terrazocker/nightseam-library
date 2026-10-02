# Independent catalog decisions

On October 1, 2026 the local catalog and website became separate repositories.
Each owns its source dependencies, catalog inputs, tests, builds and release
versions. The catalog begins at 0.1.0; the website retains its existing history.
No sibling source imports or symlinks are used. Existing archive paths and both
desktop libraries were preserved. The remaining entries below are historical
decisions from the combined project; references to removed cross-project paths
describe that historical state, not current build requirements.

# DECISIONS.md — why NightSeam works the way it does

One entry per decision that an agent or maintainer would otherwise re-litigate.
Format: **decision** → why → what was rejected → where it is enforced.

`AGENTS.md` states the rules; this file records the reasoning. `CATALOG.md`
covers the data pipeline. `BUGS.md` is the current unresolved backlog. Closed
audit/review bundles were removed from the working tree on 2026-09-17; Git
history, tests and `RELEASES.md` preserve their durable evidence.

---

## 2026-10-01 - FITS identity and nonblocking archive IO

**Decision.** Complete FITS file bytes define identity through SHA-256. Renaming
or selecting another session/frame override does not create another archive
copy or library record. The UI explicitly reports Already imported. Future
calibration sharing and reclassification need separate validated operations.

**Why.** The user requires awareness of previous imports with no duplication.
Destination-based identity would silently multiply identical exposures and
inflate library totals. Hashes imported from a backup are not trusted identity
evidence; local original bytes establish fingerprints.

**Implementation.** Blocking file IO runs in workers under archive-operation
serialization, separate from the short SQLite lock. Indexed candidates replace
full-table per-upload scans. Positive fingerprints are verified against current
original bytes. Reindex refreshes fingerprints after external archive edits.
No SQLite RLock may be held across an await. Missing-original repair preserves
the record and is reported separately from newly imported captures.

## 2026-10-01 - Retired model and cached-core compatibility

**Decision.** The experimental modeled sky-brightness feature was deliberately
removed from both planner shells and shared core. Its controls/data are not part
of the next release, and no modeled value is presented as a measured sky reading.

The narrow `--retire-model-bearing-previous` bake excludes the old model-bearing
tree from local distributions (`previous: null`). Ordinary bakes retain N-1.
The exception does not authorize deleting deployed old trees referenced by
cached stubs. Verify those remote files and keep compatibility trees available
when deploying the retirement; publish modules before HTML. No deployment or
live cached-client compatibility check is performed by this implementation pass.

## 2026-09-27 — Workbench is the only shipped presentation

**Decision.** Workbench is the only presentation in the desktop app and web
planner. There is no theme picker or Glass layout. Both shells use the same
role palette; their component styles remain separate because their DOMs differ.

**Why.** The planning workflow benefits from a restrained instrument-panel
surface: opaque graphite keeps charts and target data legible, brass gives the
user one clear action/focus signal, and the compact layout leaves more room for
the actual night plan. Opaque surfaces and compact controls keep chart and
target information legible.

**Compatibility.** `dome`, `classic`, empty, and unknown values are accepted as
legacy inputs and normalize to `workbench`. Desktop settings and web backups
use the same canonicalization rule, so removing the picker does not strand an
existing preference or backup.

**What was rejected.** Keeping a second selectable presentation would add
duplicate layout overrides and leave visual behavior dependent on a stored
setting. Legacy values remain accepted only at the compatibility boundary.

**Where enforced.** `app/theme.py`, `app/database.py`, `app/server.py`,
`web/webstore.js`, `web/app.js`, `static/js/header.js`, `web/themes.css`,
`static/themes.css`, and the theme migration tests.

---

## Unreleased review decisions - 2026-09-20

This section mixes decisions implemented in the current working tree with
explicitly pending product/architecture choices. Implemented entries are still
not release claims until a tag is made; pending entries must be resolved before
their decision-dependent features are implemented.

### Shared planner-state invalidation

**Decision.** Treat planner state changes as typed invalidation events with a
revision-bearing payload. Panorama/profile edits, site changes, rig changes,
date changes, selected time changes, and planning preferences must publish the
same conceptual contract in both shells. Derived night state and observation
caches must include the relevant revisions instead of relying on object ids
alone.

**Why.** The current shells can display an edited panorama while still showing
derived results from the old profile. This is a correctness problem, not merely
a repaint problem. A single event contract prevents every future consumer from
inventing a second invalidation rule.

**Proposed payload.** `{ kind, revision, causes, siteId, panoId, dateKey }`,
with optional domain revisions for site, rig, panorama, and preferences. A
completed user gesture emits once; pointer samples remain local to the editor.

**What was rejected.** Recomputing on every pointer sample would waste work and
make the UI race its own worker. Invalidating only by panorama id is insufficient
because a restored or edited record can retain the same id.

**Where to enforce.** The shell event adapters belong in `static/js/` and
`web/`; pure revision/key helpers may live in `core/`. `derive.js`,
`planning_client.js`, `web/app.js`, `web/horizon.js`, and the observation caches
must consume the contract. Browser tests must prove that a profile edit changes
dependent results in both shells.

**Working-tree status.** The shared foundation is implemented in
`core/plannerstate.js`; desktop and web now emit the contract for panorama,
site, rig, date, selection, and preference changes. The event model is ready
for future planner consumers. The accessibility foundation now has a 32/32
cross-shell/theme browser matrix; the remaining manual accessibility audit and
the roadmap features below still need their own coverage.

### Panorama persistence boundary

**Decision.** Keep the explicit-save model: edits affect the active in-memory
panorama immediately, while durable storage changes only on Save or when the
user leaves the editor. A page-exit flush is a safety net, not the normal user
interface. Save should remain visibly meaningful and dirty state should be
truthful.

**Why.** This matches the existing design contract, makes undo/redo and Revert
understandable, and gives users a clear persistence boundary for destructive
editor operations.

**What was rejected.** Silent timed autosave makes the Save button ambiguous and
can persist a gesture before the user has reviewed it. If autosave is later
chosen, it must be a deliberate contract change in both shells.

### Site elevation contract — decision still required

**Decision.** Until a defined observer model exists, elevation remains stored
site metadata and must not be described as affecting calculations. A later
observer-model feature may use it for explicitly documented effects such as
horizon dip or atmospheric parameters, but those effects must be implemented in
`core/` and shared by both shells.

**Why.** The current calculation path accepts latitude and longitude but does
not use elevation consistently. Claiming more than the implementation does is
worse than accurately labeling the field as metadata.

**What was rejected.** Adding an unexplained correction in one shell would make
desktop and web disagree and would create no trustworthy reference values.

**Exit condition.** Either remove the claim from user documentation and add a
metadata test, or implement a specified elevation-aware observer model with
reference anchors, migration rules, and parity tests.

### Solar-system ephemeris policy — decision still required

**Recommended direction.** Use a bounded, versioned, offline-capable ephemeris
with explicit date coverage and provenance stored alongside the generated
result. This keeps the planner reproducible and usable without a network
request. The target-result contract should expose the same rise, set,
culmination, altitude, interval, and reason fields as deep-sky targets.

**Alternatives requiring an explicit choice.** A larger, higher-accuracy
bundled model increases artifact size and maintenance cost; an external
provider can improve coverage but requires a cache, offline behavior, source
version display, and defined failure semantics. No planet, comet, or asteroid
implementation should begin until the source, accuracy target, supported date
range, and out-of-range behavior are chosen.

### Unknown theme values during web restore

**Decision.** Normalize legacy, empty, and unknown theme values to Workbench in
web restore, matching desktop settings and the documented compatibility rule.

**Why.** A backup should be deterministic across shells. Keeping the current
theme for an unknown value is defensively conservative, but it makes the same
backup behave differently depending on the user's current preference.

**What was rejected.** Treating `null` as a third semantic theme or allowing
unknown values to pass through to DOM attributes would create compatibility and
styling ambiguity.

**Where to enforce.** Use one canonicalizer in `web/webstore.js`, update the
backup tests, and keep the `data-theme` value fixed to `workbench`.

### Feature-data principle

**Decision.** New planner features should return structured, explainable data
from shared core calculations first, then let each shell format that data for
its UI. Reasons, intervals, provenance, and uncertainty must not be reconstructed
independently in desktop and web presentation code.

**Why.** The planned target-explanation, transit, horizon, and catalog-provenance
features all need the same facts in both shells. Keeping only a final boolean or
formatted sentence in the UI will recreate the parity drift that the shared core
was introduced to prevent.

**Where to enforce.** Add typed/plain-object contracts to `core/`, freeze
fixture-level examples, and require both shells to consume those fields without
duplicating astronomy or planning math.

### Accessibility foundation

**Decision.** Treat charts as visualizations with a parallel semantic surface:
each canvas has an accessible role and a concise live-updated text summary;
planner tabs expose selected state; dialogs dismiss with Escape; focus-visible,
high-contrast, and reduced-motion behavior are explicit in both shells.

**Why.** A canvas can be perfectly legible to a sighted user while exposing no
target state, timing, or editor status to a keyboard or screen-reader user.
The summary is intentionally concise and derived from the same structured
result the chart draws, so it does not become a second planning implementation.

**What remains.** This is the source/UI contract, not a completed conformance
claim. Focus restoration after closing dialogs and menus, dynamic announcements
for long-running planner updates, and manual testing with actual assistive
technology remain required before calling accessibility complete.

**Where enforced.** `static/index.html`, `web/index.html`, both shell chart
renderers, `static/style.css`, `web/style.css`, and the paired browser checks
`desktop-accessibility-essentials` / `web-accessibility-essentials`.

---

## 0.6.33 fix pass — the web policy is enforced from the DOCUMENT, not a header

**Decision.** `web/index.html` carries the policy as
`<meta http-equiv="Content-Security-Policy">`, with the same directives as
`web/.htaccess` except `frame-ancestors`. The production smoke accepts either
carrier (a response header still wins when present) and a missing policy is a hard
failure.

**Why.** Measured 2026-09-17 through Chrome on the live origin: `/astro/` answers
`server: nginx` + `content-type: text/html` with NO policy header and NO meta
policy. nginx serves the document itself, so `web/.htaccess` never applies to it,
and the Plesk console on this host exposes no nginx directive field. The choices
were a meta policy, a hosting-panel action that is not available, or leaving the
public origin with no enforced policy at all while a check claimed otherwise -
which is precisely the false assurance BUG-003 exists to remove.

**What the meta carrier cannot do, stated plainly.** Browsers IGNORE
`frame-ancestors`, `report-uri`/`report-to` and `sandbox` in a meta policy, so
those are omitted there and the check reports them as findings if anyone adds them
(claiming protection that does not exist is the bug, not the workaround). A meta
policy is also weaker than a header in principle: it is parsed with the document,
so a flaw that corrupts the response before the tag could drop it. Against
injected MARKUP it is sound - a `<meta>` placed in `<body>` is ignored - which is
the threat this origin actually faces.

**Required directive that looked like a relaxation.** `style-src 'self' 'unsafe-inline'`
in BOTH carriers. The shell styles several controls with inline `style=`
attributes, and `style-src 'self'` blocks them with NO console message: measured
2026-09-17, the range inputs and theme swatches silently lost their styling while
the page booted normally (`Refused to` messages: 0). Inline STYLE cannot execute,
so this is not the same class of exemption as `script-src 'unsafe-inline'`, which
stays forbidden. The production smoke now measures a computed background on an
inline-styled element, so the failure mode cannot be silent again.

**Rejected.** (a) Leaving it open until the host offers nginx directives - the
exposure is on the user's data today, and the workaround costs one file. (b)
Serving the app through Plesk's Node.js/PHP handlers to gain a header - a large
deployment change for a marginal carrier upgrade. (c) Quietly weakening the smoke
to pass without a policy - that is the defect, restated.

**Enforced by.** `tests/test_prod_csp.py` (both carriers against one rule table,
the no-header-only-directive rule, and the shared directives), and `web-prod`,
which reads the live carrier and measures the inline-style outcome.

## 0.6.33 fix pass — the Tonight view publishes what the SCRUBBER names

**Decision (BUG-005).** `web/app.js` publishes `#view[data-ns-gen]` /
`[data-ns-sel]` after every completed Tonight paint. `data-ns-sel` is the selected
instant on the scrubber's OWN grid (`scrubTimeMs`), and the browser check polls
`wait_tonight_after(gen, sel=<requested instant>)` until the view carries exactly
that value. `applyTonightMarker()` publishes the generation plus `Date.now()` -
deliberately a value a wait can never match - whenever the paint does not belong
to the current selection.

**Why.** The check slept a nominal 120 ms while the app debounced the same 120 ms
and then deferred its ranking by another tick, so it sampled the previous
selection about as often as the new one (25/26 full-sequence failures, green in
isolation). Waiting for "a newer paint" is not enough either: the boot sequence
can leave a stale marker behind, and a check that accepted any generation bump
sampled whatever happened to be on screen.

**Found by turning the check on (three real defects, all fixed at the source).**
(1) `renderTonight` captured `S.skyTime` when it was CALLED, but its compute runs
~30 ms later - a selection clamped in between painted cards for the old instant;
it now reads the live value inside the deferred callback. (2) `clampSkyTime()`
announced `sky:time` only when it CHANGED the time, so at boot the announcement
landed before the scrub listener was registered and both the slider and the
Tonight view kept the PRE-clamp selection; it now announces unconditionally
(`syncScrub()` re-pins the slider from the current range on every announcement).
(3) `#tb-scrub` is `<input type="range" step="15">`, so the browser SNAPS what
`syncScrub()` writes (write 204, DOM 210) and a scrub writes that snapped value
back into `S.skyTime` - a marker publishing raw milliseconds could never equal
what the scrubber names (measured 6 minutes apart).

**Rejected.** (a) Comparing raw milliseconds - mismatch by construction for any
page loaded at a non-zero second, and by one step for any load whose offset is not
a multiple of 15. (b) Publishing the raw `S.skyTime` and teaching the check to
tolerate a step of slop - that is a sleep by another name; the check must demand
the exact instant it requested. (c) Polling "rows changed" - the lists can be
identical between two adjacent samples, so it cannot distinguish "painted" from
"stale". (d) Only clearing the marker on a moved selection - a waiting check would
then hang on a boot-time stale paint instead of reporting it.

**Enforced by.** `web-tonight-render-marker-converges` and the corrected
`web-tonight-pool-includes-unmagnituded-standard-rows` in the browser gate
(`--web`: 18/18), plus `tests/test_tonight_marker.py` for the offline contract
(including that the two sides agree on the attribute names).

## 0.6.33 fix pass — a restore is staged, not streamed into place

**Decision.** `apply_import_zip` runs three phases in a fixed order: (1) validate
the ENTIRE bundle with `database.prepare_import_bundle` (a side-effect-free
function the transaction also uses, so the pre-flight check and the insert loop
cannot disagree); (2) stage every payload into `<target>.import-<n>.part` on the
destination filesystem while moving any file it would replace into a
`payload.journal` directory beside it; (3) run the database transaction, and only
on success swap the staged files in and delete the journal. Any exception before
the swap restores the journal and removes the staged files.

**Why.** The audit reproduced the failure shape: `_write_payloads` ran BEFORE
`Database.import_bundle` did its own validation, so a bundle with a valid
colliding payload plus one invalid record replaced the user's file
(`ORIGINAL` -> `REPLACED`) and then reported "the import failed" - which reads as
"nothing happened". Validation had to move before the first write, and "written
first" was not enough on its own: PY-03's temp-file-then-rename made ONE file
atomic, not the RESTORE.

**Rejected.** (a) A second validation copy inside `backup.py` - two predicates
drift, which is exactly how PY-06/SP-05 happened; the shared function is used by
both callers. (b) Backing up the whole payload tree (a full copy of a multi-GB
image library before every restore) - the journal holds only the files this
restore actually replaces. (c) Hidden `.bak` copies next to the payloads - the
panorama/library readers would have to learn to skip them, and a crashed restore
would leave disguised payload files behind; one `payload.journal` directory is
obvious and removable. (d) Making the zip extraction itself transactional - the
zip is untrusted input, so validating it up front is both simpler and safer than
rolling back arbitrary writes.

**Enforced by.** `tests/test_backup.py` (the rejected-restore cases assert both
the database rows AND the payload bytes are unchanged, and that no `.part` file or
journal survives), plus the existing PY-03/SP-01/PY-06 cases which stay green.

## 0.6.33 fix pass — the desktop CSP allowlists the bootstrap by hash

**Decision.** `app/server.py::CSP_HEADER` is
`script-src 'self' 'sha256-lOMdolhkUqnnqFBUwwNKJYbMdAlQvEBKqybJqHdppbw='` - no
`'unsafe-inline'`. The two inline `onerror=` thumbnail fallbacks in
`static/js/targets.js` and `static/js/tonight.js` became `addEventListener`
handlers so nothing else needs the exemption. `tests/test_update_check_csp.py`
recomputes the hash from `static/index.html` and fails if either side drifts.

**Why.** `'unsafe-inline'` in `script-src` also permits inline EVENT HANDLERS.
With it, an attribute that reached the DOM unescaped executed: the audit rendered
the imported custom id `u_x" onmouseover="globalThis.__pwned=1` as a working
handler through `static/js/setup.js`. Two independent fixes were needed, and both
shipped: never let untrusted data into the database unvalidated (the `u_` + token
rule, `imagetyp` normalization), and never allow the markup to execute even if it
gets there (attribute escaping, no inline handlers, a script-src that forbids
inline code).

**Rejected.** (a) Keeping `'unsafe-inline'` and relying on the escaping alone -
one missed `esc()` would be a live XSS again. (b) Moving the desktop theme
bootstrap out of `index.html` into a module - it must run before first paint, and
`main.js` runs after; the hash costs nothing and matches what `web/.htaccess`
already does for the web shell. (c) Dropping `style-src 'unsafe-inline'` too -
inline STYLE cannot execute, and the shipped themes plus the JS use `style=`
throughout the UI.

**Enforced by.** `tests/test_update_check_csp.py::test_the_desktop_csp_hashes_the_one_inline_bootstrap`,
`tests/js/dom_escape.mjs` (mutation-proven: removing one `esc()` fails it) and the
browser gate.

## 0.6.33 fix pass — imported record ids are tokens, not just prefixed

**Decision.** An imported custom target id must start with `u_` AND satisfy
`paths.safe_token()` (`database.custom_id_problem`, used by both the pre-flight
validation and the insert loop). An imported image `imagetyp` is normalized
through `ingest.classify_frame` at the boundary, so only light/dark/flat/bias can
be stored.

**Why.** Both values are interpolated into HTML ATTRIBUTES by the desktop shell
(`data-delcust="${o.id}"`, and the archive's per-session type rollup in a `title`),
and `imagetyp` is free-form TEXT in the database. The `u_` prefix was the only
rule, so `u_x" onmouseover="..."` was accepted and executed.

**Rejected.** (a) Escaping only in the renderers - it leaves the hostile value in
the database, where the next renderer (or an export/import round trip) reads it
again. (b) Rejecting an `imagetyp` the classifier does not recognize - the FITS
path has always mapped an unknown IMAGETYP to `light`, and turning that into a
hard import failure would reject bundles this app itself exported from an older
library. (c) A new "unknown" frame class - it would change the library's grouping
and `lights/` folder mapping for a value that has never had one.

**Enforced by.** `tests/test_backup.py` (the id table and the hostile-imagetyp
round trip) and `tests/js/dom_escape.mjs` (the rendering half, independent of the
boundary).

## 0.6.33 fix pass — the production smoke reads the DOCUMENT's CSP

**Decision.** `scripts/check_browser.py --prod-url` reads
`Content-Security-Policy` from the `page.goto()` response only. A missing document
policy is a hard failure; the `version.json` / `catalog.manifest.json` fallback is
gone. The document rule drops the web shell's inline-bootstrap-hash requirement
(the entry point may be the static desktop shell) but keeps every other policy
rule.

**Why.** A CSP protects only the response that carries it. Measured 2026-09-15:
`/astro/` and `/astro/app.js` served no policy while `/astro/version.json` did, so
the old fallback reported "the host applies the policy we ship" for a deployment
where every browser ran the HTML unprotected. That is worse than no check: it is
false assurance about the exact property the check exists to prove.

**Rejected.** (a) Deleting the production smoke - the host-layer gap would then be
invisible. (b) Keeping the fallback and adding a warning - warnings in a gate log
are not evidence. (c) Fixing it in `web/.htaccess` alone - nginx serves
`.html`/`.js`/`.css` itself on this host, so the Apache file never sees the
document (this is why the fix is an nginx directive plus a strict check).

**Enforced by.** `tests/test_prod_document_csp.py` (the rule table, the
`document=True` call site, and the absence of any other-path probe) and, live,
`--prod-url https://nightseam.de/astro/` - which now FAILS until the nginx
directive is applied. That failure is the point.

---

## 0.6.32 — No distance field, and the image lookup uses the designation

**Decision (a).** `distance` is gone from the catalogue: out of
`build_adv_catalog.py`'s row layout, out of the CSV and the hand-kept supplement,
out of the seeder's INSERT and column reader, out of the API payload and both
detail views. `Database._drop_retired_fields()` strips it from `SELECT *`
results, so an existing user database (whose table still carries the column -
user schemas are never re-shaped) cannot ship it either.

**Why.** It was ALWAYS empty - the curated list that once filled it was deleted in
0.6.26 - and OpenNGC has no distance column. Measured on the shipped sources:
`Pax` exists for 709 of 12,160 rows and for galaxies it is a foreground star's
parallax (M31: 6.0 mas => 167 pc against a true 780 kpc); `Redshift` covers 10,626
rows but is not a distance - M101's z gives 11 Mly against a true 23 Mly, M31 and
M33 are blueshifted (a naive conversion yields a negative distance), and 2,893
rows sit below z = 0.01 where peculiar motion dominates. A field that can only be
filled with a wrong number is worse than no field.

**Rejected.** (a) Filling it from `Pax` for the ~630 plausible rows: nothing tells
the user whether a parallax belongs to the object or to a star in front of it.
(b) Filling it from redshift with H0 = 70: defensible for distant galaxies, wrong
by 2x for exactly the famous nearby ones. (c) Keeping the empty column "for
later" - the empty column is what prompted the question.

**Decision (b).** The Wikipedia lookup tries the full designation first.
`core/catalog.js::wikiTitles()` returns the stored `wiki` title (none ships any
more), then `Messier N`, then the NGC/IC designation, then the display name; both
shells skip DISAMBIGUATION pages, and the detail view's Wikipedia link uses the
same first candidate (the web then rewrites it to the canonical title the API
returns, post-redirect).

**Why.** The display name of a Messier row without a common name IS the bare
`M101`, and `M101` on Wikipedia is a disambiguation page (motorways, highways, a
rifle). Measured: `Messier 101` and `NGC 5457` return BOTH a Wikidata P18 image
and a Wikipedia thumbnail; `M101` returns a Wikidata image but no page image - so
the old single-shot lookup gave up and the user saw "no photo on Wikipedia".

**Rejected.** (a) Free-text search - the module's own history (unrelated pages for
"M29") and the repo's rule against fuzzy matching. (b) Remembering a rate-limited
attempt as "no photo": probing showed the 6th rapid request returns HTTP 429, and
the web used to cache that as definitive for 30 days; it now stores only a
definitive failure and says "unavailable right now" otherwise.

**Enforced by.** `test_wiki_titles_avoid_the_messier_disambiguation_trap` (the
`wikititles` engine), `tests/test_catalog_integrity.py` (no editorial columns in
the CSV), `tests/test_supplement.py`, and the `data`/`col` test tuples.

## 0.6.31 — Progress must not move the layout

**Decision.** A live status may change its TEXT, never the geometry. The
catalog's count and progress live in a fixed-height row with a reserved status
width; the web table uses `table-layout: fixed` so a cell filling in ("…" → "42%")
cannot resize a column; the busy chip sits in a reserved `.busy-slot` in the
topbar of BOTH shells, and the slot reserves its HEIGHT as well as its width
(because at 1366 px the topbar already wraps, so an empty slot is a zero-height
row); the desktop's `#resultCount` is a fixed-width right-aligned block.

**Why.** Measured before/after: the desktop topbar grew 93 → 111 px whenever the
busy chip appeared, pushing the whole app down; the catalog table widened the PAGE
to 721 px at a 360 px viewport; the percentage columns redistributed on every
cell update. Only reserving WIDTH is not enough - the wrapping topbar made the
slot's height the real culprit.

**Enforced by.** `test_progress_and_status_never_move_the_layout` plus the browser
checks, which MEASURE table top, column widths, stats-row height and topbar height
before/while/after progress runs.

---

## 0.6.29 — Supplement batch 2: the popular targets OpenNGC's *scope* excludes

**Decision.** `app/data/supplement.csv` gained nine rows (16 total, catalogue
12,176): the Tulip Nebula (`sh2-101`), Simeis 147 / Spaghetti (`sh2-240`), the
Flying Bat + Squid (`sh2-129`), the Dolphin Head (`sh2-308`), the Lion Nebula
(`sh2-132`), Barnard 150 / Seahorse (`b150`), LBN 437, LDN 1622 and the Pipe
Nebula (`pipe`).

**Why these nine.** They are the first tier of a checklist of ~70 famous hobby
targets the shipped catalogue could not answer for. Every one is absent from
OpenNGC **structurally** (Sharpless/Barnard/LDN/LBN designations are outside its
NGC/IC + addendum scope) rather than dropped by the type filter, and the popularity source
already ranks every one of them - all nine above the Standard-mode cut
(`STANDARD_TOP_N` = 200), so the app's own popularity data selects them into
Standard mode with no hand-made rule. The same checklist cleared the catalogues
everybody asks about: Messier is 109/110 (M102 is upstream `Dup` of M101),
Caldwell's non-NGC members are already in the addendum (`c9` Cave, `c41` Hyades,
`c99` Coalsack, plus `c14` as a pair row), the Small Magellanic Cloud is
`ngc292`, M24 is `ic4715` and the Witch Head is `ngc1909` (upstream's IC 2118 is
the `Dup` pointer at it).

**Magnitude policy: `vmag` stays EMPTY.** An extended or dark nebula has no
credible integrated V magnitude, and the pre-0.6.26 curated list's proxies
(Barnard's Loop "5.0") are exactly the editorial data 0.6.26 removed. Empty is
also what the shipped addendum already does for `b33` (Horsehead), `c9`, `c99`
and `mel111`. The consequence is intended, not a bug: per `magVisible`, a row
with no magnitude appears only once the magnitude slider is at its maximum, so
these are plan-able at full depth and never at a brighter limit.

**Naming rule (id care).** Use the object's own catalogue designation when it has
one (`sh2-101`, `b150`, `lbn437`, `ldn1622`); use a NAME token when the identity
*is* a proper name with no single catalogue number (`pipe`). Every alternative
designation goes into `other`, so search still finds it. Never add an id whose
hyphen/space-stripped form equals another row's id: `M2-9` (Minkowski's
Butterfly) normalizes to `m29` = NGC 6913, so it is NOT usable as an id and needs
the alias treatment if it is ever added.

**Provenance.** Astrometry is SIMBAD ICRS/J2000 (`Sh2-101`, `Sh2-240`, `Sh2-129`,
`Sh2-308`, `Sh2-132`, `Barnard 150`, `LDN 1622`, `LBN 437`, and `NAME the Pipe
Nebula`, whose 240x180 arcmin size is a measurement citing 2018MNRAS.474.4672L).
Sizes are published values (Sharpless 1959, Lynds 1965 LBN, Wikipedia); LDN 1622
has NO published angular size, so its `size_am` is empty (like `c99`).
`distance`/`wiki`/`blurb` stay empty - the catalogue carries no prose.

**Rejected.** (a) Leaving the objects out because OpenNGC does not list them -
the app is a planner, not an OpenNGC mirror. (b) Bulk-importing Sharpless/LDN:
Sh2 alone is 313 objects and LDN is 1,800+, their positions come from papers with
their own licensing (not OpenNGC's CC BY-SA), and they are overwhelmingly not
hobby targets. (c) Inventing magnitudes to make them visible at the default limit
(see the magnitude policy). (d) Raising `STANDARD_TOP_N` so the nine displace
nobody - rejected for now: 200 is the documented promise, and the ranking is what
makes the displacement defensible (these nine are genuinely more observed than
the nine they replace).

**Enforced by.** `tests/test_supplement.py` (membership, types, no editorial
text, no invented magnitude, searchable designations, the normalization guard),
the advertised size in `tests/test_catalog_integrity.py`, `CATALOG.md`.

## 0.6.28 — The catalogue is OpenNGC *plus* a documented supplement (batch 1)

**Decision.** `app/data/supplement.csv` was introduced with seven objects OpenNGC
cannot represent (Medusa Nebula, Headphone Nebula, Barnard's Loop, Alpha Persei
Cluster, Sadr region/IC 1318, M40, M73) - objects it either lacks or files as
non-objects. 0.6.29 added batch 2 above; total catalogue: 12,176.

**Why.** They are real, plan-able targets that the pre-0.6.26 curated list
carried, and OpenNGC either lacks them or files them as non-objects (`IC 1318`
is typed `*` for gamma Cyg, `M73` `Other`, `M40` `**`). Dropping them silently
made the app worse at the exact moment it became more complete.

**Rejected.** (a) Staying "OpenNGC only" and losing the objects. (b) Editing the
OpenNGC CSVs. (c) A second curated list - the supplement is data in the
catalogue's own column layout, appended by the generator, with its own test that
pins the exact membership.

**Enforced by.** `tests/test_supplement.py` (membership, types, no editorial
text), the advertised size in `tests/test_catalog_integrity.py`, `CATALOG.md`.

## 0.6.28 — Keep the editorial text gone, keep popularity as it is

**Decision.** The catalogue carries no blurbs, distances or Wikipedia titles;
popularity is not refreshed on a schedule or checked for staleness.

**Why.** OpenNGC has no prose, and inventing a second prose source would
re-introduce exactly the curated/OpenNGC split the 0.6.26 rebuild removed. The
Popularity ranking moves slowly (a live refresh shifted scores by ~0.5 % and no
standard-mode member for score reasons), so a refresh is a deliberate act, not a
chore.

**Superseded in 0.6.33.** The network refresh implementation and `--live` flag
were removed completely. The committed popularity data is a frozen snapshot;
`scripts/match_popularity.py --live` now exits 2 instead of fetching.

**Rejected.** Re-attaching the old curated prose as a decoration layer (kept as
an option, not taken); a staleness warning in the guards.

## 0.6.26 — The catalogue IS OpenNGC; standard mode is the 200 most popular

**Decision.** The 197-row hand-curated list is deleted. Every row comes from
OpenNGC (`NGC.csv` + `addendum.csv`, then the supplement); `objects` holds the
200 highest-scoring objects and `advanced_objects` the rest.

**Why.** Two sources meant two truths for the same object (positions, types,
sizes), a hand-maintained dedup rule between them, and a catalogue that could not
be reproduced from its sources - the committed CSV could not even be regenerated
by its own script.

**Rejected.** (a) Keeping both lists. (b) Standard mode = a brightness cut
(arbitrary and not what "popular" means). (c) Standard mode = everything (loses
the point of a quiet default view). (d) Merging curated text into OpenNGC rows
(rejected with it).

**Enforced by.** `Database.seed_catalog()` (one CSV fills both tables),
`tests/test_catalog_integrity.py`, `tests/test_catalog.py`
(`standard_ids`), `CATALOG.md`.

## 0.6.26 — The seeder compares id SETS, not counts

**Decision.** `seed_catalog()` rebuilds both tables whenever the stored ids
differ from what the CSV + popularity rule select.

**Why.** A count check cannot see a popularity change (still 200 rows, different
200) and cannot repair an old curated-era database. Both tables are pure seed
data, so a rebuild is safe and needs no migration.

**Rejected.** Insert-only seeding with a version stamp in `settings`; a real SQL
migration.

**Enforced by.** `tests/test_advanced_catalog.py`
(`test_a_pre_openngc_database_is_rebuilt_on_open`,
`test_reseeding_is_idempotent`).

## 0.6.26/0.6.27 — Popularity must be deterministic

**Decision.** `scripts/match_popularity.py` scores the **highest** match over
every designation of a row, normalizes padded numbers, and carries a small alias
table for cross-catalogue names.

**Why.** The old matcher iterated a `set` and broke on the first hit, so M27 came
out as 73,260 (`M 27`) or ~4 (`NGC 6853`) depending on `PYTHONHASHSEED` - the
standard-mode selection built on it was not reproducible. Padding and naming
differences silently cost famous objects their score (the LMC, the Horsehead).

**Rejected.** Designation priority lists (fragile); fixing it by freezing one
lucky output.

**Enforced by.** `scripts/match_popularity.py` docstrings,
`tests/test_catalog.py` (identity, not numbers), `CATALOG.md`.

## 0.6.27 — Legacy library ids are migrated; unknown ones re-resolve at display

**Decision.** `LEGACY_ID_MAP` (114 entries) + `Database.migrate_legacy_object_ids()`
rewrite stored `images`/`import_batches` ids on first open; `app/imglib.py`
additionally re-resolves an id the catalogue does not know from the capture's own
OBJECT header/coordinates.

**Why.** A capture stores the id it resolved to at ingest time, so the catalogue
switch would otherwise show an existing library as "Unknown object". The two
mechanisms cover different cases: known renames are rewritten once, unknowns (a
build that is gone, a hand-edited library) are resolved for display.

**Rejected.** Leaving it to the user's `/api/library/reindex`; a migration that
touches user data beyond the two id columns.

**Enforced by.** `tests/test_library_ids.py`.

## 0.6.27 — The scoring-parity fixture carries its own inputs

**Decision.** `tests/fixtures/scoring_goldens.json` stores the 697 input objects
its stats were captured for.

**Why.** The engine used to rebuild its inputs from `web/catalog.json`, so any
catalogue change looked like a scoring regression - removing the curated list
"failed" the proof without a single scoring change.

**Rejected.** Regenerating the goldens (destroys the pre-change baseline).

**Enforced by.** `tests/test_scoring_parity.py`, `tests/js/scoring_parity.mjs`.

## 0.6.24/0.6.25 — Cache tokens are verified against a baseline, not a tag

**Decision.** `scripts/check_js_tokens.py` embeds a `{module: {token, sha256}}`
baseline; bytes changed while the token still equals the baseline token = FAIL.
Refreshing is only via `--update`, which refuses while a missed bump exists.

**Why.** v0.6.24 shipped 0.6.23 tokens naming 0.6.24 bytes, so returning visitors
kept every old module; no guard could see it. Comparing against `HEAD` or a tag
misses the post-tag repair shape and depends on release timing.

**Rejected.** Auto-bumping tokens in a build step; trusting review.

**Enforced by.** the guard's `--self-test`, `tests/test_cache_tokens.py`,
`tests/test_pre_tag_check.py`.

## 0.6.25/0.6.27 — `tests/conftest.py` overrides `tmp_path`

**Decision.** The fixture uses `logs/.pytest-tmp` (gitignored, sandbox-owned)
instead of pytest's basetemp.

**Why.** Under the agent sandbox `--basetemp=.pytest-tmp` can acquire an
owner-SID-only DACL, so every `tmp_path` test ERRORS at cleanup with WinError 5 -
an environment artifact that made the suite look broken (142 errors, 0 assertion
failures). Keeping the workaround in `conftest.py` means a plain
`python -m pytest tests/` is clean, instead of needing a scratch plugin that a
fresh clone would not have.

**Rejected.** Committing the plugin and a `-p` flag; adding `-p no:cacheprovider`
to every documented command.

**Enforced by.** `tests/conftest.py` (documented) + `AGENTS.md`.

## 0.6.20 — One JS core; Python astronomy deleted (Phase C)

**Decision.** `core/` is the only astronomy/planning implementation; `/api/night`
and `/api/object/{oid}` are gone; Python is storage/platform/IO.

**Why.** Three implementations of the same formulas had drifted (an X-axis
precession bug survived in all three because the regression goldens came from the
same lineage).

**Rejected.** Keeping the Python oracle behind a flag.

**Enforced by.** `scripts/check_core.py` (`MOVED_PY`), `tests/test_core_drift.py`,
`tests/js/reference_anchors.mjs` as the correctness authority.

## 0.6.12 — Catalogue ids are globally unique

**Decision.** An id is unique across curated + advanced rows together; both the
generator and the schema enforce it.

**Why.** Two rows sharing one id made the web `byId` map and `db.get_object()`
disagree about which object the id named.

**Rejected.** Deduplicating at runtime with a map.

**Enforced by.** `_assert_global_unique`, `tests/test_catalog_integrity.py`.

## Docs policy

**Decision.** `AGENTS.md` is canonical for invariants, commands, release and
deploy. `CATALOG.md` covers the data pipeline. `DECISIONS.md` (this file) records
reasoning. `README.md` is the user-facing feature list, `DESIGN.md` the UI
language. `BUGS.md` is the only active defect backlog. Closed review artifacts
belong in Git history, not in the working tree.

**Rejected on purpose.** A `WORKFLOWS.md` and an `ARCHITECTURE.md`: both would
duplicate sections of `AGENTS.md` and drift out of sync, which is worse than not
having them. A `CONTRIBUTING.md` and a second `CHANGELOG.md`: no external
contributors, and `landing/changelog.json` + the `CURATED` table in
`scripts/build_changelog.py` already are the changelog.

## CO-07 - the two shells' "% of night" windows are NOT identical (documented, not unified)

**Measured during the 0.6.32 review (the report is retained in Git history):** the web
scores on a dusk-anchored 6-min grid; the desktop clips its noon-anchored
241-sample grid to `dusk..dawn`. The denominators differ by one sample (80 vs
79) and 20-66 of the 200 standard-mode rows differ by up to 4 pp (Berlin
2026-09-22, Berlin 2026-06-21, Tenerife). Since 0.6.30 the web's Min-visibility
gate keys on the WEB number, so list membership can differ between the shells.

**Decision: document the divergence rather than unify it in a fix batch.** The
two candidate fixes (snap the web's scoring window to whole noon-grid steps, or
pass the core an explicit sample range) both CHANGE user-visible planning numbers
on the public web app, and the second reaches into the time model that the scrub,
the charts and the tier/min-visibility browser checks are built on. That is a
release-level change with its own release note, not a correctness patch.

**Consequence:** `AGENTS.md`'s claim that "the web scores the same window as the
desktop on a 6-min grid" was FALSE and has been replaced with the measurement
above. Both shells still compute every number inside `dusk..dawn` (that
invariant holds); they merely sample that window on different phases.

## 0.6.33 batch - other decisions taken while fixing the 0.6.32 review

- **`CD-05` matcher fix without a re-bake.** The deterministic tie-break is fixed
  in `scripts/match_popularity.py` (highest score wins; equal score -> first in
  source order), but `app/popularity_data.py` is deliberately NOT regenerated:
  on the shipped snapshot the only affected key is `ngc6542` (3 -> 115), which is
  far outside the standard-mode cut. Re-baking would rewrite a tracked module and
  can re-select standard mode, so it stays a separate, deliberate act.
- **`SP-02` CSP keeps `script-src 'unsafe-inline'`.** `static/index.html`'s
  synchronous theme bootstrap (`XMLHttpRequest GET /api/state`) must run before
  first paint, so inline script stays permitted; remote script is still fully
  blocked (`'self'`), the JSONP path is gone, and the update check now runs
  server-side via `POST /api/update-check`. Verified against the real server:
  the header is served, the inline bootstrap runs, and an inline `onerror=`
  attribute still fires (so the two `onerror=` fallbacks in
  `static/js/{targets,tonight}.js` keep working - the feared "cosmetic
  regression" does not exist).
  **Audit correction, 2026-09-17:** permitting inline handlers is now a real
  security boundary, not only a cosmetic choice. Imported custom IDs and FITS
  `imagetyp` values can reach unescaped HTML attributes. BUG-002 remains open;
  the replacement direction is DOM/attribute-safe rendering plus a hashed
  bootstrap and removal of `'unsafe-inline'`.
- **`CD-02` ships the `search` column, at a size cost.** `web/catalog.json` grows
  from 3,834,258 to 5,088,280 bytes (+33%) because the OpenNGC `search` blob is
  now in the payload; that is what makes 25 rows (e.g. `tarantula`,
  `coathanger`, `silver coin`) findable on the web. It is text, so gzip absorbs
  much of it, but the trade is deliberate and worth re-measuring if the payload
  is ever streamed.

## Shell unification pass (0.6.33 work tree) - one rule, one loop, one codebase

The owner's directive: the web shell is further along and more current, both
shells should run on mostly the same code so a change lands in both at once, and
the BETTER implementation is used for both - but only where it works in a
browser too, and never at the cost of a regression.

**What was unified (real cross-shell code, not prose):**

- `core/visibility.js::inTonightPool(vmag, isAdv, advCap)` +
  `vmagKnownAndUnder` + `ADV_TONIGHT_MAG_CAP` + `TONIGHT_MAG_UNCAPPED` - which
  objects may reach the Tonight list is ONE rule now, called by
  `web/app.js::renderTonight` and `static/js/tonight.js::renderTonight`. This
  FIXES a defect rather than only deduplicating: the web's inline
  `o.vmag != null` dropped 45 of the 200 standard-mode objects (IC 1396, the
  Running Man, NGC 281, the Flame, the Tulip, Simeis 147, the Double Cluster,
  the Pipe Nebula, ...) while the comment above it claimed desktop parity. The
  unknown-magnitude answer is deliberately YES - the same reasoning as
  `magVisible`'s "a no-magnitude object shows at the slider maximum".
- `core/scoring.js::scoreNightTracks(..., ctx.range = [lo, hi])` - the scoring
  SPAN is a parameter, so the desktop (241-sample noon->noon payload for its
  charts, clipped to dusk..dawn) and the web (grid == the dark window, no range)
  run the SAME loop. `derive.js::recomputeNightDerived` no longer owns a second
  aggregation; that second loop is what let CO-07 drift. The desktop's
  `best`/`best_above`/`score`/`score_moon` contracts and the
  `tests/js/scoring_parity.mjs` characterization goldens are preserved
  bit-for-bit (`bestStart` is captured incrementally, never recomputed as
  `jd - run*step`).

**What deliberately did NOT move into `core/`:**

- **CSS.** `web/style.css` + `web/themes.css` and `static/style.css` +
  `static/themes.css` share the Workbench role language, not the files. The two
  shells have different DOMs (a 6-pane tab app vs a JS-rendered single view),
  so a shared stylesheet would be a rewrite with no correctness gain. There is
  one structural style; legacy theme values normalize at load boundaries.
- **Storage, backup and data delivery.** IndexedDB + `localStorage` versus
  SQLite + FastAPI is a platform split, not duplication. The web's
  stage-validate-commit restore remains the stronger model. **Audit correction,
  2026-09-17:** the desktop's payload-first path is not atomic: a later database
  validation failure can leave an existing payload overwritten. BUG-001 and
  BUG-004 require side-effect-free preflight plus payload rollback; the safety
  copy alone is recovery evidence, not atomicity.
- **The advanced-mode magnitude cap.** The web caps the Tonight pool at mag 9
  because scoring ~5,900 objects per repaint is not viable in a browser. That is
  a PERFORMANCE parameter, so it is passed in per shell (`TONIGHT_MAG_UNCAPPED`
  on the desktop, which carries the whole catalog in its payload anyway) instead
  of being duplicated as a second inline expression. Same rule, different
  budget.
- **The grid anchor (CO-07).** Still open on purpose: aligning the web's grid to
  the desktop's noon->noon payload would move user-visible web numbers
  (`% of night`, the scores, the clear window). The divergence is now confined to
  the anchor, because the arithmetic is shared.

**Consequence: no two shells, one codebase for the domain.** The rule for the
next change is the same as the one this pass followed: if a decision can be
expressed as a pure function of data, it belongs in `core/` and both shells call
it; if it needs the DOM, storage or a network, it stays in the shell and gets a
parity pin instead.

### The scoring grid anchor stays as it is (CO-07, closed as DECIDED)

The two shells score the same dark window with slightly different sample sets:
the desktop's payload grid is a 24 h noon->noon day for its charts and is clipped
to dusk..dawn (87 samples at 47.5 N on 2026-03-14), while the web's grid IS
dusk..dawn and lands on 88. That one sample moves `% of night` by a point and a
score by up to 0.07 on 12 of 14 probed rows (and 20-66 of 200 standard rows by up
to 4 pp in the original 0.6.32 survey).

**Rejected: aligning either side.** Re-anchoring the web's grid to noon->noon
changes numbers the web has shipped for releases (and its `View full day` span),
while re-anchoring the desktop's payload breaks the chart margins and the
`scrubgrid` contract. Both are user-visible behaviour changes traded for a 1 pp
cosmetic difference.

**Rejected: making the desktop pass a sample list instead of an index range.**
It would let both engines share identical sample times, but it changes the
already-characterized `tests/js/scoring_parity.mjs` goldens for no user-visible
gain.

**What WAS done (shell-unification pass):** the arithmetic is shared
(`core/scoring.js::scoreNightTracks` with `ctx.range`), so the divergence is
confined to the ANCHOR - a grid choice each shell makes for its own rendering -
instead of living in two independent aggregation loops. Pinned as "one loop, one
span parameter" and documented as a deliberate anchor difference.

### The repository ships a web CSP policy file

`web/.htaccess` sets `default-src 'self'`, `script-src 'self'` plus the SHA-256
of the ONE inline theme bootstrap (deliberately NOT `'unsafe-inline'`:
`tests/browser/harness.py` recomputes the hash, and the web shell has no inline
handler at all), `style-src 'self'`, `img-src 'self' data:
https://upload.wikimedia.org`, `connect-src 'self' https://en.wikipedia.org
https://api.open-meteo.com`,
plus `base-uri`/`form-action`/`frame-ancestors`/`object-src` and
`Referrer-Policy`/`X-Content-Type-Options`. The desktop has carried an equivalent
policy in `app/server.py` since SP-02; the STATIC shell is the public origin and
had none, which is the wrong way round.

The file is deployed by hand (the FTPS uploader skips dotfiles).
`tests/test_prod_csp.py` pins the policy content and inline hash offline. **Audit
correction, 2026-09-17:** the current production smoke does not prove document
protection; it accepts a policy found on a JSON response when the HTML lacks the
header. BUG-003 requires the live HTML response itself to carry the policy.

### Cache-token register history (moved out of AGENTS.md)

AGENTS.md carries only the CURRENT register; the cascade record lives here so the
instructions stay inside their byte budget. Every core re-bake moves the stub
token and changes the bytes of every module importing it, so those tokens move in
the same commit:

* 0.6.32-review fixes touched `core/horizon.js`, `core/astro.js`,
  `core/catalog.js`, `core/scoring.js`, `core/time.js` -> stub 24 -> 25.
* CR3 (CO-01, review 0.6.24) touched `core/horizon.js` + `core/index.js` ->
  stub 17 -> 19.
* the WE-03 comment corrections -> stub 19 -> 20 (that re-bake exercised the N-1
  retention: the manifest now records `previous` and BOTH trees stay in place).
* shell-unification pass: `core/visibility.js` + `core/scoring.js` +
  `core/index.js` -> stub 26 -> 33 across the intermediate bakes (25..32), each
  one cascading the web tokens; `core/panohistory.js` was added in the same pass
  (10 core modules now).
* the tagged v0.6.24 register was 60/10/41/17/7/20 with stub 17; 0.6.23 (live)
  was 59/9/40/16/6/19.

### The deployed CSP is limited by the host's nginx front end (measured)

`web/.htaccess` sets the policy, but on the live host **nginx serves
`.html`/`.js`/`.css` itself** and never reaches Apache for those paths, so the
directives arrive only where nginx proxies (measured 2026-09-15:
`/astro/version.json` carries `Content-Security-Policy` + `Referrer-Policy` +
`X-Content-Type-Options`; `/astro/`, `/astro/app.js` and `/astro/style.css` carry
none). `mod_headers` is working - the path split is the host's architecture, not
a broken config, and it is the same reason `web/ftps_deploy.py` uploads the file
by hand (the uploader skips dotfiles).

To cover the HTML, the policy has to be added at the nginx layer
(Plesk: domain -> Apache & nginx Settings -> Additional nginx directives), e.g.
`add_header Content-Security-Policy "<the same policy>" always;`. That is a
host-side action outside this repo.

Current release-gate behavior is therefore insufficient: `web-prod` probes other
paths and can pass on the JSON-only header. The replacement decision is to treat
deployment as observable release state: the main document response must carry a
valid policy or the production smoke fails. The policy's content and hash remain
pinned offline by `tests/test_prod_csp.py`.


## The carried-over four: what each turned out to be (0.6.35 working tree)

The 0.6.20/0.6.24 reviews deferred four S4 items. Reassessing them in 0.6.35
confirmed two and refuted the details of two, which is the reason this entry
exists: a deferred item is a hypothesis, not a finding, and two of these were
described in a way that would have sent a maintainer to the wrong place.

**CR2 - "malformed horizon guards: defense-in-depth, all shipped producers
normalize before preview."** The premise was wrong. `visibleSkyBands` had been
fixed (CO-04), but a probe of the whole family found `skyBandPolygons` and
`profileSegments` still threw on 7 malformed shapes, and the desktop editor
passes `profileTop` - so a sky-mode record with no top array crashed a render
pass, exactly the outcome the guards are for. Bigger: `ceilFor` validated the
profile's LENGTH but not its ELEMENTS, so a 720-long array holding NaN returned
`ceil: NaN` - and that function's own comment names the hazard (`alt > NaN` is
false, so every object at that azimuth reads as blocked and vanishes from the
planning). "Producers normalize" is not a boundary when the values are STORED and
EDITABLE. Decision: every entry point returns its empty sentinel; the contract is
pinned by `tests/js/horizon_degrade.mjs`.

**DS4 - "worker/sync diverge on max_alt (rounded vs raw argmax); one-sample/0.05
deg difference near ties; cosmetic."** Two separate things were conflated. The
night and detail payloads use DIFFERENT grids by design (6 min noon-anchored vs
4 min dusk-anchored), so their peaks legitimately differ and that is not a
defect. The real one was internal to the detail payload: it took the argmax over
the 2-dp-ROUNDED array while the night path used the raw argmax, so `max_time`
and `max_alt` could describe different samples. Fixed to the raw argmax.
**Measured limit of the fix:** because `round2(max(raw)) >= round2(raw[j])` for
every j, the rounded argmax can only land at or BEFORE the raw one, so the
reported peak VALUE is identical either way - only WHICH of several equal-valued
samples `max_time` names can differ. The shipped ic434 row holds a single sample
at its detail maximum over four dates, so no live fixture separates the two. The
pin therefore asserts the user-visible contract (the two fields describe one
sample; the peak is not below the grid's own maximum) and the engine records the
limit instead of inventing a fixture that cannot exist in the data.

**DS5 - "worker revive: sync fallback always planned; dev-hook-only impact."**
Correct that it was dev-hook-only, wrong that it was therefore harmless: the hook
is the app's declared recovery path and it was broken. `_worker.onerror` set
`_workerBroken` but left the DEAD Worker in `_worker`; `__NS_REVIVE_WORKER`
clears the flag, so the next build handed the corpse to `postMessage`. The kill
hook was already correct - which is precisely why the existing browser check
passed while this path was dead. Decision: extract `onWorkerError`, terminate and
null like the timeout path and the kill hook, and drive the REAL path from a
browser check with a genuine `ErrorEvent` (`__NS_FAIL_WORKER`, inert unless
`__NS_DEBUG_WORKER`).

**WB7 - "bundle version still ignored (forward-compat choice)."** Correct, and
the choice was the wrong one: a bundle from a LATER build was accepted and its
fields read under this build's rules, so a misreading was reported to the user as
a successful restore. Decision: `stageBackup` refuses a version above
`BUNDLE_VERSION` with a message that says to update; v1 (no stamp, same field
layout) and v2 still restore; an unusable stamp is refused rather than crashing.
A future incompatible format gets an explicit migration hook instead of silent
best-effort parsing.

**Why all four are closed rather than deferred again.** Each now has a
mutation-proven pin: reverting the guard makes the engine fail (verified for the
two CR2 guards, the DS5 disposal, the DS4 argmax and the WB7 version check).
That is the standard `BUGS.md` sets for closing a finding, and it is what was
missing when these were first carried over.

## Catalog provenance stays in a deterministic sidecar (CP-01, 2026-09-20)

The catalog's 14-column planning row remains the stable identity/data contract.
Source audit data belongs in `catalog_provenance.json`, generated from the
exact OpenNGC inputs plus `app/data/supplement.csv`. Every row records its
source file and byte-level `sha256:<digest>` version, aliases, structured
designation history, and a mapping note when NightSeam's display or supplement
rule changes the source representation. The top-level catalog hash is the
generated `openned.csv` hash, so a provenance export can be reproduced and
checked without embedding the large raw source files in the application.

**Rejected: putting aliases and provenance into every catalog row.** It would
expand both the desktop seed and the roughly 4 MB web payload, make the
planner's hot path carry audit-only text, and couple future provenance changes
to the catalog schema. The sidecar is loaded once, shown in the detail view,
and downloadable as a complete audit artifact instead.

**Rejected: using a mutable upstream release label as the version.** OpenNGC
downloads can change without a stable label being available in the local input.
The exact source-file SHA-256 is unambiguous; the sidecar also retains the
human source URL and license for attribution.

Both copies are byte-identical and checked against the catalog id set, the
desktop CSV hash, a manifest, and `CATALOG_PROVENANCE_TOKEN`. A future rebuild
must bump that token and refresh the generated baseline in the same change.
