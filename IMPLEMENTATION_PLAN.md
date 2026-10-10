# NightSeam Catalog roadmap

The 0.1.0 extraction preserves the catalog UI and import/storage behavior.
Develop and release this repository independently; never couple its version or
build inputs to the sibling website.

## Current scope - October 5, 2026

1. Capture fresh full-suite results and verify the existing 0.1.0 package with
   isolated browser/data copies.
2. Establish main for independent development, preserve v0.1.0, and document
   release/remote setup separately from public executable publication.

## Deferred by user - October 5, 2026

These tasks remain open. Numbers refer to the shared status report's items 3-7.

- Item 3: native Windows acceptance on mixed-DPI/multi-monitor setups, including
  Snap, taskbar behavior, system menu and keyboard move/resize. Record the tested
  displays/scaling and confirm stable density and window placement.
- Item 4: manual accessibility acceptance for keyboard focus, chooser/dialog
  focus restoration, dynamic announcements and screen-reader use.
- Item 6: retire the retained maintenance planner and unused endpoints in a
  separate tested cleanup. Preserve existing database migrations and backups.
- Item 7: consolidate backlog/plan authority and repair stale map-roadmap
  references. Choose alternative map styles deliberately before implementing.
- Later performance work: profile representative real archives before changing
  sequential import/reindex batching; retain cancellation, archive locking,
  duplicate identity and transactional publication.
- Later dependency maintenance: review the Starlette/httpx test warning without
  broad dependency changes during this validation/repository pass.

Item 5 (website elevation semantics) remains in the website roadmap. Confirmed
bugs remain in BUGS.md; deferral does not mark acceptance gaps resolved.

## Completed workspace upkeep - October 10, 2026

- Remove accumulated disposable test/build output; retain data, current release,
  build environment and split recovery evidence.
- Isolate test data per test, close test database connections, restore scratch
  working directories and make cleanup failures visible.
- Own the packaged browser process tree and verify its listener exits before
  removing private scratch under logs/.
- Add safe preview/apply housekeeping, protect active owners and tracked/linked
  paths, and retain three executable backups after successful builds.

The repeatable commands and handoff routine are in DEVELOPMENT.md. Actual
validation outcomes are recorded in RELEASES.md.
