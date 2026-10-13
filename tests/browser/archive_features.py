"""Browser acceptance for health workflows and session FITS previews."""
import io
import json

from PIL import Image
from harness import check


@check("desktop-catalog-archive-health", "catalog")
def health_scan(c):
    report = None
    modes, held_cancel, held_latest = [], [], []
    hold_cancel = hold_latest = False
    hostile = '<img src=x onerror="window.__healthInjected=true">.fit'
    def route_handler(route):
        nonlocal report
        path = route.request.url.split("/api/library/health", 1)[1]
        if route.request.method == "GET" and not path and hold_latest:
            held_latest.append(route); return
        if route.request.method == "POST" and not path:
            mode = route.request.post_data_json["mode"]; modes.append(mode)
            report = dict(id=f"health-{len(modes)}", mode=mode, status="running", phase="checking",
                archive="D:/archive", total=2, processed=1, verified=0, metadata_unchanged=0,
                unverified=1, findings=[], finding_counts={}, baseline_sources={"import": 2},
                omitted_findings=0, elapsed_seconds=1, finding_limit=10000)
        elif path.endswith("/cancel"):
            if hold_cancel: held_cancel.append((route, dict(report))); return
            report = {**report, "status": "cancelled"}
        route.fulfill(json=report)
    c.page.route("**/api/library/health**", route_handler)
    c.goto("/catalog.html"); c.wait_hidden("#busy")
    c.page.locator("#btnTopHealth").click(); c.page.locator("#healthQuick").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    c.expect(c.page.locator("#btnTopIngest").is_disabled(), "active scans must lock conflicting actions")
    c.page.locator("#healthClose").click(); c.page.locator("#btnTopHealth").click()
    c.page.locator("#healthCancel").click()
    c.page.wait_for_function("() => !document.querySelector('#healthQuick').disabled")
    c.expect("partial" in c.page.locator("#healthStatus").inner_text(), "cancelled results must say partial")
    c.page.locator("#healthDeep").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    report = {**report, "status": "completed", "processed": 2, "verified": 1,
              "findings": [{"code": "missing", "path": hostile, "message": "Missing exposure."}], "finding_counts": {"missing": 1}}
    c.page.wait_for_function("() => !document.querySelector('#healthDownload').disabled")
    c.expect_eq(modes, ["quick", "deep"], "scan mode must reach API")
    c.expect(hostile in c.page.locator("#healthFindings").inner_text(), "finding paths must remain text")
    c.expect_eq(c.page.locator("#healthFindings img").count(), 0, "finding paths must not create markup")
    with c.page.expect_download() as downloaded: c.page.locator("#healthDownload").click()
    with open(downloaded.value.path(), encoding="utf-8") as stream: payload = json.load(stream)
    c.expect_eq(payload["findings"][0]["path"], hostile, "report must preserve finding text")
    for width in (480, 1440):
        c.page.set_viewport_size({"width": width, "height": 800})
        c.expect(c.page.evaluate("() => { const panel=document.querySelector('#archiveHealthModal .modal-box'); return panel.scrollWidth <= panel.clientWidth+1; }"), "health dialog must fit width")
    c.page.keyboard.press("Escape")
    c.expect_eq(c.page.evaluate("() => document.activeElement.id"), "btnTopHealth", "closing restores opener focus")
    c.page.locator("#btnTopHealth").click(); c.page.locator("#healthQuick").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    hold_cancel = True
    with c.page.expect_request(lambda request: request.url.endswith("/cancel")): c.page.locator("#healthCancel").click()
    c.page.evaluate("() => 0")
    report = {**report, "status": "completed", "processed": 2}
    c.page.wait_for_function("() => !document.querySelector('#healthDeep').disabled")
    c.page.locator("#healthDeep").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    old_route, old_report = held_cancel.pop()
    old_route.fulfill(json={**old_report, "status": "cancelled"})
    c.expect(c.page.locator("#btnTopIngest").is_disabled(), "old cancellation must not unlock new scan")
    report = {**report, "status": "completed", "processed": 2}
    c.page.wait_for_function("() => !document.querySelector('#healthQuick').disabled")
    c.page.locator("#healthQuick").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    c.goto("/catalog.html"); c.wait_hidden("#busy"); hold_latest = True
    with c.page.expect_request(lambda request: request.url.endswith("/api/library/health")): c.page.locator("#btnTopHealth").click()
    c.page.locator("#healthClose").click()
    with c.page.expect_request(lambda request: request.url.endswith("/api/library/health")): c.page.locator("#btnTopHealth").click()
    c.page.evaluate("() => 0")
    c.expect_eq(len(held_latest), 2, "both open requests must be held")
    held_latest[1].fulfill(json=report)
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    held_latest[0].fulfill(json=report)
    report = {**report, "status": "completed", "processed": 2}
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")


