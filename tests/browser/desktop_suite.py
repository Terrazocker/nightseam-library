"""Desktop browser checks against the PACKED exe (tests/browser/harness.py
runs `dist/NightSeam.exe --no-browser` in a throwaway temp dir, so the user's
real database is never touched).  The UI served at `/` is `static/`."""

from __future__ import annotations

import struct
import tempfile
import zlib
from time import monotonic
from pathlib import Path

from harness import check

COUNT_RE = r"filtered"
READY = "#targetRows tr[data-id]"

# TR-04 (0.6.32 review): installed BEFORE the app boots so the wrapper sees the
# worker the app really creates. `__NS_KILL_WORKER` claims to terminate it and
# null it out; counting real `terminate()` calls (and later `new Worker()` calls)
# is what makes the sync-fallback check able to fail.
WATCH_WORKERS = """
(() => {
  const Orig = window.Worker;
  window.__nsWorkerCtor = 0;
  window.__nsWorkerTerm = 0;
  if (typeof Orig !== 'function') return;
  window.Worker = function (...args) {
    const w = new Orig(...args);
    const term = w.terminate.bind(w);
    w.terminate = () => { window.__nsWorkerTerm++; return term(); };
    window.__nsWorkerCtor++;
    return w;
  };
})();
"""


def boot(c):
    c.goto("/")
    c.page.wait_for_selector(READY, state="attached", timeout=90000)
    c.page.wait_for_function(
        """() => { const n = document.querySelector('#nightInfo');
                   return n && n.textContent.trim().length > 8; }""",
        timeout=90000)
    c.page.wait_for_function(
        """() => { const b = document.querySelector('#busy');
                   return !b || b.classList.contains('hidden'); }""",
        timeout=90000)


def text(c, selector: str) -> str:
    """textContent even when the element is on an inactive (hidden) tab."""
    return (c.page.evaluate(
        """(sel) => document.querySelector(sel)?.textContent || ''""",
        selector) or "").strip()


def count_text(c) -> str:
    return text(c, "#resultCount")


def rows(c) -> int:
    return c.page.locator(READY).count()


def arm_night_loaded(c):
    c.page.evaluate(
        """() => { window.__gateNight = false;
             document.addEventListener('night:loaded',
               () => { window.__gateNight = true; }, { once: true }); }""")


def wait_night_loaded(c, timeout: float = 90000.0):
    c.page.wait_for_function("() => window.__gateNight === true", timeout=timeout)
    c.page.wait_for_function(
        """() => { const b = document.querySelector('#busy');
                   return !b || b.classList.contains('hidden'); }""", timeout=20000)


def set_date(c, iso: str):
    c.page.evaluate(
        """(d) => { const el = document.querySelector('#dateInput');
             el.value = d;
             el.dispatchEvent(new Event('change', { bubbles: true })); }""", iso)


# ---------------------------------------------------------------- checks

@check("desktop-boot", "desktop")
def desktop_boot(c):
    boot(c)
    ver = text(c, "#versionTag")
    c.expect("NightSeam" in ver and any(ch.isdigit() for ch in ver),
             f"version tag missing: {ver!r}")
    c.expect(rows(c) > 0, "the target table must render rows")
    info = text(c, "#nightInfo")
    c.expect(("Sunset" in info) or ("Sun" in info),
             f"the night summary must be populated: {info[:120]!r}")
    c.assert_clean()


@check("desktop-workbench-only-render", "desktop")
def desktop_workbench_only_render(c):
    """Exercise the sole supported Workbench presentation in both chart views."""
    boot(c)
    c.expect_eq(c.page.locator("html").get_attribute("data-theme"), "workbench",
                "the desktop must boot in the only supported Workbench presentation")
    for view in ("sky", "tonight"):
        c.page.click(f"[data-tab='{view}']")
        c.page.wait_for_timeout(250)
        accent = c.page.evaluate(
            """() => getComputedStyle(document.documentElement)
                       .getPropertyValue('--accent').trim()""")
        c.expect(accent, "Workbench must expose an accent token")
        if view == "sky":
            c.expect(c.page.locator("#skyCanvas").bounding_box(),
                     "Sky View canvas missing in Workbench")
        else:
            c.page.wait_for_selector("#tonightAltitudeOverview", state="visible")
            c.expect(c.page.locator("#tonightAltitudeOverview").bounding_box(),
                     "Tonight overview chart missing in Workbench")
    c.assert_clean()


@check("desktop-accessibility-essentials", "desktop")
def desktop_accessibility_essentials(c):
    """The packed desktop shell must expose tab semantics and descriptions for
    its chart canvases, not only draw pixels."""
    boot(c)
    tabs = c.page.locator("#tabs[role='tablist'] .tab[role='tab']")
    c.expect_eq(tabs.count(), 5, "the desktop planner tablist must expose five tabs")
    c.expect_eq(c.page.locator("#tabs .tab[aria-selected='true']").count(), 1,
                "exactly one desktop planner tab must be selected")
    c.page.click("[data-tab='sky']")
    c.page.wait_for_selector("#skyCanvas[role='img'][aria-describedby='skyChartSummary']")
    c.expect(text(c, "#skyChartSummary"),
             "the desktop sky chart needs a non-empty screen-reader summary")
    c.page.click("[data-tab='targets']")
    c.page.wait_for_selector("#targetRows tr[data-id]:visible")
    c.page.wait_for_selector("#btnCatalogProvenance")
    provenance = c.page.evaluate(
        """async () => { const j = await (await fetch('/api/catalog/provenance')).json();
             return { schema: j.schema_version,
                      source: j.objects?.ngc1976?.source,
                      aliases: j.objects?.ngc1976?.aliases?.length || 0 }; }""")
    c.expect(provenance["schema"] == 1 and provenance["source"] == "OpenNGC"
             and provenance["aliases"] > 0,
             f"the desktop provenance endpoint was not loaded: {provenance}")
    row = c.page.locator("#targetRows tr[data-id]:visible").first
    c.expect_eq(row.get_attribute("role"), "button",
                "target rows must be keyboard-openable controls")
    row.focus()
    c.page.keyboard.press("Enter")
    c.page.wait_for_selector("#detailModal:not(.hidden)")
    details = c.page.locator("#detailModal details.catalog-details")
    c.expect(not details.evaluate("el => el.open"),
             "catalog provenance should start collapsed behind the target's live visibility summary")
    details.locator("summary").click()
    c.expect("Catalog source" in c.page.inner_text("#dMeta")
             and "Designation history" in c.page.inner_text("#dMeta"),
             "the desktop detail must show catalog provenance")
    c.expect(text(c, "#altChartSummary"),
             "the desktop altitude chart needs a non-empty screen-reader summary")
    overview_summary = text(c, "#tonightOverviewSummary").lower()
    c.expect("horizon or ceiling blocked" in overview_summary and
             "below your minimum altitude" in overview_summary and
             "below the horizon" in overview_summary and
             "fail the current visibility rules" not in overview_summary,
             f"the desktop Tonight chart summary must describe each rendered state precisely: {overview_summary}")
    # The detail chart can still be painting while the modal header is visible;
    # dispatch the real DOM click without making Playwright wait for a fresh
    # hit-test frame. The close handler itself must still restore focus.
    c.page.evaluate("() => document.querySelector('#dClose')?.click()")
    c.expect(c.page.evaluate("() => document.activeElement?.matches('#targetRows tr[data-id]')"),
             "closing the desktop detail dialog must restore focus to its target row")
    c.assert_clean()


