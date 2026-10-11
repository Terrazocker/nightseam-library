# Importing exposures into NightSeam

## Import now

1. Open **Import Exposures**.
2. Check the **Archive folder**. Use **Browse...** in the native window or type
   a path, then **Save** when changing it. This is the destination for copies.
3. Use **Import exposures...**, the toolbar's **Import**, or drop `.fit` and
   `.fits` files into the drop area. Originals stay in their original location.
4. Watch **files processed / total** and **elapsed time**. Counts advance after
   each batch of up to eight files returns. Elapsed time keeps updating during
   a batch. A failure counter appears when a returned file result fails.
5. Review **Import results**, then open **Library** to inspect the objects and
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
upload history, so separate upload actions can remain separate entries even
when Library combines their capture sessions.

## Reindex an existing archive

**Reindex folders** reads the saved Archive folder; select the root above the
object folders. It refreshes capture/session metadata from headers and indexes
exposure files already arranged in either of these
supported layouts:

```text
<archive>/<object>/<session>/<lights|darks|flats|bias>/<file.fit or file.fits>
<archive>/<object>/<lights|darks|flats|bias>/<file.fit or file.fits>  (legacy)
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
or **+ Bias**. The exposure picker opens and Import Exposures identifies the
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

## Export and duplicate handling

**Export library** writes the full library's metadata as CSV. The separate
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

When installing an updated executable, close NightSeam and replace the executable
in its existing folder, retaining its `data` folder. Packaged data is stored next
to the executable, so launching a copy from a different folder uses a different
local database.
