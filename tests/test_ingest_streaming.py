"""Streamed multipart import: early publication, request refusal and disconnects."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import server
from test_compression import exposure, library

BOUNDARY = b'nsboundary'
CONTENT_TYPE = 'multipart/form-data; boundary=nsboundary'


def field(name, value):
    return (b'--' + BOUNDARY + b'\r\nContent-Disposition: form-data; name="' + name.encode() +
            b'"\r\n\r\n' + str(value).encode() + b'\r\n')


def file_part(filename, data):
    return (b'--' + BOUNDARY + b'\r\nContent-Disposition: form-data; name="files"; filename="' +
            filename.encode() + b'"\r\nContent-Type: application/fits\r\n\r\n' + data + b'\r\n')


END = b'--' + BOUNDARY + b'--\r\n'


def no_import_stages(archive):
    assert not list(archive.rglob('.nightseam-import-*'))


async def call(app, steps):
    """Drive one ASGI request; steps are body chunks, coroutine gates or 'disconnect'."""
    steps, sent = list(steps), []
    async def receive():
        while steps and callable(steps[0]):
            await steps.pop(0)()
        if not steps:
            return {'type': 'http.disconnect'}
        step = steps.pop(0)
        if step == 'disconnect':
            return {'type': 'http.disconnect'}
        more = any(not callable(item) for item in steps)
        return {'type': 'http.request', 'body': step, 'more_body': more}
    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/images/ingest', 'raw_path': b'/api/images/ingest',
             'query_string': b'', 'scheme': 'http', 'server': ('testserver', 80), 'client': ('127.0.0.1', 1),
             'http_version': '1.1', 'root_path': '',
             'headers': [(b'host', b'testserver'), (b'content-type', CONTENT_TYPE.encode())]}
    try:
        await app(scope, receive, send)
    except Exception:
        pass  # a disconnected request has no response to deliver
    return next((message['status'] for message in sent if message['type'] == 'http.response.start'), None)


def waiter(condition, timeout=10):
    async def wait():
        for _ in range(int(timeout / .02)):
            if condition():
                return
            await asyncio.sleep(.02)
        raise AssertionError('condition not reached while the body was still open')
    return wait


@pytest.mark.parametrize('declared', [True, False])
def test_declared_requests_publish_before_the_body_ends(tmp_path, declared):
    db, archive, _ = library(tmp_path, count=0)
    app = server.create_app(db)
    second = file_part('second.fit', exposure(2))
    head = (field('file_count', 2) if declared else b'') + file_part('first.fit', exposure(1))
    split = second.index(b'\r\n\r\n') + 64   # the second file has begun, but is incomplete
    if declared:
        gate = waiter(lambda: len(db.images()) == 1)
    else:
        async def gate():
            await asyncio.sleep(.5)
            # Undeclared requests validate the complete body before publishing.
            assert db.images() == []
    status = asyncio.run(call(app, [head + second[:split], gate, second[split:] + END]))
    assert status == 200
    assert [record['orig_name'] for record in db.images()] == ['first.fit', 'second.fit']
    no_import_stages(archive)


def test_refused_requests_publish_nothing(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    headers = {'content-type': CONTENT_TYPE}
    with TestClient(server.create_app(db)) as client:
        late = file_part('a.fit', exposure(1)) + field('frame', 'dark') + END
        response = client.post('/api/images/ingest', content=late, headers=headers)
        assert response.status_code == 400 and 'precede' in response.json()['detail']
        declared = field('file_count', 97) + file_part('a.fit', exposure(1)) + END
        assert client.post('/api/images/ingest', content=declared, headers=headers).status_code == 400
        monkeypatch.setattr(server, 'MAX_FILES_PER_REQUEST', 2)
        overflow = b''.join(file_part(f'{index}.fit', exposure(index)) for index in range(3)) + END
        response = client.post('/api/images/ingest', content=overflow, headers=headers)
        assert response.status_code == 400 and 'too many files' in response.json()['detail']
        assert client.post('/api/images/ingest', content=field('frame', 'dark') + END,
                           headers=headers).status_code == 422
        assert client.post('/api/images/ingest', content=b'not multipart',
                           headers={'content-type': 'text/plain'}).status_code == 400
    assert db.images() == []
    no_import_stages(archive)


def test_disconnect_mid_file_leaves_no_stage_or_record(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    app = server.create_app(db)
    second = file_part('second.fit', exposure(2))
    body = file_part('first.fit', exposure(1)) + second[:len(second) // 2]
    asyncio.run(call(app, [body, 'disconnect']))
    assert db.images() == []
    no_import_stages(archive)
    assert db.conn.execute('SELECT COUNT(*) FROM import_batches').fetchone()[0] == 0


class ChunkedRequest:
    def __init__(self, body, size):
        self.headers = {'content-type': CONTENT_TYPE}
        self.body, self.size = body, size

    async def stream(self):
        for start in range(0, len(self.body), self.size):
            yield self.body[start:start + self.size]


@pytest.mark.parametrize('size', [1, 7, 4093, 65536, 1 << 20, 3 << 20])
def test_streamed_parts_are_byte_exact_across_chunk_boundaries(size):
    from app.upload_stream import multipart_parts
    # Boundary-like bytes inside the payload exercise the parser's lookbehind.
    first = (b'\r\n--' + BOUNDARY[:-1] + b'x') * 50 + bytes(range(256)) * 9000
    second = bytes(range(255, -1, -1)) * 5000
    files = [first, second] if size > 64 else [first[:6000], second[:3000]]
    body = field('frame', 'dark') + file_part('a.fit', files[0]) + file_part('b.fit', files[1]) + END

    async def collect():
        received, fields, tasks = [], {}, []
        async def consume(upload, header):
            data = await upload.read(header)
            while chunk := await upload.read1(1 << 20):
                data += chunk
            return data
        async for kind, name, value in multipart_parts(ChunkedRequest(body, size)):
            if kind == 'field':
                fields[name] = value
            else:
                received.append(value.filename)
                tasks.append(asyncio.create_task(consume(value, 1_500_000)))
        return fields, received, await asyncio.gather(*tasks)

    fields, names, contents = asyncio.run(collect())
    assert fields == {'frame': 'dark'} and names == ['a.fit', 'b.fit']
    assert contents == files