@check("desktop-docked-inspector-fit-and-shortcuts", "desktop")
def desktop_docked_inspector_fit_and_shortcuts(c):
    """Docked details stay vertically readable at planner widths, while their
    arrow handling is scoped to the inspector container and global shortcuts
    remain available to the workspace."""
    boot(c)

    def eval_stage(stage, script):
        try:
            return c.page.evaluate(script)
        except Exception as exc:
            raise AssertionError(f"{stage} geometry evaluation failed: {exc}") from exc

    def choose_theme(theme):
        c.expect_eq(theme, "workbench", "only Workbench is supported")
        c.expect_eq(c.page.locator("html").get_attribute("data-theme"), "workbench",
                    "the legacy theme system must not reappear")

    def switch_view(view):
        c.page.evaluate("""(name) => {
          const tab = document.querySelector(`#tabs [data-tab="${name}"]`);
          if (!tab) throw new Error(`missing primary tab: ${name}`);
          tab.click();
        }""", view)

    def assert_fit(context):
        c.page.wait_for_selector("#detailModal:not(.hidden) .modal-box", state="visible")
        c.page.wait_for_function("""() => {
          const canvas = document.querySelector('#altChart');
          return canvas && canvas.clientWidth > 0 &&
            canvas.width === Math.round(canvas.clientWidth * (devicePixelRatio || 1));
        }""", timeout=10000)
        state = eval_stage(f"{context} inspector", """() => {
          const outer = document.querySelector('#detailModal');
          const panel = outer.querySelector('.modal-box');
          const hero = panel.querySelector('.detail-hero');
          const visibility = panel.querySelector('#dVisibilitySummary');
          const chart = panel.querySelector('.detail-chart');
          const canvas = panel.querySelector('#altChart');
          const panelRect = panel.getBoundingClientRect();
          const img = panel.querySelector('#dImage');
          const image = img.classList.contains('hidden')
            ? panel.querySelector('#dImageFallback') : img;
          const title = panel.querySelector('#dTitle');
          const close = panel.querySelector('#dClose');
          const details = panel.querySelector('details.catalog-details');
          const collapsedByDefault = !details.open;
          details.open = true;
          const metaTable = panel.querySelector('#dMeta');
          const metaValue = panel.querySelector('#dMeta td:last-child');
          const metadataRows = [...metaTable.querySelectorAll('tr')].map((row) => {
            const cells = row.querySelectorAll('td');
            const label = cells[0], value = cells[1];
            const range = document.createRange();
            range.selectNodeContents(label);
            const textRects = [...range.getClientRects()];
            const labelRect = label.getBoundingClientRect();
            const valueRect = value.getBoundingClientRect();
            return { name: label.textContent.trim(), labelFits: textRects.length > 0 &&
              textRects.every((r) => r.left >= labelRect.left - 1 && r.right <= labelRect.right + 1),
              valueStartsInSecondCell: valueRect.left >= labelRect.right - 1 };
          });
          const designationHistory = metadataRows.find((row) =>
            row.name.toLowerCase().includes('designation history')) || null;
          const legend = panel.querySelector('.chart-legend');
          const toggle = panel.querySelector('#chkMoon');
          const disclosure = details.querySelector('summary');
          const favorite = panel.querySelector('#dFavorite');
          const dpr = window.devicePixelRatio || 1;
          const rects = [title, close, favorite, image, hero, visibility, canvas, disclosure, metaValue, legend, toggle]
            .map(el => el?.getBoundingClientRect());
          const horizontalBounds = rects.every(r => r && r.width > 0 &&
            r.left >= panelRect.left - 1 && r.right <= panelRect.right + 1 &&
            r.left >= -1 && r.right <= innerWidth + 1);
          const inPanelViewport = el => {
            const r = el.getBoundingClientRect();
            return r.top >= panelRect.top - 1 && r.bottom <= panelRect.bottom + 1 &&
              r.top >= -1 && r.bottom <= innerHeight + 1;
          };
          const isTopmost = el => {
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2,
              r.top + r.height / 2);
            return !!hit && el.contains(hit);
          };
          const reach = el => {
            let r = el.getBoundingClientRect();
            if (r.top < panelRect.top) panel.scrollTop += r.top - panelRect.top;
            else if (r.bottom > panelRect.bottom) panel.scrollTop += r.bottom - panelRect.bottom;
            return inPanelViewport(el);
          };
          panel.scrollTop = 0;
          const closeInitiallyVisible = inPanelViewport(close) && isTopmost(close);
          const dialogHeaderVisible = isTopmost(title);
          const reachable = [title, close, favorite, image, hero, visibility, canvas, disclosure, metaValue, legend, toggle]
            .map(reach).every(Boolean);
          const imageHeight = image.getBoundingClientRect().height;
          panel.scrollTop = 0;
          // Keep this geometry probe observational: opening provenance for
          // reachability must not make the next view look pre-expanded.
          details.open = false;
          return {
            docked: outer.classList.contains('detail-docked'),
            role: outer.getAttribute('role'), modal: outer.getAttribute('aria-modal'),
            panelWidth: panel.clientWidth, panelOverflow: panel.scrollWidth - panel.clientWidth,
            contentWidth: panel.scrollWidth, contentOverflow: panel.scrollWidth - panel.clientWidth,
            heroWidth: hero.getBoundingClientRect().width,
            chartWidth: chart.getBoundingClientRect().width,
            heroBeforeChart: hero.getBoundingClientRect().top < chart.getBoundingClientRect().top,
            chartBeforeMetadata: chart.getBoundingClientRect().top < details.getBoundingClientRect().top,
            collapsedByDefault, imageHeight, imageAndChartFit: horizontalBounds,
            metadataLabelsFitTheirCells: metadataRows.length > 0 &&
              metadataRows.every((row) => row.labelFits),
            metadataValuesStartInSecondCell: metadataRows.length > 0 &&
              metadataRows.every((row) => row.valueStartsInSecondCell),
            metadataHasNoHorizontalOverflow: metaTable.scrollWidth - metaTable.clientWidth <= 1,
            designationHistory,
            closeInitiallyVisible, dialogHeaderVisible,
            contentReachableAfterScroll: reachable,
            canvasWidth: canvas.width,
            expectedCanvasWidth: Math.round(canvas.clientWidth * dpr),
            viewportHeight: innerHeight,
            pageOverflow: document.documentElement.scrollWidth - innerWidth
          };
        }""")
        c.expect(state["panelWidth"] > 0 and state["panelOverflow"] <= 1 and
                 state["contentOverflow"] <= 1 and state["pageOverflow"] <= 1,
                 f"{context}: detail dock/modal must not overflow horizontally: {state}")
        c.expect(state["heroBeforeChart"] and state["chartBeforeMetadata"] and
                 state["collapsedByDefault"] and state["heroWidth"] > 0 and
                 state["chartWidth"] > 0 and state["contentReachableAfterScroll"],
                 f"{context}: target facts, chart, and collapsed metadata must follow the required order and remain reachable: {state}")
        c.expect(state["imageAndChartFit"] and state["closeInitiallyVisible"] and
                 state["dialogHeaderVisible"],
                 f"{context}: detail content must stay within the inspector and the close control must be visible: {state}")
        c.expect(state["metadataLabelsFitTheirCells"] and
                 state["metadataValuesStartInSecondCell"] and
                 state["metadataHasNoHorizontalOverflow"],
                 f"{context}: every metadata label must wrap inside its own cell without shifting its value: {state}")
        if "M31" in context:
            c.expect(state["designationHistory"] and state["designationHistory"]["labelFits"] and
                     state["designationHistory"]["valueStartsInSecondCell"],
                     f"{context}: M31 Designation history must stay within its label cell: {state}")
        image_limit = 190 if state["docked"] else min(260, state["viewportHeight"] * .28) + 1
        c.expect(state["imageHeight"] <= image_limit,
                 f"{context}: detail image must fit its dock/modal layout: {state}")
        c.expect_eq(state["canvasWidth"], state["expectedCanvasWidth"],
                    f"{context}: detail chart must be redrawn for its dock width")
        return state

    def assert_tonight_structure(theme, width):
        state = eval_stage(f"{theme} {width}px Tonight structure", """() => {
          const head = document.querySelector('#nightBrief');
          const facts = head.querySelector('.night-brief-facts');
          const track = head.querySelector('.night-brief-times');
          const h = head.getBoundingClientRect(), f = facts.getBoundingClientRect();
          return { head: getComputedStyle(head).display,
            facts: getComputedStyle(facts).display,
            track: getComputedStyle(track).display,
            columns: getComputedStyle(head).gridTemplateColumns.split(' ').length,
            factsAfterTitle: f.top >= h.top,
            headOverflow: head.scrollWidth - head.clientWidth,
            trackOverflow: track.scrollWidth - track.clientWidth };
        }""")
        expected_columns = 1 if width <= 900 else 2
        c.expect_eq(state["head"], "grid",
                    f"{theme} {width}px: Tonight summary should remain a grid: {state}")
        c.expect(state["facts"] == "grid" and state["track"] != "none" and
                 state["columns"] == expected_columns and state["factsAfterTitle"] and
                 state["headOverflow"] <= 1 and
                 state["trackOverflow"] <= 1,
                 f"{theme} {width}px: session facts and booked night must fit: {state}")

    def assert_header_geometry(theme, width):
        state = eval_stage(f"{theme} {width}px header", """() => {
          const weatherChip = document.querySelector('#wxChip');
          const oldWeather = weatherChip.textContent;
          weatherChip.textContent = 'Weather forecast text long enough to exercise the ellipsis';
          const bar = document.querySelector('#topbar').getBoundingClientRect();
          const names = ['#timeLabel', '#btnWeather', '#btnMenu'];
          const items = names.map((selector) => {
            const el = document.querySelector(selector), style = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
              el.getClientRects().length > 0 && r.width > 0 && r.height > 0;
            const hit = visible && ['#btnWeather', '#btnMenu'].includes(selector)
              ? (() => { const target = document.elementFromPoint(r.left + r.width / 2,
                  r.top + r.height / 2); return !!target && el.contains(target); })() : true;
            return { selector, visible, hit, left: r.left, right: r.right,
              top: r.top, bottom: r.bottom,
              withinBar: !visible || (r.left >= bar.left - 1 && r.right <= bar.right + 1 &&
                r.top >= bar.top - 1 && r.bottom <= bar.bottom + 1) };
          });
          const visibleItems = items.filter((item) => item.visible);
          const collisions = [];
          for (let i = 0; i < visibleItems.length; i++) for (let j = i + 1; j < visibleItems.length; j++) {
            const a = visibleItems[i], b = visibleItems[j];
            const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
            const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
            if (x > 1 && y > 1) collisions.push([a.selector, b.selector, x, y]);
          }
          const textStyle = (el) => ({ overflow: getComputedStyle(el).overflow,
            textOverflow: getComputedStyle(el).textOverflow,
            clientWidth: el.clientWidth, scrollWidth: el.scrollWidth });
          const chips = {
            weatherButtonWidth: document.querySelector('#btnWeather').getBoundingClientRect().width,
            weather: textStyle(weatherChip)
          };
          const tabsTop = document.querySelector('#tabs').getBoundingClientRect().top;
          const tabsBottom = document.querySelector('#tabs').getBoundingClientRect().bottom;
          weatherChip.textContent = oldWeather;
          return { items, collisions, chips, barBottom: bar.bottom, tabsTop, tabsBottom,
            tabsBelowBar: tabsTop >= bar.bottom - 1,
            pageOverflow: document.documentElement.scrollWidth - innerWidth };
        }""")
        required = ("#timeLabel", "#btnWeather", "#btnMenu")
        for selector in required:
            item = next(item for item in state["items"] if item["selector"] == selector)
            c.expect(item["visible"] and item["withinBar"],
                     f"{theme} {width}px: required header control {selector} must fit in the topbar: {state}")
        for selector in ("#btnWeather", "#btnMenu"):
            item = next(item for item in state["items"] if item["selector"] == selector)
            c.expect(item["hit"],
                     f"{theme} {width}px: {selector} center must be hit-testable: {state}")
        positions = [(item["selector"], round(item["left"]), round(item["right"]),
                      round(item["top"]), round(item["bottom"])) for item in state["items"]]
        c.expect(not state["collisions"] and state["pageOverflow"] <= 1,
                 f"{theme} {width}px: header collision/page overflow: "
                 f"{state['collisions']} / {state['pageOverflow']}; "
                 f"items={positions}")
        c.expect(state["chips"]["weatherButtonWidth"] <= 141 and
                 state["chips"]["weather"]["overflow"] == "hidden" and
                 state["chips"]["weather"]["textOverflow"] == "ellipsis",
                 f"{theme} {width}px: Weather text must ellipsize inside its capped chip: {state}")
        c.expect(state["tabsBelowBar"],
                 f"Workbench {width}px: the tab dock must start below the topbar: {state}")

    def assert_catalog_rail(theme, width):
        state = eval_stage(f"{theme} {width}px Catalog rail", """() => {
          const rail = document.querySelector('.catalog-filter-rail');
          const advanced = document.querySelector('#moreCatalogFilters');
          const wasAdvancedOpen = advanced.open;
          advanced.open = true;
          advanced.offsetHeight;
          const railRect = rail.getBoundingClientRect();
          const controls = [...rail.querySelectorAll(
            'input[type=search], input[type=text], input[type=range], input[type=number], input[type=checkbox], select, button')]
            .filter((el) => {
              const style = getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden' &&
                el.getClientRects().length > 0;
            }).map((el) => {
              const r = el.getBoundingClientRect();
              return { id: el.id || el.getAttribute('aria-label') || el.tagName,
                left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                fits: r.left >= railRect.left - 1 && r.right <= railRect.right + 1 };
            });
          const wrap = document.querySelector('.catalog-results .tablewrap');
          const table = document.querySelector('#targetTable');
          const oldWidth = wrap.style.width;
          wrap.style.width = '320px';
          wrap.scrollLeft = 0;
          const narrowClient = wrap.clientWidth, narrowScroll = wrap.scrollWidth;
          wrap.scrollLeft = Math.min(120, narrowScroll);
          const canScrollIndependently = wrap.scrollLeft > 0;
          const narrowPageOverflow = document.documentElement.scrollWidth - innerWidth;
          const railOverflow = rail.scrollWidth - rail.clientWidth;
          wrap.style.width = oldWidth;
          wrap.scrollLeft = 0;
          advanced.open = wasAdvancedOpen;
          const result = { controls, railOverflow,
            overflowX: getComputedStyle(wrap).overflowX, tableWidth: table.scrollWidth,
            tableViewportWidth: wrap.clientWidth, narrowClient, narrowScroll,
            canScrollIndependently, narrowPageOverflow,
            pageOverflow: document.documentElement.scrollWidth - innerWidth };
          return result;
        }""")
        bad_controls = [control for control in state["controls"] if not control["fits"]]
        c.expect(state["controls"] and not bad_controls and state["railOverflow"] <= 1,
                 f"{theme} {width}px: Catalog rail controls/overflow: "
                 f"{bad_controls}; {state['railOverflow']}")
        c.expect(state["overflowX"] in ("auto", "scroll") and
                 state["narrowScroll"] > state["narrowClient"] and
                 state["canScrollIndependently"] and state["narrowPageOverflow"] <= 1 and
                 state["pageOverflow"] <= 1,
                 f"{theme} {width}px: Catalog results must scroll within their own table viewport: {state}")

    for theme, widths in (("workbench", (1672, 1300, 1240, 1201, 1100, 900, 640, 360)),):
        choose_theme(theme)
        for width in widths:
            c.page.set_viewport_size({"width": width, "height": 900})
            c.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            assert_header_geometry(theme, width)

    for theme in ("workbench",):
        choose_theme(theme)
        for width, height in ((1672, 941), (1240, 900)):
            c.page.set_viewport_size({"width": width, "height": height})
            c.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            switch_view("tonight")
            assert_tonight_structure(theme, width)
            target = c.page.locator("#topPicks .pick-target:visible").first
            if target.count():
                target.click()
            else:
                # A late-September default night can have no above-horizon
                # Tonight cards. Open a deterministic catalog target, then
                # return to Tonight so the same docked inspector is exercised.
                switch_view("targets")
                c.page.locator("#targetRows tr[data-id]").first.click()
                c.page.wait_for_selector("#detailModal.detail-docked .modal-box", state="visible")
                switch_view("tonight")
            c.page.wait_for_selector("#detailModal.detail-docked .modal-box", state="visible")
            assert_fit(f"{theme} {width}px Tonight")
            for view in ("targets", "sky"):
                switch_view(view)
                c.page.wait_for_timeout(180)
                c.expect(c.page.locator("#detailModal.detail-docked").is_visible(),
                         f"{theme} {width}px: inspector should remain docked in {view}")
                assert_fit(f"{theme} {width}px {view}")
                if view == "targets":
                    m31 = c.page.locator("#targetRows tr[data-id='ngc224']")
                    m31.wait_for(state="visible", timeout=10000)
                    m31.click()
                    c.page.wait_for_function("() => /M31|Andromeda/i.test(document.querySelector('#dTitle')?.textContent || '')",
                                             timeout=10000)
                    assert_fit(f"{theme} {width}px M31 Catalog detail")
                    assert_catalog_rail(theme, width)

        if theme == "workbench":
            panel = c.page.locator("#detailModal .modal-box")
            switch_view("tonight")
            c.expect_eq(panel.get_attribute("tabindex"), "0",
                        "the detail container must be a keyboard focus target")
            before_title = text(c, "#dTitle")
            before_detail_arrow_time = int(c.page.locator("#timeSlider").input_value())
            panel.focus()
            c.page.keyboard.press("ArrowRight")
            c.page.wait_for_function("(old) => document.querySelector('#dTitle')?.textContent !== old",
                                     arg=before_title, timeout=10000)
            c.expect_eq(int(c.page.locator("#timeSlider").input_value()),
                        before_detail_arrow_time,
                        "an arrow on the detail container must change target, not scrub time")

            target_title = text(c, "#dTitle")
            for slider_selector in ("#timeSlider", "#skyTimeSlider"):
                if slider_selector == "#skyTimeSlider":
                    switch_view("sky")
                slider = c.page.locator(slider_selector)
                before_time = int(slider.input_value())
                arrow = "ArrowLeft" if before_time >= int(slider.get_attribute("max")) else "ArrowRight"
                slider.focus()
                c.page.keyboard.press(arrow)
                c.page.wait_for_function("([sel, old]) => document.querySelector(sel)?.value !== old",
                                         arg=[slider_selector, str(before_time)], timeout=10000)
                c.expect_eq(text(c, "#dTitle"), target_title,
                            f"{slider_selector} arrows must preserve the selected detail target")

            switch_view("tonight")
            slider_after = int(c.page.locator("#timeSlider").input_value())
            c.page.evaluate("""() => { const e = document.querySelector('#nightInfo');
                e.setAttribute('tabindex', '-1'); e.focus(); }""")
            workspace_arrow = "ArrowLeft" if slider_after >= int(
                c.page.locator("#timeSlider").get_attribute("max")) else "ArrowRight"
            c.page.keyboard.press(workspace_arrow)
            c.page.wait_for_function("(old) => document.querySelector('#timeSlider')?.value !== old",
                                     arg=str(slider_after), timeout=10000)
            c.expect_eq(text(c, "#dTitle"), target_title,
                        "workspace arrows must scrub without changing the detail target")

            c.page.locator("#dClose").focus()
            c.page.keyboard.press("n")
            c.page.wait_for_function("() => document.body.dataset.primaryView === 'targets'",
                                     timeout=10000)
            c.expect(c.page.locator("#detailModal.detail-docked").is_visible(),
                     "N must cycle workspace tabs while the docked inspector is open")
            switch_view("tonight")

        # The compact breakpoint retains modal semantics and a reachable
        # vertical detail flow rather than clipping its chart. Its focus trap
        # owns focus and its N shortcut must not change the selected view.
        c.page.set_viewport_size({"width": 900, "height": 900})
        switch_view("tonight")
        assert_tonight_structure(theme, 900)
        c.page.wait_for_timeout(180)
        c.expect_eq(c.page.locator("#detailModal").get_attribute("role"), "dialog",
                    f"{theme}: compact inspector must remain a dialog")
        assert_fit(f"{theme} 900px compact dialog")

        if theme == "workbench":
            panel = c.page.locator("#detailModal .modal-box")
            c.page.locator("#dClose").focus()
            c.page.keyboard.press("Tab")
            c.expect(c.page.evaluate("""() => document.querySelector('#detailModal .modal-box')
                ?.contains(document.activeElement)"""),
                "compact detail focus must stay inside its modal")
            compact_title = text(c, "#dTitle")
            panel.focus()
            c.page.keyboard.press("ArrowRight")
            c.page.wait_for_function("(old) => document.querySelector('#dTitle')?.textContent !== old",
                                     arg=compact_title, timeout=10000)
            c.page.keyboard.press("n")
            c.expect_eq(c.page.evaluate("() => document.body.dataset.primaryView"), "tonight",
                        "compact modal N shortcuts must remain confined")

        c.page.evaluate("() => document.querySelector('#dClose')?.click()")
        c.page.wait_for_selector("#detailModal", state="hidden")
        switch_view("targets")
        m31 = c.page.locator("#targetRows tr[data-id='ngc224']")
        m31.wait_for(state="visible", timeout=10000)
        m31.click()
        c.page.wait_for_function("() => /M31|Andromeda/i.test(document.querySelector('#dTitle')?.textContent || '')",
                                 timeout=10000)
        assert_fit(f"{theme} 900px M31 compact Catalog detail")
        c.page.evaluate("() => document.querySelector('#dClose')?.click()")
        c.page.wait_for_selector("#detailModal", state="hidden")
        assert_catalog_rail(theme, 900)
    c.assert_clean()


