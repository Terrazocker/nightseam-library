"""Archive map labels remain useful at crowded, narrow and high-DPI layouts."""

from math import hypot

from harness import check
from catalog_suite import _settle_catalog_layout


_DRAW_RECORDING = """(() => {
  const proto = CanvasRenderingContext2D.prototype;
  const fillRect = proto.fillRect, fillText = proto.fillText;
  const arc = proto.arc, fill = proto.fill;
  const beginPath = proto.beginPath, moveTo = proto.moveTo, lineTo = proto.lineTo, stroke = proto.stroke;
  window.__ns_map_labels = [];
  window.__ns_map_markers = [];
  window.__ns_map_leaders = [];
  window.__ns_map_frame = 0;
  proto.fillRect = function(x, y, w, h) {
    if (this.canvas.id === 'skymapCanvas' && x === 0 && y === 0 &&
        Math.abs(w * this.getTransform().a - this.canvas.width) <= 1 &&
        Math.abs(h * this.getTransform().d - this.canvas.height) <= 1) {
      window.__ns_map_labels = [];
      window.__ns_map_markers = [];
      window.__ns_map_leaders = [];
      this.__ns_map_label_box = null;
      window.__ns_map_frame++;
    } else if (this.canvas.id === 'skymapCanvas') {
      this.__ns_map_label_box = {x, y, w, h};
    }
    return fillRect.apply(this, arguments);
  };
  proto.fillText = function(text, x, y) {
    if (this.canvas.id === 'skymapCanvas') {
      window.__ns_map_labels.push({text, x, y, width: this.measureText(text).width,
        box: this.__ns_map_label_box && {...this.__ns_map_label_box}});
    }
    return fillText.apply(this, arguments);
  };
  proto.arc = function(x, y, radius) {
    if (this.canvas.id === 'skymapCanvas') this.__ns_map_arc = {x, y, radius};
    return arc.apply(this, arguments);
  };
  proto.fill = function() {
    if (this.canvas.id === 'skymapCanvas' && this.__ns_map_arc)
      window.__ns_map_markers.push({...this.__ns_map_arc});
    return fill.apply(this, arguments);
  };
  proto.beginPath = function() {
    this.__ns_map_path = [];
    return beginPath.apply(this, arguments);
  };
  proto.moveTo = function(x, y) {
    this.__ns_map_path?.push({x, y});
    return moveTo.apply(this, arguments);
  };
  proto.lineTo = function(x, y) {
    this.__ns_map_path?.push({x, y});
    return lineTo.apply(this, arguments);
  };
  proto.stroke = function() {
    if (this.canvas.id === 'skymapCanvas' && this.__ns_map_path?.length === 2 &&
        this.getLineDash().length === 0 && this.lineWidth === 1)
      window.__ns_map_leaders.push({start: {...this.__ns_map_path[0]},
        end: {...this.__ns_map_path[1]}});
    return stroke.apply(this, arguments);
  };
})();"""


