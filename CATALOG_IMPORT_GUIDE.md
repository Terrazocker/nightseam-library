# Importing exposures into NightSeam

## Compress automatically during import

In **Import**, check **Compress on import with lossless Rice** before
selecting or dropping files. The option starts unchecked and stays selected in
that application window, including when attaching calibration frames. It applies
to the whole import action; you cannot change it halfway through upload chunks.

Checking it reveals the sliding **Fast / Safe** control below the checkbox:

- **Fast** (left, initial choice) trusts the lossless Rice encoder and skips
  decompressing each file for comparison.
- **Safe** (right) also decompresses each file with `funpack -C` and checks its
  complete original SHA-256 and size before finishing the import.

Hover over the mode labels or **i**, focus the control, or tap **i** for the
explanation. Space toggles the switch; Escape dismisses the explanation. The
mode is enabled only while compression is selected and stays fixed throughout
an import, including calibration attachments.

Both modes compress in temporary staging, verify the stored compressed bytes
and commit the catalog before removing temporary originals. Your source-folder
files are never deleted or changed. The original hash is retained for checking
when **Uncompress** restores the file; Fast postpones that restoration check.
Session compression and library copying have their own Fast/Safe choice.
Missing-file representation recovery always keeps strict restoration checks.

Unsupported images, including floating point FITS, and already compressed files
are imported unchanged, with a visible reason in the results. A compression or
verification failure publishes no exposure for that file. Earlier successful
files remain imported. Duplicate uploads preserve their existing record and
storage format. Re-uploading either known representation of a missing managed
exposure restores that existing record in its recorded format.

## Compress or restore a session

Select a capture session in **Library**, then choose **Compress losslessly**.
It includes attached calibration exposures and creates standard Rice `.fz` FITS
files. The dialog has a **Fast / Safe** sliding switch; it starts **Safe** to
preserve the existing manual-conversion policy. **Safe** restores every encoded
file and checks its original SHA-256. **Fast** trusts lossless Rice and skips that
round trip. Both verify stored bytes and retain originals until all replacements
and the catalog update succeed. Expand **i**, or hover over it, for the explanation.

Choose **Uncompress** to restore ordinary FITS for software that requires them.
Files compressed by NightSeam must match their recorded original hash. Metadata,
session membership and calibration attachments remain intact. Importing an
original again after compression still finds the existing exposure.

Rice conversion supports 8-, 16- and 32-bit integer images. Session compression
skips unsupported files, including floating point stacks, and files missing from
disk, while converting the supported exposures. The result counts missing and
unsupported exposures separately; expand **Skipped exposures** to see up to 50
paths. Existing Rice files are kept; other compressed formats are not converted.
Missing records remain in the catalog: use **Check archive health** to review
them, or import the original again to restore it. Permission, integrity and
conversion failures still abort and recover the operation. Library copies and
restoration retain their existing completeness and verification requirements.
Imported Rice can be uncompressed, but an unknown historical
original-file hash cannot be verified.

The dialog has separate **Preparing files** and **Writing and replacing files**
bars. The second counts actual swaps and verification. Checking originals,
updating the catalog, removing retained originals and refreshing the library are
shown explicitly; full bars do not bypass those steps. Conversion prepares and
publishes files concurrently using available CPU cores, up to 24 workers.
The dialog offers **Cancel**. Closing it lets work continue;
**Copy library** reopens active work. Before commit, cancellation restores the
whole session. Interrupted changes recover on the next launch: keep the original
disks available and preserve any reported recovery files. Compression needs
temporary space while originals remain retained.

## Make a one-time library copy

Choose **Tools › Copy library**, select an existing destination folder, then start.
A new folder contains individual usable FITS under `images/`, `nightseam.db`
with catalog metadata, and `manifest.json` with hashes and provenance. Supported
integer FITS use Rice with the dialog's independent Fast/Safe policy; existing Rice and
unsupported files are copied unchanged. Destination files are read back and
hash-checked. Scratch space is on the destination disk.

The active catalog and archive location stay unchanged. No daily jobs are
enabled. Automatic ingest compression is a separate opt-in checkbox. If you move the copy later, set the
copied catalog's archive location to its new `images` folder before using it.

