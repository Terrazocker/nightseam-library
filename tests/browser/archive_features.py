"""Browser acceptance for health workflows and session FITS previews."""
import json

from harness import check, tool


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
    tool(c.page, "#btnTopHealth").click(); c.page.locator("#healthQuick").click()
    c.page.wait_for_function("() => !document.querySelector('#healthCancel').disabled")
    c.expect(c.page.locator("#btnTopIngest").is_disabled(), "active scans must lock conflicting actions")
    c.page.locator("#healthClose").click(); tool(c.page, "#btnTopHealth").click()
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
    c.expect_eq(c.page.evaluate("() => document.activeElement.id"), "btnTools", "closing restores focus to the menu that opened it")
    tool(c.page, "#btnTopHealth").click(); c.page.locator("#healthQuick").click()
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
    with c.page.expect_request(lambda request: request.url.endswith("/api/library/health")): tool(c.page, "#btnTopHealth").click()
    c.page.locator("#healthClose").click()
    with c.page.expect_request(lambda request: request.url.endswith("/api/library/health")): tool(c.page, "#btnTopHealth").click()
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
    c.goto("/catalog.html"); c.wait_hidden("#busy"); tool(c.page, "#btnTopHealth").click()
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
        c.page.locator("#healthClose").click(); tool(c.page, "#btnTopHealth").click()
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


@check("desktop-catalog-health-session-and-all-deletions", "catalog")
def health_session_deletions(c):
    sessions = [dict(id='session-a', object_name='M31', label='Night 2026-08-22', missing_count=2, pending_count=2, acknowledged_count=0),
                dict(id='session-b', object_name='M42', label='Night 2026-08-22', missing_count=1, pending_count=1, acknowledged_count=0)]
    findings = [dict(image_id=index + 1, code='missing', path=f'frame-{index}.fit', message='Missing exposure.',
                     session_id='session-a' if index < 2 else 'session-b') for index in range(3)]
    findings.append(dict(image_id=9, code='content_changed', path='edited.fit', message='Changed contents.'))
    report = dict(id='session-ack', mode='deep', status='completed', total=4, processed=4,
        finding_counts=dict(missing=3, content_changed=1), findings=findings, deletion_sessions=sessions,
        pending_deletions=3, acknowledged_count=0)
    requests, confirmations = [], []
    def handler(route):
        if route.request.url.endswith('/acknowledge'):
            body = route.request.post_data_json
            requests.append(body)
            selected = [group for group in sessions if body['scope'] == 'all' or group['id'] == body.get('session_id')]
            for group in selected:
                group.update(pending_count=0, acknowledged_count=group['missing_count'])
                for item in findings:
                    if item.get('session_id') == group['id']:
                        item.update(acknowledged_at='2026-10-15T12:00:00Z', acknowledgement='forget_missing')
            report.update(pending_deletions=sum(group['pending_count'] for group in sessions),
                          acknowledged_count=sum(group['acknowledged_count'] for group in sessions))
        route.fulfill(json=report)
    def confirm(dialog):
        confirmations.append(dialog.message)
        dialog.accept()
    c.page.route('**/api/library/health**', handler)
    c.goto('/catalog.html'); c.wait_hidden('#busy'); tool(c.page, '#btnTopHealth').click()
    buttons = c.page.get_by_role('button', name='Acknowledge session', exact=True)
    buttons.nth(1).wait_for(state='visible')  # Opening precedes the async report render.
    c.expect_eq(buttons.count(), 2, 'deletions group by object and session')
    c.expect_eq(c.page.locator('#healthAcceptAll').inner_text(), 'Accept all deletions (3)')
    c.page.screenshot(path=str(c.artifacts / 'health-session-deletions-pending.png'), full_page=True)
    c.page.once('dialog', lambda dialog: dialog.dismiss()); buttons.first.click()
    c.expect_eq(len(requests), 0, 'cancelled session confirmation must not acknowledge')
    c.page.once('dialog', confirm); buttons.first.click()
    c.page.wait_for_function("() => document.querySelector('#healthAcceptAll').textContent === 'Accept all deletions (1)'")
    c.expect_eq(requests[0], dict(scope='session', session_id='session-a', action='forget_missing', confirmed=True))
    c.expect_in('2 missing exposures in M31', confirmations[0])
    c.page.wait_for_function("() => !document.querySelector('#healthAcceptAll').disabled")
    c.page.once('dialog', confirm); c.page.locator('#healthAcceptAll').click()
    c.page.wait_for_function("() => document.querySelector('#healthAcceptAll').hidden && !document.querySelector('#healthQuick').disabled")
    c.expect_eq(requests[1], dict(scope='all', action='forget_missing', confirmed=True))
    c.expect(c.page.get_by_role('button', name='Accept current contents', exact=True).is_visible(),
             'accepting all deletions must leave changed contents pending')
    c.page.screenshot(path=str(c.artifacts / 'health-session-deletions.png'), full_page=True)