@check("desktop-catalog-map-object-labels", "catalog")
def desktop_catalog_map_object_labels(c):
    objects = [
        {"name": "Primary library field", "ra_hours": 6.2, "dec_deg": 0, "n_images": 200},
        {"name": "Close library neighbor", "ra_hours": 6.25, "dec_deg": 3, "n_images": 10},
        {"name": "Sparse library field", "ra_hours": 18, "dec_deg": -40, "n_images": 100},
        {"name": "Edge library field", "ra_hours": 0, "dec_deg": 80, "n_images": 80},
        *[{"name": f"Dense library field {i:02d}", "ra_hours": 12 + (i % 8) * .01,
           "dec_deg": -10 + (i // 8) * .1, "n_images": 2} for i in range(40)],
    ]
    for obj in objects:
        obj.update(object_id=None, otype="nebula", n_sessions=1,
                   total_exposure=obj["n_images"] * 60, images=[], sessions=[])
    c.page.add_init_script(_DRAW_RECORDING)
    c.page.route("**/api/library", lambda route: route.fulfill(json={
        "image_dir": "", "total_images": sum(o["n_images"] for o in objects),
        "total_exposure": sum(o["total_exposure"] for o in objects), "objects": objects}))
    c.goto()
    c.page.locator("#libraryObjectList [data-library-object]").first.wait_for()
    c.expect_eq(c.page.locator("#libraryDropZone, #btnLibraryIngest, .library-import-dock").count(), 0,
                "Library must not retain the redundant bottom import panel")
    c.page.locator("#catalog-tab-import").click()
    c.expect(c.page.locator("#btnIngest").is_visible() and c.page.locator("#fitFiles").count() == 1,
             "the dedicated Import Exposures workflow must remain available")
    c.page.locator("#catalog-tab-skymap").click()
    c.page.locator("#smCons").uncheck()
    c.expect(c.page.locator("#smLabels").is_checked(),
             "useful object names must be visible by default")
    c.expect_eq(c.page.locator("#smSize").count(), 0,
                "the removed dot-integration explanation must not remain in the map controls")
    canvas = c.page.locator("#skymapCanvas")

    def labels():
        return c.page.evaluate("() => window.__ns_map_labels.filter(l => l.text.includes('library field') || l.text === 'Close library neighbor')")

    def wait_labels():
        c.page.wait_for_function("() => window.__ns_map_labels.some(l => l.text === 'Primary library field')")

    original = c.page.viewport_size
    try:
        for width in (1366, 900, 480):
            c.page.set_viewport_size({"width": width, "height": 900})
            canvas.scroll_into_view_if_needed()
            _settle_catalog_layout(c.page)
            wait_labels()
            painted = labels()
            c.expect(2 <= len(painted) < len(objects),
                     f"decluttering must leave useful names without labeling every crowded object at {width}px")
            box = canvas.bounding_box()
            logical = canvas.evaluate("node => ({width: node.clientWidth, height: node.clientHeight})")
            for label in painted:
                c.expect(label["x"] >= 0 and label["x"] + label["width"] <= logical["width"] and
                         0 <= label["y"] <= logical["height"],
                         f"map names must remain inside the fitted plot at {width}px: {label}")
            for index, label in enumerate(painted):
                for other in painted[index + 1:]:
                    c.expect(label["x"] + label["width"] <= other["x"] or
                             other["x"] + other["width"] <= label["x"] or
                             abs(label["y"] - other["y"]) >= 18,
                             f"visible default names must not overlap at {width}px")
            markers = c.page.evaluate("() => window.__ns_map_markers")
            leaders = c.page.evaluate("() => window.__ns_map_leaders")
            linked = 0
            for label in painted:
                obj = next(obj for obj in objects if obj["name"] == label["text"])
                px = logical["width"] * obj["ra_hours"] / 24
                py = logical["height"] * (1 - (obj["dec_deg"] + 90) / 180)
                marker = next(marker for marker in markers if
                              hypot(marker["x"] - px, marker["y"] - py) < .1)
                rect = label["box"]
                ex = min(max(px, rect["x"]), rect["x"] + rect["w"])
                ey = min(max(py, rect["y"]), rect["y"] + rect["h"])
                distance = hypot(ex - px, ey - py)
                matches = [line for line in leaders if
                           hypot(line["end"]["x"] - ex, line["end"]["y"] - ey) < .1 and
                           abs(hypot(line["start"]["x"] - px, line["start"]["y"] - py) -
                               marker["radius"] - 1.5) < .1]
                if label["text"].startswith("Dense") or label["text"] == "Primary library field":
                    c.expect(matches, f"crowded labels need an explicit link to their own dot: {label['text']}")
                    linked += 1
                else:
                    c.expect(distance - marker["radius"] <= 7 or matches,
                             f"a displaced name must have a line back to its own dot: {label['text']}")
                if label["text"] == "Sparse library field":
                    c.expect(distance - marker["radius"] <= 7 and not matches,
                             "an isolated object should keep its name directly beside its dot without an extra line")
            c.expect(linked > 0, f"the crowded fixture must exercise leader lines at {width}px")
            c.page.screenshot(path=str(c.artifacts / f"catalog-object-names-{width}.png"))

        canvas.focus()
        canvas.press("Home")
        canvas.press("Enter")
        c.page.locator("#skymapSelectionContent .library-map-name").filter(
            has_text="Primary library field").wait_for()
        c.page.locator("#smLabels").uncheck()
        c.page.wait_for_function("""() => window.__ns_map_labels.length === 1 &&
          window.__ns_map_labels[0].text === 'Primary library field'""")
        c.expect_eq([label["text"] for label in labels()], ["Primary library field"],
                    "the selected object's name remains visible with general names off")
        box = canvas.bounding_box()
        neighbor = objects[1]
        c.page.mouse.move(box["x"] + box["width"] * neighbor["ra_hours"] / 24,
                          box["y"] + box["height"] * (1 - (neighbor["dec_deg"] + 90) / 180))
        c.page.wait_for_function("() => window.__ns_map_labels.some(l => l.text === 'Close library neighbor')")
        c.expect("Primary library field" in [label["text"] for label in labels()],
                 "hovering a neighboring dot must preserve the selected name")
        c.expect(c.page.evaluate("""() => {
          const canvas = document.querySelector('#skymapCanvas');
          return Math.abs(canvas.width - canvas.clientWidth * devicePixelRatio) <= 1;
        }"""), "labels and markers must share the same DPR-aware canvas")
    finally:
        c.page.set_viewport_size(original)
    c.expect(not c.page_errors, f"map labels must not throw page errors: {c.page_errors}")


@check("desktop-catalog-map-session-sizing", "catalog")
def desktop_catalog_map_session_sizing(c):
    objects = [
        {"name": "Repeated captures", "n_sessions": 8, "n_images": 8, "total_exposure": 80},
        {"name": "One long capture", "n_sessions": 1, "n_images": 2000, "total_exposure": 900000},
        {"name": "One short capture", "n_sessions": 1, "n_images": 1, "total_exposure": 10},
        {"name": "Session-list fallback", "n_sessions": None,
         "sessions": [{"day": "2026-09-29", "n_images": 1} for _ in range(4)]},
        {"name": "No captures", "n_sessions": 0, "n_images": 999, "total_exposure": 999999},
        {"name": "Unavailable count", "n_sessions": "invalid"},
        {"name": "Large session archive", "n_sessions": 1000000000},
    ]
    for index, obj in enumerate(objects):
        obj.update(object_id=None, otype="nebula", ra_hours=2 + index * 3, dec_deg=0,
                   images=[])
        obj.setdefault("n_images", 4)
        obj.setdefault("total_exposure", 240)
        if obj["name"] != "Unavailable count":
            obj.setdefault("sessions", [])
    c.page.add_init_script(_DRAW_RECORDING)
    c.page.route("**/api/library", lambda route: route.fulfill(json={
        "image_dir": "", "total_images": sum(o["n_images"] for o in objects),
        "total_exposure": sum(o["total_exposure"] for o in objects), "objects": objects}))
    c.goto()
    c.page.locator("#libraryObjectList [data-library-object]").first.wait_for()
    c.page.locator("#catalog-tab-skymap").click()
    old_frame = c.page.evaluate("() => window.__ns_map_frame")
    c.page.locator("#smCons").uncheck()
    c.page.wait_for_function("prev => window.__ns_map_frame > prev", arg=old_frame)
    _settle_catalog_layout(c.page)
    c.page.wait_for_function("() => window.__ns_map_markers.length === 7")
    markers = c.page.evaluate("() => window.__ns_map_markers")
    radii = [marker["radius"] for marker in sorted(markers, key=lambda marker: marker["x"])]
    c.expect(radii[0] > radii[1],
             "more capture sessions must produce a larger dot even with fewer images and less exposure")
    c.expect_eq(radii[1], radii[2],
                "equal capture-session counts must have equal radii despite different image/exposure totals")
    c.expect(radii[1] < radii[3] < radii[0],
             "an available session list must provide a truthful fallback count")
    c.expect_eq(radii[4], radii[5], "zero and unavailable counts must use the same safe minimum radius")
    c.expect(0 < radii[4] < radii[1] < radii[0] < radii[6] <= 12,
             "session sizing must remain finite, monotonic and bounded for large archives")
    c.expect_in("Marker size shows capture-session count", c.page.locator("#skymapChartSummary").text_content(),
                "assistive chart instructions must explain the measured count")

    canvas = c.page.locator("#skymapCanvas")
    box = canvas.bounding_box()
    unknown = objects[5]
    c.page.mouse.move(box["x"] + box["width"] * unknown["ra_hours"] / 24,
                      box["y"] + box["height"] / 2)
    c.page.locator("#skymapTip").filter(has_text="capture sessions unavailable").wait_for()
    canvas.click(position={"x": box["width"] * unknown["ra_hours"] / 24,
                           "y": box["height"] / 2})
    c.page.wait_for_function("""() => [...document.querySelectorAll('#skymapSelectionContent dl div')]
      .some(row => row.querySelector('dt')?.textContent === 'Capture sessions' &&
                   row.querySelector('dd')?.textContent === '—')""")
    fallback = objects[3]
    c.page.mouse.move(box["x"] + box["width"] * fallback["ra_hours"] / 24,
                      box["y"] + box["height"] / 2)
    c.page.locator("#skymapTip").filter(has_text="4 capture sessions").wait_for()
    canvas.click(position={"x": box["width"] * fallback["ra_hours"] / 24,
                           "y": box["height"] / 2})
    c.page.wait_for_function("""() => [...document.querySelectorAll('#skymapSelectionContent dl div')]
      .some(row => row.querySelector('dt')?.textContent === 'Capture sessions' &&
                   row.querySelector('dd')?.textContent === '4')""")
    c.page.screenshot(path=str(c.artifacts / "catalog-session-sized-markers.png"))
    c.expect(not c.page_errors, f"session sizing must not throw page errors: {c.page_errors}")
