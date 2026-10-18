"""Browser checks for the packaged executable's catalog-only default shell."""

from harness import check
from time import monotonic
import archive_features


@check('desktop-catalog-ingest-fast-safe-control', 'catalog')
def desktop_catalog_ingest_fast_safe_control(c):
    c.goto()
    c.page.locator('#catalog-tab-import').click()
    mode = c.page.locator('#ingestSafeMode')
    c.expect(not mode.is_checked() and mode.is_disabled(), 'Fast starts selected; mode requires compression')
    c.page.locator('#ingestCompressionMode .ingest-mode-switch').hover()
    c.expect(c.page.locator('#ingestModeTip').is_visible(), 'hover explains Fast and Safe')
    c.expect_in('skips restoring each file', c.page.locator('#ingestModeTip').inner_text())
    explanation = c.page.locator('#ingestModeTip').bounding_box()
    c.page.mouse.move(explanation['x'] + 30, explanation['y'] + 25, steps=12)
    c.expect(c.page.locator('#ingestModeTip').is_visible(), 'pointer can move onto the explanation')
    c.page.locator('#ingestModeInfo').focus()
    c.expect(c.page.locator('#ingestModeTip').is_visible(), 'keyboard focus reveals explanation')
    c.page.keyboard.press('Escape')
    c.expect(not c.page.locator('#ingestModeTip').is_visible(), 'Escape dismisses explanation')
    c.page.locator('#ingestModeInfo').click()
    c.expect(c.page.locator('#ingestModeTip').is_visible(), 'tap/click opens explanation')
    c.page.keyboard.press('Escape')
    c.page.locator('#compressOnIngest').check()
    c.expect(mode.is_enabled(), 'compression enables mode switch')
    mode.focus()
    mode.press('Space')
    c.expect(mode.is_checked(), 'Space selects Safe')
    c.page.wait_for_function("() => new DOMMatrix(getComputedStyle(document.querySelector('#ingestSafeMode'), '::after').transform).m41 === 18")
    c.expect(c.page.locator('#ingestModeTip').evaluate("""node => {
      const box = node.getBoundingClientRect();
      return box.left >= 0 && box.right <= innerWidth && box.bottom <= innerHeight &&
        node.contains(document.elementFromPoint(box.left + box.width / 2, box.bottom - 3));
    }"""), 'explanation must be painted within the viewport')
    c.page.screenshot(path=str(c.artifacts / 'ingest-fast-safe.png'))
    mode.press('Space')
    c.expect(not mode.is_checked(), 'Space returns to Fast')
    c.page.keyboard.press('Escape')
    c.page.locator('#compressOnIngest').uncheck()
    c.expect(mode.is_disabled(), 'compression off disables mode')


@check('desktop-catalog-rice-auto-ingest', 'catalog')
def desktop_catalog_rice_auto_ingest(c):
    """Use the opt-in checkbox, physical source file, native codec and Uncompress."""
    import hashlib
    from pathlib import Path
    from fits_fixtures import compressed_gate_image

    c.goto()
    c.page.locator('#catalog-tab-import').click()
    c.expect(not c.page.locator('#compressOnIngest').is_checked(), 'automatic Rice is opt-in')
    c.expect(not c.page.locator('#ingestSafeMode').is_checked(), 'new UI imports default to Fast')
    archive = Path(c.page.request.get(c.url('/api/state')).json()['image_dir']).resolve()
    c.expect(archive.is_relative_to((Path(__file__).resolve().parents[2] / 'logs/browser').resolve()), 'private archive required')
    original = compressed_gate_image(False).replace(b'Compressed gate capture', b'Auto Rice test'.ljust(len(b'Compressed gate capture')))
    source = archive.parent / 'auto-rice-source.fit'
    source.write_bytes(original)
    c.page.locator('#compressOnIngest').check()
    with c.page.expect_response(lambda response: response.url.endswith('/api/images/ingest') and response.status == 200) as response:
        c.page.locator('#fitFiles').set_input_files(str(source))
    result = response.value.json()['results'][0]
    c.expect(result.get('ok') and result.get('compression') == 'rice', str(result))
    c.expect_eq(result.get('compression_mode'), 'fast')
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    c.expect_in('Rice compressed', c.page.locator('#ingestResults').inner_text())
    c.expect((archive / result['stored']).is_file(), 'compressed exposure is stored')
    c.expect(not (archive / result['stored'][:-3]).exists(), 'no uncompressed archive duplicate remains')
    c.expect_eq(hashlib.sha256(source.read_bytes()).digest(), hashlib.sha256(original).digest(), 'selected source file remains untouched')
    c.page.locator('#ingestSafeMode').check()
    c.page.locator('#catalog-tab-library').click()
    c.page.locator('[data-library-object]').filter(has_text='Auto Rice test').first.click()
    c.page.locator('#librarySessions tr.library-session').first.click()
    c.page.locator('[data-library-add=dark]').click()
    with c.page.expect_response(lambda response: response.url.endswith('/api/images/ingest') and response.status == 200) as response:
        dark = original.replace(b'LIGHT', b'DARK ')
        c.page.locator('#fitFilesCal').set_input_files([{'name': 'auto-dark.fit', 'mimeType': 'application/octet-stream', 'buffer': dark}])
    calibration = response.value.json()['results'][0]
    c.expect(calibration.get('ok') and calibration.get('compression') == 'rice', str(calibration))
    c.expect_eq(calibration.get('compression_mode'), 'safe')
    c.expect_eq(calibration['imagetyp'], 'dark')
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    c.page.locator('#catalog-tab-library').click()
    c.page.locator('[data-library-object]').filter(has_text='Auto Rice test').first.click()
    c.page.locator('#librarySessions tr.library-session').first.click()
    c.page.locator('[data-rice-mode=uncompress]').click()
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Complete')", timeout=30000)
    c.page.locator('#compressionClose').click()
    c.expect_eq((archive / result['stored'][:-3]).read_bytes(), original)
    c.expect_eq((archive / calibration['stored'][:-3]).read_bytes(), dark)
    c.expect_eq(source.read_bytes(), original)


@check('desktop-catalog-rice-ingest-chunks', 'catalog')
def desktop_catalog_rice_ingest_chunks(c):
    """Every multipart chunk carries the captured option and the control locks."""
    requests = []

    def accept(route):
        body = route.request.post_data_buffer
        c.expect(b'name="compress"\r\n\r\ntrue' in body, 'every chunk explicitly requests compression')
        c.expect(b'name="compression_mode"\r\n\r\nsafe' in body, 'every chunk carries the captured Safe choice')
        count = body.count(b'name="files"; filename=')
        requests.append(count)
        c.expect(c.page.locator('#compressOnIngest').is_disabled(), 'checkbox locked during upload')
        c.expect(c.page.locator('#ingestSafeMode').is_disabled(), 'mode locked during upload')
        results = [{'file': f'fixture-{len(requests)}-{i}.fit', 'ok': True, 'compression': 'rice'} for i in range(count)]
        if len(requests) == 2:
            results[-1].update(compression='unchanged', compression_reason='Floating point FITS stored unchanged.')
        route.fulfill(json={'results': results})
    c.page.route('**/api/images/ingest', accept)
    c.goto()
    c.page.locator('#catalog-tab-import').click()
    c.page.locator('#compressOnIngest').check()
    c.page.locator('#ingestSafeMode').check()
    c.page.locator('#fitFiles').set_input_files([{'name': f'chunk-{i}.fit', 'mimeType': 'application/octet-stream', 'buffer': b'SIMPLE  = T'} for i in range(26)])
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    c.expect_eq(requests, [24, 2])
    c.expect_in('25 Rice compressed', c.page.locator('#ingestSummary').inner_text())
    c.expect_in('Floating point FITS stored unchanged.', c.page.locator('#ingestResults').inner_text())


@check('desktop-catalog-rice-job-controls', 'catalog')
def desktop_catalog_rice_job_controls(c):
    """Rejected start, reopen, focus and cancellation keep job state coherent."""
    c.allow_console_errors('one deliberate rejected conversion start (HTTP 422)')
    c.allow_failed_responses('one deliberate rejected conversion start (HTTP 422)')
    library = {'total_images': 1, 'total_exposure': 10, 'objects': [{
        'object_id': None, 'name': 'Rice UI fixture', 'otype': 'galaxy', 'n_images': 1,
        'n_sessions': 1, 'total_exposure': 10, 'sessions': [{'start': '2026-08-12T22:00:00',
            'end': '2026-08-12T22:00:00', 'n_images': 1, 'exposure': 10, 'image_ids': [991], 'types': {}, 'filters': {}}],
        'images': [{'id': 991, 'file': 'Rice UI fixture/session/lights/frame.fit', 'date_obs': '2026-08-12T22:00:00', 'imagetyp': 'light'}]}]}
    state = {'reject': True, 'job': {'id': 'old-job', 'mode': 'copy', 'status': 'completed', 'processed': 1, 'total': 1}}

    def compression_route(route):
        url = route.request.url
        if url.endswith('/cancel'):
            state['job'] = {**state['job'], 'status': 'cancelled'}
        elif route.request.method == 'POST':
            if state['reject']:
                state['reject'] = False
                route.fulfill(status=422, json={'detail': 'Synthetic invalid session'})
                return
            payload = route.request.post_data_json
            c.expect_eq(payload['image_ids'], [991])
            c.expect_eq(payload['compression_mode'], 'fast')
            state['job'] = {'id': 'new-job', 'mode': payload['mode'], 'status': 'running', 'phase': 'publishing', 'processed': 1, 'total': 1, 'published': 0, 'publication_total': 1, 'compression_mode': payload['compression_mode']}
        route.fulfill(json=state['job'])

    c.page.route('**/api/library', lambda route: route.fulfill(json=library))
    c.page.route('**/api/library/imports', lambda route: route.fulfill(json=[]))
    c.page.route('**/api/library/compression**', compression_route)
    c.goto()
    c.page.locator('#librarySessions tr.library-session').first.click()
    c.page.locator('[data-rice-mode=compress]').click()
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.includes('Synthetic invalid session')")
    c.expect(not c.page.locator('#compressionStart').is_disabled(), 'a rejected start can be retried')
    c.page.locator('#compressionSafeMode').uncheck()
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => !document.querySelector('#compressionCancel').disabled")
    c.expect_eq(c.page.locator('#compressionProgress').get_attribute('value'), '1')
    c.expect_eq(c.page.locator('#compressionWriteProgress').get_attribute('value'), '0')
    c.expect_in('Writing and replacing files', c.page.locator('#compressionStatus').inner_text())
    c.expect(c.page.locator('#compressionSafeMode').is_disabled(), 'running mode is locked')
    c.page.screenshot(path=str(c.artifacts / 'compression-writing-progress.png'), full_page=True)
    state['job'].update(published=1, phase='committing')
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Updating catalog')")
    c.expect(c.page.locator('#compressionStart').is_disabled(), 'full bars do not finish catalog publication')
    state['job']['phase'] = 'cleaning'
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Removing retained originals')")
    c.expect(c.page.locator('#btnTopIngest').is_disabled(), 'conversion locks import')
    c.page.locator('#compressionClose').click()
    c.expect(not c.page_errors, f'job controls must have no uncaught script errors: {c.page_errors}')
    c.expect(c.page.locator('#btnTopCopy').evaluate('node => node === document.activeElement'), 'focus returns to an enabled action')
    c.page.locator('#btnTopCopy').click()
    c.page.wait_for_function("() => document.querySelector('#compressionTitle').textContent === 'Compressing session losslessly'")
    c.expect(not c.page.locator('#compressionDestination').is_visible(), 'session resume does not show copy destination')
    c.expect(not c.page.locator('#compressionSafeMode').is_checked(), 'reopening restores the running Fast choice')
    c.page.locator('#compressionCancel').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Cancelled')")
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    c.page.locator('#compressionClose').click()