@check("desktop-offline-image-fallback", "desktop")
def desktop_offline_image_fallback(c):
    """A missing optional target image has a stable, labelled offline tile."""
    boot(c)
    c.allow_console_errors("deliberately aborted optional target-image request")
    c.allow_failed_responses("deliberately aborted optional target-image request")
    c.page.route("**/api/object/*/image*", lambda route: route.abort())
    target = c.page.locator("#topPicks .pick-target:visible").first
    if target.count():
        target.click()
    else:
        c.page.click("#tabs [data-tab='targets']")
        c.page.locator("#targetRows tr[data-id]").first.click()
    c.page.wait_for_selector("#dImageFallback:not(.hidden)", state="visible", timeout=15000)
    c.expect_eq(text(c, "#dImageFallback"), "Image unavailable offline",
                "the desktop detail must explain a missing offline image")
    c.expect(c.page.locator("#dImage.hidden").count() == 1,
             "a broken image element must not remain visible under its fallback")
    c.page.evaluate("() => document.querySelector('#dClose')?.click()")
    c.assert_clean()


@check("desktop-image-catalog-navigation-and-retry", "desktop")
def desktop_image_catalog_navigation_and_retry(c):
    """The fifth primary tab owns three keyboard-selectable child tabs; the
    top-bar import action opens Import Exposures, and a failed first library
    load can be retried by returning to Library."""
    boot(c)
    calls = 0

    def fail_first_library(route):
        nonlocal calls
        calls += 1
        if calls == 1:
            route.fulfill(status=503, content_type="application/json",
                          body='{"detail":"temporary browser-test failure"}')
        else:
            route.continue_()

    c.allow_failed_responses("one deliberate Library retry response")
    c.allow_console_errors("one deliberate Library retry response")
    c.page.route("**/api/library", fail_first_library)
    children = c.page.locator("#catalogTabs[role='tablist'] [role='tab']")
    c.expect_eq(children.count(), 3,
                "Image Catalog must expose Library, Import Exposures, and Sky Map")
    c.page.click("#tabs [data-tab='image-catalog']")
    c.page.wait_for_function("() => document.querySelector('#catalog-tab-library')?.getAttribute('aria-selected') === 'true'")
    c.page.wait_for_timeout(250)
    c.expect_eq(calls, 1, "entering Image Catalog must attempt the Library load")
    c.expect(c.page.evaluate("() => getComputedStyle(document.querySelector('.planner-control-slot')).display === 'none'"),
             "planner controls must be hidden while Image Catalog is active")
    c.expect(c.page.evaluate("() => getComputedStyle(document.querySelector('#imageCatalogTools')).display !== 'none'"),
             "Image Catalog actions must be visible in their primary view")

    library = c.page.locator("#catalog-tab-library")
    library.focus()
    c.page.keyboard.press("ArrowRight")
    c.page.wait_for_selector("#tab-import.active:not([hidden])")
    c.expect(c.page.locator("#catalog-tab-import").get_attribute("aria-selected") == "true",
             "ArrowRight must select Import Exposures in the child tablist")
    c.page.keyboard.press("ArrowRight")
    c.page.wait_for_selector("#tab-skymap.active:not([hidden])")
    c.page.keyboard.press("ArrowLeft")
    c.page.wait_for_selector("#tab-import.active:not([hidden])")

    with c.page.expect_response(lambda r: r.url.endswith("/api/library") and r.status == 200):
        c.page.click("#catalog-tab-library")
    c.page.wait_for_selector("#tab-library.active:not([hidden])")
    c.expect_eq(calls, 2,
                "returning to Library must retry after its first load failed")

    c.page.evaluate("""() => {
        const nativeClick = HTMLInputElement.prototype.click;
        window.__ns_fit_picker_opened = false;
        HTMLInputElement.prototype.click = function() {
          if (this.id === 'fitFiles') { window.__ns_fit_picker_opened = true; return; }
          return nativeClick.call(this);
        };
      }""")
    c.page.click("#btnTopIngest")
    c.page.wait_for_selector("#tab-import.active:not([hidden])")
    c.expect(c.page.evaluate("() => window.__ns_fit_picker_opened === true"),
             "the top-bar Import FITS action must reach the file picker")
    c.expect(c.page.locator("#catalog-tab-import").get_attribute("aria-selected") == "true",
             "top-bar Import FITS must select Import Exposures first")
    c.page.click("#catalog-tab-skymap")
    c.page.wait_for_selector("#tab-skymap.active:not([hidden]) #skymapCanvas")
    map_before = c.page.evaluate("""() => {
      const cv = document.querySelector('#skymapCanvas');
      return { width: cv.clientWidth, backing: cv.width, dpr: window.devicePixelRatio || 1 };
    }""")
    c.page.evaluate("""() => {
      document.querySelector('#skymapWrap').style.setProperty('width', '480px', 'important');
    }""")
    c.page.wait_for_function("""(old) =>
      document.querySelector('#skymapCanvas').clientWidth < old""",
        arg=map_before["width"], timeout=10000)
    map_after = c.page.evaluate("""() => {
      const cv = document.querySelector('#skymapCanvas');
      const dpr = window.devicePixelRatio || 1;
      const rect = cv.getBoundingClientRect();
      return { width: rect.width, height: rect.height,
        backingWidth: cv.width, backingHeight: cv.height, dpr };
    }""")
    c.expect(map_after["backingWidth"] == round(map_after["width"] * map_after["dpr"]) and
             map_after["backingHeight"] == round(map_after["height"] * map_after["dpr"]) and
             map_after["backingWidth"] == 2 * map_after["backingHeight"],
             f"Sky Map must preserve its 2:1 DPR canvas after container resize: {map_after}")
    c.page.evaluate("() => document.querySelector('#skymapWrap').style.removeProperty('width')")
    c.page.wait_for_timeout(150)
    c.page.locator("#btnMenu").focus()
    c.page.keyboard.press("n")
    c.page.wait_for_selector("#tabs [data-tab='tonight'].active")
    c.expect(c.page.locator("#tabs .tab[aria-selected='true']").get_attribute("data-tab") == "tonight",
             "N must cycle from Image Catalog to the next primary view")
    c.assert_clean()