## Import now

1. Open the **Import** tab.
2. Check the destination shown under the drop area (**Copied to …**). To change
   it, choose **Change** or scroll to **Archive settings**, use **Browse…** in
   the native window or type a path, then **Save**.
3. Leave **Frame type** on **Auto** to read each exposure's header, or choose a
   type to override it for the next import.
4. Use **Choose files…**, the header's **Import**, or drop FITS exposures into
   the drop area. **Choose folder…** searches the selected folder and all
   its subfolders for supported exposures, ignoring other files. In the
   desktop window it opens the Windows folder dialog and reads the files in
   place, so no browser upload confirmation appears; an archive folder inside
   the selected folder is skipped. Existing
   exposures are checked by complete contents and skipped even if renamed;
   different contents with the same filename are imported separately.
   Supported names are `.fit`, `.fits`, `.fts`, tiled `.fz`
   (including `.fits.fz`), and their `.gz` wrappers. Originals stay in their
   original location; imported copies preserve the complete original bytes.
5. Watch **files processed / total** and **elapsed time**. Counts advance after
   each batch returns: up to 96 files normally, or 24 with compression enabled.
   Batches also stay within 512 MiB (2 GiB for a folder read in place by the
   desktop window), except that a larger individual exposure is sent alone (the
   per-file limit is 1 GiB). Elapsed time keeps updating during
   a batch. A failure counter appears when a returned file result fails.
6. Review **Import results**, then open **Library** to inspect the objects and
   capture sessions. Final progress remains visible until another import or
   **Clear**. If a request fails, the UI distinguishes unconfirmed file outcomes
   from files that were never attempted. Repeating an uncertain batch is safe:
   files whose identical contents are already present are skipped.

## Do I need to choose Lights or Darks?

Usually no: NightSeam reads the recorded frame type (**IMAGETYP**). Light/science, dark,
flat/skyflat, bias/zero and recognized master-frame variants are classified
automatically. A missing or unrecognized value defaults to **Light**.

The initially highlighted **Lights** button represents that fallback; it is
not an active forced override. Clicking a type explicitly forces that type
for **every file in the next selection**, even if the headers say otherwise.
The override resets after that import. Clicking the selected override again
returns to header-based classification.

Use automatic classification for a mixed selection with correct headers. For
files without usable frame-type headers, import each type separately: select
**Darks**, choose only dark files, then repeat for flats or bias as needed.

## How separate imports join sessions

NightSeam identifies an object from **OBJECT**, the filename or coordinates.
For each object and observing location, **sunset through the next sunrise is
one Night session**. **Sunrise through sunset is one Day session**. Capture
gaps and upload order do not define the boundaries. A capture exactly at sunrise
belongs to Day; one exactly at sunset belongs to Night.

For example, M31 lights captured at 22:00 and more M31 lights at 01:00 join the
same session even when uploaded on different days. Filter and frame type do
not split that session. A different object is grouped separately. Even a winter
night longer than twelve hours remains one session. Automatically imported
daytime flats belong to Day; use a selected night's **+ Flats** action when you
want to attach them explicitly to that night instead.

**SITELAT/SITELONG** in the FITS header supply the observing location. For files
without either field, enter their known latitude and longitude in **Observing
location fallback**, then **Save**. Header location takes priority; invalid or
incomplete header coordinates remain **Unassigned location**, grouped by UTC
capture date. Leave both fallback fields blank and save to clear the setting.
Stored locations are retained when the fallback later changes. Continuous
polar day/night uses explicitly labelled UTC-date intervals.

**DATE-OBS** is the capture time. A timestamp without an offset is UTC;
explicit offsets are converted to UTC and fractional seconds are preserved.
Missing or invalid timestamps, ambiguous slash dates and unsupported explicit
**TIMESYS** values form **Undated** sessions. **DATE**, the file-creation field,
is never used as a capture-time substitute. Original files remain unchanged.

The archive stores copies in:

```text
<archive>/<object>/<session>/lights/
<archive>/<object>/<session>/darks/
<archive>/<object>/<session>/flats/
<archive>/<object>/<session>/bias/
```

