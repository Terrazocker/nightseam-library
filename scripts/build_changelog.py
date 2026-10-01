"""Generate landing/changelog.json from git release tags (newest first).

Run from the repo root:  python scripts/build_changelog.py
                         python scripts/build_changelog.py --check
The download page (landing/download.html) fetches changelog.json and
renders "what's new". Summaries are CURATED user-facing text — big changes
only, plain words — not raw commit messages.

Tags without a curated summary are skipped (versions only get an entry
when there is something worth telling users about) — but they are no longer
silently skipped: the run reports them in two classes and `--check` FAILS
(exit 1) when a tag NEWER than the newest curated entry has no summary. That
is the class that makes the download page go stale; the pre-0.4.2 backlog is
historical noise and is only counted.

`--check` writes nothing, so it is safe as a pre-flight gate.
scripts/check_release.py reads the SAME CURATED table (by importing this
file) and fails the release when the tag being released has no summary.
Curate the entry BEFORE tagging: editing this file after the tag makes every
published version string '-dirty'.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "landing" / "changelog.json"

CURATED = {
    # Prepared for the next release; tags alone determine publication.
    "0.6.35": "The Windows app now opens a local FITS Image Catalog with Library, Import Exposures and an archive Sky Map. Its native Windows frame supports moving, resizing, maximizing and Snap, and its layout adapts without scaling the text. Imports show files processed, elapsed time and separate counts for failures, already imported files and restored originals. Identical file contents are skipped even when renamed, while different captures sharing a filename remain separate. Existing libraries are checked without blocking the server on file hashing, and library export works from the native window. The full planner remains available from source and with --planner; its archive folder picker and Sky Map selection panel are repaired. Both planners use the Workbench design language. The experimental modeled sky-brightness feature has been removed, including its data and controls; this release does not present an estimated value as a measured sky reading. Sites, equipment, panoramas and existing library data are preserved.",
    # 0.6.34 is the security/reliability batch for the six findings in BUGS.md:
    # a refused restore can no longer overwrite your files, the desktop can no
    # longer execute an imported value, the web app finally enforces a policy,
    # malformed backups answer 400 instead of 500, the browser check waits for
    # the Tonight view instead of guessing, and the active panorama can no
    # longer point at something that is not there. Written for users, not for
    # the changelog reader.
    "0.6.34": "A safety release for importing backups, plus a fix to the web app's \"tonight\" list. Restoring a setup file is now all-or-nothing: the whole backup is checked first, and your existing files are only replaced once it is known to be usable. Before this, a backup that mixed one good panorama with one broken record could overwrite that panorama's image and then report the import as failed - so \"nothing happened\" and \"your file was replaced\" could be the same message. The import boundary is also stricter about what it accepts: a malformed backup is refused with a clear message rather than an error page, and values that arrive from an imported file can no longer take effect as code - in the desktop app an imported target name or frame type was able to run as a click handler, which is no longer possible. Both apps now escape everything they display from imported data, and the desktop app's script policy no longer permits inline code at all. The web app now enforces a real Content-Security-Policy on the page itself, which it had been missing: its content is restricted to this site's own scripts and styles, plus the two hosts it legitimately loads images from. Separately, the web app's \"tonight\" list no longer lags behind the time scrubber: moving through the night refreshed the ranking asynchronously, so the suggestions could briefly describe the moment you were at before - the list now always matches the selected time, including at startup, and the automatic check that watches for this waits for the list to actually catch up. Deleting the panorama that planning was using also leaves the app with no active panorama instead of an active one that does not exist; activating one that is not there is refused. Everything else is unchanged.",
    # 0.6.33 is the review batch plus the shell-unification pass: the web
    # "tonight" list gained the 45 bright nebulae it was silently filtering out,
    # the desktop panorama editor gained Redo, and the web shell now ships a
    # Content-Security-Policy. Written for users, not for the changelog reader.
    "0.6.33": "Bright nebulae are back in the web target list, and a long list of smaller things got fixed. The web app's \"tonight\" suggestions were quietly skipping every object whose brightness the catalogue does not record - which is exactly the case for the big emission nebulae: IC 1396, the Running Man, NGC 281, the Flame, the Tulip, Simeis 147, the Double Cluster and the Pipe Nebula could never show up there, while the desktop app listed them all along. Both apps now use one and the same rule (an object with no recorded magnitude counts as bright, never as invisible), so the two lists agree. Undo in the desktop panorama editor also gained a matching Redo, so a stroke you took back is no longer gone for good. Everything else in this release is repair work: visibility percentages and clear-window times are measured over the same window in both apps; a blocked-horizon object is no longer reported as observable at selected times; civil twilight is no longer mistaken for full darkness; timezone and daylight-saving offsets are right on the days the clock changes; the two seeded catalogues can no longer disagree about an object; long site and target names no longer run out of their panel; the view no longer shifts while a percentage fills in; and photo loading, the update check and the web deployment are all a little more careful about what they trust. the popularity source, the source of the popularity ranking, is no longer contacted at all: the ranking ships with the app and stays local. The web app now also sends the browser security headers it should have had from the start. Everything else is unchanged.",
    # 0.6.32 removes the always-empty Distance field and makes the Wikipedia
    # lookup use the object's DESIGNATION (a bare "M101" is a disambiguation
    # page), skipping disambiguation pages and remembering only a definitive
    # "no photo" instead of 30 days of it.
    "0.6.32": "Object photos now resolve for Messier objects, and the detail view no longer shows an empty Distance field. \"M101\" on Wikipedia is a disambiguation page (motorways, a rifle), not the galaxy - the app now looks the object up by its full designation (\"Messier 101\", or the NGC number), follows the redirect to the real article and skips disambiguation pages, so the photo and the Wikipedia link both land on the right page. A lookup that is only temporarily blocked is also no longer remembered as \"no photo there\". Distance is gone because the catalogue has none to give: OpenNGC's parallax column is a foreground star's value for galaxies (it puts Andromeda at 167 parsecs), and a redshift distance would put M101 at half its real distance. Everything else is unchanged.",
    # 0.6.31 is the layout/ranking polish on top of 0.6.30: progress never moves
    # the page, the catalog table keeps its column widths and stays inside its
    # card on narrow screens, and both apps agree on the tier letter.
    "0.6.31": "Target-list polish, in both apps. The catalog keeps its column widths while the visibility percentages fill in, and a running progress note (\"computing visibility...\") no longer nudges the page around: the count and the progress sit on their own fixed line, and the busy indicator has a reserved spot instead of pushing the toolbar. On a narrow screen the catalog table scrolls inside its card rather than stretching the page sideways. The popularity tier letter is now guaranteed to be the same object-for-object in the desktop and the web app, in standard and advanced mode alike. Everything else is unchanged.",
    # 0.6.30 brings the two apps to ONE target-list method: a popularity TIER
    # (A-F rank bands) as the default sort, the frame coverage as a percentage
    # (green fits / amber mosaic), and a user-controlled Min visibility gate.
    "0.6.30": "Target-list update, in both apps. The catalog now leads with a popularity tier (A to F) and sorts by it by default: the ordering is shown as a letter, while the underlying community score is never displayed. The Frame column shows the coverage as a percentage - green while the object fits in one frame, amber once it needs a mosaic - and a new Min visibility slider in the targets toolbar lets you decide when an object is dropped from the list: it hides targets whose share of tonight's dark window is below the percentage you pick, where 0% shows everything and the default 1% only drops objects that never rise. Your setting is remembered. Everything else is unchanged.",
    # 0.6.29 adds supplement batch 2 (9 rows): the popular targets outside
    # OpenNGC's SCOPE (Sharpless/Barnard/LDN/LBN). Every one already ranks above
    # the Standard-mode cut, so the 200-target default view changes. No
    # popularity refresh and no invented magnitudes - see DECISIONS.md.
    "0.6.29": "Catalog update: nine more targets that OpenNGC's scope cannot carry - the Tulip Nebula, Simeis 147 (the Spaghetti Nebula), the Flying Bat Nebula with the Squid inside it, the Dolphin Head Nebula, the Lion Nebula, Barnard 150 (the Seahorse), LBN 437, LDN 1622 and the Pipe Nebula - so the catalog holds 12,176 objects. Every one of them is popular enough that the app's own ranking puts it straight into the 200-target default view, which means nine less-observed objects moved to advanced mode. Their positions come from SIMBAD and their sizes from the published catalogues, and no magnitude or description text is invented for them, so - like the Horsehead - they appear once the magnitude slider is at its maximum. Everything else is unchanged.",
    # 0.6.28 adds the 7-object supplement (app/data/supplement.csv): the
    # objects OpenNGC cannot carry. No refresh, no re-ranking beyond the new
    # rows' own scores.
    "0.6.28": "Catalog follow-up: the seven objects OpenNGC cannot carry are back - the Medusa and Headphone nebulae, Barnard's Loop, the Alpha Persei Cluster, the Sadr region (IC 1318) and the Messier objects M40 and M73 - so the catalog holds 12,167 objects. Standard mode still shows the 200 most popular, and the four of the seven the community ranks highly join that list. Their positions, sizes and magnitudes are the app's own published values, and they carry no description text, in line with the rest of the catalog. Everything else is unchanged.",
    # 0.6.27 is the catalog follow-up to 0.6.26: refreshed the popularity source
    # popularity (re-ranks Standard mode), OpenNGC label mappings (Cl+N ->
    # nebula, Se1/Se2 -> Ser, duplicate common names disambiguated) and the
    # automatic migration of library ids the old catalogue handed out.
    "0.6.27": "Catalog follow-up to the complete-OpenNGC switch. The target list now carries up-to-date community popularity, so Standard mode's 200 most popular objects are re-ranked (M42 still leads, M31 follows), and object labels follow the source more closely: the great star-forming nebulae OpenNGC classifies as a cluster with nebula (the Orion Nebula, IC 1396, the Heart and Soul nebulae, the Cocoon Nebula) are shown as nebulae instead of clusters, Serpens objects no longer display the catalogue's Se1/Se2 codes, and where OpenNGC gives one common name to two rows the designation is added - 'Eagle Nebula (NGC 6611)' beside 'Eagle Nebula (IC 4703)'. Your image library is migrated automatically: entries that pointed at the old catalogue's ids (M42, the Magellanic Clouds, Collinder 399 and about 110 more) are re-pointed at the objects OpenNGC names, and a capture whose object the new catalogue does not list at all keeps displaying its own name and coordinates.",
    # 0.6.26 replaces the hand-written 197-object list with the complete
    # OpenNGC catalogue (NGC/IC + its addendum): one catalog, one set of
    # positions/magnitudes/types, standard mode = the 200 most popular
    # objects. No planning-number change beyond the catalog itself.
    "0.6.26": "Catalog rebuild: the target list is now the complete OpenNGC catalogue. The NGC/IC catalogue plus OpenNGC's addendum of notable objects that have no NGC or IC number (the Horsehead, the Hyades, the Coalsack, the Magellanic Clouds, the Sculptor, Fornax and Sextans dwarf galaxies and Seyfert's Sextet among them) gives 12,160 objects, and the older hand-written 197-object list is gone, so every object now has one consistent set of positions, magnitudes, sizes and types. Objects that OpenNGC itself marks as duplicates or non-existent are no longer listed at all (for example NGC 651, a duplicate entry for NGC 650, and the double star M40). Standard mode shows the 200 most popular of these objects, and advanced mode shows all 12,160. Names follow OpenNGC, so an object the old list gave a nickname may now appear under its designation (M101 in place of a nickname), and the old one-line descriptions are gone. Your sites, rigs, panoramas and image library are untouched; a library entry that pointed at a former catalog id is matched again from the frame's own object name and coordinates.",
    # 0.6.25 ships the complete 0.6.24 review batch: 6 high, 10 medium and 26
    # low findings, each with a regression pin. Mostly robustness (import
    # boundary, backup/restore, panorama-editor save + undo, weather, charts)
    # plus a few visible interface fixes. No planning-number changes.
    "0.6.25": "Reliability and interface-fix release: 42 issues found by an independent review of the previous version are fixed, each with an automated regression test. Editing a panorama is safe around switches and shutdowns - an edit made moments before you open another panorama or close the app is saved instead of silently lost, the editor always shows the selected panorama's own horizon mask, and undoing back to an unpainted horizon restores the stored mask rather than a blank one. Replaying the introduction no longer overwrites the active site's coordinates. Weather for one site can no longer appear under another site's name. Dismissing the update notice no longer opens the download page, the library's edit dialog closes with Escape, the magnifier's crosshair marks the exact pixel it samples, and both sky charts redraw smoothly while you hover. In sky mode the Sky View no longer draws a spurious centre ring and spokes, and each chart's object-type legend stays in sync with the other. Importing a damaged backup is refused with a clear message instead of quietly losing a frame, a failed safety copy of your previous data is now reported, the rolling database backup is a consistent snapshot, and a malformed stored horizon can no longer stop the app from starting. Images are still fetched only from the two astronomy hosts the app allows. Every displayed planning number is unchanged.",
    # 0.6.24 ships the shared kept-band panorama crop (web + desktop) and the
    # desktop stored-FOV fix. No planning-number changes.
    "0.6.24": "Panorama crop. In the web planner you can now trim a panorama to the azimuth band you want by dragging the band to keep: the horizon keeps its paint (the photo is not modified) and the crop is a single undo step. The Windows app's crop tool now stores the correct field of view - it previously stored the removed portion, so an off-centre crop was stretched; a panorama cropped in an older Windows version can be fixed by re-cropping, or by correcting the field of view directly in the editor. Every displayed planning number is unchanged.",
    # 0.6.23 ships the Batch 3 review hardening (API/import numeric + custom-id
    # validation, web storage/IDB/cross-tab/pano-section robustness) and the
    # first real-browser release gate. No planning-number or interface changes.
    "0.6.23": "Hardening release with a new internal safety net - no planning or interface changes. Restoring a backup or editing a site, rig or panorama now refuses invalid numbers (such as infinity) and custom-object ids that are not properly namespaced, so a damaged or hand-edited file can no longer break the app on startup. The web planner is more robust around browser storage: a save that fails because storage is full says so and keeps your edit, another tab's changes reload instead of silently overwriting each other, an in-browser database upgrade blocked by another tab no longer hangs, and an imported file without a horizon section can no longer wipe your horizons. A new automated real-browser test gate (system Chrome for the web planner, and the packaged Windows app) now runs as part of every release, so interface regressions are caught before shipping. Your data, every displayed number and the way you plan are unchanged.",
    # 0.6.22 ships the Batch 2 review fixes: site-noon polar fallback, sync
    # cache identity (tz + profile content), web save durability, plus
    # test-quality/guard work. No planning-number changes.
    "0.6.22": "Planning-window, cache and save fixes. A polar-day or polar-twilight site now always shows its dark window anchored to the selected night and the site's timezone instead of a window based on the current clock. In the web planner, changing the timezone or repainting a horizon can no longer serve a stale plan from the in-browser cache, and a Save that fails because browser storage is full now says so - keeping the previous state - instead of reporting success. The internal guard and reference-anchor test suites were also tightened. No planning numbers or interface changes.",
    # 0.6.21 fixes the code-review Batch 1 findings: worker/prefs race,
    # catalog identity + cache invalidation, import validation, guard/release
    # holes and the stale web cache tokens. No planning-number changes.
    "0.6.21": "Fixes a rare planning race and hardens restores. Changing a preference (minimum altitude, Bortle class, moon-aware ranking or filters) while a new night was being calculated could cancel that calculation and leave the busy indicator running; the calculation now finishes, the new preference is applied to it, and the indicator clears. Custom targets you add or remove are now reflected in planning immediately, and tracks can never be attached to the wrong object after a catalog change. Restoring a backup now validates its horizon profiles and settings up front: a damaged file is rejected with a clear message instead of silently disabling your horizon or preventing the app from starting. Internal checks and the reference-value test suite were also tightened; the interface and every planning number are unchanged.",
    # 0.6.20 is an internal architecture cleanup: the duplicated Python
    # astronomy/planning layer and the desktop's server fallback are gone; the
    # shared engine is the only planner. No behavior change, exe ~19 KB smaller.
    "0.6.20": "Internal cleanup release - no behavior change. The desktop app and the web planner now use one shared planning and astronomy engine, and the duplicated local-server calculation path was removed. This makes the Windows app about 19 KB smaller and the code easier to keep correct; the interface, every displayed number and the way you plan are unchanged. Your sites, rigs, targets, panoramas and image library carry over as before, and backups from earlier 0.6.x versions restore normally.",
    # 0.6.19 moves the desktop planning compute into a worker: no behavior
    # change, but date/site changes stay responsive and pref changes rescore
    # off-thread; fallbacks (sync client -> server) unchanged.
    "0.6.19": "Faster planning. The desktop app now calculates its night planning off the main thread: changing the date or site keeps the window responsive while a busy indicator works, rapid date/site or preference changes cancel the superseded calculation instead of blocking, and preference changes (minimum altitude, Bortle class, moon-aware ranking, filters) update almost instantly. Every displayed number is unchanged. If the background calculation cannot start or stops, the app falls back automatically to the previous in-app calculation and then to the local server, so planning never stops working. Two polish items are planned for a follow-up: chunking the list rendering on very slow machines and a memory hardening for very long sessions with the full 12,000-object catalog.",
    # 0.6.18 is the authority flip: the desktop plans entirely with the shared
    # core (client-side tracks + scoring), the server payload stays as the
    # runtime fallback. One flagged feature, no interface change.
    "0.6.18": "Planning is now computed on your machine. The desktop app plans with the same shared engine as the web planner: object positions, twilight windows, the Sun and Moon, visibility percentages, clear windows and scores are calculated locally when you change the date or site, and preference changes (minimum altitude, Bortle class, moon-aware ranking, filters) update instantly without recalculating the night. Every displayed number is the same as before - behind the scenes the sky position is now precessed once per night instead of at every sample, a change far below anything shown. On slower or heavily throttled machines, changing the date or site with the full 12,000-object catalog enabled can take noticeably longer than in 0.6.17 (the app shows a busy indicator while it works); preference changes stay instant, and an asynchronous compute update is planned. Your local server remains as an automatic fallback, and the interface is unchanged.",
    # 0.6.17 completes the shared-core extraction (Phase 2) and carries the
    # three user-visible corrections it surfaced (catalog palette, polar
    # sunrise pairing, degenerate scrub ranges).
    "0.6.17": "Shared-core release. The desktop app and the web planner now compute every planning number with one shared engine, so the two can no longer drift apart. Three visible corrections come with it: object-type names and colours are identical in both apps (the desktop adopts the web palette); a grazing polar-night day can no longer report a sunrise without a sunset (Tromso in January showed a \"sunrise\" 29 minutes before its own sunset); and the night slider and the altitude chart now use the dark window whenever the sunset-to-sunrise pair is degenerate, instead of building an inverted range. Ordinary nights, DST transitions and southern-hemisphere nights are unchanged. Python still computes the desktop's planning payload.",
    # 0.6.14 is the desktop sky-view patch: the sky-mode chart artifacts are
    # gone, the horizon legend/hatch now match the web, and the assumed-open
    # tail of the 0.6.13 sky-band work is finished.
    "0.6.14": "Desktop patch release. The sky view is corrected: in \"paint visible sky\" mode the chart no longer draws a spurious centre ring or radial connector, never strokes the band outline, and hatches the area above the painted band instead of shading it as blocked. The same horizon legend now appears in the desktop and the web (\"blocked by horizon\" and \"above band: open (assumed)\"), and the web editor's profile preview hatches that assumed-open area too. This build also carries the immediately preceding sky-band fixes: painting the visible sky assumes the sky above the band is open unless you tick \"block above band\", and View full day is a real 24-hour toggle in both apps.",
    # 0.6.13 is the panorama-editor + reliability release: the web editor was
    # rebuilt (toolbar, history, HiDPI, zoom), sky mode assumes open above, the
    # scrub spans the whole night with a real full-day toggle in both apps, and
    # the catalog/backups/desktop robustness fixes landed.
    "0.6.13": "Editing and reliability release. The web panorama editor was rebuilt for precise work: a grouped two-row toolbar with the view controls on the image, gesture-level undo/redo, a confirmed and undoable Clear, a Revert, a clear saved/unsaved indicator, sharp HiDPI rendering, zoom and pan, denser alignment guides, and a live azimuth/altitude readout. Painting the visible sky now assumes the sky ABOVE your band is open (hatched with a legend entry) unless you tick \"block above band\", and holding the right mouse button erases continuously. The night scrub now spans sunset to sunrise by default, with a \"View full day\" option that restores a full 24 hours in BOTH apps - the desktop now has a real full-day grid too - and the minimum-altitude slider is longer in the top bar. The object list no longer has two entries sharing one id, web backups are truthful and roll back a failed restore, the desktop no longer dead-ends if you skip choosing a site, and only one panorama is active at a time.",
    # 0.6.11 is a correctness + chart-tools release: Sun/Moon visibility, the
    # web magnitude slider, desktop zoom parity and the web panorama editor.
    "0.6.11": "Bug-fix and chart-tools release. On the all-sky chart the Sun and Moon could appear in the sky while they were really below the horizon - both apps now draw them only while they are actually up at the selected time. The web chart gains the desktop's magnitude slider (hide objects fainter than a chosen limit), the desktop chart's wheel-zoom now opens the same full rectangular window as the web, and the web panorama editor gains alignment guides (azimuth, altitude and horizon lines), joins a 360-degree panorama seamlessly at the edges, and fixes sky-mask \"open above\" handling and the compass labels on partial panoramas. Web cache tokens were bumped so the updated files reach returning visitors.",
    # 0.6.10 is a desktop+web consistency release: the eight confirmed
    # desktop/web parity differences from the 0.6.9 audit are fixed.
    "0.6.10": "Consistency release: the desktop app and the web planner now agree everywhere. The night window is identical in both apps at every latitude - including polar twilight, where the web could pick the wrong night - the Dark time card shows the twilight level actually used for scoring, the minimum altitude follows the Bortle class immediately, and \"Best targets tonight\" only lists targets that really clear the sky during the night. Frame coverage and filter advice now read the same in both apps, and the arrow-key time step is 6/60 minutes in both.",
    # 0.6.9 is the first desktop release since the rename: it carries every
    # 0.6.1-0.6.8 web change AND the accuracy/security/consistency work.
    "0.6.9": "Accuracy, safety and consistency release. Object positions were off by up to half a degree (a precession bug, fixed in all sky-calculation engines), importing a setup file can no longer read or write files outside your own archive, and the clear-window times no longer include twilight. The web planner now resolves the same night, moon and filter scores as the desktop in any time zone. This build also brings the desktop app up to date with every web change since 0.6.0.",
    # 0.6.1-0.6.8 were WEB-only releases (no new desktop build was published),
    # so each entry says so - a desktop user reading the download page should
    # not expect these in their 0.6.0 exe.
    "0.6.8": "Web: internal stylesheet version bump so the 0.6.7 layout reaches visitors who still had the old file cached.",
    "0.6.7": "The imaging-rig (framing) selector moved out of the menu and into the top bar, right next to the site selector - in both the desktop app and the web version. Web release; the desktop change arrives with 0.6.9.",
    "0.6.6": "Web: the Sites, Rigs and Targets forms no longer render before you open them.",
    "0.6.5": "Web: the last visible traces of the popularity score are gone (the \"Popular\" filter chip). The desktop's popularity sort option was removed in the same step and ships with 0.6.9.",
    "0.6.4": "Telescope presets are now a picker instead of automatic entries: a fresh setup starts empty and \"Add rig\" offers the Seestar / DWARF models or a custom rig. Web release; the desktop version arrives with 0.6.9.",
    "0.6.3": "Web: the community-popularity number is no longer displayed anywhere - it still breaks ties in the ranking, silently.",
    "0.6.2": "Web: fixed an update-order problem that could briefly serve the app's page before its scripts, leaving visitors with a broken view until they reloaded.",
    "0.6.1": "Web: sample telescope presets (ZWO Seestar, DwarfLab) with their field of view, plus layout fixes. They were added automatically at the time; 0.6.4 turned them into a picker.",
    "0.6.0": "AstroPilot is now NightSeam: new name, and the website has moved to nightseam.de. Planning and the image library work exactly as before. If you replace an older installation in place, rename data/astropilot.db to data/nightseam.db to keep your sites and captures.",
    "0.5.12": "Reliability release: a failed backup import can no longer wipe your data, planning now resolves the correct night at every location (timezone fix), safer panorama handling, and many small fixes in both apps.",
    "0.5.11": "Targets are ranked with community popularity as a silent tiebreaker, and moon-aware scoring now accounts for the Moon's phase, your filters and your sky brightness (Bortle class).",
    "0.5.10": "The web setup wizard is centred correctly, more controls carry proper accessibility labels, and the library and panorama handling got small fixes.",
    "0.5.9": "Better keyboard and screen-reader support (dialog roles, focus traps, labelled search), smoother handling of very large imports, and internal cleanups.",
    "0.5.8": "Safer backup import (a safety copy is written before anything is replaced), streamed uploads that no longer need the whole session in memory, and small web fixes.",
    "0.5.7": "The session journal was removed from both versions — planning and the image library are unchanged, but observed marks and journal notes are gone.",
    "0.5.6": "Horizon photos now survive reloading the page, and the “free space” layout setting works on any screen.",
    "0.5.5": "More stable time slider; changing the minimum altitude no longer flashes the target list.",
    "0.5.4": "Public release: open source (MIT), credits and notices, automatic update checker in the desktop app, download page.",
    "0.5.3": "Target grades (S–D) in the desktop app; weather with a 4-day forecast in both versions; new website.",
    "0.5.2": "Target grades (S–D) for the web; the sky map zoom now opens a wider rectangular view.",
    "0.5.1": "New interface: hamburger menu, Glass theme, desktop-style layout in the browser.",
    "0.5.0": "Web version: sites, rigs, custom targets and first-run setup.",
    "0.4.2": "The web version joined the project.",
}


def git(*args):
    """stdout of git, captured WITHOUT pipes.

    The DSH sandbox denies pipe creation to spawned processes, so
    capture_output=True dies with PermissionError [WinError 5]. The fallback
    redirects stdout into a file inside the repo (the platform temp dir is not
    writable there) - the same helper as scripts/check_release.py:git().
    """
    argv = ["git", *args]
    try:
        return subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        pass
    tmp_dir = ROOT / "logs" if (ROOT / "logs").is_dir() else ROOT
    tmp = tmp_dir / f".git-capture-{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            subprocess.run(argv, cwd=str(ROOT), stdout=fh,
                           stderr=subprocess.DEVNULL)
        return tmp.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def has_summary(curated: dict, version: str) -> bool:
    """True when `version` has a CURATED entry carrying REAL text.

    TR-05 (0.6.32 review): the curation gates tested MEMBERSHIP (`v in curated`)
    while `build_entries` tested truthiness (`if summary:`), so
    `CURATED["0.6.33"] = ""` - the "add the key now, write the text later" slip -
    passed `pre_tag_check.py`, passed `check_release.py` and reported `--check`
    OK, while the generated `landing/changelog.json` silently omitted the shipped
    release. ONE predicate now decides both."""
    return bool(str(curated.get(version) or "").strip())


def split_uncurated(tags, curated):
    """Split uncurated tags into (since the newest curated entry, older).

    `tags` is newest-first (git tag --sort=-version:refname). Only the first
    list matters for release integrity: those are the releases that happened
    AFTER the newest entry the public changelog knows about. Everything older
    is the historical backlog (pre-0.4.2 tags) and is never curated. An EMPTY
    curated value is uncurated (TR-05 - it generates no entry)."""
    versions = [t.lstrip("v") for t in tags]
    newest = next((i for i, v in enumerate(versions) if has_summary(curated, v)),
                  None)
    if newest is None:
        return list(versions), []
    return ([v for v in versions[:newest] if not has_summary(curated, v)],
            [v for v in versions[newest + 1:] if not has_summary(curated, v)])


def build_entries(tags, dates, curated=None):
    """Curated entries, newest first, with each tag's date."""
    curated = CURATED if curated is None else curated
    entries = []
    for t in tags:
        ver = t.lstrip("v")
        summary = curated.get(ver)
        if has_summary(curated, ver):
            entries.append({"version": ver, "date": dates(t), "summary": summary})
    return entries