@check("desktop-sky-local-scrub-and-container-resize", "desktop")
def desktop_sky_local_scrub_and_container_resize(c):
    """The Sky View scrub mirrors the top-bar range and selected instant; a
    chart-container-only width change resizes its DPR-aware canvas."""
    boot(c)
    c.page.click("#tabs [data-tab='sky']")
    c.page.wait_for_selector("#skyCanvas[tabindex='0']")

    def state():
        return c.page.evaluate("""() => {
          const top = document.querySelector('#timeSlider');
          const local = document.querySelector('#skyTimeSlider');
          return { topMin: top.min, topMax: top.max, topValue: top.value,
            localMin: local.min, localMax: local.max, localValue: local.value,
            topLabel: document.querySelector('#timeLabel').textContent,
            localLabel: document.querySelector('#skyTimeLabel').textContent };
        }""")

    current = state()
    c.expect((current["topMin"], current["topMax"], current["topValue"]) ==
             (current["localMin"], current["localMax"], current["localValue"]),
             f"the local and top-bar scrubs must start synchronized: {current}")
    c.page.evaluate("""() => {
        const local = document.querySelector('#skyTimeSlider');
        const lo = Number(local.min), hi = Number(local.max);
        local.value = String(Math.floor((lo + hi) / 2));
        local.dispatchEvent(new Event('input', { bubbles: true }));
      }""")
    c.page.wait_for_function("""() => {
        const top = document.querySelector('#timeSlider');
        const local = document.querySelector('#skyTimeSlider');
        return top.value === local.value &&
          document.querySelector('#timeLabel').textContent ===
          document.querySelector('#skyTimeLabel').textContent;
      }""")
    before_step = int(c.page.locator("#skyTimeSlider").input_value())
    c.page.locator("#skyTimeSlider").focus()
    c.page.keyboard.press("Shift+ArrowRight")
    c.page.wait_for_function("""(before) =>
        Number(document.querySelector('#skyTimeSlider').value) === before + 10""",
        arg=before_step)
    stepped = state()
    c.expect(stepped["localValue"] == stepped["topValue"] and
             stepped["localLabel"] == stepped["topLabel"],
             f"Shift+Arrow must update both scrubbers and labels: {stepped}")

    span_before = c.page.evaluate("""() => {
      const s = document.querySelector('#skyCanvas'), b = document.querySelector('#skyWrap');
      return { width: s.clientWidth, backing: s.width,
        dpr: window.devicePixelRatio || 1, box: b.clientWidth };
    }""")
    c.page.evaluate("""() => {
      const box = document.querySelector('#skyWrap');
      box.style.setProperty('width', '360px', 'important');
      box.style.setProperty('justify-self', 'start', 'important');
    }""")
    c.page.wait_for_function("""(old) => {
      const s = document.querySelector('#skyCanvas');
      return s.clientWidth < old &&
        s.width === Math.round(s.clientWidth * (window.devicePixelRatio || 1));
    }""", arg=span_before["width"], timeout=10000)
    c.page.evaluate("""() => {
      const box = document.querySelector('#skyWrap');
      box.style.removeProperty('width'); box.style.removeProperty('justify-self');
    }""")
    c.page.wait_for_timeout(200)
    c.assert_clean()