@check('desktop-catalog-compression-refresh-before-complete', 'catalog')
def desktop_catalog_compression_refresh_before_complete(c):
    library = dict(total_images=1, total_exposure=10, objects=[dict(name='Refresh fixture', object_id=None,
        n_images=1, n_sessions=1, total_exposure=10, sessions=[dict(start='2026-08-22T22:00:00',
        end='2026-08-22T22:00:00', n_images=1, exposure=10, image_ids=[991], types={}, filters={})],
        images=[dict(id=991, file='Refresh fixture/session/lights/a.fit', date_obs='2026-08-22T22:00:00', imagetyp='light')])])
    state = dict(hold=False, job=dict(id='resume-refresh', mode='compress', compression_mode='fast',
        status='running', phase='publishing', total=1, processed=1, publication_total=1, published=0, converted=1, skipped=0))
    held, starts = [], []
    def library_route(route):
        if state['hold']: held.append(route)
        else: route.fulfill(json=library)
    def conversion_route(route):
        if route.request.method == 'POST': starts.append(route.request.post_data_json)
        route.fulfill(json=state['job'])
    c.page.route('**/api/library', library_route)
    c.page.route('**/api/library/imports', lambda route: route.fulfill(json=[]))
    c.page.route('**/api/library/compression**', conversion_route)
    c.goto()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Writing and replacing files')")
    # The background reconnect locks archive buttons. Open the running job's
    # dialog through the existing action without triggering another operation.
    c.page.locator('#btnTopCopy').click()
    c.page.wait_for_function("() => document.querySelector('#compressionTitle').textContent === 'Compressing session losslessly'")
    state['hold'] = True
    state['job'].update(status='completed', phase='complete', published=1)
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Refreshing library')")
    c.expect(c.page.locator('#compressionStart').is_disabled(), 'no completion before library refresh')
    c.page.evaluate('() => 0')
    c.expect_eq(len(held), 1)
    state['hold'] = False
    held.pop().fulfill(json=library)
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Complete')")
    c.expect(c.page.locator('#compressionStart').is_disabled(), 'resumed session cannot restart a hidden Copy action')
    c.expect_eq(starts, [])
    c.page.screenshot(path=str(c.artifacts / 'compression-refreshed-complete.png'), full_page=True)


@check('desktop-catalog-rice-session-roundtrip', 'catalog')
def desktop_catalog_rice_session_roundtrip(c):
    """Packaged native tools, real UI conversion and exact restored bytes."""
    import hashlib
    import json
    from pathlib import Path
    from fits_fixtures import compressed_gate_image

    raw = compressed_gate_image(False).replace(b'Compressed gate capture', b'Rice session test'.ljust(len(b'Compressed gate capture')))
    c.goto()
    c.page.locator('#catalog-tab-import').click()
    with c.page.expect_response(lambda response: response.url.endswith('/api/images/ingest') and response.status == 200) as response:
        c.page.locator('#fitFiles').set_input_files([{'name': 'rice-gate.fit', 'mimeType': 'application/octet-stream', 'buffer': raw}])
    result = response.value.json()['results'][0]
    c.expect(result.get('ok'), str(result))
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    c.page.locator('#catalog-tab-library').click()
    c.page.locator('[data-library-object]').filter(has_text='Rice session test').first.click()
    c.page.locator('#librarySessions tr.library-session').first.click()
    c.expect_eq(c.page.locator('[data-rice-mode=compress]').get_attribute('data-rice-ids'), str(result['id']))
    c.expect_eq(c.page.locator('.session-compression').first.get_attribute('data-compression'), 'uncompressed')
    c.page.locator('[data-rice-mode=compress]').click()
    c.expect(c.page.locator('#compressionModal').is_visible(), 'compression dialog opens')
    c.expect(c.page.locator('#compressionSafeMode').is_checked(), 'manual conversion starts Safe')
    c.page.locator('#compressionVerification summary').click()
    c.expect(c.page.locator('#compressionModeHelp').is_visible(), 'Fast/Safe explanation is keyboard/tap accessible')
    c.page.locator('#compressionSafeMode').focus()
    c.page.locator('#compressionSafeMode').press('Space')
    c.expect(not c.page.locator('#compressionSafeMode').is_checked(), 'Space selects Fast')
    c.page.screenshot(path=str(c.artifacts / 'session-compression-fast-safe.png'), full_page=True)
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Complete')", timeout=30000)
    c.expect_in('1 converted', c.page.locator('#compressionStatus').inner_text())
    c.expect_eq(c.page.locator('#compressionWriteProgress').get_attribute('value'), '1')
    c.page.locator('#compressionClose').click()
    library = c.page.request.get(c.url('/api/library')).json()
    group = next(o for o in library['objects'] if o['name'] == 'Rice session test')
    compressed = group['images'][0]['file']
    c.expect(compressed.endswith('.fz'), compressed)
    c.expect_eq(c.page.locator('.session-compression').first.get_attribute('data-compression'), 'compressed')
    c.page.locator('[data-rice-mode=uncompress]').click()
    c.expect(not c.page.locator('#compressionVerification').is_visible(), 'restoration always checks its original hash')
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Complete')", timeout=30000)
    c.page.locator('#compressionClose').click()
    archive = Path(c.page.request.get(c.url('/api/state')).json()['image_dir']).resolve()
    c.expect_eq(c.page.locator('.session-compression').first.get_attribute('data-compression'), 'uncompressed')
    c.expect(archive.is_relative_to((Path(__file__).resolve().parents[2] / 'logs/browser').resolve()), 'private archive required')
    c.expect_eq(hashlib.sha256((archive / result['stored']).read_bytes()).digest(), hashlib.sha256(raw).digest())
    disk = archive.parent / 'rice-copy-disk'
    disk.mkdir()
    c.page.locator('#btnTopCopy').click()
    c.page.locator('#compressionPath').fill(str(disk))
    c.page.locator('#compressionStart').click()
    c.page.wait_for_function("() => document.querySelector('#compressionStatus').textContent.startsWith('Complete')", timeout=30000)
    c.page.locator('#compressionClose').click()
    saved, = disk.iterdir()
    manifest = json.loads((saved / 'manifest.json').read_text())
    copied = next(r for r in manifest['files'] if r['record']['id'] == result['id'])
    c.expect(copied['rice'] and (saved / copied['stored_file']).is_file(), 'copy contains usable compressed FITS')
    c.expect((saved / 'nightseam.db').is_file(), 'copy includes catalog snapshot')


@check("desktop-catalog-compressed-fits-metadata", "catalog")
def desktop_catalog_compressed_fits_metadata(c):
    """Real UI uploads/readback use the harness's verified private archive."""
    import gzip
    import hashlib
    from pathlib import Path
    from fits_fixtures import compressed_gate_image as image

    c.goto()
    c.page.locator('#catalog-tab-import').click()
    c.expect_in('.fits.gz', c.page.locator('#fitFiles').get_attribute('accept'))
    c.expect_in('.fz', c.page.locator('#fitFilesCal').get_attribute('accept'))
    payloads = {'compressed-gate.fits.gz': gzip.compress(image(False), mtime=0),
                'compressed-gate.fits.fz': image(True),
                'compressed-gate.fz.gz': gzip.compress(image(True), mtime=0)}
    files = [{"name": name, "mimeType": "application/octet-stream", "buffer": data}
             for name, data in payloads.items()]
    with c.page.expect_response(lambda response: response.url.endswith('/api/images/ingest')
                                and response.status == 200) as response:
        c.page.locator('#fitFiles').set_input_files(files)
    results = response.value.json()['results']
    c.expect_eq(len(results), 3)
    c.expect(all(result.get('ok') and not result.get('skipped') for result in results), str(results))
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    archive = Path(c.page.request.get(c.url('/api/state')).json()['image_dir']).resolve()
    # The harness already pins this path before uploads; additionally refuse
    # any direct read outside its own scratch archive.
    scratch = (Path(__file__).resolve().parents[2] / 'logs' / 'browser').resolve()
    c.expect(archive.is_relative_to(scratch) and archive.name == 'archive', 'private archive required')
    for result in results:
        c.expect_eq(result['exposure'], 75.5)
        c.expect_eq(result['date_obs'], '2026-09-15T22:14:05')
        c.expect_eq(result['session_kind'], 'night')
        c.expect_eq(result['telescope'], 'Compressed gate camera')
        stored = (archive / result['stored']).resolve()
        c.expect(stored.is_relative_to(archive), 'stored bytes must stay inside private archive')
        c.expect_eq(hashlib.sha256(stored.read_bytes()).digest(),
                    hashlib.sha256(payloads[result['file']]).digest(), 'original bytes must survive')
    duplicate = c.page.request.post(c.url('/api/images/ingest'), multipart={
        'files': {'name': 'renamed.fits.gz', 'mimeType': 'application/octet-stream',
                  'buffer': payloads['compressed-gate.fits.gz']}}).json()['results'][0]
    c.expect(duplicate.get('skipped'), 'compressed duplicates must retain the original record')
    reindex = c.page.request.post(c.url('/api/library/reindex')).json()
    c.expect_eq(reindex['failed'], 0)
    c.expect_eq(reindex['added'], 0)
    library = c.page.request.get(c.url('/api/library')).json()
    group = next(obj for obj in library['objects'] if obj['name'] == 'Compressed gate capture')
    c.expect_eq(group['n_images'], 3)
    c.page.locator('#catalog-tab-library').click()
    c.page.locator('[data-library-object]').filter(has_text='Compressed gate capture').first.click()
    c.page.locator('#librarySessions tr.library-session').first.wait_for()


