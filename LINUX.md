# Linux browser build

The portable Linux x86_64 bundle includes Python, the local JavaScript engine,
catalog assets and verified native FITS compression tools. It opens the catalog
in your installed browser. GTK/Qt native windows and their folder pickers are
not bundled. The **Open folder** action uses `xdg-open` from `xdg-utils`; if it
is unavailable, the catalog reports that requirement without launching anything.

Extract the `.tar.gz` into a **user-writable folder**, then run:

```sh
./NightSeam --browser --console
```

Stop it with Ctrl+C in the terminal. Closing the browser tab leaves the local
server running until shutdown or its idle timeout. `--no-browser --console`
starts a headless server; the default address is `http://127.0.0.1:8407`.

The bundle targets glibc Linux on x86_64. The initial bundle's highest numeric
GLIBC requirement is 2.35; `build-info.json` records every bundled ELF's
requirements, source commit, exact dependencies and executable SHA-256. CI
builds and tests on Ubuntu 22.04 (glibc 2.35); local development checks also run
on Debian/glibc 2.41. Other distributions need compatible system libraries.
ARM and Alpine/musl are not targets of this build. Onefile extraction needs an
executable temporary filesystem.

`data/` and `logs/` are created beside the extracted executable. Choose the
image archive in the catalog. Do not copy this directory over an existing
library, install it in `/usr/bin`, or run it as root. The distributed bundle
contains no profile database, images or logs. The adjacent `.sha256` file
verifies the downloaded archive.

To build from a clean checkout on glibc Linux x86_64, with Python 3.12, Node
(source checks) and binutils installed:

```sh
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements-build-linux-lock.txt
.venv-build/bin/python scripts/build_linux.py
```

Output is `dist/NightSeam-Catalog-VERSION-linux-x86_64/` plus `.tar.gz` and its
checksum. Off-tag builds include their commit in VERSION; `--allow-dirty`
explicitly marks uncommitted development builds. Existing output is refused;
use a new `--output` directory to preserve earlier binaries and data. The
version is baked into an owned source copy, leaving the working checkout alone.
Failed builds retain diagnostics in `logs/`; successful builds remove their
owned build scratch. This is a development artifact, not a public release.

The Catalog validation workflow retains the tarball and checks its extracted
contents, frozen version/assets, empty-PATH operation, actual Rice ingest and
exact restoration, duplicate metadata, history/integrity publication, graceful
SIGINT exit, listener/process/extraction/staging cleanup, and the complete
packaged Chromium browser suite. Normal Linux source and Windows gates remain
required. The workflow accepts stacked pull requests so the Linux packaging
change can be reviewed separately from the preceding performance change.