def report(backlog, older, newest_curated):
    """Print the curation status; returns the number of blocking tags."""
    if backlog:
        shown = ", ".join(backlog[:8]) + (" ..." if len(backlog) > 8 else "")
        print(f"WARNING: {len(backlog)} release tag(s) SINCE the newest curated "
              f"entry ({newest_curated}) have no summary, so the download "
              f"page's \"What's new\" is stale: {shown}")
        print("         Add a plain-words entry to CURATED above - commit it "
              "BEFORE tagging, or the published version string turns '-dirty'. "
              "A tag that was web-only (no new desktop build) still deserves an "
              "entry saying so.")
    if older:
        shown = ", ".join(older[:8]) + (" ..." if len(older) > 8 else "")
        print(f"note: {len(older)} further uncurated tag(s) predate the newest "
              f"curated entry (historical backlog, never curated): {shown}")
    return len(backlog)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate landing/changelog.json from the curated summaries, "
                    "or --check that no release since the newest curated entry "
                    "is missing one.")
    parser.add_argument("--check", action="store_true",
                        help="write nothing and exit non-zero when a release tag "
                             "newer than the newest curated entry has no summary")
    args = parser.parse_args(argv)

    tags = [t for t in git("tag", "--sort=-version:refname").splitlines() if t]
    if not tags:
        print("no git tags found - run from the repo root of a tagged checkout.")
        return 1 if args.check else 0

    backlog, older = split_uncurated(tags, CURATED)
    newest_curated = next((t.lstrip("v") for t in tags if t.lstrip("v") in CURATED),
                          "none")

    if not args.check:
        def date_of(tag):
            return git("log", "-1", "--format=%ad", "--date=short", tag)

        entries = build_entries(tags, date_of)
        OUT.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
        print(f"{OUT}: {len(entries)} curated entries")

    blocking = report(backlog, older, newest_curated)
    if args.check:
        if blocking:
            print(f"curation check: FAILED - {blocking} release(s) after "
                  f"{newest_curated} are missing from the public changelog.")
            return 1
        print(f"curation check: OK - every release after {newest_curated} has a "
              "curated summary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