@check("desktop-catalog-solar-sessions", "catalog")
def desktop_catalog_solar_sessions(c):
    """Exercise packaged V8, actual uploads and the observing-location form."""
    def capture(stamp, identity, object_name='M31', site=None):
        import struct
        cards = [("SIMPLE", "T"), ("BITPIX", "16"), ("NAXIS", "2"),
                 ("NAXIS1", "4"), ("NAXIS2", "4"),
                 ("OBJECT", repr(object_name)), ("EXPTIME", "60"),
                 ("DATE-OBS", repr(stamp)), ("IMAGETYP", "'LIGHT'")]
        if site:
            cards += [('SITELAT', str(site[0])), ('SITELONG', str(site[1]))]
        header = "".join(f"{key:<8}= {value}".ljust(80) for key, value in cards)
        header += "END".ljust(80)
        offset = sum(map(ord, identity))
        pixels = struct.pack('>16h', *[offset + index for index in range(16)])
        return header.ljust(2880).encode("ascii") + pixels.ljust(2880, b'\0')

    c.goto()
    c.page.wait_for_function("() => document.querySelector('#versionTag').textContent.includes('NightSeam v')")
    c.page.locator('#catalog-tab-import').click()
    c.page.locator('#siteLat').fill('52.52')
    c.page.locator('#siteLon').fill('13.405')
    with c.page.expect_response(lambda response: response.url.endswith('/api/settings')
                                and response.status == 200):
        c.page.locator('#btnSiteSave').click()
    c.page.locator('#toasts .toast').filter(has_text='Observing location saved.').wait_for()
    with c.page.expect_file_chooser() as picked:
        c.page.locator('#btnTopIngest').click()
    picked.value.set_files([
        {"name": name + ".fit", "mimeType": "application/octet-stream",
         "buffer": capture(stamp, name)}
        for name, stamp in [('late', '2026-12-22T06:00:00Z'),
                            ('early', '2026-12-21T16:00:00Z'),
                            ('day', '2026-12-22T12:00:00Z')]])
    c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
    library = c.page.request.get(c.url('/api/library')).json()
    obj = next(obj for obj in library['objects'] if obj['object_id'] == 'ngc224')
    c.expect_eq(obj['n_sessions'], 2, 'a fourteen-hour winter night and following day must remain separate')
    night, day = obj['sessions']
    c.expect_eq([night['session_kind'], day['session_kind']], ['night', 'day'])
    c.expect_eq(night['n_images'], 2, 'reverse import order must preserve whole-night membership')
    c.expect_eq([night['start'], night['end']], ['2026-12-21T16:00:00', '2026-12-22T06:00:00'])
    c.expect(night['session_start'] < night['start'] and night['session_end'] > night['end'],
             'packaged solar windows must cover both captures')
    c.page.locator('#catalog-tab-library').click()
    c.page.locator('[data-library-object]').filter(has_text=obj['name']).first.click()
    c.page.locator('#librarySessions tr.library-session').first.wait_for()
    c.expect_eq(c.page.locator('#librarySessions tr.library-session').count(), 2)
    c.expect_in('Night', c.page.locator('#librarySessions').inner_text())
    c.expect_in('Day', c.page.locator('#librarySessions').inner_text())

    # Supplying a previously missing location must carry an explicit flat along
    # with its chosen target, even when its own capture date/site differ.
    response = c.page.request.post(c.url('/api/settings'), data={'observing_site': None})
    c.expect_eq(response.status, 200)
    def upload(stamp, identity, **fields):
        response = c.page.request.post(c.url('/api/images/ingest'), multipart={
            'files': {'name': identity + '.fit', 'mimeType': 'application/octet-stream',
                      'buffer': capture(stamp, identity, 'M42', (40, -74) if fields else None)},
            **fields})
        c.expect_eq(response.status, 200)
        result = response.json()['results'][0]
        c.expect(result['ok'], 'real packaged calibration upload must succeed')
        return result
    anchor = upload('2026-08-22T23:00:00Z', 'unassigned-anchor')
    c.expect_eq(anchor['session_kind'], 'unassigned')
    upload('2026-09-15T12:00:00Z', 'attached-flat', frame='flat', session_anchor_id=str(anchor['id']))
    response = c.page.request.post(c.url('/api/settings'), data={
        'observing_site': {'latitude': 52.52, 'longitude': 13.405}})
    c.expect_eq(response.status, 200)
    response = c.page.request.post(c.url('/api/library/reindex'))
    c.expect_eq(response.status, 200)
    obj = next(obj for obj in c.page.request.get(c.url('/api/library')).json()['objects']
               if obj['object_id'] == 'ngc1976')
    c.expect_eq(obj['n_sessions'], 1, 'location backfill must preserve an explicit attachment')
    c.expect_eq(obj['sessions'][0]['session_kind'], 'night')
    flat = next(image for image in obj['images'] if image['imagetyp'] == 'flat')
    c.expect_eq(flat['date_obs'], '2026-09-15T12:00:00')
    c.expect_eq([flat['site_lat'], flat['site_lon']], [40, -74])
    c.assert_clean()


def _selected_session_actions(c):
    """Legacy folders use explicit targets; real chooser events reach uploads."""
    import re
    stamp = "2026-08-12T01:10:00"
    session = {"start": stamp, "end": stamp, "undated": False,
               "n_images": 1, "exposure": 60, "image_ids": [701],
               "types": {"light": {"n": 1, "exposure": 60}}, "filters": {}}
    obj = {"name": "Legacy target", "object_id": None, "otype": "nebula",
           "n_images": 1, "n_sessions": 1, "total_exposure": 60,
           "sessions": [session], "images": [
               {"id": 701, "file": "Legacy capture/lights/light.fit",
                "date_obs": stamp, "imagetyp": "light"}]}
    library = {"image_dir": "D:/archive", "objects": [obj],
               "total_images": 1, "total_exposure": 60}
    imports, folders = [], []

    def import_calibration(route):
        body = route.request.post_data or ""
        def field(name):
            match = re.search(r'name="' + name + r'"\r\n\r\n([^\r\n]+)', body)
            return match.group(1) if match else None
        imports.append({"anchor": field("session_anchor_id"), "frame": field("frame"),
                        "session": field("session")})
        frame = imports[-1]["frame"]
        image_id = 701 + len(imports)
        obj["images"].append({"id": image_id,
            "file": f"Legacy capture/20260812_0110/{frame}s/calibration.fit",
            "date_obs": "2030-01-01T00:00:00", "session_date_obs": stamp,
            "imagetyp": frame})
        session["image_ids"].append(image_id)
        session["n_images"] += 1
        session["types"][frame] = {"n": 1, "exposure": 0}
        obj["n_images"] += 1
        library["total_images"] += 1
        route.fulfill(json={"results": [{"file": "calibration.fit", "ok": True}]})

    def open_folder(route):
        folders.append(route.request.post_data_json["rel"])
        route.fulfill(json={"ok": True})

    c.page.route("**/api/library", lambda route: route.fulfill(json=library))
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[]))
    c.page.route("**/api/images/ingest", import_calibration)
    c.page.route("**/api/library/open_folder", open_folder)
    c.goto()
    if c.target == "desktop":
        c.page.locator('#tabs [data-tab="image-catalog"]').click()
    c.page.locator('#librarySessions tr.library-session').first.wait_for()
    c.expect_eq(c.page.locator('#btnTopIngest').inner_text().strip(), "Import",
                "the primary action must use plain Import wording")
    open_button = c.page.locator('#librarySessionActions [data-open-library-folder]')
    open_button.focus()
    open_button.press('Enter')
    c.page.locator('#toasts .toast').filter(has_text='Opening archive folder in the file manager.').wait_for()
    c.expect_eq(folders, ['Legacy capture/lights'], "Open folder must send the actual selected directory")

    for index, frame in enumerate(('dark', 'flat', 'bias'), 1):
        button = c.page.locator(f'#librarySessionActions [data-library-add="{frame}"]')
        c.expect(button.is_enabled(), "legacy exposure folders must allow calibration imports")
        with c.page.expect_file_chooser() as picked:
            button.click()
        c.expect(c.page.locator('#tab-import').is_visible(), "calibration selection must show Import Exposures")
        frame_button = c.page.locator(f'[data-frame-override="{frame}"]')
        if frame_button.count():
            c.expect_eq(frame_button.get_attribute('aria-pressed'), 'true',
                        "the exposure selector must identify the pending calibration type")
        c.expect_in('Legacy target', c.page.locator('.calibration-target').inner_text(),
                    "the import pane must identify the selected target")
        with c.page.expect_response(lambda response: response.url.endswith('/api/images/ingest')
                                    and response.status == 200):
            picked.value.set_files({"name": "calibration.fit", "mimeType": "application/octet-stream",
                                    "buffer": b"SIMPLE  =                    T\n"})
        c.page.wait_for_function("() => !document.querySelector('#btnTopIngest').disabled")
        c.expect_eq(imports[-1], {"anchor": "701", "frame": frame, "session": None},
                    "calibration actions must send an existing exposure anchor and the chosen type")
        c.page.locator('#catalog-tab-library').click()
        c.page.wait_for_function("count => document.querySelector('#libraryFrameCount').textContent.includes(String(count))",
                                 arg=index + 1)
        c.expect_eq(c.page.locator('#librarySessions tr.library-session').count(), 1,
                    "out-of-date calibration metadata must remain associated with the selected session")
        c.expect_eq(c.page.locator('#librarySessionDetail [data-open-library-folder]').count(), index + 1,
                    "session membership must include attached exposures outside the original timestamp range")

    c.allow_failed_responses('one deliberate folder-open failure')
    c.allow_console_errors('one deliberate folder-open failure')
    c.page.unroute("**/api/library/open_folder", open_folder)
    c.page.route("**/api/library/open_folder", lambda route: route.fulfill(
        status=404, json={"detail": "folder not found on disk"}))
    c.page.locator('#librarySessionActions [data-open-library-folder]').click()
    c.page.locator('#toasts .toast').filter(
        has_text='Cannot open archive folder: folder not found on disk').wait_for()
    c.expect(c.page.locator('#librarySessionActions [data-library-add=dark]').is_enabled(),
             "a folder-open failure must leave calibration actions usable")
    c.assert_clean()


@check("desktop-catalog-selected-session-actions", "catalog")
def desktop_catalog_selected_session_actions(c):
    _selected_session_actions(c)


@check("desktop-planner-selected-session-actions", "desktop")
def desktop_planner_selected_session_actions(c):
    _selected_session_actions(c)