New session folders use the fixed UTC solar boundary and an identity suffix,
such as `night_2026-08-22_181703_<identity>`. Unresolved folders use
`unassigned_YYYY-MM-DD` or `undated`. Earlier captures from the same interval
use the same folder. Reindex refreshes old logical membership without moving
existing files, so one Library session can still refer to multiple older folders.
**Recent imports** is
upload history grouped by archive session folder: one entry lists that night's
lights, darks, flats and bias. Uploads stored in different folders remain
separate entries even when Library combines their capture sessions.

## Reindex an existing archive

**Reindex folders** reads the saved Archive folder; select the root above the
object folders. It refreshes capture/session metadata from headers and indexes
exposure files already arranged in either of these
supported layouts:

```text
<archive>/<object>/<session>/<lights|darks|flats|bias>/<supported FITS file>
<archive>/<object>/<lights|darks|flats|bias>/<supported FITS file>  (legacy)
```

The frame folder determines the frame type. Session folder names do not have
to be timestamps. Empty folders and unsupported file types are ignored; manually copied
files do not need all four frame folders beside them.

NightSeam checks every exposure path before updating any library records or cached
fingerprints. If even one file has an unexpected folder structure, the entire
reindex is rejected with an error naming its containing folder and showing
the accepted layouts. The library index and files remain unchanged by that
rejected reindex.

A flat Seestar capture folder containing original exposures is an import
source, not a managed archive. Choose a separate destination as the Archive
folder, then use **Import Exposures** to select those original files. NightSeam
copies them into its object/session/frame folders and leaves the originals in
place. Reindex does not rearrange a source folder into that layout.

Keep the archive root pointed above the object folders. Layout validation
cannot identify every mistaken root selection: a modern object's
`<session>/<frame>/<file>` subtree can resemble the supported legacy layout
when viewed from the wrong root.

## Add calibration exposures to a session

Select an object and capture session in Library, then use **+ Darks**, **+ Flats**
or **+ Bias** under **Add calibration frames**. The exposure picker opens and Import identifies the
target. These actions force the chosen frame type and attach new exposures to
that object and logical session. Missing object metadata or a different capture
date does not send calibration exposures to another session. Undated sessions
and legacy `<object>/lights` folders are supported too.

Original capture timestamps and exposure-file contents remain intact; the
explicit association is stored in the database alongside the capture rows.
Legacy archives gain a canonical object/session/calibration folder without
moving their existing light exposures. Recent-import calibration actions use
the corresponding session still present in the library. If that session is
gone, the action explains that it is unavailable.

The ordinary **Import** action continues to identify objects and group capture
times automatically. An already-imported exposure is skipped and retains its
existing association; selecting a different session does not move it.

## Archive health

Open **Tools › Archive health** in the header. **Quick check** inspects availability,
paths, readability, sizes and modification times. It also discovers unindexed
supported FITS files, including compressed exposures. Unchanged metadata does
not verify contents. **Deep
verification** reads complete indexed files and compares SHA-256 against a
separately preserved integrity baseline. Neither checks image quality, FITS
structure or embedded FITS checksums. Scans never repair or change anything.

New imports preserve import-time hashes. Earlier libraries preserve their
previously recorded fingerprint, honestly labelled as historical; it may already
reflect a prior external edit. Reindex/cache refreshes preserve existing
baselines. Files without a baseline remain unverified. An unavailable/replaced
archive is reported as unavailable, instead of treating every file as missing.
Linked paths are not followed. Progress shows checked exposures and elapsed
time. **Cancel scan** leaves explicit partial results; closing the dialog lets
the scan continue. The latest report stays in memory until the next scan or app
exit. **Download JSON report** preserves it. The dialog shows 200 findings; the
report retains 10,000, with further omitted findings counted explicitly.

