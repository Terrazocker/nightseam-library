"""Web browser checks (Playwright + system Chrome over the static `web/` tree).

Each check is registered with `@check(name, "web")`; `scripts/check_browser.py`
runs them against a throwaway static server.  Selectors mirror `web/index.html`
and the module-built DOM.
"""

from __future__ import annotations

import hashlib
import re
import struct
import time
import zlib
from pathlib import Path

from harness import ROOT, WEB_DIR, check

# ---------------------------------------------------------------- helpers

# The Direction B shell renders Tonight as a ranked table rather than the old
# `.pick`/`.card` list. Wait for an actual populated target row so callers do
# not continue against the static shell before the first planning render.
READY = "#tonight-rank-table tbody tr[data-tonight-id]"


def boot(c):
    """Reach a usable planning view from empty storage (the first-run welcome's
    'Skip' keeps the deterministic Berlin default)."""
    c.goto("/")
    try:
        c.page.wait_for_selector("#w-skip", state="visible", timeout=15000)
        c.page.click("#w-skip")
    except Exception:
        pass  # no welcome (storage was pre-seeded)
    c.page.wait_for_selector(READY, timeout=60000)


def tab(c, name: str):
    c.page.click(f"#tabs a[data-v='{name}']")
    c.page.wait_for_timeout(250)


def canvas_digest(c, selector: str) -> str:
    data = c.page.evaluate(
        """(sel) => { const cv = document.querySelector(sel);
             return cv ? cv.toDataURL() : null; }""", selector)
    return hashlib.sha256((data or "").encode()).hexdigest()


def horizon_test_png(width: int, height: int) -> bytes:
    """Tiny RGB panorama with edge colors to catch side-cropping in the editor."""
    rows = bytearray()
    for _y in range(height):
        rows.append(0)
        for x in range(width):
            color = (220, 70, 75) if x < width * 0.12 else (
                (80, 125, 230) if x >= width * 0.88 else (65, 145, 110))
            rows.extend(color)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))


def open_editor(c):
    tab(c, "horizon")
    c.page.wait_for_selector("#pano-blank", timeout=20000)
    c.page.click("#pano-blank")
    c.page.wait_for_selector("#pano-canvas", state="visible", timeout=20000)
    # The editor is tall (canvas ~608 px below the topbar): give the viewport
    # room for the whole canvas so pointer paint/crop gestures land on it.
    c.page.set_viewport_size({"width": 1366, "height": 1500})
    c.page.evaluate("() => window.scrollTo(0, 0)")
    c.page.locator("#pano-canvas").scroll_into_view_if_needed()
    c.page.wait_for_timeout(250)


def paint(c, x0=0.30, x1=0.62, y=0.35, button="left"):
    box = c.page.locator("#pano-canvas").bounding_box()
    c.expect(box and box["width"] > 20, "pano canvas must have a size")
    y0 = box["y"] + min(box["height"] * y, box["height"] - 4)
    sx = box["x"] + box["width"] * x0
    ex = box["x"] + box["width"] * x1
    c.page.mouse.move(sx, y0)
    c.page.mouse.down(button=button)
    for i in range(1, 13):
        c.page.mouse.move(sx + (ex - sx) * i / 12, y0)
    c.page.mouse.up(button=button)
    c.page.wait_for_timeout(180)


def pano_state(c):
    return c.page.evaluate(
        """() => { try { return JSON.parse(localStorage.getItem('nightseam_panos_v1') || 'null'); }
                  catch (e) { return null; } }""")


def active_strokes(c):
    st = pano_state(c)
    if not st or not st.get("panos"):
        return -1
    p = next((p for p in st["panos"] if p.get("id") == st.get("activeId")),
             st["panos"][0])
    return len(p.get("strokes") or [])


# ---------------------------------------------------------------- checks

@check("web-load-smoke", "web")
def web_load_smoke(c):
    boot(c)
    ver = c.page.inner_text("#foot-ver").strip()
    c.expect(re.search(r"\d+\.\d+\.\d+", ver), f"version not displayed: {ver!r}")
    about = c.page.inner_text("#aboutVersion").strip()
    c.expect(re.search(r"\d+\.\d+\.\d+", about), f"about version not shown: {about!r}")
    c.expect(c.page.locator("#tb-site option").count() >= 1,
             "the site picker must be populated")
    c.expect(c.page.locator(READY).count() > 0,
             "the Tonight ranking must render at least one target row")
    c.assert_clean()


@check("web-cache-tokens", "web")
def web_cache_tokens(c):
    """A mixed module set (old index.html + new modules) is prevented by the
    per-module `?v=` tokens.  Observe what the browser really requests."""
    reqs: list[str] = []
    c.page.on("request", lambda r: reqs.append(r.url))
    boot(c)
    idx = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    m = re.search(r"app\.js\?v=(\d+)", idx)
    c.expect(m, "index.html must load app.js with a cache token")
    app_url = f"app.js?v={m.group(1)}"
    c.expect(any(u.endswith(app_url) for u in reqs),
             f"browser did not request {app_url}")
    bad = [u for u in reqs
           if u.endswith(".js") and "?" not in u and "/core/" not in u]
    c.expect(not bad, f"module requests without a cache token: {bad[:4]}")
    app_src = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    for name, tok in re.findall(r"\./([\w.]+)\.js\?v=(\d+)", app_src):
        c.expect(any(u.endswith(f"{name}.js?v={tok}") for u in reqs),
                 f"{name}.js?v={tok} was never requested")
    c.assert_clean()


@check("web-horizon-actions-not-full-width", "web")
def web_horizon_actions(c):
    """ITEM B regression: an action button used as a label must stay
    content-width at 1024 and 1366 px.  Rendered layout, not source."""
    boot(c)
    tab(c, "horizon")
    c.page.wait_for_selector(".btnrow.action-row", timeout=20000)
    for width in (1024, 1366):
        c.page.set_viewport_size({"width": width, "height": 900})
        c.page.wait_for_timeout(250)
        info = c.page.evaluate(
            """() => {
              const row = document.querySelector('.btnrow.action-row');
              if (!row) return null;
              const rw = row.getBoundingClientRect().width;
              const items = Array.from(
                row.querySelectorAll(':scope > .btn, :scope > label.btn'));
              return { rw, items: items.map(e => {
                const b = e.getBoundingClientRect();
                return { t: (e.textContent || '').trim().slice(0, 24), w: b.width };
              }) };
            }""")
        c.expect(info and info["items"], "the horizons action row must have controls")
        for it in info["items"]:
            c.expect(it["w"] < info["rw"] * 0.9,
                     f"'{it['t']}' spans the full row at {width}px "
                     f"({it['w']:.0f}/{info['rw']:.0f})")
    c.page.set_viewport_size({"width": 1366, "height": 900})
    c.assert_clean()


@check("web-pano-paint-save-persist", "web")
def web_pano_paint_save_persist(c):
    boot(c)
    open_editor(c)
    paint(c)
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "painting must mark the editor dirty")

    # a held right-button drag erases (one gesture)
    painted = canvas_digest(c, "#pano-canvas")
    paint(c, 0.30, 0.62, 0.35, button="right")
    c.expect(canvas_digest(c, "#pano-canvas") != painted,
             "a right-button drag must erase the painted band")

    paint(c)
    c.page.click("#ed-save")
    c.page.wait_for_function(
        """() => (document.querySelector('#ed-status')?.textContent || '')
                   .includes('Saved')""", timeout=10000)
    c.expect(active_strokes(c) > 0, "saving must persist the stroke")

    # reload: the saved horizon survives
    c.goto("/")
    tab(c, "horizon")
    c.page.wait_for_selector("#pano-list li[data-id]", timeout=30000)
    c.page.click("#pano-list li[data-id]")
    c.page.wait_for_selector("#pano-canvas", timeout=30000)
    c.expect(active_strokes(c) > 0, "the horizon must persist across a reload")
    c.assert_clean()


@check("web-pano-undo-redo-clear", "web")
def web_pano_undo_redo_clear(c):
    boot(c)
    open_editor(c)
    paint(c)
    c.page.click("#ed-save")
    def wait_status(text_value: str, timeout: int = 8000):
        try:
            c.page.wait_for_function(
                """(want) => (document.querySelector('#ed-status')?.textContent || '')
                           .includes(want)""", arg=text_value, timeout=timeout)
        except Exception as exc:
            raise AssertionError(
                f"editor status did not reach {text_value!r}: "
                f"{c.page.inner_text('#ed-status').replace('●', '*')!r}; "
                f"undo-disabled={c.page.is_disabled('#ed-undo')}, "
                f"redo-disabled={c.page.is_disabled('#ed-redo')}") from exc

    wait_status("Saved", timeout=10000)

    # a second gesture: undo returns to the saved baseline, redo re-applies it
    paint(c, 0.55, 0.78, 0.5)
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "the second stroke must dirty the editor")
    c.page.click("#ed-undo")
    wait_status("Saved")
    c.page.click("#ed-redo")
    wait_status("Unsaved")

    # Clear is confirmed in-page (never alert()) and is undoable
    before = canvas_digest(c, "#pano-profile")
    c.page.click("#ed-clear")
    c.page.wait_for_selector(".ed-confirm [data-ok]", timeout=5000)
    c.page.click(".ed-confirm [data-ok]")
    c.page.wait_for_timeout(300)
    c.expect(canvas_digest(c, "#pano-profile") != before,
             "Clear must change the obstruction profile")
    c.page.click("#ed-undo")
    c.page.wait_for_timeout(300)
    c.expect(canvas_digest(c, "#pano-profile") == before,
             "Clear must be undoable")
    c.assert_clean()