def _active_object_workspace(c):
    import csv
    import io
    import json
    from datetime import datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlparse

    def object_record(name, oid, sessions, frames, seconds):
        records, images = [], []
        for i in range(sessions):
            start = datetime(2026, 1, 1, 20, tzinfo=timezone.utc) + timedelta(days=i)
            end = start + timedelta(minutes=30)
            stamp = start.replace(tzinfo=None).isoformat()
            records.append({"undated": False, "start": stamp,
                            "end": end.replace(tzinfo=None).isoformat(), "n_images": frames,
                            "exposure": frames * seconds,
                            "types": {"light": {"n": frames, "exposure": frames * seconds}},
                            "filters": {}})
            images.extend({"file": f"{oid}/session-{i:03d}/lights/frame-{j}.fits",
                           "date_obs": stamp, "imagetyp": "light"}
                          for j in range(frames))
        return {"name": name, "object_id": oid, "designation": oid.upper(),
                "otype": "galaxy", "ra_hours": 6, "dec_deg": 20, "size_am": 120,
                "n_images": sessions * frames, "n_sessions": sessions,
                "total_exposure": sessions * frames * seconds,
                "sessions": records, "images": images}

    objects = [object_record("Andromeda active field", "ngc224", 80, 10, 300),
               object_record("Second active field", "ngc598", 2, 2, 60)]
    library = {"image_dir": "C:/archive", "objects": objects,
               "total_images": sum(o["n_images"] for o in objects),
               "total_exposure": sum(o["total_exposure"] for o in objects)}
    export_requests, unit_requests = [], []

    def export_response(route):
        params = parse_qs(urlparse(route.request.url).query)
        export_requests.append(params)
        selected = [o for o in objects if o["name"] == params["object_name"][0]] \
            if "object_name" in params else objects
        payload = {**library, "objects": selected,
                   "total_images": sum(o["n_images"] for o in selected),
                   "total_exposure": sum(o["total_exposure"] for o in selected)}
        if params.get("format", ["csv"])[0] == "json":
            body, media, suffix = json.dumps(payload), "application/json", "json"
        else:
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["file", "object_name", "imagetyp"])
            for obj in selected:
                writer.writerows([image["file"], obj["name"], "light"] for image in obj["images"])
            body, media, suffix = stream.getvalue(), "text/csv", "csv"
        route.fulfill(body=body, content_type=media, headers={
            "Content-Disposition": f'attachment; filename="scope.{suffix}"'})

    c.page.route("**/api/library", lambda route: route.fulfill(json=library))
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[]))
    c.page.route("**/api/library/export?*", export_response)
    def save_units(route):
        unit_requests.append(route.request.post_data_json["size_unit"])
        route.fulfill(json={"ok": True})
    c.page.route("**/api/settings", save_units)
    c.page.set_viewport_size({"width": 1586, "height": 900})
    c.goto()
    if c.target == "desktop":
        c.page.locator('#tabs [data-tab="image-catalog"]').click()
    c.page.locator('#libraryObjectList [data-library-object]').first.wait_for()
    c.expect_eq(c.page.locator('#libraryArchiveTools, #libraryObjectTotals, #libraryTable').count(), 0,
                "the old global disclosure and duplicate object table must be removed")

    def select_object(name, sessions, images, integration):
        c.page.locator('#libraryObjectList [data-library-object]').filter(has_text=name).click()
        c.page.locator('#libraryHeroIdentity strong').filter(has_text=name).wait_for()
        c.page.wait_for_function("""expected => {
          const values = [...document.querySelectorAll('#libSummary .library-metric')]
            .map(node => ({label: node.querySelector('span').textContent.trim(),
                          value: node.querySelector('b').textContent.trim()}));
          return values.some(v => v.label === 'Sessions' && v.value === expected[0]) &&
                 values.some(v => v.label === 'Images' && v.value === expected[1]);
        }""", arg=[str(sessions), str(images)])
        c.expect_in(integration, c.page.locator('#libSummary').inner_text(),
                    "integration must describe only the active object's light frames")
        c.expect(c.page.evaluate("""() => {
          const summary = document.querySelector('#libSummary').getBoundingClientRect();
          const heading = document.querySelector('.library-title').getBoundingClientRect();
          return summary.top >= heading.top && summary.bottom <= heading.bottom + 1;
        }"""), "active-object information must be part of the top workspace heading")

    select_object(objects[0]["name"], 80, 800, "66h")
    heights = []
    for height in (900, 1400):
        c.page.set_viewport_size({"width": 1586, "height": height})
        _settle_catalog_layout(c.page)
        bounds = _expect_catalog_bounds(c, c.page, '.library-layout', True)
        c.expect(height - bounds["bottom"] <= 24,
                 f"Library must use the bottom space left by the removed import panel: {bounds}")
        heights.append(c.page.locator('.library-workspace .tablewrap').bounding_box()['height'])
    c.expect(heights[1] - heights[0] >= 440,
             "extra window height must reveal more session rows rather than an empty reserved row")
    select_object(objects[1]["name"], 2, 4, "4m")
    c.expect_eq(c.page.locator('#librarySessions tr.library-session').count(), 2,
                "changing active object must replace capture sessions along with summary totals")
    c.expect_eq(c.page.locator('#libExportFmt, #btnLibExport, #libUnit').count(), 0,
                "the separate export and size-unit strip must be removed")
    unit = c.page.locator('.library-size-unit')
    original_symbol = unit.inner_text()
    unit.focus()
    unit.press('Enter')
    c.page.wait_for_function("original => document.querySelector('.library-size-unit').textContent !== original",
                             arg=original_symbol)
    c.expect_eq(len(unit_requests), 1, "switching displayed units must persist the preference")
    c.expect(c.page.evaluate("() => document.activeElement.matches('.library-size-unit')"),
             "switching units must preserve keyboard focus after rerendering")
    c.expect(c.page.evaluate("""() => [...document.querySelectorAll('#libSummary .library-metric, .library-size-unit')]
      .every(node => {const style = getComputedStyle(node); return parseFloat(style.borderTopWidth) === 0 &&
        style.backgroundColor === 'rgba(0, 0, 0, 0)';})"""),
             "metrics and inline units must use the open, unframed presentation")
    with c.page.expect_download():
        c.page.locator('#btnTopExport').click()
    c.expect('object_name' not in export_requests[-1],
             "top Export library must retain the global scope after choosing an object")

    for width, height in ((900, 800), (480, 620)):
        c.page.set_viewport_size({"width": width, "height": height})
        _settle_catalog_layout(c.page)
        _expect_catalog_bounds(c, c.page, '.library-layout')
        unit.scroll_into_view_if_needed()
        c.expect(unit.is_visible(), "inline unit switching must remain reachable at narrow widths")
        if c.target == 'catalog':
            c.expect_eq(c.page.locator('#topbar img, #topbar .catalog-logo').count(), 0,
                        "the compact catalog toolbar must omit the logo")
            c.expect(c.page.locator('#topbar').bounding_box()['height'] <= 52,
                     "the catalog toolbar must fit its controls without an oversized brand row")
        c.page.screenshot(path=str(c.artifacts / f'{c.target}-active-object-{width}.png'))
    if c.target == 'catalog':
        # DejaVu's wider metrics reproduce the Ubuntu 22.04 toolbar wrap even
        # when the controller's default system font happens to be narrower.
        c.page.set_viewport_size({'width': 900, 'height': 800})
        fallback = c.page.add_style_tag(content='body.catalog-only { --font-ui: "DejaVu Sans", system-ui, sans-serif; }')
        try:
            _settle_catalog_layout(c.page)
            c.expect(c.page.locator('#topbar').bounding_box()['height'] <= 52,
                     'wider system fonts must retain the compact toolbar')
            c.expect(c.page.locator('#btnTopIngest span').is_visible(), 'primary Import label remains visible')
            for label in ('Copy library', 'Download support report'):
                button = c.page.get_by_role('button', name=label, exact=True)
                c.expect(button.is_visible() and button.is_enabled(), 'secondary actions remain named and reachable')
            c.expect_eq(c.page.evaluate('getComputedStyle(document.documentElement).fontSize'), '14px',
                        'font fallback must not change interface density')
            c.page.screenshot(path=str(c.artifacts / 'catalog-toolbar-wide-system-font.png'))
        finally:
            fallback.evaluate('(node) => node.remove()')
    c.assert_clean()


@check("desktop-catalog-library-active-object", "catalog")
def desktop_catalog_library_active_object(c):
    _active_object_workspace(c)


@check("desktop-planner-library-active-object", "desktop")
def desktop_planner_library_active_object(c):
    _active_object_workspace(c)


@check("desktop-catalog-archive-folder-errors", "catalog")
def desktop_catalog_archive_folder_errors(c):
    """Existing flat rows stay readable; invalid reindex leaves them intact."""
    def library(files):
        count = len(files)
        stamp = "2026-09-15T21:00:00"
        return {
            "image_dir": "D:/seestar", "total_images": count,
            "total_exposure": count * 10,
            "objects": [{
                "object_id": "ngc6960", "name": "NGC 6960", "otype": "nebula",
                "ra_hours": 20, "dec_deg": 30, "n_images": count,
                "n_sessions": 1, "total_exposure": count * 10,
                "sessions": [{"undated": False, "start": "2026-09-15T20:00:00",
                              "end": "2026-09-15T22:00:00", "n_images": count,
                              "exposure": count * 10,
                              "types": {"light": {"n": count, "exposure": count * 10}},
                              "filters": {}}],
                "images": [{"id": i + 1, "file": path, "date_obs": stamp, "imagetyp": "light"}
                           for i, path in enumerate(files)],
            }],
        }

    fixture = {"library": library([
        f"NGC 6960_sub/Light_NGC_6960_{i:03d}.fits" for i in range(296)])}
    c.page.route("**/api/library", lambda route: route.fulfill(json=fixture["library"]))
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[]))
    c.goto()
    c.page.locator("#librarySessions tr.library-session").first.click()
    folders = c.page.locator("#librarySessionDetail [data-open-library-folder]")
    c.expect_eq(folders.count(), 1, "flat files must produce one actual folder, not 296 filenames")
    c.expect_eq(folders.first.get_attribute("data-open-library-folder"), "NGC 6960_sub",
                "the folder action must name the containing directory")
    c.expect(not c.page.locator("#librarySessionDetail .library-folder-rollup").filter(
        has_text="Light_NGC_6960_").count(), "archive details must not list flat filenames as folders")
    c.expect(c.page.locator("#librarySessionActions [data-library-add=dark]").is_enabled(),
             "a recorded session must support calibration attachments regardless of its folder layout")

    # A genuine session can span many directories without obscuring totals/actions.
    fixture["library"] = library([
        f"NGC 6960_sub/session-{i:03d}/lights/frame.fits" for i in range(80)])
    c.goto()
    c.page.locator("#librarySessions tr.library-session").first.click()
    for width, height in ((1366, 800), (900, 800), (480, 620)):
        c.page.set_viewport_size({"width": width, "height": height})
        c.page.locator(".library-archive-folders").scroll_into_view_if_needed()
        c.expect(c.page.evaluate("""() => {
          const list = document.querySelector('.library-archive-folders');
          const style = getComputedStyle(list);
          const bounds = list.getBoundingClientRect();
          const actions = document.querySelector('#librarySessionActions').getBoundingClientRect();
          // A bounded detail body can clip the nested folder scroller. Assess
          // its painted extent, not the invisible part of its logical box.
          let visibleBottom = bounds.bottom;
          for (let parent = list.parentElement; parent; parent = parent.parentElement) {
            if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(parent).overflowY)) {
              visibleBottom = Math.min(visibleBottom, parent.getBoundingClientRect().bottom);
            }
          }
          return ['auto', 'scroll'].includes(style.overflowY) &&
            list.scrollHeight > list.clientHeight && visibleBottom <= actions.top + 1;
        }"""), f"folder scrolling must stay above session actions at {width}x{height}")
        c.page.locator("#librarySessionActions [data-library-add=dark]").scroll_into_view_if_needed()
        c.expect(c.page.locator("#librarySessionActions [data-library-add=dark]").is_visible(),
                 "actions must remain reachable beneath a long folder list")
        c.expect(c.page.locator("#librarySessionActions [data-library-add=dark]").evaluate("""node => {
          const box = node.getBoundingClientRect();
          return node.contains(document.elementFromPoint(box.left + box.width / 2,
                                                         box.top + box.height / 2));
        }"""), "scrolling folder contents must not intercept the session action button")

    detail = ("Unexpected archive folder structure: NGC 6960_sub. Expected "
              "<object>/<session>/{lights,darks,flats,bias}/file.fit[s] or "
              "<object>/<frame-type>/file.fit[s]. Import flat folders with Import Exposures.")
    c.page.route("**/api/library/reindex", lambda route: route.fulfill(
        status=400, json={"detail": detail}))
    c.allow_failed_responses("one deliberate invalid archive layout response")
    c.allow_console_errors("one deliberate invalid archive layout response")
    c.page.locator("#btnTopReindex").click()
    error = c.page.locator(".archive-reindex-error")
    error.wait_for(state="visible")
    c.expect_in("NGC 6960_sub", error.inner_text(), "reindex must identify the unexpected folder")
    c.expect_in("Import Exposures", error.inner_text(), "reindex must explain how to import raw folders")
    c.page.wait_for_function("() => !document.querySelector('#btnTopReindex').disabled")
    c.expect_eq(c.page.locator("#librarySessionDetail [data-open-library-folder]").count(), 80,
                "a refused reindex must retain the previously displayed library")
    c.assert_clean()


def _catalog_test_files(count):
    return [{"name": f"exposure-{i:02d}.fits", "mimeType": "application/fits",
             "buffer": b"SIMPLE  =                    T\n"}
            for i in range(count)]


def _observe_pending_batches(page, pending):
    # Expose the route queue so waiting pumps Playwright events until the
    # handler has actually received a batch, regardless of machine speed.
    page.expose_function("__ns_pending_batches", lambda: len(pending))


def _wait_pending_batch(page):
    # evaluate awaits the exposed callback's Promise; wait_for_function treats
    # a Promise returned by its predicate as truthy before it resolves.
    deadline = monotonic() + 10
    while page.evaluate("() => window.__ns_pending_batches()") != 1:
        if monotonic() > deadline:
            raise AssertionError("the intercepted import batch did not reach its route handler")


def _expect_host_owned_chrome(c, page):
    # Check draggable/resizable DOM affordances and caption actions rather
    # than relying on two hypothetical class names alone. OS behavior itself
    # is covered by the native window tests, not a Chromium page.
    controls = page.evaluate("""() => [...document.querySelectorAll(
      '.pywebview-drag-region, .window-titlebar, .window-caption, ' +
      '.native-window-controls, .native-resize-handles, [data-window-action], ' +
      '[data-resize-edge], button, [role="button"]')].filter(node => {
        const label = node.getAttribute('aria-label') || node.title || node.textContent;
        return node.matches('.pywebview-drag-region, .window-titlebar, .window-caption, ' +
          '.native-window-controls, .native-resize-handles, [data-window-action], [data-resize-edge]') ||
          /^(minimize|maximize|restore|close) window$/i.test(label.trim());
      }).map(node => node.id || node.className || node.tagName)""")
    c.expect(not controls, f"window chrome must belong to the host: {controls}")


