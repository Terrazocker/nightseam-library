# Linux browser build

The portable Linux x86_64 bundle includes Python, the local JavaScript engine,
catalog assets and verified native FITS compression tools. It opens the catalog
in your installed browser. GTK/Qt native windows are not bundled. **Choose
folder…** uses the desktop's `zenity` or `kdialog` folder dialog when either is
installed (for example `sudo apt install zenity`), and then imports the folder
in place as one continuous run. The same dialog serves **Browse…** for the
archive folder and the library copy destination. Without them, folder import
falls back to the browser's folder upload, which asks to confirm the upload,
and those fields take a typed path. Browser launching and the
**Open folder** action use `xdg-open`
from `xdg-utils`. If it is unavailable, open the logged local URL manually;
the folder action reports the missing requirement. Desktop programs receive
the system library path rather than the frozen application's bundled libraries.

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
contains no profile database, images or logs. Verify the adjacent checksum
before extraction, from the directory containing both downloaded files:

```sh
sha256sum --check NightSeam-Catalog-VERSION-linux-x86_64.tar.gz.sha256
tar -xzf NightSeam-Catalog-VERSION-linux-x86_64.tar.gz
```

Replace VERSION with the downloaded version. Keep existing installations and
their data directories separate; do not extract over an existing library.

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
required.

The same tarball also runs in minimal Ubuntu 22.04, Ubuntu 24.04 and Debian 12
containers. The application runs as a non-root user with a read-only base OS,
no installed Python/Node, no mounted checkout/profile, and no external network.
A separate curl probe shares only its loopback network. The gate checks the
offline first launch, repeat-instance handoff, captured desktop launcher
arguments/system library environment, real Rice/restore/duplicate operations,
and graceful cleanup. Image digests, installed OS packages and binary hashes
are retained in `logs/linux-package-clean-*.json`. These checks do not establish
real browser/file-manager behavior or physical desktop acceptance.

To repeat a clean-system check with Docker running locally:

```sh
.venv-build/bin/python scripts/check_linux_clean_system.py \
  --bundle dist/NightSeam-Catalog-VERSION-linux-x86_64.tar.gz \
  --image ubuntu:22.04 --output logs/linux-package-clean-ubuntu22.json
```

Image downloads occur before testing; the application itself remains offline.
The host controller needs Python, but the application container receives only
the base OS, bundle and a captured `xdg-open` test double. See
[Linux manual acceptance](docs/linux-manual-acceptance.md) for the remaining
interactive desktop checks.