@check("web-pano-storage-quota", "web")
def web_pano_storage_quota(c):
    """A full localStorage must surface 'Save failed' AND keep the edit."""
    boot(c)
    open_editor(c)
    paint(c)
    c.page.evaluate(
        """() => {
          window.__origSet = Storage.prototype.setItem;
          Storage.prototype.setItem = function () {
            throw new DOMException('quota', 'QuotaExceededError');
          };
        }""")
    try:
        c.page.click("#ed-save")
        c.page.wait_for_function(
            """() => (document.querySelector('#ed-status')?.textContent || '')
                       .includes('Save failed')""", timeout=10000)
        c.expect(active_strokes(c) <= 0,
                 "a failed save must not have persisted strokes")
        c.expect("Unsaved" in c.page.inner_text("#ed-status")
                 or "Save failed" in c.page.inner_text("#ed-status"),
                 "the edit must remain (dirty) after a failed save")
    finally:
        c.page.evaluate("() => { Storage.prototype.setItem = window.__origSet; }")
    c.allow_console_errors("intentional QuotaExceededError")
    c.assert_clean()


@check("web-pano-canvas-dpr-resize", "web")
def web_pano_canvas_dpr_resize(c):
    """The editor canvas is DPR-aware and follows a resize (ResizeObserver)."""
    boot(c)
    open_editor(c)

    def dims():
        return c.page.evaluate(
            """() => { const cv = document.querySelector('#pano-canvas');
                 const boxEl = document.querySelector('#pano-box');
                 return { w: cv.width, h: cv.height,
                          bw: boxEl.clientWidth, bh: boxEl.clientHeight,
                          dpr: window.devicePixelRatio || 1 }; }""")

    for width in (1024, 1500):
        c.page.set_viewport_size({"width": width, "height": 900})
        c.page.wait_for_timeout(400)
        d = dims()
        c.expect(abs(d["w"] - d["bw"] * d["dpr"]) <= 2,
                 f"canvas backing store {d['w']} != box width {d['bw']} * dpr "
                 f"{d['dpr']} at {width}px")
        c.expect(d["w"] > 0 and d["h"] > 0, "canvas must keep a backing store")
    c.page.set_viewport_size({"width": 1366, "height": 900})
    c.assert_clean()


@check("web-horizon-stage-preserves-photo-aspect", "web")
def web_horizon_stage_preserves_photo_aspect(c):
    """The panorama stage follows each source aspect and retains its edge pixels."""
    boot(c)
    tab(c, "horizon")
    for label, width, height in (("wide", 144, 48), ("portraitish", 96, 72)):
        expected = width / height
        c.page.locator("#pano-file").set_input_files({
            "name": f"aspect-{label}.png", "mimeType": "image/png",
            "buffer": horizon_test_png(width, height),
        })
        c.page.wait_for_function(
            """(expected) => {
              const cv = document.querySelector('#pano-canvas');
              if (!cv || cv.width < 80 || cv.height < 20) return false;
              const ratio = cv.width / cv.height;
              if (Math.abs(ratio - expected) > 0.03) return false;
              const ctx = cv.getContext('2d');
              const y = Math.floor(cv.height / 2);
              const left = ctx.getImageData(Math.floor(cv.width * .02), y, 1, 1).data;
              const right = ctx.getImageData(Math.floor(cv.width * .98), y, 1, 1).data;
              return left[0] > left[2] && right[2] > right[0];
            }""",
            arg=expected, timeout=15000)
        measured = c.page.evaluate("""() => {
          const cv = document.querySelector('#pano-canvas');
          const r = cv.getBoundingClientRect();
          return { backing: cv.width / cv.height, display: r.width / r.height,
            width: r.width, height: r.height };
        }""")
        c.expect(abs(measured["backing"] - expected) <= 0.03
                 and abs(measured["display"] - expected) <= 0.05,
                 f"{label} panorama source aspect was not preserved: {measured}")
    c.assert_clean()


@check("web-horizon-profile-region", "web")
def web_horizon_profile_region(c):
    """The Horizon view has a readable graph, honest summaries, and bin details in its rail."""
    boot(c)
    tab(c, "horizon")
    c.page.click("#pano-blank")
    panel = c.page.locator(".horizon-profile-panel")
    panel.wait_for(state="visible", timeout=10000)
    c.expect(panel.bounding_box()["height"] >= 190,
             "the profile panel must reserve the required first-viewport height")
    c.expect_eq(c.page.locator(".horizon-profile-metrics > span").count(), 4,
                "profile summary must show coverage, profile value, landmarks, and confidence counts")
    c.expect(c.page.locator(".horizon-selected-bin").is_visible(),
             "selected-bin values belong in the settings rail")
    c.page.locator("#pano-profile").click(position={"x": 220, "y": 70})
    c.expect("Azimuth" in c.page.inner_text("#panoProfileBinSummary"),
             "clicking the graph must update the right-rail selected-bin values")
    metrics = c.page.inner_text(".horizon-profile-metrics")
    c.expect("highest obstruction" in metrics.lower(),
             f"obstacle mode must label the measured peak honestly: {metrics}")
    c.assert_clean()


@check("web-sky-band-no-diagonal-chord", "web")
def web_sky_band_no_chord(c):
    """Regression pin (0.6.13 follow-up): the sky-mode blocked region is drawn
    per contiguous azimuth run, so a gap is never bridged by a straight chord.
    Runs the SHARED geometry as served in the browser."""
    boot(c)
    res = c.page.evaluate(
        """async () => {
          const H = await import('/horizon.js');
          const BINS = 720;
          const fill = (from, to, v) => {
            const a = new Array(BINS).fill(90);
            for (let i = from; i < to; i++) a[i] = v;
            return a;
          };
          const mixed = {
            azOffset: 0, altBottom: 0, altTop: 90, fov: 360, paintMode: 'sky',
            blockAbove: false,
            profile: fill(0, 200, 10).map((v, i) => (i >= 300 && i < 500 ? 5 : v)),
            profileTop: fill(0, 200, 40).map((v, i) => (i >= 300 && i < 500 ? 90.5 : v)),
          };
          const polys = H.skyBandPolygons(mixed, 720, 56, 0, 40, 0, 360);
          // complement contract at three columns (band / gap / outside)
          const bands = H.visibleSkyBands(mixed, mixed.altBottom, 90);
          const selfIntersects = (pts) => {
            const n = pts.length;
            const cross = (o, a, b) => (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0]);
            const proper = (p1,p2,p3,p4) => {
              const d1=cross(p3,p4,p1), d2=cross(p3,p4,p2), d3=cross(p1,p2,p3), d4=cross(p1,p2,p4);
              return ((d1>0)!==(d2>0)) && ((d3>0)!==(d4>0));
            };
            for (let i=0;i<n;i++) for (let j=i+1;j<n;j++) {
              if ((i+1)%n===j || (j+1)%n===i) continue;
              if (proper(pts[i],pts[(i+1)%n],pts[j],pts[(j+1)%n])) return true;
            }
            return false;
          };
          return {
            polys: polys.length,
            intersects: polys.map(selfIntersects),
            gapVis: bands[250].vis,
            bandVis: bands[100].vis,
            openVis: bands[400].vis,
          };
        }""")
    c.expect(res["polys"] == 2, f"a blocked gap must split the band into 2 runs, got {res['polys']}")
    c.expect(not any(res["intersects"]), "a sky-band polygon self-intersects")
    c.expect(res["gapVis"] is None,
             f"the fully-blocked columns must have no visible band: {res['gapVis']}")
    c.expect(res["bandVis"] and abs(res["bandVis"][0] - 10) < 1e-6,
             f"the band column must start at the painted ceiling: {res['bandVis']}")
    c.expect(res["openVis"] and abs(res["openVis"][0] - 5) < 1e-6,
             f"the open-above run must start at its painted floor: {res['openVis']}")
    c.assert_clean()


@check("web-sun-moon-above-horizon", "web")
def web_sun_moon_above_horizon(c):
    """The Sun/Moon are only sky objects while the APPARENT altitude > 0:
    below at local midnight, above at local noon (never 'high all night')."""
    boot(c)
    res = c.page.evaluate(
        """async () => {
          const A = await import('/astro.js');
          const lat = 52.52, lon = 13.4;
          const at = (h) => {
            const dt = new Date(Date.UTC(2026, 7, 12, h, 0, 0));
            const jd = A.julianDay(dt);
            const s = A.sunAltAz(jd, lat, lon);   // [alt, az]
            const m = A.moonAltAz(jd, lat, lon);
            return { h, sun: s[0], sunVis: A.aboveHorizon(s[0]),
                     moon: m[0], moonVis: A.aboveHorizon(m[0]) };
          };
          return [0, 6, 12, 18].map(at);
        }""")
    by_h = {r["h"]: r for r in res}
    c.expect(by_h[0]["sun"] < 0 and not by_h[0]["sunVis"],
             f"the Sun must be below the horizon at 00:00 UTC: {by_h[0]}")
    c.expect(by_h[12]["sun"] > 0 and by_h[12]["sunVis"],
             f"the Sun must be above the horizon at 12:00 UTC: {by_h[12]}")
    for r in res:
        c.expect(r["moon"] == r["moon"] and abs(r["moon"]) < 90,
                 f"moon altitude must be finite: {r}")
        c.expect(r["sunVis"] == (r["sun"] > 0), "aboveHorizon must follow the sign")
    c.assert_clean()