@check("desktop-planner-archive-selection-and-folder-picker", "desktop")
def desktop_planner_archive_selection_and_folder_picker(c):
    c.page.add_init_script("""(() => {
      window.__ns_picker_calls = 0;
      window.pywebview = {api: {
        async host_capabilities() {
          return {window_chrome: 'native', native_folder_picker: false};
        },
        async pick_archive_dir() { window.__ns_picker_calls++; return null; },
      }};
    })();""")
    c.page.route("**/api/library", lambda route: route.fulfill(json={
        "image_dir": "", "total_images": 1, "total_exposure": 300,
        "objects": [{"object_id": None, "name": "Archive test field",
                     "otype": "nebula", "ra_hours": 6, "dec_deg": 0,
                     "n_images": 1, "n_sessions": 1, "total_exposure": 300,
                     "images": [], "sessions": []}]}))
    c.goto()
    c.page.locator("#tabs [data-tab='image-catalog']").click()
    c.page.locator("#libraryObjectList [data-library-object]").wait_for()
    c.page.locator("#catalog-tab-skymap").click()
    canvas = c.page.locator("#skymapCanvas")
    canvas.focus()
    c.page.wait_for_function("() => document.querySelector('#skymapChartSummary').textContent.includes('1 library objects')")
    c.expect_eq(canvas.get_attribute("role"), "application",
                "the interactive map must allow its keyboard commands to reach the canvas")
    canvas.press("Home")
    canvas.press("Enter")
    c.page.locator("#skymapSelectionContent .library-map-name").filter(
        has_text="Archive test field").wait_for()
    c.expect_in("Home or End", c.page.locator("#skymapChartSummary").inner_text(),
                "the chart must describe its keyboard interaction")
    c.page.locator("#catalog-tab-import").click()
    c.page.locator("#btnArcBrowse").click()
    c.page.wait_for_function("() => window.__ns_picker_calls === 1")
    c.expect(not c.page.locator("#toasts .toast").filter(
        has_text="Folder dialog is only available").count(),
        "the planner must use an available native picker bridge")
    c.expect(not c.page_errors, f"planner archive interactions must not throw: {c.page_errors}")


@check("desktop-catalog-import-progress", "catalog")
def desktop_catalog_import_progress(c):
    pending = []
    requests = []
    reindexes = []
    _observe_pending_batches(c.page, pending)

    def hold_batch(route):
        requests.append(route.request)
        pending.append(route)

    c.page.route("**/api/images/ingest", hold_batch)
    c.page.route("**/api/library/reindex", lambda route: (
        reindexes.append(route.request), route.fulfill(json={})))
    c.allow_failed_responses("one deliberate interrupted import request")
    c.allow_console_errors("one deliberate interrupted import request")
    c.goto()
    c.page.locator("#versionTag").filter(has_text="NightSeam v").wait_for()
    c.page.locator("#catalog-tab-import").click()
    progress = c.page.locator("#ingestProgress")
    files = c.page.locator("#fitFiles")

    def select(count):
        with c.page.expect_request("**/api/images/ingest"):
            files.set_input_files(_catalog_test_files(count))
        _wait_pending_batch(c.page)
        c.expect_eq(len(pending), 1, "one batch should be awaiting its response")

    def answer(count, failed=False):
        route = pending.pop(0)
        route.fulfill(json={"results": [
            {"file": f"exposure-{i:02d}.fits", "ok": not (failed and i == 0),
             "error": "Unreadable FITS header" if failed and i == 0 else ""}
            for i in range(count)]})

    def finished(count, total):
        c.page.wait_for_function("""([count, total]) =>
          document.querySelector('#ingestProgress .import-files').textContent ===
            `${count} / ${total} files processed` &&
          !document.querySelector('#btnIngest').disabled""", arg=[count, total])
        elapsed = progress.locator(".import-elapsed").inner_text()
        c.page.clock.run_for(3100)
        c.expect_eq(progress.locator(".import-elapsed").inner_text(), elapsed,
                    "completed elapsed time must stay frozen")
        c.expect(progress.is_visible(), "completed import counters must remain visible")

    select(26)
    c.expect_eq(progress.locator(".import-files").inner_text(),
                "0 / 26 files processed", "pending files are not processed yet")
    c.expect(not progress.locator(".import-failed").is_visible(),
             "successful imports must not show a failure counter")
    c.page.clock.run_for(2100)
    c.expect(progress.locator(".import-elapsed").inner_text() != "00:00 elapsed",
             "elapsed time must advance while a batch is pending")
    c.expect_eq(progress.locator(".import-files").inner_text(),
                "0 / 26 files processed", "time alone must not advance file progress")
    c.expect(c.page.locator("#btnTopIngest").is_disabled() and
             c.page.locator("#btnTopReindex").is_disabled() and
             c.page.locator("#btnTopExport").is_disabled(),
             "other archive actions must be unavailable during import")
    files.set_input_files(_catalog_test_files(2))
    c.page.evaluate("() => document.querySelector('#btnTopReindex').click()")
    c.page.locator("#toasts .toast").filter(
        has_text="Wait for the current archive operation to finish.").wait_for()
    c.expect_eq(len(requests), 1, "repeated file selection must not overlap an import")
    c.expect_eq(len(reindexes), 0, "a reindex must not overlap an import")
    with c.page.expect_request("**/api/images/ingest"):
        answer(24)
    _wait_pending_batch(c.page)
    c.expect_eq(progress.locator(".import-files").inner_text(),
                "24 / 26 files processed", "confirmed response advances file progress")
    c.expect_eq(len(pending), 1, "the remaining files wait for the second response")
    answer(2)
    finished(26, 26)
    c.expect(not progress.locator(".import-failed").is_visible(),
             "completed successful import keeps the failure counter hidden")

    select(26)
    with c.page.expect_request("**/api/images/ingest"):
        answer(24, failed=True)
    _wait_pending_batch(c.page)
    c.expect_eq(progress.locator(".import-failed").inner_text(), "1 failed",
                "a per-file failure has its own counter")
    c.expect(progress.locator(".import-failed").is_visible(),
             "the failure counter appears when a file fails")
    original_viewport = c.page.viewport_size
    try:
        for width in (1586, 1366, 1281, 1280, 1279, 1240, 1101, 900, 621, 620, 480):
            c.page.set_viewport_size({"width": width, "height": 800})
            _settle_catalog_layout(c.page)
            collisions = c.page.evaluate("""() => {
              const busy = document.querySelector('#busy').getBoundingClientRect();
              return [...document.querySelectorAll('#brand, #imageCatalogTools button')]
                .filter(node => {
                  const r = node.getBoundingClientRect();
                  return Math.min(busy.right, r.right) > Math.max(busy.left, r.left) &&
                    Math.min(busy.bottom, r.bottom) > Math.max(busy.top, r.top);
                }).map(node => node.id);
            }""")
            c.expect(not collisions,
                     f"active import status must not overlap toolbar controls at {width}px: {collisions}")
            if width in (1240, 480):
                c.page.screenshot(path=str(c.artifacts / f"active-import-progress-{width}x800.png"))
    finally:
        c.page.set_viewport_size(original_viewport)
    answer(2)
    finished(26, 26)
    c.expect_in("Unreadable FITS header", c.page.locator("#ingestResults").inner_text(),
                "the failed file's reason remains reviewable")

    select(60)
    with c.page.expect_request("**/api/images/ingest"):
        answer(24)
    _wait_pending_batch(c.page)
    pending.pop(0).fulfill(status=503, content_type="application/json",
                          body='{"detail":"temporary import interruption"}')
    finished(24, 60)
    c.expect(not progress.locator(".import-failed").is_visible(),
             "unconfirmed outcomes must not be reported as confirmed file failures")
    c.expect_eq(progress.locator(".import-pending").inner_text(), "12 not attempted",
                "the remaining queue was never submitted")
    c.expect_in("24 file outcomes unconfirmed", progress.inner_text(),
                "an interrupted request must distinguish unknown outcomes")
    c.expect_eq(len(requests), 6, "the interrupted import must stop without another batch")
    c.expect(c.page.locator("#btnTopIngest").is_enabled() and
             c.page.locator("#btnTopExport").is_enabled(),
             "archive actions recover after an interrupted import")

    # A server exception is now a successful HTTP response with explicit
    # partial outcomes. It must halt the remaining browser chunks too.
    select(60)
    with c.page.expect_request("**/api/library"):
        pending.pop(0).fulfill(json={"results": [
            {"file": "exposure-00.fits", "ok": True},
            {"file": "exposure-01.fits", "ok": False, "unconfirmed": True,
             "error": "Import outcome unconfirmed after a database error."},
            *[{"file": f"exposure-{index:02d}.fits", "ok": False,
               "unattempted": True, "error": "Not attempted after the import stopped."}
              for index in range(2, 24)],
        ]})
    finished(1, 60)
    c.expect_eq(progress.locator(".import-pending").inner_text(), "58 not attempted",
                "both unattempted server files and unsent browser chunks remain pending")
    c.expect_in("1 file outcomes unconfirmed", progress.inner_text(),
                "only the exceptional file has an unknown outcome")
    c.expect(not progress.locator(".import-failed").is_visible(),
             "unattempted files and unknown outcomes are not confirmed failures")
    c.expect_eq(len(requests), 7, "explicit partial outcomes must stop later browser chunks")
    c.expect_eq(len(pending), 0, "no later chunk remains in flight after partial completion")
    c.expect(c.page.locator("#btnTopIngest").is_enabled() and
             c.page.locator("#btnTopReindex").is_enabled() and
             c.page.locator("#btnTopExport").is_enabled(),
             "partial completion must unlock archive controls and refresh the library")
    c.expect(not c.page_errors, f"import progress must not throw page errors: {c.page_errors}")