@check("desktop-catalog-session-compression-symbols", "catalog")
def session_compression_symbols(c):
    summaries = [dict(compressed=2, uncompressed=0, unknown=0),
                 dict(compressed=0, uncompressed=2, unknown=0),
                 dict(compressed=1, uncompressed=1, unknown=0),
                 dict(compressed=0, uncompressed=0, unknown=2)]
    sessions = [dict(label='Night', start=f'2026-08-{12-i:02}T01:10:00',
                     end=f'2026-08-{12-i:02}T01:10:00', n_images=2, exposure=60,
                     image_ids=[], types={}, filters={}, compression=summary)
                for i, summary in enumerate(summaries)]
    library = dict(image_dir='D:/archive', total_images=8, total_exposure=240,
        objects=[dict(name='Compression symbols', object_id=None, n_images=8,
                      n_sessions=4, total_exposure=240, images=[], sessions=sessions)])
    c.page.route('**/api/library', lambda route: route.fulfill(json=library))
    c.page.route('**/api/library/imports', lambda route: route.fulfill(json=[]))
    c.goto('/catalog.html'); c.wait_hidden('#busy')
    symbols = c.page.locator('#librarySessions .session-compression')
    c.expect_eq(symbols.count(), 4, 'every session has a storage symbol')
    shapes = []
    for i, state in enumerate(('compressed', 'uncompressed', 'mixed', 'unknown')):
        symbol = symbols.nth(i)
        c.expect_eq(symbol.get_attribute('data-compression'), state)
        c.expect(symbol.is_visible(), state + ' symbol visible')
        c.expect_eq(symbol.get_attribute('title'), symbol.get_attribute('aria-label'))
        shapes.append(symbol.inner_html())
        button = c.page.locator(f'[data-select-session="{i}"]')
        button.focus(); button.press('Enter')
        c.expect_eq(c.page.locator('#librarySessionFacts dd[title]').get_attribute('title'), symbol.get_attribute('title'))
    c.expect_eq(len(set(shapes)), 4, 'status is conveyed by shape as well as color')
    c.expect_in('Reindex', symbols.nth(3).get_attribute('title'))
    c.page.screenshot(path=str(c.artifacts / 'session-compression-symbols.png'), full_page=True)


@check("desktop-catalog-session-selection", "catalog")
def session_selection(c):
    images=[dict(id=601,file="M31/first/lights/a.fit",orig_name="a.fit",date_obs="2026-08-12T01:10:00",imagetyp="light"),
            dict(id=602,file="M31/second/darks/b.fit",orig_name="b.fit",date_obs="2026-08-11T01:10:00",imagetyp="dark")]
    sessions=[dict(start="2026-08-12T01:10:00",end="2026-08-12T01:10:00",n_images=1,exposure=60,image_ids=[601],types={"light":{"n":1,"exposure":60}},filters={}),
              dict(start="2026-08-11T01:10:00",end="2026-08-11T01:10:00",n_images=1,exposure=30,image_ids=[602],types={"dark":{"n":1,"exposure":30}},filters={})]
    library=dict(image_dir="D:/archive",total_images=2,total_exposure=90,objects=[dict(name="M31",object_id=None,otype="galaxy",n_images=2,n_sessions=2,total_exposure=90,images=images,sessions=sessions)])
    retired=[]
    c.page.on('request',lambda request: retired.append(request.url) if '/preview' in request.url or '/trash' in request.url else None)
    c.page.route('**/api/library',lambda route:route.fulfill(json=library))
    c.page.route('**/api/library/imports',lambda route:route.fulfill(json=[]))
    c.goto('/catalog.html'); c.wait_hidden('#busy')
    rows=c.page.locator('#librarySessions tr.library-session')
    c.expect_eq(rows.count(),2)
    c.expect_eq(c.page.locator('#btnTopTrash,#fitsPreviewModal,#trashListModal,#trashConfirmModal,.session-exposure-row,.session-exposure-tile,[data-preview-image],[data-trash-image],[aria-expanded][data-select-session]').count(),0,'retired controls and expansion markup must be absent')
    first=c.page.locator('[data-select-session="0"]'); second=c.page.locator('[data-select-session="1"]')
    second.click()
    c.expect_eq(rows.nth(1).get_attribute('aria-selected'),'true')
    c.expect_in('Darks',c.page.locator('#librarySessionFacts').inner_text())
    c.expect_eq(c.page.locator('[data-library-anchor]').first.get_attribute('data-library-anchor'),'602','calibration attachment retains exact selected session membership')
    first.press('Enter'); c.expect_eq(rows.first.get_attribute('aria-selected'),'true')
    first.click(); first.click()
    c.expect_eq(c.page.locator('#librarySessions tr').count(),2,'repeated session clicks never expand or add rows')
    c.expect_eq(retired,[],'session selection never requests image previews or trash')
    for width in (480,1440):
        c.page.set_viewport_size({'width':width,'height':850})
        c.page.screenshot(path=str(c.artifacts/f'session-selection-{width}.png'),full_page=True)