@check("desktop-worker-date-change", "desktop")
def desktop_worker_date_change(c):
    """FIX-1 scenario: a date change must clear the busy chip and keep a plan."""
    boot(c)
    c.page.evaluate("() => { window.__NS_DEBUG_WORKER = true; }")
    arm_night_loaded(c)
    set_date(c, "2026-08-16")
    wait_night_loaded(c)
    c.expect(rows(c) > 0, "a superseded/edited night must still render targets")
    c.expect("of" in count_text(c), f"the count must be populated: {count_text(c)!r}")
    c.assert_clean()


@check("desktop-worker-kill-sync-fallback", "desktop")
def desktop_worker_kill_sync_fallback(c):
    """A dead worker must fall back to the in-thread build, not hang.

    TR-04 (0.6.32 review): this check could not fail. It asserted
    `killed is False or killed is True` (true for any bool) and then ran the
    plain date-change sequence `desktop-worker-date-change` already covers -
    a no-op `__NS_KILL_WORKER` was undetectable. It now asserts the hook's REAL
    effect (the live Worker instance is terminated), that the fallback still
    produced a plan, and that after `__NS_REVIVE_WORKER` the next build
    constructs a FRESH worker - which is only true if the kill truly disposed of
    the old one (a no-op kill leaves it alive and is reused)."""
    c.page.add_init_script(WATCH_WORKERS)
    boot(c)
    c.page.evaluate("() => { window.__NS_DEBUG_WORKER = true; }")
    killed = c.page.evaluate("() => window.__NS_KILL_WORKER()")
    c.expect(killed is True, "the dev kill hook must report that it ran when armed")
    terminated = c.page.evaluate("() => window.__nsWorkerTerm")
    c.expect(terminated >= 1,
             f"the kill hook must TERMINATE the live worker, not just claim it "
             f"(terminate() calls: {terminated})")
    arm_night_loaded(c)
    set_date(c, "2026-08-17")
    wait_night_loaded(c)
    c.expect(rows(c) > 0, "the sync fallback must still produce a plan")
    c.expect("of" in count_text(c), f"the count must be populated: {count_text(c)!r}")
    revived = c.page.evaluate("() => window.__NS_REVIVE_WORKER()")
    c.expect(revived is True, "the dev revive hook must report that it ran")
    constructed = c.page.evaluate("() => window.__nsWorkerCtor")
    arm_night_loaded(c)
    set_date(c, "2026-08-18")
    wait_night_loaded(c)
    c.expect(c.page.evaluate("() => window.__nsWorkerCtor") > constructed,
             "after the revive the next build must construct a FRESH worker - "
             "proof the kill really disposed of the old one")
    c.assert_clean()