@check("desktop-catalog-import-duplicates", "catalog")
def desktop_catalog_import_duplicates(c):
    pending = []
    _observe_pending_batches(c.page, pending)
    c.page.route("**/api/images/ingest", lambda route: pending.append(route))
    c.goto()
    c.page.locator("#versionTag").filter(has_text="NightSeam v").wait_for()
    c.page.locator("#catalog-tab-import").click()
    progress = c.page.locator("#ingestProgress")
    files = c.page.locator("#fitFiles")

    def result(i, skipped=False, failed=False, restored=False):
        return {"file": f"exposure-{i:02d}.fits", "ok": not failed,
                "skipped": skipped, "restored": restored,
                "reason": "already_imported" if skipped else "original_restored" if restored else "",
                "stored": f"orion/session/lights/exposure-{i:02d}.fits",
                "object": {"name": "Orion Nebula"}, "exposure": 300,
                "date": "2026-09-30T22:00:00", "filter": "Ha", "imagetyp": "light",
                "error": "Unreadable FITS header" if failed else ""}

    def select(count):
        with c.page.expect_request("**/api/images/ingest"):
            files.set_input_files(_catalog_test_files(count))
        _wait_pending_batch(c.page)

    def finish(count):
        c.page.wait_for_function("""count =>
          document.querySelector('#ingestProgress .import-files').textContent ===
            `${count} / ${count} files processed` &&
          !document.querySelector('#btnIngest').disabled""", arg=count)

    select(26)
    c.expect(not progress.locator(".import-skipped").is_visible(),
             "the already-imported counter stays hidden before a duplicate is found")
    with c.page.expect_request("**/api/images/ingest"):
        pending.pop(0).fulfill(json={"results": [
            result(0, skipped=True), result(1, skipped=True),
            *[result(i) for i in range(2, 23)], result(23, failed=True)]})
    _wait_pending_batch(c.page)
    c.expect_eq(progress.locator(".import-files").inner_text(), "24 / 26 files processed",
                "duplicates are processed files, alongside new files and failures")
    c.expect_eq(progress.locator(".import-skipped").inner_text(), "2 already imported",
                "duplicate responses have their own neutral counter")
    c.expect(progress.locator(".import-skipped").is_visible(),
             "duplicate counter becomes visible while a later batch is pending")
    c.expect_eq(progress.locator(".import-failed").inner_text(), "1 failed",
                "only an actual failed file contributes to the failure counter")
    pending.pop(0).fulfill(json={"results": [result(24, skipped=True), result(25, skipped=True)]})
    finish(26)
    c.expect_eq(c.page.locator("#ingestSummary").inner_text(),
                "21 of 26 files imported · 4 already imported · 1 failed",
                "the imported count excludes duplicates even when existing exposure metadata is present")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.ok").count(), 21,
                "only new files receive the successful new-import presentation")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.skipped").count(), 4,
                "duplicates receive a neutral presentation rather than success or failure")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.err").count(), 1,
                "the actual failure remains distinct")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.skipped .obj").first.inner_text(),
                "Already imported · Orion Nebula", "each duplicate clearly states its existing status")

    select(3)
    c.expect(not progress.locator(".import-skipped").is_visible() and
             not progress.locator(".import-failed").is_visible(),
             "new runs reset both conditional outcome counters")
    pending.pop(0).fulfill(json={"results": [result(i, skipped=True) for i in range(3)]})
    finish(3)
    c.expect_eq(progress.locator(".import-skipped").inner_text(), "3 already imported",
                "an all-duplicate import still processes every selected file")
    c.expect(not progress.locator(".import-failed").is_visible(),
             "an all-duplicate selection must never display a failed-files counter")
    c.expect_eq(c.page.locator("#ingestSummary").inner_text(),
                "0 of 3 files imported · 3 already imported",
                "existing frames must not be described as newly imported")

    select(2)
    c.expect(not progress.locator(".import-restored").is_visible(),
             "original restoration counter is reset for a new import")
    pending.pop(0).fulfill(json={"results": [result(0, restored=True), result(1, skipped=True)]})
    finish(2)
    c.expect_eq(progress.locator(".import-restored").inner_text(), "1 original restored",
                "missing archive originals are counted independently of new imports")
    c.expect_eq(c.page.locator("#ingestSummary").inner_text(),
                "0 of 2 files imported · 1 already imported · 1 original restored",
                "restoring an original must not inflate newly imported totals")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.restored .obj").inner_text(),
                "Original restored · Orion Nebula", "restored files have an explicit neutral status")
    c.expect_eq(c.page.locator("#ingestResults .ingest-item.ok").count(), 0,
                "a restored original is not a new imported frame")
    c.expect(not c.page_errors, f"duplicate import status must not throw page errors: {c.page_errors}")


@check("desktop-catalog-library-export", "catalog")
def desktop_catalog_library_export(c):
    import csv
    import io

    c.goto()
    c.page.locator("#versionTag").filter(has_text="NightSeam v").wait_for()
    for format in ("csv",):
        with c.page.expect_download() as captured:
            c.page.locator("#btnTopExport").click()
        download = captured.value
        c.expect_eq(download.suggested_filename, f"nightseam-library.{format}",
                    "the primary export action must download the library CSV")
        c.expect_eq(download.failure(), None, "the library download must complete")
        content = download.path().read_text(encoding="utf-8-sig")
        if format == "csv":
            rows = list(csv.reader(io.StringIO(content)))
            c.expect(rows and "file" in rows[0] and "imagetyp" in rows[0],
                     "the CSV download must contain real library columns")
        c.expect(c.page.locator("#tab-library").is_visible() and
                 c.page.locator("#catalogTabs").is_visible(),
                 "downloading an export must leave the catalog open")
        c.page.wait_for_function("() => !document.querySelector('#btnTopExport').disabled")

    c.allow_failed_responses("one deliberate library export failure")
    c.allow_console_errors("one deliberate library export failure")
    c.page.route("**/api/library/export?*", lambda route: route.fulfill(
        status=503, content_type="application/json",
        body='{"detail":"temporary export failure"}'))
    c.page.locator("#btnTopExport").click()
    c.page.locator("#toasts .toast").filter(
        has_text="Library export failed: temporary export failure").wait_for()
    c.page.wait_for_function("() => !document.querySelector('#btnTopExport').disabled")
    c.expect(c.page.locator("#btnTopIngest").is_enabled() and
             c.page.locator("#tab-library").is_visible(),
             "failed exports must leave an explanatory error and usable catalog controls")
    c.expect(not c.page_errors, f"library export must not throw page errors: {c.page_errors}")


@check('desktop-catalog-support-report', 'catalog')
def desktop_catalog_support_report(c):
    import json

    c.goto()
    c.page.wait_for_function("() => !document.querySelector('#btnTopExport').disabled")
    held = []
    c.page.route('**/api/library/export?*', lambda route: held.append(route))
    c.page.locator('#btnTopExport').click()
    c.page.wait_for_function("() => document.querySelector('#btnTopIngest').disabled")
    button = c.page.get_by_role('button', name='Download support report', exact=True)
    c.expect(button.is_enabled(), 'support remains available during archive work')
    button.focus()
    with c.page.expect_download() as captured:
        button.press('Enter')
    download = captured.value
    c.expect_eq(download.suggested_filename, 'nightseam-support-report.json')
    c.expect_eq(download.failure(), None)
    raw = download.path().read_text(encoding='utf-8')
    report = json.loads(raw)
    c.expect_eq(report['application']['frozen'], True)
    c.expect_eq(report['fits_tools']['integrity'], 'verified')
    c.expect(all(version != 'unavailable' for version in report['runtime']['packages'].values()),
             'frozen distribution metadata is present')
    c.expect_eq(report['client_context']['window_chrome'], 'browser')
    archive = c.page.request.get(c.url('/api/state')).json()['image_dir']
    c.expect(archive not in raw and json.dumps(archive)[1:-1] not in raw,
             'support report excludes the configured archive path')
    c.expect_eq(len(held), 1)
    with c.page.expect_download():
        held.pop().fulfill(content_type='text/csv', body='file,imagetyp\n')
    c.page.wait_for_function("() => !document.querySelector('#btnTopExport').disabled")
    c.expect(not c.page_errors, f'support report must not throw: {c.page_errors}')


def _settle_catalog_layout(page):
    # Emulated window metrics and CSS dynamic viewport units can update on
    # different browser frames. Fake-clock rAFs do not synchronize that change.
    viewport = page.viewport_size
    if viewport:
        page.wait_for_function("""size => innerWidth === size.width &&
          innerHeight === size.height && document.body &&
          Math.abs(document.body.getBoundingClientRect().height - size.height) <= 1
        """, arg=viewport, timeout=10000)
    # The harness's fake clock owns rAF while Chromium owns ResizeObserver.
    # Two clock frames alone do not establish that browser layout has converged.
    page.evaluate("""() => new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)))""")
    page.wait_for_function("""() => {
      const pane = document.querySelector('#tab-skymap');
      if (!pane || pane.hidden) return true;
      const cv = document.querySelector('#skymapCanvas');
      const rect = cv.getBoundingClientRect();
      const plot = document.querySelector('#skymapPlot');
      const height = Math.floor(Math.min(plot.clientHeight, plot.clientWidth / 2));
      if (height <= 0 || Math.abs(rect.height - height) > 1 ||
          Math.abs(rect.width - height * 2) > 1 ||
          Math.abs(cv.width - rect.width * devicePixelRatio) > 1 ||
          Math.abs(cv.height - rect.height * devicePixelRatio) > 1) return false;
      const aligned = (selector, count, coord, start, span) =>
        [...document.querySelectorAll(selector)].every((node, i) => {
          const r = node.getBoundingClientRect();
          const center = coord === 'x' ? r.left + r.width / 2 : r.top + r.height / 2;
          return Math.abs(center - (start + span * i / (count - 1))) <= 1.5;
        });
      return aligned('#skymapRaAxis span', 13, 'x', rect.left, rect.width) &&
        aligned('#skymapDecAxis span', 7, 'y', rect.top, rect.height);
    }""", timeout=10000)


def _catalog_density(page):
    return page.evaluate("""() => {
      const selectors = ['html', 'body', '#brand', '#catalogTabs button',
        '#btnTopIngest', '#libSearch', '#librarySessionsHeading',
        '#librarySessionTable th', '#libraryObjectList .empty-icon'];
      return selectors.map(selector => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return {selector, font: style.fontSize, line: style.lineHeight,
          iconWidth: node.matches('svg') ? rect.width : null,
          controlHeight: node.matches('button, input') ? rect.height : null};
      });
    }""")


def _expect_catalog_bounds(c, page, selector, fit_height=False):
    bounds = page.evaluate("""selector => {
      const region = document.querySelector(selector);
      const rect = region.getBoundingClientRect();
      const headerControls = [...document.querySelectorAll(
        '#imageCatalogTools button, #catalogTabs [role=tab]')].map(node => {
        const r = node.getBoundingClientRect();
        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
      });
      return {left: rect.left, right: rect.right, top: rect.top,
        bottom: rect.bottom, width: innerWidth, height: innerHeight,
        pageWidth: Math.max(document.documentElement.scrollWidth,
          document.body.scrollWidth),
        pageHeight: Math.max(document.documentElement.scrollHeight,
          document.body.scrollHeight),
        controlsFit: headerControls.every(r => r.left >= -1 &&
          r.right <= innerWidth + 1 && r.top >= -1 && r.bottom <= innerHeight + 1)};
    }""", selector)
    c.expect(bounds['left'] >= -1 and bounds['right'] <= bounds['width'] + 1
             and bounds['pageWidth'] <= bounds['width'] + 1
             and bounds['controlsFit'],
             f"catalog region and controls must fit horizontally: {selector}: {bounds}")
    if fit_height:
        c.expect(bounds['top'] >= -1 and bounds['bottom'] <= bounds['height'] + 1
                 and bounds['pageHeight'] <= bounds['height'] + 1,
                 f"wide/short catalog must use available client height: {selector}: {bounds}")
    return bounds


def _expect_catalog_map_geometry(c, page):
    geometry = page.evaluate("""() => {
      const cv = document.querySelector('#skymapCanvas');
      const rect = cv.getBoundingClientRect();
      const plot = document.querySelector('#skymapPlot').getBoundingClientRect();
      const centers = selector => [...document.querySelectorAll(selector)].map(node => {
        const r = node.getBoundingClientRect();
        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
      });
      const ra = centers('#skymapRaAxis span');
      const dec = centers('#skymapDecAxis span');
      return {width: rect.width, height: rect.height, backingWidth: cv.width,
        backingHeight: cv.height, dpr: devicePixelRatio,
        inside: rect.width > 0 && rect.height > 0 && rect.left >= plot.left - 1 &&
          rect.right <= plot.right + 1 && rect.top >= plot.top - 1 &&
          rect.bottom <= plot.bottom + 1,
        raCount: ra.length, decCount: dec.length,
        raError: Math.max(...ra.map((r, i) =>
          Math.abs(r.x - (rect.left + rect.width * i / 12)))),
        decError: Math.max(...dec.map((r, i) =>
          Math.abs(r.y - (rect.top + rect.height * i / 6))))};
    }""")
    c.expect(geometry['inside'] and abs(geometry['width'] - 2 * geometry['height']) <= 1,
             f"map must fit its plot with the 2:1 coordinate geometry: {geometry}")
    c.expect(geometry['raCount'] == 13 and geometry['decCount'] == 7
             and geometry['raError'] <= 1.5 and geometry['decError'] <= 1.5,
             f"axis tick centers must match canvas grid lines, including endpoints: {geometry}")
    c.expect(abs(geometry['backingWidth'] - geometry['width'] * geometry['dpr']) <= 1
             and abs(geometry['backingHeight'] - geometry['height'] * geometry['dpr']) <= 1,
             f"map bitmap must follow displayed size and DPR: {geometry}")