@check("desktop-catalog-health-acknowledgements", "catalog")
def health_acknowledgements(c):
    report = dict(id="ack-job", mode="deep", status="completed", archive="D:/archive", total=2,
        processed=2, verified=0, unverified=2, finding_counts={"missing": 1, "content_changed": 1},
        acknowledged_count=0, findings=[
            {"image_id": 101, "code": "missing", "path": "blurry.fit", "message": "Missing exposure."},
            {"image_id": 102, "code": "content_changed", "path": "edited.fit", "message": "Changed contents."}])
    requests, pending = [], []
    def handler(route):
        if route.request.url.endswith("/acknowledge"):
            requests.append(route.request.post_data_json); pending.append(route); report["acknowledging"] = True
        else: route.fulfill(json=report)
    c.page.route("**/api/library/health**", handler)
    c.goto("/catalog.html"); c.wait_hidden("#busy"); c.page.locator("#btnTopHealth").click()
    deletion = c.page.get_by_role("button", name="Acknowledge deletion", exact=True); deletion.wait_for()
    c.page.once("dialog", lambda dialog: dialog.dismiss()); deletion.click()
    c.expect_eq(len(requests), 0, "cancelled confirmation must not mutate")
    for index, (name, action) in enumerate((("Acknowledge deletion", "forget_missing"), ("Accept current contents", "accept_contents"))):
        messages = []
        def confirm(dialog): messages.append(dialog.message); dialog.accept()
        c.page.once("dialog", confirm)
        with c.page.expect_request(lambda request: request.url.endswith("/acknowledge")):
            c.page.get_by_role("button", name=name, exact=True).click()
        c.page.evaluate("() => 0")
        c.expect_eq(requests[-1], {"image_id": 101 + index, "action": action, "confirmed": True}, "action must bind selected finding")
        c.expect("No files will be deleted" in messages[0] if index == 0 else "new integrity baseline" in messages[0], "confirmation must explain consequences")
        c.expect(c.page.locator("#healthQuick").is_disabled(), "acknowledgement must serialize scans")
        c.page.locator("#healthClose").click(); c.page.locator("#btnTopHealth").click()
        report["findings"][index].update(acknowledged_at="2026-10-13T12:00:00Z", acknowledgement=action)
        report.update(acknowledging=False, acknowledged_count=index + 1)
        pending.pop().fulfill(json=report)
        c.page.wait_for_function("() => !document.querySelector('#healthQuick').disabled")
        c.page.wait_for_function("() => document.activeElement.id === 'healthQuick'")
    c.expect("0 open findings" in c.page.locator("#healthStatus").inner_text(), "resolved findings must not remain open")
    c.expect_eq(c.page.locator("#healthFindings button").count(), 0, "resolved findings must not offer another action")
    with c.page.expect_download() as downloaded: c.page.locator("#healthDownload").click()
    with open(downloaded.value.path(), encoding="utf-8") as stream: payload = json.load(stream)
    c.expect_eq(payload["acknowledged_count"], 2, "report must retain acknowledgements")


