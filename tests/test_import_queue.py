"""Import requests queue in order: the next stages while the current publishes."""
import asyncio
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from app import ingest, server
from test_compression import exposure, library


@pytest.fixture
def running(tmp_path):
    """A real server on a private socket, with a hook that holds publication."""
    db, archive, _ = library(tmp_path, count=0)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(server.create_app(db), host='127.0.0.1', port=port,
                                            log_level='error', access_log=False, loop='asyncio'))
    worker = threading.Thread(target=lambda: service.run(sockets=[listener]), daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not service.started:
        assert time.monotonic() < deadline, 'server did not start'
        time.sleep(.02)
    try:
        yield db, archive, f'http://127.0.0.1:{port}'
    finally:
        service.should_exit = True
        worker.join(timeout=15)


def instrument(monkeypatch, hold_staging=()):
    """Record staging and publication per file; hold publication of names in
    `hold`, and the staging (so also the reading) of names in `hold_staging`."""
    events, hold, release = [], set(), threading.Event()
    stream_to, finalize = ingest._stream_to, ingest._finalize_import

    async def staged(archive, target, f, *args, **kwargs):
        if f.filename in hold_staging:
            events.append(('receiving', f.filename))
            assert await asyncio.to_thread(release.wait, 30), 'staging was never released'
        result = await stream_to(archive, target, f, *args, **kwargs)
        events.append(('staged', f.filename))
        return result

    def published(db, archive, stage, digest, name, *args, **kwargs):
        events.append(('publishing', name))
        if name in hold:
            assert release.wait(30), 'publication was never released'
        return finalize(db, archive, stage, digest, name, *args, **kwargs)
    monkeypatch.setattr(ingest, '_stream_to', staged)
    monkeypatch.setattr(ingest, '_finalize_import', published)
    return events, hold, release


def post(url, names, run, results, key, count=None):
    data = {'run_id': run, 'file_count': str(count or len(names))}
    files = [('files', (name, exposure(int(name.split('-')[1].split('.')[0])))) for name in names]
    response = httpx.post(f'{url}/api/images/ingest', data=data, files=files, timeout=60)
    results[key] = response.status_code, response.json()


def wait_for(condition, what, seconds=15):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(.02)


def test_the_next_request_stages_while_this_one_publishes_then_publishes_after_it(running, monkeypatch):
    db, archive, url = running
    events, hold, release = instrument(monkeypatch)
    hold.add('a-1.fit')
    run, results = uuid.uuid4().hex, {}
    first = threading.Thread(target=post, args=(url, ['a-1.fit', 'a-2.fit'], run, results, 'a'))
    first.start()
    wait_for(lambda: ('publishing', 'a-1.fit') in events, 'first request publishing')
    second = threading.Thread(target=post, args=(url, ['b-3.fit'], run, results, 'b'))
    second.start()
    # The second request's upload arrives and stages while the first is held.
    wait_for(lambda: ('staged', 'b-3.fit') in events, 'second request staging ahead')
    time.sleep(.3)
    assert ('publishing', 'b-3.fit') not in events
    release.set()
    first.join(30)
    second.join(30)
    assert results['a'][0] == results['b'][0] == 200
    assert all(item['ok'] for item in results['a'][1]['results'] + results['b'][1]['results'])
    order = [name for kind, name in events if kind == 'publishing']
    assert order == ['a-1.fit', 'a-2.fit', 'b-3.fit']
    assert len(db.images()) == 3
    assert not list(archive.glob('*.part'))


@pytest.mark.parametrize('same_run', [True, False])
def test_a_failed_request_stops_the_next_request_of_its_import(running, monkeypatch, same_run):
    db, archive, url = running
    events, hold, release = instrument(monkeypatch, hold_staging={'a-1.fit'})
    run, results = uuid.uuid4().hex, {}
    # Two files against a declared count of one: the request is refused at its
    # second file. Holding the first file's staging keeps the request from
    # reading on (and failing) until the next request has queued behind it.
    first = threading.Thread(target=post, args=(url, ['a-1.fit', 'a-2.fit'], run, results, 'a'),
                             kwargs={'count': 1})
    first.start()
    wait_for(lambda: ('receiving', 'a-1.fit') in events, 'first request receiving')
    second_run = run if same_run else uuid.uuid4().hex
    second = threading.Thread(target=post, args=(url, ['b-3.fit'], second_run, results, 'b'))
    second.start()
    wait_for(lambda: ('staged', 'b-3.fit') in events, 'second request staging ahead')
    release.set()
    first.join(30)
    second.join(30)
    assert results['a'][0] == 400
    status, body = results['b']
    assert status == 200
    (outcome,) = body['results']
    if same_run:
        assert outcome['unattempted'] and 'earlier request' in outcome['error']
        assert ('publishing', 'b-3.fit') not in events
        assert not any(image['orig_name'] == 'b-3.fit' for image in db.images())
    else:
        assert outcome['ok'] and not outcome.get('skipped')
    assert not list(archive.glob('*.part'))


def test_the_queue_admits_one_import_ahead_in_arrival_order():
    async def scenario():
        queue = server.ImportQueue()
        first_prev, first_done = await queue.enter()
        second_prev, second_done = await queue.enter()
        assert first_prev is None and second_prev is first_done
        third = asyncio.create_task(queue.enter())
        await asyncio.sleep(.05)
        assert not third.done()                 # only one import waits ahead
        queue.leave(first_done)
        third_prev, third_done = await asyncio.wait_for(third, 1)
        assert third_prev is second_done and first_done.is_set() and not second_done.is_set()
        queue.leave(second_done)
        queue.leave(third_done)
        queue.fail('run-1234')
        assert queue.failed('run-1234') and not queue.failed('other-run') and not queue.failed(None)
    asyncio.run(scenario())