@check("desktop-catalog-only-launch", "catalog")
def desktop_catalog_only_launch(c):
    requests = []
    library_calls = []
    ingest_posts = []
    reindex_calls = []

    def fail_first_library(route):
        library_calls.append(route.request.url)
        if len(library_calls) == 1:
            route.fulfill(status=503, content_type="application/json",
                          body='{"detail":"temporary archive failure"}')
        else:
            route.fulfill(json={
                "image_dir": "C:/archive",
                "total_images": 2,
                "total_exposure": 3600,
                "objects": [{
                    "object_id": "ngc224", "name": "M31",
                    "designation": "NGC 224", "otype": "galaxy",
                    "ra_hours": 6, "dec_deg": 0, "size_am": 190,
                    "n_images": 2, "n_sessions": 1,
                    "total_exposure": 3600,
                    "images": [
                        {"id": 1, "file": "M31/20260927/lights/a.fits"},
                        {"id": 2, "file": "M31/20260927/lights/b.fits"},
                    ],
                    "sessions": [{
                        "undated": True, "start": None, "end": None,
                        "n_images": 2, "exposure": 3600,
                        "types": {"light": {"n": 2, "exposure": 3600}},
                        "filters": {"L": {"n": 2, "exposure": 3600}},
                    }],
                }],
            })

    def mock_ingest(route):
        ingest_posts.append(route.request.post_data or "")
        route.fulfill(json={"results": [{"file": "calibration.fits", "ok": True}]})

    def mock_reindex(route):
        reindex_calls.append(route.request.url)
        route.fulfill(json={"added": 0, "skipped": 0, "failed": 0})

    c.page.on("request", lambda request: requests.append(request.url))
    c.page.add_init_script("""(() => {
      window.pywebview = {api: {
        async host_capabilities() {
          return {window_chrome: 'native', native_folder_picker: true};
        },
        async pick_archive_dir() { return null; },
      }};
    })();""")
    c.allow_failed_responses("one deliberate catalog Library retry response")
    c.allow_console_errors("one deliberate catalog Library retry response")
    c.page.route("**/api/library", fail_first_library)
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[{
        "slug": "M31", "session": "20260927", "frame": "light",
        "n_files": 2, "total_exposure": 3600, "object_name": "M31",
    }]))
    c.page.route("**/api/images/ingest", mock_ingest)
    c.page.route("**/api/library/reindex", mock_reindex)
    with c.page.expect_response(
            lambda response: response.url.endswith("/api/library")
            and response.status == 503) as first_response:
        c.goto()
    c.expect_eq(first_response.value.status, 503,
                "the test must exercise an initial Library failure")
    c.page.locator("#versionTag").filter(has_text="Image Catalog").wait_for()
    planner_direct = c.page.request.get(c.url("/index.html"))
    c.expect_eq(planner_direct.status, 404,
                "catalog-only executable must not serve the planner at /index.html")
    c.expect("/js/main.js" not in planner_direct.text() and
             'data-tab="tonight"' not in planner_direct.text(),
             "direct planner route must not reveal planner markup")
    c.expect_eq(len(library_calls), 1,
                "catalog startup should make its initial Library request")

    c.expect_eq(c.page.locator("#catalogTabs [role='tab']").count(), 3,
                "the catalog shell must expose Library, Import Exposures, and Sky Map")
    for selector in ("#tabs", "#tab-tonight", "#tab-sky", "#tab-horizon",
                     "#siteSelect", "#weatherPanel", "#wizardModal"):
        c.expect_eq(c.page.locator(selector).count(), 0,
                    f"catalog-only shell unexpectedly contains {selector}")
    c.expect(c.page.locator("#tab-library").is_visible(),
             "Library should be the initial catalog view")
    c.page.locator('body[data-window-chrome="native"]').wait_for()
    _expect_host_owned_chrome(c, c.page)

    c.page.locator("#catalog-tab-import").click()
    c.expect(c.page.locator("#tab-import").is_visible(),
             "the FITS import and reindex view should remain available")
    for selector in ("#btnIngest", "#btnArcReindex", "#fitFilesCal",
                     "#importHistory"):
        c.expect_eq(c.page.locator(selector).count(), 1,
                    f"the archive import workflow is missing {selector}")
    c.expect("Recent imports" in c.page.locator("#tab-import").inner_text(),
             "recent session imports should remain available")
    c.page.locator("#catalog-tab-skymap").click()
    c.expect(c.page.locator("#tab-skymap").is_visible(),
             "the fixed library Sky Map should remain available")

    c.page.locator("#catalog-tab-import").click()
    with c.page.expect_response(
            lambda response: response.url.endswith("/api/library")
            and response.status == 200) as retry_response:
        c.page.locator("#catalog-tab-library").click()
    c.expect_eq(retry_response.value.status, 200,
                "returning to Library should retry the failed request")
    c.expect_eq(len(library_calls), 2,
                "Library selection should issue exactly one retry")
    c.page.locator("#libraryObjectList .library-object").filter(
        has_text="M31").wait_for(state="attached")
    c.page.locator("#versionTag").filter(has_text="NightSeam v").wait_for()
    version_text = c.page.locator("#versionTag").inner_text()
    c.expect(version_text.startswith("NightSeam v") and
             "Image Catalog" in version_text,
             "a recovered Library load should restore the versioned catalog tag")
    c.page.locator("#librarySessions tr.library-session").wait_for()
    c.expect("+ Darks" in c.page.locator("#librarySessionActions").inner_text(),
             "a retried Library load should restore its selected-session actions")

    c.page.evaluate("""() => {
      const nativeClick = HTMLInputElement.prototype.click;
      window.__ns_file_inputs_clicked = [];
      HTMLInputElement.prototype.click = function() {
        if (this.id === 'fitFiles' || this.id === 'fitFilesCal') {
          window.__ns_file_inputs_clicked.push(this.id);
          return;
        }
        return nativeClick.call(this);
      };
    }""")

    c.page.locator("#librarySessionActions [data-library-add='dark']").click()
    c.expect(c.page.locator("#tab-import").is_visible(),
             "session calibration actions should open Import Exposures")
    c.expect("fitFilesCal" in c.page.evaluate("() => window.__ns_file_inputs_clicked.join(',')"),
             "a selected session's + Darks action should open its FITS picker")
    with c.page.expect_response(
            lambda response: response.url.endswith("/api/images/ingest")
            and response.status == 200):
        c.page.locator("#fitFilesCal").set_input_files({
            "name": "dark.fits", "mimeType": "application/fits",
            "buffer": b"SIMPLE  =                    T\n",
        })
    c.expect('name="frame"' in ingest_posts[-1] and "dark" in ingest_posts[-1]
             and 'name="session_anchor_id"' in ingest_posts[-1]
             and '\r\n1\r\n' in ingest_posts[-1],
             "session calibration import should post its frame and selected exposure anchor")
    c.page.locator("#importHistory .imp-item").wait_for()

    c.page.locator("#importHistory [data-imp='flat']").click()
    with c.page.expect_response(
            lambda response: response.url.endswith("/api/images/ingest")
            and response.status == 200):
        c.page.locator("#fitFilesCal").set_input_files({
            "name": "flat.fits", "mimeType": "application/fits",
            "buffer": b"SIMPLE  =                    T\n",
        })
    c.expect('name="frame"' in ingest_posts[-1] and "flat" in ingest_posts[-1]
             and 'name="session_anchor_id"' in ingest_posts[-1],
             "recent-import calibration should post its frame and validated session anchor")

    c.page.locator("#btnTopIngest").click()
    c.expect("fitFiles" in c.page.evaluate("() => window.__ns_file_inputs_clicked.join(',')"),
             "the primary Import FITS action should open the light-frame picker")
    with c.page.expect_response(
            lambda response: response.url.endswith("/api/library/reindex")
            and response.status == 200):
        c.page.locator("#btnTopReindex").click()
    c.expect_eq(len(reindex_calls), 1,
                "the primary reindex action should call the archive reindex endpoint")

    c.page.locator("#catalog-tab-skymap").click()
    _settle_catalog_layout(c.page)
    canvas = c.page.locator("#skymapCanvas")
    bounds = canvas.bounding_box()
    c.page.mouse.click(bounds["x"] + bounds["width"] / 4,
                       bounds["y"] + bounds["height"] / 2)
    c.expect("M31" in c.page.locator("#skymapSelection").inner_text(),
             "map selection should show an archive-only object summary")
    c.page.wait_for_timeout(100)

    original_viewport = c.page.viewport_size
    try:
        for width, height in ((480, 620), (500, 720), (640, 480), (900, 800), (1240, 900),
                              (1586, 992), (2000, 1400)):
            c.page.set_viewport_size({"width": width, "height": height})
            c.page.wait_for_timeout(100)
            c.page.locator("#catalog-tab-library").click()
            layout = c.page.evaluate("""() => {
              const rect = (selector) => {
                const r = document.querySelector(selector).getBoundingClientRect();
                return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
              };
              const tabs = [...document.querySelectorAll('#catalogTabs [role=tab]')]
                .map((tab) => {
                  const r = tab.getBoundingClientRect();
                  return {left: r.left, right: r.right};
                });
              const actions = [...document.querySelectorAll('#imageCatalogTools button')]
                .map((button) => {
                  const r = button.getBoundingClientRect();
                  return {left: r.left, right: r.right};
                });
              const brand = rect('#brand');
              const tools = rect('#imageCatalogTools');
              return {
                viewportWidth: innerWidth,
                documentWidth: Math.max(document.documentElement.scrollWidth,
                  document.body.scrollWidth),
                headerWidth: document.querySelector('#topbar').scrollWidth,
                brandToolsOverlap: brand.left < tools.right && tools.left < brand.right &&
                  brand.top < tools.bottom && tools.top < brand.bottom,
                tabsInBounds: tabs.every((r) => r.left >= -1 && r.right <= innerWidth + 1),
                actionsInBounds: actions.every((r) => r.left >= -1 && r.right <= innerWidth + 1),
                libraryVisible: !document.querySelector('#tab-library').hidden,
              };
            }""")
            c.expect(layout["documentWidth"] <= width + 1,
                     f"catalog should avoid horizontal page overflow at {width}x{height}: {layout}")
            c.expect(layout["headerWidth"] <= width + 1 and
                     not layout["brandToolsOverlap"] and layout["tabsInBounds"] and
                     layout["actionsInBounds"],
                     f"catalog header actions and tabs should remain reachable at {width}x{height}: {layout}")
            c.expect(layout["libraryVisible"],
                     f"Library should remain selectable at {width}x{height}")
            c.page.locator("#librarySessionRail").scroll_into_view_if_needed()
            c.expect(c.page.locator("#librarySessionDetail").is_visible(),
                     f"Selected-session details should remain reachable at {width}x{height}")

            c.page.locator("#catalog-tab-import").click()
            c.page.locator("#btnIngest").scroll_into_view_if_needed()
            c.expect(c.page.locator("#btnIngest").is_visible(),
                     f"Import FITS should remain reachable at {width}x{height}")

            c.page.locator("#catalog-tab-skymap").click()
            c.page.wait_for_timeout(80)
            map_layout = c.page.evaluate("""() => {
              const plot = document.querySelector('#skymapPlot').getBoundingClientRect();
              const canvas = document.querySelector('#skymapCanvas').getBoundingClientRect();
              return {
                plotWidth: plot.width, plotHeight: plot.height,
                canvasWidth: canvas.width, canvasHeight: canvas.height,
                canvasInsidePlot: canvas.width > 0 && canvas.height > 0 &&
                  canvas.left >= plot.left - 1 && canvas.right <= plot.right + 1 &&
                  canvas.top >= plot.top - 1 && canvas.bottom <= plot.bottom + 1,
              };
            }""")
            c.expect(map_layout["canvasInsidePlot"],
                     f"Sky Map canvas should fit its responsive plot at {width}x{height}: {map_layout}")
            c.expect(c.page.locator("#skymapRaAxis").is_visible() and
                     c.page.locator("#skymapDecAxis").is_visible(),
                     f"Sky Map axes should remain available at {width}x{height}")
            canvas_box = c.page.locator("#skymapCanvas").bounding_box()
            c.page.mouse.click(canvas_box["x"] + canvas_box["width"] / 4,
                               canvas_box["y"] + canvas_box["height"] / 2)
            c.expect("M31" in c.page.locator("#skymapSelection").inner_text(),
                     f"Sky Map selection hit testing should remain aligned at {width}x{height}")
    finally:
        c.page.set_viewport_size(original_viewport)
        c.page.wait_for_timeout(100)

    requested_paths = [url.split("?", 1)[0].split("/", 3)[-1]
                       for url in requests]
    for forbidden in ("api/night", "api/panoramas", "api/catalog", "api/weather",
                      "api/sites", "api/update-check", "js/detail.js",
                      "api/object/ngc224"):
        c.expect(not any(forbidden in path for path in requested_paths),
                 f"catalog-only startup or map selection requested {forbidden}")
    c.expect(not any("open-meteo.com" in url.lower() for url in requests),
             "catalog-only startup must not request planner weather")
    c.expect(not any("wikipedia.org" in url.lower() for url in requests),
             "catalog-only startup must not prefetch planner example images")
    c.expect(any("api/library" in path for path in requested_paths),
             "catalog-only startup should load the image library")

    fallback_page = c.page.context.new_page()
    fallback_errors = []
    fallback_page.on("pageerror", lambda error: fallback_errors.append(str(error)))
    try:
        fallback_page.set_viewport_size({"width": 480, "height": 620})
        fallback_page.goto(c.url("/catalog.html"), wait_until="domcontentloaded")
        fallback_page.locator('body[data-window-chrome="browser"]').wait_for()
        c.expect(fallback_page.evaluate("() => !window.pywebview"),
                 "regular-browser fallback must not expect a native bridge")
        _expect_host_owned_chrome(c, fallback_page)
        fallback_page.locator("#catalog-tab-import").click()
        fallback_page.locator("#btnArcBrowse").click()
        fallback_page.locator("#toasts .toast").filter(
            has_text="Folder dialog is only available in the native window").wait_for()
        c.expect(not fallback_errors,
                 f"browser fallback should operate without native calls: {fallback_errors}")
    finally:
        fallback_page.close()

    # The planner retirement removed the handoff notice entirely: a stale
    # ?planner-unavailable=1 query string must be ignored, not break the boot.
    c.goto("/catalog.html?planner-unavailable=1")
    c.page.locator('body[data-window-chrome]').first.wait_for()
    c.expect(c.page.locator(".catalog-launch-notice").count() == 0,
             "the retired handoff notice must not reappear")