@check("desktop-worker-error-disposal", "desktop")
def desktop_worker_error_disposal(c):
    """DS5 (carried over from the 0.6.20/0.6.24 reviews, closed in 0.6.35): a
    worker whose script DIES must be disposed of, not just flagged.

    `_worker.onerror` set `_workerBroken = true` and left the dead Worker in
    `_worker`. `__NS_REVIVE_WORKER` clears the flag, so the next build handed the
    corpse back to `postMessage` instead of constructing a fresh worker: the
    recovery hook could not actually recover the worker path. The kill hook was
    already correct, which is why the existing check passed while this path was
    broken.

    This drives the REAL failure path - `__NS_FAIL_WORKER` dispatches a genuine
    `ErrorEvent` at the live worker, so the registered `onerror` handler runs.
    Asserts the disposal (a real terminate) and the recovery (a fresh Worker)."""
    c.page.add_init_script(WATCH_WORKERS)
    boot(c)
    c.page.evaluate("() => { window.__NS_DEBUG_WORKER = true; }")
    arm_night_loaded(c)
    set_date(c, "2026-08-16")
    wait_night_loaded(c)
    c.expect(rows(c) > 0, "the worker-backed build must render targets")
    ctor_before = c.page.evaluate("() => window.__nsWorkerCtor")
    term_before = c.page.evaluate("() => window.__nsWorkerTerm")

    failed = c.page.evaluate("() => window.__NS_FAIL_WORKER()")
    c.expect(failed is True, "the dev failure hook must report that it ran")
    terminated = c.page.evaluate("() => window.__nsWorkerTerm")
    c.expect(terminated > term_before,
             f"a worker `onerror` must TERMINATE the dead worker, not just mark it "
             f"broken (terminate() calls: {term_before} -> {terminated})")

    # the sync fallback must still plan while the worker is dead
    arm_night_loaded(c)
    set_date(c, "2026-08-17")
    wait_night_loaded(c)
    c.expect(rows(c) > 0, "the sync fallback must still produce a plan")
    c.expect("of" in count_text(c), f"the count must be populated: {count_text(c)!r}")

    # and revive must construct a FRESH worker - only possible if the dead one is
    # gone from `_worker` (a flagged-but-retained worker is reused, ctor unchanged)
    revived = c.page.evaluate("() => window.__NS_REVIVE_WORKER()")
    c.expect(revived is True, "the dev revive hook must report that it ran")
    arm_night_loaded(c)
    set_date(c, "2026-08-18")
    wait_night_loaded(c)
    ctor_after = c.page.evaluate("() => window.__nsWorkerCtor")
    c.expect(ctor_after > ctor_before,
             f"after revive the next build must construct a FRESH worker "
             f"(ctor calls: {ctor_before} -> {ctor_after}) - proof the errored "
             f"worker was disposed of")
    c.assert_clean()