@check("desktop-catalog-fits-preview", "catalog")
def exposure_preview(c):
    stamp = "2026-08-12T01:10:00"
    hostile = '<img src=x onerror="window.__previewInjected=true">.fit'
    images = [dict(id=601, file="M31/lights/a.fit", orig_name=hostile, date_obs=stamp, imagetyp="light"),
        dict(id=602, file="M31/darks/b.fit", orig_name="dark.fit", date_obs="2026-08-01T01:10:00", session_date_obs=stamp, imagetyp="dark"),
        dict(id=603, file="M31/other/c.fit", orig_name="other.fit", date_obs="2026-08-11T01:10:00", imagetyp="light")]
    sessions = [dict(start=stamp, end=stamp, n_images=2, exposure=120, image_ids=[601, 602], types={"light": {"n": 1, "exposure": 60}, "dark": {"n": 1, "exposure": 60}}, filters={}),
        dict(start="2026-08-11T01:10:00", end="2026-08-11T01:10:00", n_images=1, exposure=60, image_ids=[603], types={"light": {"n": 1, "exposure": 60}}, filters={})]
    library = dict(image_dir="D:/archive", total_images=3, total_exposure=180, objects=[dict(name="M31", object_id=None,
        otype="galaxy", n_images=3, n_sessions=2, total_exposure=180, images=images, sessions=sessions)])
    stream = io.BytesIO(); Image.new("L", (80, 40), color=128).save(stream, format="PNG"); png = stream.getvalue()
    requests, pending = [], []
    hold, missing = True, False
    def fulfill(route): route.fulfill(body=png, content_type="image/png", headers={"X-FITS-HDU": "1", "X-FITS-Size": "800x400", "X-FITS-Plane": "1"})
    def handler(route):
        requests.append(route.request.url)
        if hold: pending.append(route)
        elif missing: route.fulfill(status=404, json={"detail": "The exposure or archive folder is missing."})
        else: fulfill(route)
    c.page.route("**/api/library", lambda route: route.fulfill(json=library))
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[]))
    c.page.route("**/api/images/*/preview", handler)
    c.goto("/catalog.html"); c.wait_hidden("#busy")
    selector = c.page.locator("#libraryPreviewExposure")
    c.expect_eq(len(requests), 0, "previews must load on demand")
    c.expect_eq(selector.locator("option").count(), 2, "selection must use exact session membership")
    c.expect(hostile in selector.inner_text(), "filename must remain literal")
    c.expect_eq(c.page.locator("#libraryExposurePreview img").count(), 1, "filename must not create image markup")
    c.page.locator("#libraryPreviewShow").focus()
    with c.page.expect_request(lambda request: request.url.endswith("/601/preview")): c.page.keyboard.press("Enter")
    selector.focus()
    with c.page.expect_request(lambda request: request.url.endswith("/602/preview")): c.page.keyboard.press("ArrowDown")
    c.page.evaluate("() => 0"); fulfill(pending[1])
    c.page.wait_for_function("() => !document.querySelector('#libraryPreviewImage').hidden")
    fulfill(pending[0])
    c.expect("dark.fit" in c.page.locator("#libraryPreviewImage").get_attribute("alt"), "old response must not replace newer preview")
    c.expect("first plane" in c.page.locator("#libraryPreviewCaption").inner_text(), "cube plane must be identified")
    c.page.locator("#libraryPreviewExpand").click(); c.wait_visible("#fitsPreviewModal")
    c.expect(c.page.evaluate("() => document.querySelector('main').inert"), "expanded viewer must isolate focus")
    c.page.keyboard.press("Escape")
    c.expect_eq(c.page.evaluate("() => document.activeElement.id"), "libraryPreviewExpand", "closing restores focus")
    c.page.locator("#librarySessions tr.library-session").nth(1).click()
    c.expect_eq(selector.input_value(), "603", "session change must update exposure selection")
    c.expect(c.page.locator("#libraryPreviewImage").is_hidden(), "session change must clear old image")
    hold, missing = False, True
    c.allow_failed_responses("one intentionally missing preview")
    c.allow_console_errors("one intentionally missing preview")
    c.page.locator("#libraryPreviewShow").click()
    c.page.wait_for_function("() => document.querySelector('#libraryPreviewStatus').textContent.includes('missing')")
    missing = False; c.page.locator("#libraryPreviewShow").click()
    c.page.wait_for_function("() => !document.querySelector('#libraryPreviewImage').hidden")
    for width in (480, 1440):
        c.page.set_viewport_size({"width": width, "height": 850})
        c.page.locator("#libraryPreviewImage").scroll_into_view_if_needed()
        c.expect(c.page.evaluate("() => { const p=document.querySelector('#librarySessionDetail'); return p.scrollWidth <= p.clientWidth+1; }"), "preview must fit inspector width")
        c.page.locator("#librarySessionActions button").first.scroll_into_view_if_needed()
        c.expect(c.page.locator("#librarySessionActions button").first.is_visible(), "session actions remain reachable")