@check("desktop-catalog-responsive-layout", "catalog")
def desktop_catalog_responsive_layout(c):
    """Window changes allocate space while density and chart geometry stay stable."""
    sessions = [{
        "undated": False, "start": f"2026-09-{day:02d}T20:00:00Z",
        "end": f"2026-09-{day:02d}T22:00:00Z", "n_images": 12,
        "exposure": 3600,
        "types": {"light": {"n": 10, "exposure": 3000},
                  "dark": {"n": 2, "exposure": 600}},
        "filters": {"Hydrogen alpha narrowband 3 nm": {"n": 10, "exposure": 3000}},
    } for day in range(1, 29)]
    object_name = "M31 — Andromeda Galaxy with a deliberately long archive label"
    populated = {
        "image_dir": "C:/archive/observations/with a long configuration path",
        "total_images": 336, "total_exposure": 100800,
        "objects": [{
            "object_id": "ngc224", "name": object_name,
            "designation": "NGC 224", "otype": "galaxy", "ra_hours": 6,
            "dec_deg": 0, "size_am": 190, "n_images": 336,
            "n_sessions": len(sessions), "total_exposure": 100800,
            "sessions": sessions,
            "images": [{
                "file": f"M31-long-object-folder/202609{day:02d}-long-session/lights/frame.fits",
                "date_obs": f"2026-09-{day:02d}T21:00:00Z",
            } for day in range(1, 29)],
        }],
    }
    empty = {"image_dir": populated['image_dir'], "total_images": 0,
             "total_exposure": 0, "objects": []}
    fixture = {"library": empty}

    def library_response(route):
        route.fulfill(json=fixture['library'])

    c.page.route("**/api/library", library_response)
    c.page.route("**/api/library/imports", lambda route: route.fulfill(json=[]))
    original_viewport = c.page.viewport_size
    try:
        c.page.set_viewport_size({"width": 2000, "height": 800})
        c.goto("/catalog.html")
        c.page.locator('#catalog-tab-library').click()
        # Loading completion is independent of compact object-list disclosure.
        c.page.locator('#libraryObjectList').filter(
            has_text='No objects yet').wait_for(state='attached')
        _settle_catalog_layout(c.page)
        density = _catalog_density(c.page)
        short_width = _expect_catalog_bounds(c, c.page, '.library-layout', True)
        for height in (1400, 992, 800):
            c.page.set_viewport_size({"width": 2000, "height": height})
            _settle_catalog_layout(c.page)
            c.expect_eq(_catalog_density(c.page), density,
                        "height-only resizing must preserve typography, icons and control density")
            tall_width = _expect_catalog_bounds(c, c.page, '.library-layout', True)
            c.expect(abs(tall_width['left'] - short_width['left']) <= 1
                     and abs(tall_width['right'] - short_width['right']) <= 1,
                     "content width must depend on available width, not client height")

        # These include the one-pixel edges of each composition transition.
        sizes = ((1586, 992), (1240, 900), (900, 800), (640, 800),
                 (480, 620), (500, 620), (1920, 1080), (2000, 800),
                 (2000, 1400), (2560, 1440), (1279, 900), (1280, 900),
                 (1281, 900), (759, 800), (760, 800), (761, 800),
                 (1099, 900), (1100, 900), (1101, 900),
                 (999, 800), (1000, 800), (1001, 800))
        for width, height in sizes:
            c.page.set_viewport_size({"width": width, "height": height})
            c.page.locator('#catalog-tab-library').click()
            c.page.evaluate('() => window.scrollTo(0, 0)')
            _settle_catalog_layout(c.page)
            _expect_catalog_bounds(c, c.page, '.library-layout', width >= 1280)
            if width <= 760:
                chooser = c.page.locator('#libraryObjectsToggle')
                c.expect(chooser.is_visible(), 'compact Library must offer object-list disclosure')
                c.expect_eq(chooser.get_attribute('aria-expanded'), 'false',
                            'compact object list should start collapsed')
                empty_layout = c.page.evaluate("""() => ({
                  objectHeight: document.querySelector('.library-object-rail').getBoundingClientRect().height,
                  workspaceTop: document.querySelector('.library-workspace').getBoundingClientRect().top
                })""")
                c.expect(empty_layout['objectHeight'] <= 200 and
                         empty_layout['workspaceTop'] <= 420,
                         f"compact empty navigation must leave sessions near the top: {empty_layout}")
                chooser.click()
                c.expect_eq(chooser.get_attribute('aria-expanded'), 'true',
                            'object chooser must expand with accessible state')
                chooser.click()
                c.expect_eq(chooser.get_attribute('aria-expanded'), 'false',
                            'object chooser must collapse again')
            c.expect_eq(c.page.locator('#libraryDropZone').count(), 0,
                        'Library must not contain the retired bottom import dock')
            c.page.locator('#btnTopIngest').scroll_into_view_if_needed()
            c.expect(c.page.locator('#btnTopIngest').is_visible(),
                     f"top import action must stay reachable at {width}x{height}")

            c.page.locator('#catalog-tab-import').click()
            c.page.evaluate('() => window.scrollTo(0, 0)')
            _settle_catalog_layout(c.page)
            _expect_catalog_bounds(c, c.page, '.arch-layout', width > 1100)
            frame_selector = c.page.locator('#ingestFrameSelector')
            frame_selector.scroll_into_view_if_needed()
            c.expect(frame_selector.bounding_box()['height'] >= 40,
                     'short Import forms must preserve readable frame-type controls')
            for selector in ('#btnIngest', '#btnArcBrowse', '#btnTopReindex'):
                c.page.locator(selector).scroll_into_view_if_needed()
                c.expect(c.page.locator(selector).is_visible(),
                         f"import action {selector} must remain reachable at {width}x{height}")

            c.page.locator('#catalog-tab-skymap').click()
            c.page.evaluate('() => window.scrollTo(0, 0)')
            _settle_catalog_layout(c.page)
            _expect_catalog_bounds(c, c.page, '.chart-layout', width > 1000)
            _expect_catalog_map_geometry(c, c.page)
            c.page.locator('#smCons').scroll_into_view_if_needed()
            c.expect(c.page.locator('#smCons').is_visible(),
                     f"map controls must remain reachable at {width}x{height}")

        # Real rollup-shaped data catches wrapping and scroll ownership hidden by empties.
        fixture['library'] = populated
        c.page.set_viewport_size({"width": 1586, "height": 992})
        c.goto('/catalog.html')
        c.page.locator('#catalog-tab-library').click()
        c.page.locator('#librarySessions tr.library-session').first.wait_for()
        c.expect_eq(c.page.locator('#librarySessions tr.library-session').count(), 28,
                    'populated fixture should provide enough sessions to require scrolling')
        for width, height in ((1586, 992), (2000, 800), (1240, 900), (900, 800), (480, 620)):
            c.page.set_viewport_size({"width": width, "height": height})
            c.page.locator('#catalog-tab-library').click()
            c.page.evaluate('() => window.scrollTo(0, 0)')
            _settle_catalog_layout(c.page)
            _expect_catalog_bounds(c, c.page, '.library-layout', width >= 1280)
            c.page.locator('#librarySessions tr.library-session').last.click()
            c.expect_eq(c.page.locator('#librarySessions tr[aria-selected=true]').get_attribute(
                'data-library-session'), '27', 'last session must remain selectable after reflow')
            for selector in ('#librarySessionActions [data-library-add=dark]', '#btnTopIngest'):
                c.page.locator(selector).scroll_into_view_if_needed()
                c.expect(c.page.locator(selector).is_visible(),
                         f"populated selection/import action must remain reachable: {selector}")
            c.page.locator('#catalog-tab-skymap').click()
            _settle_catalog_layout(c.page)
            _expect_catalog_map_geometry(c, c.page)
            canvas = c.page.locator('#skymapCanvas')
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            c.page.mouse.click(box['x'] + box['width'] / 4,
                               box['y'] + box['height'] / 2)
            c.expect(object_name in c.page.locator('#skymapSelection').inner_text(),
                     'letterboxed map coordinates must select the archive object')

        dpr_context = c.page.context.browser.new_context(
            viewport={"width": 1240, "height": 900}, device_scale_factor=2,
            locale='en-US', timezone_id='UTC', color_scheme='dark')
        dpr_errors = []
        try:
            page = dpr_context.new_page()
            page.on('pageerror', lambda error: dpr_errors.append(str(error)))
            page.route('**/api/library', library_response)
            page.route('**/api/library/imports', lambda route: route.fulfill(json=[]))
            page.goto(c.url('/catalog.html'), wait_until='domcontentloaded')
            page.locator('#librarySessions tr.library-session').first.wait_for()
            page.locator('#catalog-tab-skymap').click()
            _settle_catalog_layout(page)
            _expect_catalog_map_geometry(c, page)
            c.expect(not dpr_errors, f"DPR context must render without page errors: {dpr_errors}")
        finally:
            dpr_context.close()
    finally:
        c.page.set_viewport_size(original_viewport)