@check("desktop-pref-rescore", "desktop")
def desktop_pref_rescore(c):
    """A client-side min-alt change re-derives without a reload."""
    boot(c)
    before = count_text(c)
    before_val = text(c, "#muaVal")
    c.page.evaluate(
        """() => { const s = document.querySelector('#minUsefulAlt');
             const v = Number(s.value);
             s.value = String(Math.min(Number(s.max), v + 15));
             s.dispatchEvent(new Event('input', { bubbles: true }));
             s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    try:
        c.page.wait_for_function(
            """(prev) => (document.querySelector('#resultCount')?.textContent
                          || '').trim() !== prev""", arg=before, timeout=15000)
    except Exception:
        raise AssertionError(f"min-alt change did not re-derive ({before!r})")
    c.expect(text(c, "#muaVal") != before_val,
             "the min-alt readout must follow the slider")
    c.assert_clean()


@check("desktop-advanced-mode", "desktop")
def desktop_advanced_mode(c):
    """Advanced mode ships 12k objects and still renders responsively."""
    boot(c)
    c.page.evaluate(
        """() => fetch('/api/settings', { method: 'POST',
             headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify({ adv_mode: true }) })""")
    c.goto("/")
    c.page.wait_for_selector("#resultCount", state="attached", timeout=90000)
    c.page.wait_for_function(
        """() => /\\d+ of (\\d{4,}) objects/.test(
                     document.querySelector('#resultCount')?.textContent || '')""",
        timeout=180000)
    total = int(c.page.evaluate(
        """() => { const m = (document.querySelector('#resultCount')?.textContent || '')
                     .match(/of (\\d+) objects/); return m ? Number(m[1]) : 0; }"""))
    c.expect(total >= 10000, f"advanced mode total too small: {total}")
    c.page.wait_for_function(
        """() => { const b = document.querySelector('#busy');
                   return !b || b.classList.contains('hidden'); }""", timeout=60000)
    c.expect(rows(c) > 0, "advanced mode must render rows")
    c.page.evaluate(
        """() => fetch('/api/settings', { method: 'POST',
             headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify({ adv_mode: false }) })""")
    c.assert_clean()


@check("desktop-catalog-direction-b-density", "desktop")
def desktop_catalog_direction_b_density(c):
    """The compact primary Catalog table follows Direction B, while the
    existing min-visibility gate continues to filter its result set."""
    c.page.set_viewport_size({"width": 1672, "height": 941})
    boot(c)
    c.page.click("[data-tab='targets']")
    c.page.wait_for_selector("#targetRows tr[data-id]:visible")
    heads = c.page.evaluate(
        """() => Array.from(document.querySelectorAll('#targetTable thead th'))
                   .map(th => th.textContent.trim())""")
    expected = ["#", "Designation", "Common name", "Type", "Mag", "Alt now",
                "Night %", "Best window", "Status"]
    c.expect_eq(heads, expected, f"the primary Catalog column order changed: {heads}")
    state = c.page.evaluate("""() => {
      const wrap = document.querySelector('.catalog-results .tablewrap');
      const table = document.querySelector('#targetTable');
      const rows = [...document.querySelectorAll('#targetRows tr[data-id]')];
      const pager = document.querySelector('#targetPager');
      const r = el => { const x = el?.getBoundingClientRect(); return x &&
        {left:x.left,right:x.right,top:x.top,bottom:x.bottom,width:x.width,height:x.height}; };
      return {rows: rows.length, wrap: r(wrap), table: r(table), pager: r(pager),
        tableScroll: table?.scrollWidth, viewport: wrap?.clientWidth,
        pageWidth: document.documentElement.scrollWidth, innerWidth,
        forbidden: document.querySelectorAll('#targetRows .tierbadge, #targetRows td.frame').length,
        sort: document.querySelector('#fSort')?.value};
    }""")
    c.expect(state["rows"] == 20,
             f"the desktop Catalog should show a 20-row page: {state}")
    c.expect(state["pager"] and state["pager"]["height"] > 0 and state["pager"]["top"] < 941,
             f"the Catalog pager must fit the first viewport: {state}")
    c.expect(state["tableScroll"] <= state["viewport"] + 1 and
             state["pageWidth"] <= state["innerWidth"] + 1,
             f"the nine columns must fit without clipping or page overflow: {state}")
    c.expect(state["forbidden"] == 0 and state["sort"] == "tier",
             f"secondary popularity sorting stays, but tier/frame cells do not: {state}")
    before = int(c.page.evaluate(
        """() => Number((document.querySelector('#resultCount')?.textContent || '')
                     .match(/^\\s*([\\d,]+)/)?.[1].replaceAll(',', '') || 0)"""))
    c.page.evaluate(
        """() => { const el = document.querySelector('#fMinVis');
                   el.value = 60;
                   el.dispatchEvent(new Event('input', { bubbles: true })); }""")
    c.page.wait_for_function(
        """() => /below 60%/.test(document.querySelector('#resultCount')?.textContent || '')""",
        timeout=60000)
    pcts = c.page.evaluate(
        """() => Array.from(document.querySelectorAll('#targetRows tr'))
                   .map(tr => parseInt(tr.children[6]?.textContent || '', 10))
                   .filter(v => Number.isFinite(v))""")
    c.expect(pcts and min(pcts) >= 60,
             f"a row below the threshold survived the gate: {sorted(pcts)[:5]}")
    after_gate = int(c.page.evaluate(
        """() => Number((document.querySelector('#resultCount')?.textContent || '')
                     .match(/^\\s*([\\d,]+)/)?.[1].replaceAll(',', '') || 0)"""))
    c.expect(after_gate < before, "the 60% gate must remove rows from the filtered result set")

    # 0 disables the gate again
    c.page.evaluate(
        """() => { const el = document.querySelector('#fMinVis');
                   el.value = 0;
                   el.dispatchEvent(new Event('input', { bubbles: true })); }""")
    c.page.wait_for_function(
        """() => !/below/.test(document.querySelector('#resultCount')?.textContent || '')""",
        timeout=60000)
    after_reset = int(c.page.evaluate(
        """() => Number((document.querySelector('#resultCount')?.textContent || '')
                     .match(/^\\s*([\\d,]+)/)?.[1].replaceAll(',', '') || 0)"""))
    c.expect(after_reset >= before, "0% must show every row again")
    c.page.evaluate(
        """() => fetch('/api/settings', { method: 'POST',
             headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify({ min_vis: 1 }) })""")

    # ---- layout stability: progress and counts must not move anything -------
    def desk_geometry():
        return c.page.evaluate(
            """() => { const tb = document.querySelector('#topbar');
                 const t = document.querySelector('#targetTable');
                 const ths = Array.from(t.querySelectorAll('thead th'))
                              .map(th => Math.round(th.getBoundingClientRect().width));
                 return { barH: Math.round(tb.getBoundingClientRect().height),
                          cols: ths }; }""")

    g0 = desk_geometry()
    c.page.evaluate(
        """() => { const b = document.querySelector('#busy');
             b.classList.remove('hidden');
             document.querySelector('#busy .busy-txt').textContent =
               'computing visibility… 100%'; }""")
    c.page.wait_for_timeout(200)
    g1 = desk_geometry()
    c.page.evaluate(
        """() => { const b = document.querySelector('#busy');
             b.classList.add('hidden');
             document.querySelector('#busy .busy-txt').textContent = ''; }""")
    c.page.wait_for_timeout(200)
    g2 = desk_geometry()
    c.expect(g0["barH"] == g1["barH"] == g2["barH"],
             f"the busy chip moves the topbar: {g0['barH']} -> {g1['barH']} -> {g2['barH']}")
    c.expect(g0["cols"] == g1["cols"],
             f"the columns changed when the status appeared: {g0['cols']} -> {g1['cols']}")

    c.page.evaluate(
        """() => { document.querySelector('#resultCount').textContent =
             '12,176 of 12,176 objects · 1,234 below 60% · filtered'; }""")
    c.page.wait_for_timeout(200)
    g3 = desk_geometry()
    c.expect(g3["barH"] == g0["barH"],
             f"the result count re-wrapped the filter bar: {g0['barH']} -> {g3['barH']}")
    c.assert_clean()


@check("desktop-scrub-window", "desktop")
def desktop_scrub_window(c):
    """View-full-day widens the scrub to the whole day and clamps."""
    boot(c)

    def span():
        return c.page.evaluate(
            """() => { const s = document.querySelector('#timeSlider');
                 return { min: Number(s.min), max: Number(s.max),
                          value: Number(s.value) }; }""")

    before = span()
    c.page.evaluate(
        """() => { const cb = document.querySelector('#viewFullDay');
             cb.checked = true;
             cb.dispatchEvent(new Event('change', { bubbles: true })); }""")
    try:
        c.page.wait_for_function(
            """(prev) => { const s = document.querySelector('#timeSlider');
                 return (Number(s.max) - Number(s.min)) > prev; }""",
            arg=before["max"] - before["min"], timeout=15000)
    except Exception:
        raise AssertionError("View full day did not widen the scrub range")
    after = span()
    c.expect(after["min"] <= after["value"] <= after["max"],
             f"the selected time must stay inside the range: {after}")
    label = text(c, "#timeLabel")
    c.expect(label, "the scrub must carry a heading/time label")
    c.assert_clean()


def _wide_png(w: int = 1024, h: int = 16) -> Path:
    """A real RGB PNG wide enough that a crop keeps >=16 px (the desktop refuses
    to keep a sliver). Written once under the temp dir - no third-party lib."""
    p = Path(tempfile.gettempdir()) / "ns-crop-gate.png"
    if not p.exists():
        raw = b"".join(b"\x00" + bytes([40, 60, 90]) * w for _ in range(h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                      + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return p


PANO_READY = """(prev) => window.Pano && window.Pano.cur
     && window.Pano.cur.id !== prev
     && window.Pano.img && window.Pano.img.complete"""


def upload_pano(c, png, prev, timeout: float = 180000.0, attempts: int = 2):
    """Upload `png` and wait for the NEW panorama to be selected and DECODED.

    TR-12 (0.6.32 review): the first upload round-trip on a COLD exe overshot
    even the 90 s budget this check was raised to (measured 91.9 s on a first
    launch, then 3.0 s on an immediate re-run of the same check), so the release
    gate could go red for a cold machine and pass on a re-run. Two changes make
    the wait robust instead of lucky: a generous 180 s budget (above the measured
    worst case) and ONE retry, which is safe because the wait asks for a
    DIFFERENT panorama id than `prev` - a second upload is simply another id."""
    # The same gate server may already contain a selected panorama from an
    # earlier check. None is not a valid "previous image" sentinel then.
    if prev is None:
        prev = c.page.evaluate("() => window.Pano?.cur?.id || null")
    last = None
    for _ in range(attempts):
        c.page.set_input_files("#panoFile", png)
        try:
            c.page.wait_for_function(PANO_READY, arg=prev, timeout=timeout)
            return c.page.evaluate("() => window.Pano.cur.id")
        except Exception as exc:  # noqa: BLE001 - retried once, then raised
            last = exc
    raise AssertionError(
        f"the panorama upload/decode round-trip did not land after {attempts} "
        f"attempt(s) of {timeout / 1000:.0f} s: {last}")


@check("desktop-pano-crop-fov", "desktop")
def desktop_pano_crop_fov(c):
    """The desktop crop must store the KEPT-band FOV (via the shared core
    helper), not the retired removed-fraction formula: select-all -> 360,
    center 80% -> 288 (the bug stored 72), off-center 20% -> 72 / az 108."""
    boot(c)
    c.page.click('.tab[data-tab="horizon"]')
    c.page.wait_for_selector("#panoFile", state="attached", timeout=30000)
    # the editor is tall; give the viewport room so the crop drag lands on the
    # canvas instead of below the fold
    c.page.set_viewport_size({"width": 1366, "height": 1500})
    png = str(_wide_png())
    cases = [
        (0.0, 1.0, 360.0, 0.0),    # select all keeps the full frame
        (0.1, 0.9, 288.0, 36.0),   # center 80%
        (0.3, 0.5, 72.0, 108.0),   # off-center 20%
    ]
    # TR-12: pay the COLD first-upload/decode cost ONCE, outside the timed cases
    # (a fresh onefile exe unpacks to %TEMP% on first launch; measured 91.9 s on
    # the old 90 s budget, 3.0 s once warm).
    prev = upload_pano(c, png, None, timeout=240000.0, attempts=2)
    c.expect(prev, "a panorama must be selected after the warm-up upload")
    for x0, x1, want_fov, want_az in cases:
        pid = upload_pano(c, png, prev)
        prev = pid
        c.expect(pid, "a panorama must be selected after upload")
        # default full-frame calibration for this fresh image
        c.page.evaluate(
            """() => { const f = document.querySelector('#fovAz');
                 f.value = '360'; f.dispatchEvent(new Event('input', { bubbles: true }));
                 const a = document.querySelector('#azOffset');
                 a.value = '0'; a.dispatchEvent(new Event('input', { bubbles: true })); }""")
        c.page.click("#toolCrop")
        c.page.locator("#panoCanvas").scroll_into_view_if_needed()
        c.page.wait_for_timeout(200)
        box = c.page.locator("#panoCanvas").bounding_box()
        c.expect(box and box["width"] > 20, "the desktop pano canvas must have a size")
        y = box["y"] + box["height"] * 0.5
        sx = box["x"] + box["width"] * x0
        ex = box["x"] + box["width"] * x1
        c.page.mouse.move(sx, y)
        c.page.mouse.down()
        for i in range(1, 9):
            c.page.mouse.move(sx + (ex - sx) * i / 8, y)
        c.page.mouse.up()
        c.page.wait_for_timeout(150)
        c.page.click("#btnCropApply")
        # Full-frame crop keeps the same FOV, so a matching API value alone
        # could let the next upload race an unfinished crop. Wait for the
        # editor's complete publication/reselection before reading the row.
        c.page.wait_for_function("""([pid, want]) => window.Pano &&
          !window.Pano.cropApplying && window.Pano.cur?.id === pid &&
          !window.Pano.pending && Math.abs(Number(window.Pano.cur.fov) - want) < 0.5""",
            arg=[pid, want_fov], timeout=30000)
        c.page.wait_for_function(
            """([pid, want]) => fetch('/api/panoramas').then((r) => r.json())
                 .then((rows) => { const p = rows.find((x) => x.id === pid);
                   return !!p && Math.abs(Number(p.fov) - want) < 0.5; })""",
            arg=[pid, want_fov], timeout=20000)
        row = c.page.evaluate(
            """async (pid) => { const rows = await (await fetch('/api/panoramas')).json();
                 return rows.find((r) => r.id === pid) || null; }""", pid)
        c.expect(abs(float(row["fov"]) - want_fov) < 0.5,
                 f"stored FOV must be the kept fraction for {x0}-{x1}: "
                 f"{row['fov']} vs {want_fov}")
        c.expect(abs(float(row["az_offset"]) - want_az) < 0.05,
                 f"stored az_offset for {x0}-{x1}: {row['az_offset']} vs {want_az}")
    c.assert_clean()


@check("desktop-pano-crop-pending-save", "desktop")
def desktop_pano_crop_pending_save(c):
    """An in-flight and a queued pre-crop save must precede publication."""
    boot(c)
    c.page.click('.tab[data-tab="horizon"]')
    c.page.set_viewport_size({"width": 1366, "height": 1500})
    pid = upload_pano(c, str(_wide_png()), None, timeout=240000.0)
    pending = []
    operations = []
    c.page.expose_function("__ns_crop_pending_saves", lambda: len(operations))

    def wait_saves(count):
        deadline = monotonic() + 10
        while c.page.evaluate("() => window.__ns_crop_pending_saves()") != count:
            if monotonic() > deadline:
                raise AssertionError(f"expected {count} held save requests, got {len(operations)}")

    def hold_save(route):
        operations.append(("save", route.request.post_data_json))
        pending.append(route)

    def crop(route):
        if route.request.method == "POST":
            operations.append(("crop", None))
        route.continue_()

    c.page.route(f"**/api/panorama/{pid}", hold_save)
    c.page.route(f"**/api/panorama/{pid}/image", crop)
    c.page.evaluate("""() => {
      const fov = document.querySelector('#fovAz');
      fov.value = '360'; fov.dispatchEvent(new Event('input', {bubbles: true}));
      const az = document.querySelector('#azOffset');
      az.value = '0'; az.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    c.page.locator("#btnSaveProfile").click()
    wait_saves(1)
    # This edit occurs while the explicit save is in flight. It must survive
    # that response and be persisted before cropping replaces the image.
    c.page.evaluate("""() => {
      const bottom = document.querySelector('#altBottom');
      bottom.value = '-5'; bottom.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    c.page.locator("#toolCrop").click()
    canvas = c.page.locator("#panoCanvas")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    y = box["y"] + box["height"] / 2
    c.page.mouse.move(box["x"] + box["width"] * .1, y)
    c.page.mouse.down()
    c.page.mouse.move(box["x"] + box["width"] * .9, y, steps=8)
    c.page.mouse.up()
    c.page.locator("#btnCropApply").click()
    c.page.wait_for_function("() => window.Pano.cropApplying")
    c.expect_eq([kind for kind, _ in operations], ["save"],
                "crop must await the earlier explicit save")
    c.expect(c.page.locator("#fovAz").is_disabled(),
             "the editor must stay stable during crop publication")
    pending.pop(0).continue_()
    wait_saves(2)
    c.expect_eq([kind for kind, _ in operations], ["save", "save"],
                "the newer queued edit must be saved before the crop")
    c.expect_eq(operations[1][1]["alt_bottom"], -5,
                "an edit made during the first save must not be discarded")
    pending.pop(0).continue_()
    c.page.wait_for_function("""pid => !window.Pano.cropApplying &&
      window.Pano.cur?.id === pid && window.Pano.cur.fov === 288 &&
      !window.Pano.pending""", arg=pid, timeout=30000)
    c.expect_eq([kind for kind, _ in operations], ["save", "save", "crop"],
                "no pre-crop save may follow the image replacement")
    c.page.route(f"**/api/panorama/{pid}", lambda route: route.continue_())
    # Leaving the editor exercises its persistence boundary after all crop
    # requests have settled, catching snapshots that would undo the crop later.
    c.page.locator('#tabs [data-tab="tonight"]').click()
    row = c.page.evaluate("""async pid =>
      (await (await fetch('/api/panoramas')).json()).find(p => p.id === pid)""", pid)
    c.expect_eq(row["fov"], 288, "the cropped FOV must remain stable after navigation")
    c.expect_eq(row["az_offset"], 36, "the kept band's offset must remain stable")
    c.expect_eq(row["alt_bottom"], -5, "the newer user edit must remain stored")
    c.assert_clean()