After a completed scan, missing exposures are grouped by object and capture
session. **Acknowledge session** accepts all retained deletions in that session;
**Accept all deletions** accepts them across the report. Expand **Exposure details**
for individual **Acknowledge deletion** actions. Confirmation removes missing
catalog records, baselines and obsolete compression mappings; no files are deleted
and present session members stay in the catalog. A whole group commits together:
if a selected file has reappeared or its record changed, nothing is acknowledged.
Batch actions include retained findings beyond the 200-detail display. If more
than 10,000 findings were found, acknowledge retained deletions then scan again
for the omitted ones. **Accept current contents**
replaces a Deep scan's changed-content baseline after confirmation and rehashing.
Restored files, further edits, changed records and unavailable/replaced archives
require a new scan. Quick metadata changes require Deep verification first.
Cancelled/failed reports cannot be acknowledged. Counts refresh, acknowledgements
remain labelled in the current report, and resolved issues do not recur in later
scans. SQLite retains action timestamps and previous/accepted hashes. Accepting
contents does not update FITS header metadata or assess an edit's correctness.

## Compressed FITS metadata

Import reads primary-image metadata, or the first nonempty image extension when
the primary is empty. The selected extension overrides primary values, with
primary metadata retained as fallback. Later images in a multi-image file do not
create extra exposure records. Reindex uses the same reader.

Gzip and tiled-image metadata are read without decoding image pixels. Files are
copied as supplied; NightSeam does not recompress them. A plain FITS and a gzip
copy have different complete-file bytes and remain distinct imports. Identical
compressed files still deduplicate even when renamed.

Malformed headers and files that exceed metadata traversal limits report a
per-file import failure. The reader limits header sizes, HDU count, gzip input
and expansion, and scan time. Metadata reads do not validate unread pixels,
later image extensions or unread gzip trailers. Use Archive Health for complete
stored-byte verification. Exposure previews remain retired.

## Export and duplicate handling

**Tools › Export library** writes the full library's metadata as CSV. The separate
object export and format strip has been removed from the Library workspace.
The native window opens Windows **Save As**; a browser uses its normal download
behavior. The FITS files remain in the archive. Export errors appear in a toast
and leave the catalog open.

Uploading the same file again shows **Already imported** and creates no extra
copy or library row. Comparison uses SHA-256 of the complete file contents, so
renaming a file does not bypass detection. Different files sharing a filename
remain distinct and receive `_2`, `_3`, etc. when necessary. Skipped files count
as processed, with a separate already-imported count; they do not increase new
import totals, exposure totals or import history. A frame-type/session override
does not reclassify or move an already-imported file.

Older library records are checked against their archived originals and gain
fingerprints lazily, without a manual migration step. The first comparison can
take longer while same-sized older originals are read and fingerprinted. If a
previously fingerprinted original is missing, reimporting its exact bytes restores
that original without adding another library row. The result shows **Original
restored**, with a separate restored count; it does not increase new-import
totals or create another import-history entry. **Reindex folders** first validates
the configured archive layout, then skips both already-recorded paths and identical
file contents. It is useful for manual copies in the supported archive layouts
described above. Duplicate files
already on disk are retained; reindex avoids adding another library record.

If another program changes files already in the archive, run **Reindex folders**
before importing those changed files. Reindex refreshes their size and
fingerprint so imports recognize the updated contents. Cached hash matches are
always verified against the actual archived bytes.

Import prepares files concurrently while publishing them in selection order.
Each file is written straight into the archive while it uploads, without a
temporary copy, and earlier files in a batch publish while later ones arrive.
Rice uploads use batches of up to 24 files, with preparation bounded by available
CPU cores. Plain uploads use batches of up to 96, bounded by 512 MiB. Temporary preparation
does not hold the archive lock; publication and catalog commits remain serialized.
The object matcher is reused across chunks until the catalog changes. On local
NTFS/ReFS with an active change journal, unchanged nonmatching files use a
process-local hash cache guarded by file identity, native change time and the
journal's per-file change number, which detects edits with restored modification
times. Files imported in the current session are known from the bytes written,
and older same-size archive files are read once; later batches and imports skip
rereading either. Other filesystems, and
volumes without a change journal, fall back to reading current bytes.
Actual duplicate matches are always rehashed before an upload is skipped. Each
request pins up to 4,096 earlier same-size candidates against writes/renames and
checks them once for the batch. Other archive mutations wait until these pins
close. Open writers and unsupported filesystems keep conservative individual
checks. Image records and import history commit together once per file.

When installing an updated executable, close NightSeam and replace the executable
in its existing folder, retaining its `data` folder. Packaged data is stored next
to the executable, so launching a copy from a different folder uses a different
local database.