@check("web-scrub-changes-time", "web")
def web_scrub_changes_time(c):
    boot(c)
    before = c.page.inner_text("#tb-scrub-time").strip()
    c.expect(before, "the scrub must show a time")
    c.page.evaluate(
        """() => { const s = document.querySelector('#tb-scrub');
             const mid = Math.round((Number(s.min) + Number(s.max)) / 2);
             const cur = Number(s.value);
             s.value = String(cur === mid ? Math.min(Number(s.max), cur + 90)
                                          : mid);
             s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    try:
        c.page.wait_for_function(
            """(prev) => (document.querySelector('#tb-scrub-time')?.textContent || '')
                          .trim() !== prev""", arg=before, timeout=8000)
    except Exception:
        raise AssertionError(f"scrubbing did not change the time from {before!r}")
    c.assert_clean()


@check("web-tonight-render-marker-converges", "web")
def web_tonight_render_marker_converges(c):
    """BUG-005: a check must be able to WAIT for the Tonight paint.

    The application debounces a scrub by 120 ms and then defers the ranking by
    another tick, so a check that sleeps a nominal 120 ms samples the PREVIOUS
    selection about as often as the new one (measured 25/26 full-sequence
    failures, passing in isolation, with stale rows in the artifact). The view
    now publishes `#view[data-ns-gen]`/`[data-ns-sel]`, and the wait demands the
    instant the REQUESTED scrub position names (not merely "a newer paint") - the
    boot sequence can leave a stale marker behind, and accepting any generation
    bump sampled that instead."""
    boot(c)
    range0 = c.page.evaluate("() => window.__ns_scrub_range()")
    c.expect(range0 and range0["end"] > range0["start"],
             f"the scrub range is empty: {range0}")
    first = c.wait_tonight_render()
    c.expect_eq(first["sel"], c.page.evaluate("() => window.__ns_scrub_sel()"),
                "the first Tonight paint did not name the selected instant")
    # move the slider a third of the way in and wait for THAT selection
    pos = c.page.eval_on_selector(
        "#tb-scrub", "el => Math.round((Number(el.min) + Number(el.max)) / 3)")
    want = c.page.evaluate("(v) => window.__ns_scrub_at(v)", pos)
    c.expect(want != first["sel"], "the scrub position did not move")
    c.page.eval_on_selector(
        "#tb-scrub", """(el, v) => { el.value = String(v);
            el.dispatchEvent(new Event('input', { bubbles: true })); }""", pos)
    later = c.wait_tonight_after(first["gen"], sel=want)
    c.expect(later["gen"] > first["gen"],
             f"the render marker did not advance: {first} -> {later}")
    c.expect_eq(later["sel"], want,
                "the marker converged on a different instant than the scrub "
                "position names (a stale paint satisfied the wait)")
    c.expect_eq(c.page.evaluate("() => window.__ns_scrub_sel()"), want,
                "the app's marker and its scrubber disagree about the selected "
                "instant")
    rows = c.page.eval_on_selector_all(
        "#tonight-rank-table tbody tr[data-tonight-id]",
        "els => els.map((e) => e.dataset.tonightId)")
    c.expect(rows, "the painted selection rendered no rows at all")
    c.assert_clean()


@check("web-workbench-only-render", "web")
def web_workbench_only_render(c):
    """Exercise the sole Workbench design and ensure retired theme controls stay absent."""
    boot(c)
    c.expect_eq(c.page.locator("html").get_attribute("data-theme"), "workbench",
                "the web planner must boot in Workbench")
    c.expect_eq(c.page.locator("#themeRow .theme-btn").count(), 0,
                "the retired theme picker must stay absent")
    for view in ("sky", "tonight"):
        c.page.click(f"#tabs a[data-v='{view}']")
        c.page.wait_for_timeout(250)
        accent = c.page.evaluate(
            """() => getComputedStyle(document.documentElement)
                       .getPropertyValue('--accent').trim()""")
        c.expect(accent, "Workbench must expose an accent token")
        if view == "sky":
            c.expect(c.page.locator("#sky").bounding_box(), "Sky View canvas missing")
        else:
            c.expect(c.page.locator("#tonightOverviewChart").bounding_box(), "Tonight chart missing")
    c.assert_clean()


@check("web-accessibility-essentials", "web")
def web_accessibility_essentials(c):
    """The real web DOM must expose tab semantics and text alternatives for
    the canvas charts after the dynamic views are rendered."""
    c.page.set_viewport_size({"width": 1366, "height": 900})
    boot(c)
    tabs = c.page.locator("#tabs a[role='tab']")
    c.expect_eq(tabs.count(), 4, "the planner tablist must expose four tabs")
    labels = [s.strip() for s in tabs.all_text_contents()]
    c.expect_eq(labels, ["Tonight", "Catalog", "Sky View", "Horizon"],
                "the web planner must use the four Direction B labels")
    c.expect_eq(c.page.locator("#tabs a[aria-selected='true']").count(), 1,
                "exactly one web planner tab must be selected")

    tab(c, "sky")
    c.page.wait_for_selector("#sky[role='img'][aria-describedby='skyChartSummary']")
    c.expect_eq(c.page.locator("#sky").get_attribute("tabindex"), "0",
                "the Sky View canvas must be keyboard focusable")
    c.expect(c.page.inner_text("#skyChartSummary"),
             "the web sky chart needs a non-empty screen-reader summary")

    tab(c, "catalog")
    c.page.wait_for_selector(".cat tbody tr[data-id]")
    c.page.locator("#cat-more summary").click()
    c.page.wait_for_selector("#cat-export-provenance")
    provenance = c.page.evaluate(
        """async () => { const r = await fetch('catalog-provenance.json?v=1');
             const j = await r.json();
             return { schema: j.schema_version,
                      source: j.objects?.ngc1976?.source,
                      aliases: j.objects?.ngc1976?.aliases?.length || 0 }; }""")
    c.expect(provenance["schema"] == 1 and provenance["source"] == "OpenNGC"
             and provenance["aliases"] > 0,
             f"the web provenance sidecar was not loaded: {provenance}")
    # The catalog may legitimately have no bundled photo for the first row;
    # the detail view then falls back to its optional Wikipedia thumbnail.
    c.allow_console_errors("expected missing optional bundled catalog photo")
    c.allow_failed_responses("expected missing optional bundled catalog photo")
    row = c.page.locator(".cat tbody tr[data-id]").first
    c.expect_eq(row.get_attribute("role"), "button",
                "catalog rows must be keyboard-openable controls")
    row.focus()
    c.page.keyboard.press("Enter")
    c.page.wait_for_selector("#graph[role='img'][aria-describedby='graphSummary']")
    c.page.locator("#modal .catalog-details > summary").click()
    c.expect("Catalog source" in c.page.inner_text("#modal")
             and "Designation history" in c.page.inner_text("#modal"),
             "the web detail must show catalog provenance")
    c.expect(c.page.inner_text("#graphSummary"),
             "the web altitude chart needs a non-empty screen-reader summary")
    c.expect_eq(c.page.locator("#modal .modal").get_attribute("role"), "complementary",
                "the wide target inspector must be exposed as a non-modal complementary panel")
    c.expect(c.page.locator("#modal .modal").get_attribute("aria-modal") is None,
             "the wide target inspector must not claim to be modal")
    c.page.evaluate("""() => {
        const panel = document.querySelector('#modal .modal');
        const stops = [...panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
          .filter((el) => el.getClientRects().length > 0);
        stops.at(-1)?.focus();
      }""")
    c.page.keyboard.press("Tab")
    c.expect(not c.page.evaluate("() => document.querySelector('#modal .modal')?.contains(document.activeElement)"),
             "Tab must continue out of the wide, docked inspector")
    c.page.keyboard.press("Escape")
    c.expect(c.page.evaluate("() => document.activeElement?.matches('.cat tbody tr[data-id]')"),
             "closing the web detail dialog must restore focus to its catalog row")

    c.page.set_viewport_size({"width": 900, "height": 900})
    c.page.wait_for_timeout(180)
    row.focus()
    c.page.keyboard.press("Enter")
    c.page.wait_for_selector("#modal:not([hidden]) .modal[role='dialog'][aria-modal='true']")
    c.page.evaluate("""() => {
        const panel = document.querySelector('#modal .modal');
        const stops = [...panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
          .filter((el) => el.getClientRects().length > 0);
        stops.at(-1)?.focus();
      }""")
    c.page.keyboard.press("Tab")
    c.expect(c.page.evaluate("() => document.querySelector('#modal .modal')?.contains(document.activeElement)"),
             "Tab must wrap within the compact modal inspector")
    c.page.keyboard.press("Escape")
    c.expect(c.page.evaluate("() => document.activeElement?.matches('.cat tbody tr[data-id]')"),
             "closing the compact inspector must restore focus to its source row")

    tab(c, "tonight")
    target_row = c.page.locator(READY).first
    target_row.focus()
    target_row_id = target_row.get_attribute("data-tonight-id")
    c.page.keyboard.press("Enter")
    c.page.wait_for_selector("#modal:not([hidden]) .modal")
    c.page.keyboard.press("Escape")
    c.expect(c.page.evaluate("(id) => document.activeElement?.matches(`#tonight-rank-table tbody tr[data-tonight-id=\"${id}\"]`)", target_row_id),
             "closing a Tonight detail must restore focus to its ranked target row")

    tab(c, "horizon")
    c.page.wait_for_selector("#pano-blank")
    c.page.click("#pano-blank")
    c.page.wait_for_selector("#pano-canvas[aria-describedby='panoChartSummary']")
    for selector in ("#ed-prov-source", "#ed-landmark-add",
                     "#ed-profile-apply", "#ed-profile-compare"):
        # The metadata groups are deliberately collapsed by default; assert
        # that the controls exist without requiring the user to expand every
        # progressive-disclosure section in this accessibility smoke.
        c.page.wait_for_selector(selector, state="attached")
    c.expect(c.page.inner_text("#panoChartSummary"),
             "the web horizon editor needs a text alternative")
    c.assert_clean()


@check("web-saved-targets-persist-and-prune", "web")
def web_saved_targets_persist_and_prune(c):
    """Saved catalog targets persist in local prefs and are pruned after the
    actual catalog is loaded; the saved-only filter uses those same IDs."""
    c.page.set_viewport_size({"width": 1366, "height": 900})
    boot(c)
    tab(c, "catalog")
    row = c.page.locator(".cat tbody tr[data-id='ngc224']").first
    row.wait_for(state="visible", timeout=30000)
    row.locator("[data-save-target]").click()
    c.page.wait_for_function(
        "() => document.querySelector('.cat tbody [data-save-target]')?.classList.contains('on')")
    stored = c.page.evaluate("""async () => {
      const Hor = await import('./horizon.js');
      return Hor.getPrefs().savedTargetIds;
    }""")
    c.expect(stored == ["ngc224"], f"the selected catalog ID was not saved: {stored!r}")

    c.page.locator("#cat-more summary").click()
    c.page.locator("#cat-saved").check()
    c.page.wait_for_function(
        "() => document.querySelectorAll('.cat tbody tr[data-id]').length === 1")
    c.expect_eq(c.page.locator(".cat tbody tr[data-id]").get_attribute("data-id"),
                "ngc224", "saved-only filtering must use the persisted catalog ID")

    c.page.evaluate("""async () => {
      const Hor = await import('./horizon.js');
      Hor.setPrefs({ savedTargetIds: ['ngc224', 'deleted-target'] });
    }""")
    c.page.reload(wait_until="domcontentloaded")
    boot(c)
    cleaned = c.page.evaluate("""async () => {
      const Hor = await import('./horizon.js');
      return Hor.getPrefs().savedTargetIds;
    }""")
    c.expect(cleaned == ["ngc224"],
             f"an ID absent from the loaded catalog should be pruned: {cleaned!r}")
    c.assert_clean()


@check("web-docked-inspector-fit-and-tonight-key", "web")
def web_docked_inspector_fit_and_tonight_key(c):
    """The docked target details use a vertical, scrollable rail at both wide
    planner widths and all supported themes; Tonight exposes the chart states
    and guides as a wrapping, theme-token-backed key."""
    boot(c)
    c.allow_console_errors("optional target images may be absent from the bundled catalog")
    c.allow_failed_responses("optional target images may be absent from the bundled catalog")
    # Use the same deterministic site and session as the visual matrix so M31
    # appears as a real Tonight pick rather than relying on the gate's August
    # clock and generic Berlin seed.
    c.page.evaluate("""async () => {
      const WS = await import('./webstore.js?v=41');
      const Hor = await import('./horizon.js?v=68');
      await WS.saveSiteRec({ id: 'browser-direction-b-black-forest',
        name: 'Black Forest', lat: 48, lon: 8.2, elev: 900, tz: 'Europe/Berlin' });
      Hor.setPrefs({ activeSiteId: 'browser-direction-b-black-forest',
        minAlt: 25, viewFullDay: false });
    }""")
    c.page.reload(wait_until="domcontentloaded")
    c.page.wait_for_selector(READY, timeout=60000)
    c.page.locator("#tb-date").fill("2026-09-26")
    c.page.locator("#tb-date").dispatch_event("change")
    tab(c, "sky")
    c.page.wait_for_selector("#sky-time", state="visible")
    c.page.locator("#sky-time").fill("2026-09-26T22:36")
    c.page.locator("#sky-time").dispatch_event("input")
    c.page.wait_for_function(
        "() => document.querySelector('#sky-time')?.value === '2026-09-26T22:36'",
        timeout=15000)
    tab(c, "tonight")
    c.wait_tonight_render()

    def choose_theme(theme):
        c.expect_eq(theme, "workbench", "only Workbench is supported")
        c.expect_eq(c.page.locator("html").get_attribute("data-theme"), "workbench",
                    "the retired theme system must remain absent")

    def assert_dock_header_visible(context, *, check_tab_offset=False):
        state = c.page.evaluate("""() => {
          const title = document.querySelector('#m-title');
          const close = document.querySelector('#m-close');
          const visibleChrome = ['.topbar', '#tabs'].map((selector) => {
            const el = document.querySelector(selector);
            if (!el || !el.getClientRects().length) return null;
            const r = el.getBoundingClientRect();
            return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth
              ? r : null;
          }).filter(Boolean);
          const inspect = (el) => {
            if (!el) return { visible: false, clear: false, hit: false, rect: null };
            const r = el.getBoundingClientRect();
            const visible = r.width > 0 && r.height > 0 && r.left >= -1 &&
              r.right <= innerWidth + 1 && r.top >= -1 && r.bottom <= innerHeight + 1;
            const clear = visibleChrome.every((chrome) => {
              const x = Math.max(0, Math.min(r.right, chrome.right) - Math.max(r.left, chrome.left));
              const y = Math.max(0, Math.min(r.bottom, chrome.bottom) - Math.max(r.top, chrome.top));
              return x <= 1 || y <= 1;
            });
            const hit = visible && (() => {
              const target = document.elementFromPoint(r.left + r.width / 2,
                r.top + r.height / 2);
              return !!target && el.contains(target);
            })();
            return { visible, clear, hit, rect: { top: r.top, bottom: r.bottom,
              left: r.left, right: r.right } };
          };
          const tabs = document.querySelector('#tabs');
          const tabsRect = tabs?.getBoundingClientRect();
          const dockTop = parseFloat(getComputedStyle(document.documentElement)
            .getPropertyValue('--detail-top'));
          const expectedTop = Math.ceil(Math.max(0, ...visibleChrome.map((r) => r.bottom)));
          return { title: inspect(title), close: inspect(close), dockTop, expectedTop,
            tabsBottom: tabsRect?.bottom,
            tabOffsetMatches: Number.isFinite(dockTop) &&
              Math.abs(dockTop - expectedTop) <= 1 && Math.abs(dockTop - tabsRect.bottom) <= 1 };
        }""")
        for name in ("title", "close"):
            c.expect(state[name]["visible"] and state[name]["clear"] and state[name]["hit"],
                     f"{context}: docked detail {name} must be visible, unobscured by chrome, and hit-testable: {state}")
        if check_tab_offset:
            c.expect(state["tabOffsetMatches"],
                     f"{context}: dock top must track the visible tab bottom within 1px: {state}")

    def exercise_deep_tonight_detail(theme, width):
        c.page.evaluate("""() => {
          const target = document.querySelector("#tonight-rank-table tbody tr[data-tonight-id='ngc224']");
          if (!target) throw new Error('Tonight is missing the M31 target row');
          target.scrollIntoView({ block: 'center' });
        }""")
        c.page.wait_for_function("""() => {
          const tabs = document.querySelector('#tabs');
          return tabs && tabs.getBoundingClientRect().bottom <= 0;
        }""", timeout=10000)
        tabs_bottom = c.page.evaluate("() => document.querySelector('#tabs').getBoundingClientRect().bottom")
        c.expect(tabs_bottom <= 0,
                 f"{theme} {width}px: the Tonight M31 regression must begin with tabs above the viewport, got {tabs_bottom}")
        c.page.locator("#tonight-rank-table tbody tr[data-tonight-id='ngc224']:visible").first.click()
        c.page.wait_for_selector("#modal.detail-docked > .modal", state="visible")
        c.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert_dock_header_visible(f"{theme} {width}px deep Tonight row")

        c.page.evaluate("window.scrollTo(0, 0)")
        c.page.wait_for_function("""() => {
          const chrome = ['.topbar', '#tabs'].map((selector) => {
            const el = document.querySelector(selector);
            if (!el || !el.getClientRects().length) return 0;
            const r = el.getBoundingClientRect();
            return r.bottom > 0 && r.top < innerHeight ? r.bottom : 0;
          });
          const expected = Math.ceil(Math.max(0, ...chrome));
          const actual = parseFloat(getComputedStyle(document.documentElement)
            .getPropertyValue('--detail-top'));
          return Number.isFinite(actual) && Math.abs(actual - expected) <= 1;
        }""", timeout=10000)
        c.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert_dock_header_visible(f"{theme} {width}px after returning to page top",
                                  check_tab_offset=True)
        c.page.locator("#m-close").click()
        c.page.wait_for_function("() => document.querySelector('#modal')?.hidden")

    def assert_night_structure(theme, width):
        state = c.page.evaluate("""() => {
          const brief = document.querySelector('.night-brief');
          const plot = document.querySelector('#tonightOverviewChart');
          const scrub = document.querySelector('.tonight-local-scrub');
          const table = document.querySelector('#tonight-rank-table');
          const rect = el => el?.getBoundingClientRect();
          const b = rect(brief), p = rect(plot), s = rect(scrub), t = rect(table);
          const rows = [...(table?.querySelectorAll('tbody tr[data-tonight-id]') || [])];
          const fullRows = rows.filter(row => {
            const r = row.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.right <= innerWidth + 1 && r.left >= -1;
          }).length;
          return { brief: !!b && b.width > 0, plot: !!p && p.width >= 250 && p.height > 0,
            scrub: !!s && s.width > 0, table: !!t && t.width > 0,
            tableScroll: table?.scrollWidth - table?.clientWidth,
            pageOverflow: document.documentElement.scrollWidth - innerWidth,
            overflows: [...document.querySelectorAll('body *')].map(el => {
              const r = el.getBoundingClientRect();
              return { id: el.id, cls: typeof el.className === 'string' ? el.className : '',
                tag: el.tagName, left: Math.round(r.left), right: Math.round(r.right),
                width: Math.round(r.width), scroll: el.scrollWidth, client: el.clientWidth };
            }).filter(x => x.right > innerWidth + 1 || x.left < -1 || x.scroll > x.client + 1)
              .sort((a,b) => (b.right-innerWidth) - (a.right-innerWidth)).slice(0,12),
            rows: rows.length, fullRows };
        }""")
        c.expect(state["brief"] and state["plot"] and state["scrub"] and
                 state["table"] and state["rows"] >= 3 and state["fullRows"] >= 3,
                 f"{theme} {width}px: Tonight brief, altitude plot, scrub, and three ranked rows must render: {state}")
        c.expect(state["tableScroll"] >= 0 and state["pageOverflow"] <= 1,
                 f"{theme} {width}px: Tonight ranking may scroll internally but must not widen the page: {state}")

    def assert_fit(context, *, docked):
        state = c.page.evaluate("""() => {
        const outer = document.querySelector('#modal');
        const panel = outer.querySelector('.modal');
        const hero = panel.querySelector('.detail-hero');
        const image = panel.querySelector('#objimg-wrap');
        const chart = panel.querySelector('#graph');
        const title = panel.querySelector('#m-title');
        const close = panel.querySelector('#m-close');
        const visibility = panel.querySelector('#d-obs');
        const details = panel.querySelector('details.catalog-details');
        const collapsedByDefault = !details.open;
        details.open = true;
        const disclosure = details.querySelector('summary');
        const metaValue = details.querySelector('.meta dd');
        const legend = panel.querySelector('.chart-legend');
        const action = panel.querySelector('#m-save-target');
        const favorite = panel.querySelector('#m-session-fav');
        const panelRect = panel.getBoundingClientRect();
        const scrollOwner = outer.classList.contains('detail-docked') ? panel : outer;
        const dpr = window.devicePixelRatio || 1;
        const rects = [title, close, favorite, hero, image, visibility, chart,
          disclosure, metaValue, legend, action].map(el => el?.getBoundingClientRect());
        const horizontalBounds = rects.every(r => r && r.width > 0 &&
          r.left >= panelRect.left - 1 && r.right <= panelRect.right + 1 &&
          r.left >= -1 && r.right <= innerWidth + 1);
        const inPanelViewport = el => {
          const r = el.getBoundingClientRect();
          const currentPanel = panel.getBoundingClientRect();
          const top = Math.max(currentPanel.top, 0);
          const bottom = Math.min(currentPanel.bottom, innerHeight);
          return r.top >= top - 1 && r.bottom <= bottom + 1;
        };
        const isTopmost = el => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2,
            r.top + r.height / 2);
          return !!hit && el.contains(hit);
        };
        const reach = el => {
          const r = el.getBoundingClientRect();
          const ownerRect = scrollOwner.getBoundingClientRect();
          if (r.top < ownerRect.top) scrollOwner.scrollTop += r.top - ownerRect.top;
          else if (r.bottom > ownerRect.bottom) scrollOwner.scrollTop += r.bottom - ownerRect.bottom;
          return inPanelViewport(el);
        };
        scrollOwner.scrollTop = 0;
        const closeInitiallyVisible = inPanelViewport(close) && isTopmost(close);
        const dialogHeaderVisible = isTopmost(title);
        const reachability = Object.fromEntries([
          ['title', title], ['close', close], ['favorite', favorite], ['hero', hero],
          ['image', image], ['visibility', visibility], ['chart', chart],
          ['details', disclosure], ['metadata', metaValue], ['legend', legend], ['action', action],
        ].map(([name, el]) => [name, reach(el)]));
        const contentReachable = Object.values(reachability).every(Boolean);
        const imageHeight = image.getBoundingClientRect().height;
        scrollOwner.scrollTop = 0;
        return {
          docked: outer.classList.contains('detail-docked'),
          role: panel.getAttribute('role'), modal: panel.getAttribute('aria-modal'),
          panelWidth: panel.clientWidth, panelOverflow: panel.scrollWidth - panel.clientWidth,
          contentWidth: panel.scrollWidth, contentOverflow: panel.scrollWidth - panel.clientWidth,
          heroWidth: hero.getBoundingClientRect().width,
          chartWidth: chart.getBoundingClientRect().width,
          heroBeforeChart: hero.getBoundingClientRect().top < chart.getBoundingClientRect().top,
          chartBeforeMetadata: chart.getBoundingClientRect().top < details.getBoundingClientRect().top,
          collapsedByDefault, imageHeight, horizontalBounds, closeInitiallyVisible,
          dialogHeaderVisible, contentReachable, reachability,
          canvasWidth: chart.width, expectedCanvasWidth: Math.round(chart.clientWidth * dpr),
          pageOverflow: document.documentElement.scrollWidth - innerWidth
          };
        }""")
        c.expect_eq(state["docked"], docked, f"{context}: inspector presentation changed unexpectedly")
        if docked:
            c.expect_eq(state["role"], "complementary",
                        f"{context}: docked detail must remain a complementary region")
            c.expect(state["modal"] is None,
                     f"{context}: docked detail must not claim aria-modal")
        else:
            c.expect_eq(state["role"], "dialog",
                        f"{context}: compact detail must remain a dialog")
            c.expect_eq(state["modal"], "true",
                        f"{context}: compact detail must keep modal semantics")
        c.expect(state["panelWidth"] > 0 and state["panelOverflow"] <= 1 and
                 state["contentOverflow"] <= 1 and state["pageOverflow"] <= 1,
                 f"{context}: detail must not create horizontal overflow: {state}")
        c.expect(state["heroBeforeChart"] and state["chartBeforeMetadata"] and
                 state["collapsedByDefault"] and state["heroWidth"] > 0 and
                 state["chartWidth"] > 0 and state["contentReachable"],
                 f"{context}: target facts, chart, and collapsed metadata must follow the required order and remain reachable: {state['reachability']}")
        c.expect(state["horizontalBounds"] and state["closeInitiallyVisible"] and
                 state["dialogHeaderVisible"],
                 f"{context}: detail content must stay inside its panel with a reachable title and close control: {state}")
        if docked:
            c.expect(state["imageHeight"] <= 150,
                     f"{context}: docked target image should stay compact: {state}")
        c.expect_eq(state["canvasWidth"], state["expectedCanvasWidth"],
                    f"{context}: detail graph must be drawn at its current width")

    def open_detail_in_view(view):
        if view == "tonight":
            target = c.page.locator("#tonight-rank-table tbody tr[data-tonight-id]:visible").first
            target.wait_for(state="visible", timeout=20000)
            target.click()
        elif view == "catalog":
            row = c.page.locator("table.cat tbody tr[data-id]").first
            row.wait_for(state="visible", timeout=20000)
            row.click()
        else:
            c.page.wait_for_selector("#sky[tabindex='0']", state="visible")
            target = c.page.locator("#sky-visible-now [data-sky-target]").first
            target.wait_for(state="visible", timeout=15000)
            target.click()
            c.page.locator("#sky-selected-detail").click()
        try:
            c.page.wait_for_selector("#modal:not([hidden]) .modal", state="visible", timeout=20000)
        except Exception as exc:
            raise AssertionError(f"could not open target detail from the {view} view: {exc}") from exc

    for theme in ("workbench",):
        choose_theme(theme)
        for width, height in ((1672, 941), (1240, 900)):
            c.page.set_viewport_size({"width": width, "height": height})
            tab(c, "tonight")
            assert_night_structure(theme, width)
            exercise_deep_tonight_detail(theme, width)
            for view in ("tonight", "catalog", "sky"):
                tab(c, view)
                if view == "tonight":
                    assert_night_structure(theme, width)
                open_detail_in_view(view)
                c.page.wait_for_selector("#modal.detail-docked > .modal", state="visible")
                assert_fit(f"{theme} {width}px {view}", docked=True)
                if view == "catalog":
                    bounds = c.page.evaluate("""() => {
                      const rail = document.querySelector('.cat-filter-rail').getBoundingClientRect();
                      const search = document.querySelector('#cat-q').getBoundingClientRect();
                      const table = document.querySelector('.catwrap');
                      return { railLeft: rail.left, railRight: rail.right,
                        searchLeft: search.left, searchRight: search.right,
                        tableWidth: table.clientWidth, tableContentWidth: table.scrollWidth,
                        pageOverflow: document.documentElement.scrollWidth - innerWidth };
                    }""")
                    c.expect(bounds["searchLeft"] >= bounds["railLeft"] - 1 and
                             bounds["searchRight"] <= bounds["railRight"] + 1 and
                             bounds["tableContentWidth"] >= bounds["tableWidth"] and
                             bounds["pageOverflow"] <= 1,
                             f"{theme} {width}px: Catalog search must fit the rail and table must scroll independently: {bounds}")
                c.page.locator("#m-close").click()
                c.page.wait_for_function("() => document.querySelector('#modal')?.hidden")

        c.page.set_viewport_size({"width": 900, "height": 900})
        tab(c, "tonight")
        assert_night_structure(theme, 900)
        open_detail_in_view("tonight")
        c.page.wait_for_selector("#modal:not([hidden]) .modal[role='dialog'][aria-modal='true']",
                                 state="visible")
        assert_fit(f"{theme} 900px compact detail", docked=False)
        c.page.locator("#m-close").click()
        c.page.wait_for_function("() => document.querySelector('#modal')?.hidden")

    for theme in ("workbench",):
      choose_theme(theme)
      for width, height in ((640, 900), (360, 800)):
        c.page.set_viewport_size({"width": width, "height": height})
        tab(c, "tonight")
        c.page.wait_for_selector("#tonightOverviewChart")
        c.page.wait_for_function("() => document.querySelectorAll('.tonight-chart-key [role=listitem]').length === 6")
        key = c.page.evaluate("""() => {
      const root = document.documentElement;
      const styles = (sel) => getComputedStyle(document.querySelector(sel));
      const probe = (value) => {
        const el = document.createElement('i'); el.style.backgroundColor = value;
        document.body.append(el); const color = getComputedStyle(el).backgroundColor; el.remove();
        return color;
      };
      const box = document.querySelector('.tonight-chart-key');
      const items = [...box.querySelectorAll('[role=listitem]')];
      const visible = items.every((el) => getComputedStyle(el).display !== 'none' && el.getClientRects().length);
      return {
        labels: items.map((el) => el.textContent.trim()), visible,
        wraps: getComputedStyle(box).flexWrap === 'wrap',
        width: box.clientWidth, overflow: box.scrollWidth - box.clientWidth,
        blocked: styles('.key-blocked').backgroundColor === probe(getComputedStyle(root).getPropertyValue('--viz-bad').trim()),
        low: styles('.key-below-min').backgroundColor === probe(getComputedStyle(root).getPropertyValue('--viz-warn').trim()),
        below: styles('.key-below-horizon').backgroundColor === probe(getComputedStyle(root).getPropertyValue('--viz-below').trim()),
        twilight: styles('.key-twilight').backgroundColor === probe(getComputedStyle(root).getPropertyValue('--viz-twilight-3').trim()),
        minimum: styles('.key-minimum').borderTopColor === probe(getComputedStyle(root).getPropertyValue('--viz-min-stroke').trim()),
        selected: styles('.key-selected-time').borderLeftColor === probe(getComputedStyle(root).getPropertyValue('--viz-target-selected').trim()),
        pageOverflow: document.documentElement.scrollWidth - innerWidth
      };
        }""")
        labels = " ".join(key["labels"])
        for phrase in ("Horizon or ceiling blocked", "Below minimum altitude", "Below horizon",
                       "Twilight", "Minimum-altitude guide", "Selected time"):
            c.expect(phrase in labels,
                     f"{theme} {width}px Tonight's visible chart key is missing {phrase!r}: {key}")
        for token_backed in ("blocked", "low", "below", "twilight", "minimum", "selected"):
            c.expect(key[token_backed],
                     f"{theme} {width}px Tonight chart key must use {token_backed} theme semantics: {key}")
        c.expect(key["visible"] and key["wraps"] and key["overflow"] <= 1 and key["pageOverflow"] <= 1,
                 f"{theme} {width}px Tonight chart key must wrap without horizontal overflow: {key}")
    c.assert_clean()


@check("web-sky-canvas-container-resize", "web")
def web_sky_canvas_container_resize(c):
    """The Sky View's resize observer follows its chart container even when
    the browser viewport itself stays the same size."""
    c.page.set_viewport_size({"width": 1366, "height": 900})
    boot(c)
    tab(c, "sky")
    c.page.wait_for_selector("#sky[tabindex='0']")
    before = c.page.evaluate("""() => {
      const cv = document.querySelector('#sky');
      return { width: cv.clientWidth, backing: cv.width,
        dpr: window.devicePixelRatio || 1 };
    }""")
    c.page.evaluate("""() => {
      const box = document.querySelector('.sky-canvas-box');
      const cv = document.querySelector('#sky');
      box.style.setProperty('width', `${Math.max(160, cv.clientWidth - 100)}px`, 'important');
      box.style.setProperty('justify-self', 'start', 'important');
    }""")
    c.page.wait_for_function("""(old) => {
      const cv = document.querySelector('#sky');
      return cv.clientWidth < old &&
        cv.width === Math.round(cv.clientWidth * (window.devicePixelRatio || 1));
    }""", arg=before["width"], timeout=10000)
    after = c.page.evaluate("""() => ({
      width: document.querySelector('#sky').clientWidth,
      backing: document.querySelector('#sky').width,
      dpr: window.devicePixelRatio || 1 })""")
    c.expect(after["backing"] == round(after["width"] * after["dpr"]),
             f"the chart backing store must track its resized container: {after}")
    c.page.evaluate("""() => {
      const box = document.querySelector('.sky-canvas-box');
      box.style.removeProperty('width'); box.style.removeProperty('justify-self');
    }""")
    c.page.wait_for_timeout(200)
    c.assert_clean()


@check("web-sky-time-single-notification", "web")
def web_sky_time_single_notification(c):
    """A range change syncs every scrubber and repaints Tonight once.

    `clampSkyTime()` must announce even when the selected instant is already
    valid, because the range can still have changed. This check makes that
    notification count explicit while retaining the BUG-005 marker proof for
    the one repaint it schedules.
    """
    boot(c)
    first = c.wait_tonight_render()
    before = c.page.evaluate("() => window.__ns_scrub_range()")
    c.expect(not c.page.is_checked("#viewFullDay"),
             "fresh web storage must start with View full day disabled")
    c.page.evaluate(
        """() => {
            window.__ns_sky_time_events = 0;
            window.addEventListener('sky:time', () => {
              window.__ns_sky_time_events += 1;
            });
        }""")
    c.page.eval_on_selector(
        "#viewFullDay", """(el) => {
            el.checked = true;
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
    after = c.page.evaluate("() => window.__ns_scrub_range()")
    c.expect(after["start"] <= before["start"] and after["end"] >= before["end"],
             f"full-day range did not contain the former night range: {before} -> {after}")
    scrub = c.page.eval_on_selector(
        "#tb-scrub", """(el) => ({ min: Number(el.min), max: Number(el.max),
            value: Number(el.value) })""")
    expected_max = max(1, round((after["end"] - after["start"]) / 60000))
    c.expect_eq(scrub["min"], 0, "the topbar scrub did not reset its lower bound")
    c.expect_eq(scrub["max"], expected_max,
                "the topbar scrub did not adopt the full-day range")
    c.expect(0 <= scrub["value"] <= scrub["max"],
             f"the selected scrub value escaped its new range: {scrub}")
    want = c.page.evaluate("() => window.__ns_scrub_sel()")
    later = c.wait_tonight_after(first["gen"], sel=want)
    c.expect_eq(later["sel"], want,
                "the one full-day notification did not repaint Tonight for its selection")
    c.expect_eq(c.page.evaluate("() => window.__ns_sky_time_events"), 1,
                "a single full-day change emitted more than one sky:time notification")
    c.assert_clean()


@check("web-mag-slider-changes-sky", "web")
def web_mag_slider_changes_sky(c):
    boot(c)
    tab(c, "sky")
    c.page.wait_for_selector("#sky", state="visible", timeout=30000)
    c.page.locator(".sky-controls-tray summary").click()
    c.page.wait_for_timeout(400)
    val0 = c.page.inner_text("#sky-mag-val").strip()
    dig0 = canvas_digest(c, "#sky")
    c.page.evaluate(
        """() => { const s = document.querySelector('#sky-mag');
             s.value = s.max; s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    c.page.wait_for_timeout(500)
    val1 = c.page.inner_text("#sky-mag-val").strip()
    dig1 = canvas_digest(c, "#sky")
    c.expect(val1 != val0, f"the magnitude readout must change ({val0!r} -> {val1!r})")
    c.expect(dig1 != dig0, "changing the magnitude limit must redraw the sky view")
    c.assert_clean()


def crop_drag(c, x0: float, x1: float):
    """Drag the crop band (fractions of the editor canvas width) at mid height."""
    box = c.page.locator("#pano-canvas").bounding_box()
    c.expect(box and box["width"] > 20, "pano canvas must have a size")
    y = box["y"] + box["height"] * 0.5
    sx = box["x"] + box["width"] * x0
    ex = box["x"] + box["width"] * x1
    c.page.mouse.move(sx, y)
    c.page.mouse.down()
    for i in range(1, 9):
        c.page.mouse.move(sx + (ex - sx) * i / 8, y)
    c.page.mouse.up()
    c.page.wait_for_timeout(150)


def active_profile(c):
    st = pano_state(c)
    if not st or not st.get("panos"):
        return None
    p = next((p for p in st["panos"] if p.get("id") == st.get("activeId")),
             st["panos"][0])
    return p.get("profile")


@check("web-pano-crop-recalibrate", "web")
def web_pano_crop_recalibrate(c):
    """Non-destructive kept-band crop: it shrinks the FOV, moves the left-edge
    azimuth and transforms the strokes (the photo and paint survive - unlike the
    desktop, which re-encodes and wipes). Crop both sides of a partial-FOV frame,
    save, reload, and confirm the blocked profile is byte-identical."""
    boot(c)
    open_editor(c)
    paint(c)
    # a partial 180 deg frame so the crop is a real recalibration
    c.page.evaluate(
        """() => { const s = document.querySelector('#ed-fov');
             s.value = '180'; s.dispatchEvent(new Event('input', { bubbles: true }));
             s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    c.page.wait_for_timeout(200)

    c.page.click("#ed-crop")
    crop_drag(c, 0.25, 0.75)                 # keep the middle half
    c.page.click("#ed-crop-apply")
    c.page.wait_for_timeout(250)
    # kept half of a 180 deg frame -> 90 deg; az = 0 + 0.25*180 = 45
    c.expect_in("90\u00B0", c.page.inner_text("#ed-fov-v"),
                "the crop must keep half the FOV")
    c.expect_in("45\u00B0", c.page.inner_text("#ed-az-v"),
                "the crop must move the left-edge azimuth")
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "a crop must dirty the editor")
    c.expect(not c.page.get_attribute("#ed-undo", "disabled"),
             "a crop must be one undoable history entry")

    # undo restores the pre-crop calibration; redo reapplies the crop
    c.page.click("#ed-undo")
    c.page.wait_for_function(
        """() => (document.querySelector('#ed-fov-v')?.textContent || '').includes('180')""",
        timeout=8000)
    c.page.click("#ed-redo")
    c.page.wait_for_function(
        """() => (document.querySelector('#ed-fov-v')?.textContent || '').includes('90')""",
        timeout=8000)

    c.page.click("#ed-save")
    c.page.wait_for_function(
        """() => (document.querySelector('#ed-status')?.textContent || '').includes('Saved')""",
        timeout=10000)
    c.expect(active_strokes(c) > 0,
             "the cropped strokes must survive (non-destructive crop)")
    saved = active_profile(c)
    c.expect(saved and len(saved) == 720, "the cropped profile must be saved")

    # reload: the cropped frame and its blocked profile survive untouched
    c.goto("/")
    tab(c, "horizon")
    c.page.wait_for_selector("#pano-list li[data-id]", timeout=30000)
    c.page.click("#pano-list li[data-id]")
    c.page.wait_for_selector("#pano-canvas", timeout=30000)
    c.expect_in("90\u00B0", c.page.inner_text("#ed-fov-v"),
                "reload must keep the cropped FOV")
    c.expect(active_profile(c) == saved,
             "the blocked profile must match the pre-save geometry")
    c.assert_clean()


@check("web-pref-change-keeps-unsaved-paint", "web")
def web_pref_change_keeps_unsaved_paint(c):
    """WE-05 (0.6.32 review): a preference change while the editor is open must
    not clear the unsaved state.

    `renderHorizon()` used to call `newHistory(p)` on EVERY render, which makes
    the current - including unsaved - state the "saved" baseline. So any
    `refreshView()` with the editor open (a min-alt, site, date, rig, Bortle,
    filter, size-unit, moon-aware or timezone change) flipped the chip to
    "Saved", made `horizonUnsaved()` false, and silently disarmed BOTH the
    `flushHorizonEdits()` call on navigating away and the `beforeunload` guard:
    the paint was lost on reload. The baseline must survive a re-render."""
    boot(c)
    open_editor(c)
    paint(c)
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "painting must mark the editor dirty")

    # trigger 1: the min-alt slider - it only re-renders on 'change' (an 'input'
    # tick just moves the label; the app deliberately avoids rebuilding the view
    # under a dragging pointer), so the event MATTERS here.
    c.page.evaluate("""() => { const s = document.querySelector('#tb-minalt');
        s.value = String(Number(s.value) + 3);
        s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    c.page.wait_for_timeout(700)
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "a min-alt change must not clear the unsaved state (WE-05)")

    # trigger 2: a DATE change - a different refreshView path
    c.page.evaluate("""() => { const d = document.querySelector('#tb-date');
        const dt = new Date(d.value + 'T12:00:00Z');
        dt.setUTCDate(dt.getUTCDate() + 1);
        d.value = dt.toISOString().slice(0, 10);
        d.dispatchEvent(new Event('change', { bubbles: true })); }""")
    c.page.wait_for_timeout(800)
    c.expect_in("Unsaved", c.page.inner_text("#ed-status"),
                "a date change must not clear the unsaved state (WE-05)")

    # and the flush path is armed again: leaving the tab persists the paint
    paint(c, 0.66, 0.80, 0.35)
    before = active_strokes(c)
    tab(c, "tonight")
    c.page.wait_for_timeout(400)
    c.expect(active_strokes(c) > before,
             "leaving the editor must FLUSH the unsaved paint (the guard was "
             "disarmed by the bug)")
    c.assert_clean()


@check("web-search-indexes-the-designation-blob", "web")
def web_search_indexes_the_designation_blob(c):
    """CD-02 (0.6.32 review): the web search must index the catalogue's own
    designation blob (`search`), not only ids/names/type/constellation.

    25 rows carry a common name ONLY in that column - `tarantula` (ngc2070),
    `coathanger` (cl399), `silver coin` (ngc253) - so they were findable on the
    desktop and invisible in the web search. The column now ships in the payload
    (`web/build_web.py::COLS`) and is indexed by `core/catalog.js::searchBlob`;
    before that change the web returned [] for these three terms."""
    boot(c)
    tab(c, "catalog")
    c.page.wait_for_selector("#cat-q", timeout=30000)
    for term, oid in (("tarantula", "ngc2070"),
                      ("coathanger", "cl399"),
                      ("silver coin", "ngc253")):
        c.page.fill("#cat-q", term)
        c.page.dispatch_event("#cat-q", "input")
        c.page.wait_for_timeout(400)
        found = c.page.eval_on_selector_all(
            "main table tbody tr[data-id]", "els => els.map((e) => e.dataset.id)")
        c.expect(oid in found,
                 f"searching {term!r} must find {oid} through the designation "
                 f"blob (got {found[:6]})")
    c.page.fill("#cat-q", "")
    c.page.dispatch_event("#cat-q", "input")
    c.assert_clean()


@check("web-tonight-pool-includes-unmagnituded-standard-rows", "web")
def web_tonight_pool_includes_unmagnituded_standard_rows(c):
    """The Tonight candidate pool is core/visibility.js::inTonightPool.

    Unknown-magnitude standard objects must qualify for the shared pool. The
    Direction B ranked table deliberately shows only its top 16 rows, so the
    UI portion scans the night and verifies at least one such row is rendered;
    the core predicate is checked directly for all four representative IDs."""
    boot(c)
    bounds = c.page.eval_on_selector(
        "#tb-scrub", "el => ({ lo: Number(el.min), hi: Number(el.max) })")
    total = bounds["hi"] - bounds["lo"]
    c.expect(total > 0, f"the scrub range is empty: {bounds}")
    step = 180 / max(1.0, total / 180)          # minutes per sample, >= 3 h
    ids = set()
    pos = bounds["lo"]
    for _ in range(30):
        gen = c.tonight_gen()
        # the instant the app will select for THIS slider value, computed by the
        # app itself (its snapping), so the wait can demand that exact paint
        want = c.page.evaluate("(v) => window.__ns_scrub_at(v)", round(pos))
        c.page.eval_on_selector(
            "#tb-scrub", """(el, v) => { el.value = String(v);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true })); }""",
            round(pos))
        # BUG-005: wait for the app to have PAINTED this selection. A fixed sleep
        # here raced the app's own 120 ms debounce (plus its deferred ranking) and
        # sampled stale/partial rows - 25/26 full-sequence failures while the
        # check passed in isolation.
        marker = c.wait_tonight_after(gen, sel=want)
        c.expect_eq(marker["sel"], want,
                    "the Tonight view painted a different instant than the "
                    "scrub position names")
        ids |= set(c.page.eval_on_selector_all(
            "#tonight-rank-table tbody tr[data-tonight-id]",
            "els => els.map((e) => e.dataset.tonightId)"))
        pos += step
        if pos > bounds["hi"]:
            break
    c.expect(ids, "the Tonight lists rendered nothing at all")
    eligible = c.page.evaluate("""async () => {
      const Core = await import('./core.js?v=44');
      const catalog = await (await fetch('catalog.json?v=10')).json();
      return ['ic1396', 'ngc1977', 'c14', 'pipe'].map(id => {
        const row = catalog.find(item => item.id === id);
        return { id, standard: !!row && !row._adv, magnitude: row?.vmag,
          included: !!row && Core.inTonightPool(row.vmag, !!row._adv, Core.ADV_TONIGHT_MAG_CAP) };
      });
    }""")
    c.expect(eligible and all(item["standard"] and item["magnitude"] is None and item["included"]
                              for item in eligible),
             f"unmagnituded standard targets must qualify for Tonight's shared pool: {eligible}")
    # The ranking cap may hide some eligible candidates at every sampled time.
    found = [oid for oid in ("ic1396", "ngc1977", "c14", "pipe") if oid in ids]
    c.expect(found,
             f"the rendered ranking should include an unmagnituded candidate somewhere in the night: {found}")
    c.assert_clean()


@check("web-catalog-advanced-minvis", "web")
def web_catalog_advanced_minvis(c):
    """ADVANCED mode + the Min-visibility gate: the whole 12k list has to warm
    through core's nightStatsBatch and then filter, while the nine-column table
    stays inside its results viewport at a narrow width."""
    c.goto("/")
    c.page.evaluate(
        """() => { const raw = JSON.parse(localStorage.getItem('nightseam_prefs_v1') || '{}');
             raw.advMode = true;
             localStorage.setItem('nightseam_prefs_v1', JSON.stringify(raw)); }""")
    boot(c)
    t0 = time.time()          # includes the click + first render, so this is an
    tab(c, "catalog")         # UPPER bound on the 12k warm (the honest number)
    head = c.page.inner_text("#view .cat-count:not(#pct-status)")
    c.expect(re.search(r"of 12,\d{3} objects", head),
             f"advanced mode must list the full catalogue: {head!r}")

    c.page.wait_for_function(
        """() => /below 1%/.test(document.querySelector('.cat-count')?.textContent || '')""",
        timeout=300000)
    warm = time.time() - t0
    print(f"        advanced-mode visibility warm: {warm:.1f}s")
    c.expect(warm < 90, f"the 12k warm took {warm:.1f}s - too slow to be the default")

    pcts = c.page.eval_on_selector_all(
        "#view table.cat td.pct",
        "els => els.map(e => parseInt(e.textContent, 10)).filter(v => Number.isFinite(v))")
    c.expect(pcts and min(pcts) >= 1,
             f"a never-visible row survived in advanced mode: {sorted(pcts)[:5]}")

    c.page.set_viewport_size({"width": 360, "height": 900})
    c.page.wait_for_timeout(400)
    box = c.page.evaluate(
        """() => { const t = document.querySelector('#view table.cat');
                   const card = t && t.closest('.card');
                   return { table: t ? t.scrollWidth : 0,
                            card: card ? card.clientWidth : 0,
                            doc: document.documentElement.scrollWidth,
                            win: window.innerWidth }; }""")
    print(f"        narrow viewport: table {box['table']}px in a {box['card']}px card, "
          f"page {box['doc']}px vs {box['win']}px")
    c.expect(box["doc"] <= box["win"] + 2,
             f"the catalog table widens the PAGE: {box['doc']}px vs {box['win']}px - "
             f"it must scroll inside its card instead")

    # ---- layout stability: progress/status must not move anything -----------
    c.page.set_viewport_size({"width": 1280, "height": 900})
    c.page.wait_for_timeout(300)

    def set_minvis(v):
        # 'input' updates the preference (the render reads it), 'change' re-renders
        c.page.eval_on_selector("#cat-minvis", """(el, val) => { el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true })); }""", v)

    def geometry():
        return c.page.evaluate(
            """() => { const t = document.querySelector('#view table.cat');
                 const ths = Array.from(t.querySelectorAll('thead th'))
                              .map(th => Math.round(th.getBoundingClientRect().width));
                 const s = document.querySelector('#pct-status');
                 return { cols: ths,
                          top: Math.round(t.getBoundingClientRect().top),
                          statusW: Math.round(s.getBoundingClientRect().width),
                          statsH: Math.round(s.parentElement.getBoundingClientRect().height) }; }""")

    base = geometry()
    c.expect(base["statusW"] >= 100,
             f"the status slot must be reserved before it has text: {base['statusW']}px")
    c.page.evaluate("""() => { document.querySelector('#pct-status').textContent =
        'computing visibility… 100%'; }""")
    c.page.wait_for_timeout(150)
    with_status = geometry()
    c.page.evaluate("() => { document.querySelector('#pct-status').textContent = ''; }")
    c.page.wait_for_timeout(150)
    cleared = geometry()
    for label, g in (("status shown", with_status), ("status cleared", cleared)):
        c.expect(g["top"] == base["top"],
                 f"{label}: the status line moved the table ({base['top']} -> {g['top']})")
        c.expect(g["cols"] == base["cols"],
                 f"{label}: the columns resized ({base['cols']} -> {g['cols']})")
        c.expect(g["statsH"] == base["statsH"],
                 f"{label}: the stats row changed height ({base['statsH']} -> {g['statsH']})")

    # moving a slider re-renders the whole view: the geometry must not change
    set_minvis(60)
    c.page.wait_for_timeout(300)
    after = geometry()
    c.expect(after["cols"] == base["cols"],
             f"re-rendering resized the columns ({base['cols']} -> {after['cols']})")
    c.expect(after["top"] == base["top"],
             f"re-rendering moved the table ({base['top']} -> {after['top']})")

    # ... and neither may the busy chip: the topbar wraps, so an empty slot is a
    # zero-height row and the whole app slides down when it fills
    c.page.evaluate(
        """() => { const b = document.querySelector('#busy');
             b.classList.remove('hidden');
             document.querySelector('#busy .busy-txt').textContent =
               'computing visibility… 100%'; }""")
    c.page.wait_for_timeout(200)
    busy = geometry()
    c.page.evaluate(
        """() => { const b = document.querySelector('#busy');
             b.classList.add('hidden');
             document.querySelector('#busy .busy-txt').textContent = ''; }""")
    c.page.wait_for_timeout(200)
    idle = geometry()
    c.expect(busy["top"] == base["top"] == idle["top"],
             f"the busy chip moved the table: {base['top']} -> {busy['top']} -> {idle['top']}")
    c.assert_clean()
@check("web-catalog-direction-b-density", "web")
def web_catalog_direction_b_density(c):
    """Keep the primary Catalog table aligned with the Direction B contract:
    20 dense rows, separate designation/common-name columns, full status and
    best-window headers, and no popularity/frame columns in the results table."""
    c.page.set_viewport_size({"width": 1672, "height": 941})
    boot(c)
    tab(c, "catalog")
    c.page.wait_for_selector(".cat-results table.cat tbody tr[data-id]")
    state = c.page.evaluate("""() => {
      const wrap = document.querySelector('.catwrap');
      const table = wrap?.querySelector('table.cat');
      const headers = [...(table?.querySelectorAll('thead th') || [])].map(el => el.textContent.trim());
      const rows = [...(table?.querySelectorAll('tbody tr[data-id]') || [])];
      const pager = document.querySelector('.cat-results .pager');
      const box = el => { const r = el?.getBoundingClientRect(); return r &&
        {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}; };
      return {headers, rows: rows.length, firstRow: box(rows[0]), lastRow: box(rows.at(-1)),
        wrap: box(wrap), pager: box(pager), tableWidth: box(table)?.width,
        tableScrollWidth: table?.scrollWidth, wrapWidth: wrap?.clientWidth,
        pageWidth: document.documentElement.scrollWidth, viewport: innerWidth,
        advancedOpen: document.querySelector('#cat-more')?.open};
    }""")
    expected = ["#", "Designation", "Common name", "Type", "Mag", "Alt now", "Night %", "Best window", "Status"]
    c.expect_eq(state["headers"], expected,
                f"Catalog must expose the compact Direction B column order: {state}")
    c.expect(state["rows"] == 20,
             f"Catalog must show a 20-row page when enough results exist: {state}")
    c.expect(state["pager"] and state["pager"]["height"] > 0 and state["pager"]["top"] < 941,
             f"the first-page pager must remain in the reference viewport: {state}")
    c.expect(state["tableWidth"] <= state["wrapWidth"] + 1 and state["tableScrollWidth"] <= state["wrapWidth"] + 1,
             f"the requested columns must fit without horizontal table scrolling: {state}")
    c.expect(state["pageWidth"] <= state["viewport"] + 1 and not state["advancedOpen"],
             f"the result page must stay in the viewport and advanced filters start collapsed: {state}")
    c.assert_clean()
