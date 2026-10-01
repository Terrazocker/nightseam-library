/* Startup update check: ask our OWN server, which fetches and validates the
   published version document (GET /downloads/version.json on nightseam.de).
   Silent and optional: any failure means "no update", nothing blocks startup,
   and no data is transmitted beyond the request itself.

   SP-02 (0.6.32 review): this used to load
   https://nightseam.de/downloads/ns-version.js as a JSONP <script>, so REMOTE
   JavaScript executed inside the privileged 127.0.0.1 origin where /api/export
   and /api/state are unauthenticated same-origin routes. Validating the payload
   could not fix that - the script itself ran. The fetch/parse/validate now
   happens in Python (`app/server.py::_fetch_remote_version`, POST
   /api/update-check, which also does the version comparison) and the page
   loads NO remote code at all. The server also ships a CSP
   (`script-src 'self'`) as defence in depth. */

import { api, toast } from "./util.js";

const SITE = "https://nightseam.de/#download";

export function checkForUpdates() {
  // give the UI a moment to settle first; never block startup
  setTimeout(() => {
    try {
      // POST: StaticFiles owns GET on unmatched paths, and this is a JSON
      // endpoint of our own origin, not a cacheable document.
      api("/api/update-check", { method: "POST" }).then((r) => {
        if (!r || r.update !== true) return;      // no update / check failed
        const remote = String(r.version || "");
        // the server already validated it strictly; this second check is
        // defence in depth for what we interpolate into the toast
        if (!/^[0-9][0-9A-Za-z.\-+]{0,31}$/.test(remote)) return;
        const box = toast(
          `Update available: NightSeam v${remote} — click for the download page.`,
          "info", 20000);
        box.style.cursor = "pointer";
        box.addEventListener("click", () => {
          window.open(SITE, "_blank");
        });
      }).catch(() => { /* offline or blocked — never bother the user */ });
    } catch (e) { /* offline or blocked — never bother the user */ }
  }, 3000);
}
