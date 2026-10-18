"""Large response encoding must leave the actual HTTP event loop available."""
from concurrent.futures import ThreadPoolExecutor
import socket
import threading

import httpx
import uvicorn
from starlette.responses import JSONResponse

from app.database import Database
from app.server import create_app
from scripts.check_import_lifecycle import wait_for


def test_library_encoding_leaves_real_http_static_requests_responsive(tmp_path, monkeypatch):
    db = Database(tmp_path / 'library.db')
    db.update_settings({'image_dir': str(tmp_path / 'archive')})
    db.add_image({'file': 'private/light/capture.fit', 'orig_name': 'capture.fit',
        'object_name': 'Literal Ω & <target>', 'exposure': 2.5,
        'ingested_at': '2026-10-18', 'content_sha256': 'a' * 64})
    entered, release = threading.Event(), threading.Event()
    render = JSONResponse.render
    def controlled_render(self, content):
        if isinstance(content, dict) and 'objects' in content and 'total_images' in content:
            entered.set()
            assert release.wait(10), 'Test controller did not release library encoding'
        return render(self, content)
    monkeypatch.setattr(JSONResponse, 'render', controlled_render)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(create_app(db), host='127.0.0.1', port=port,
        log_level='error', access_log=False, loop='asyncio'))
    worker = threading.Thread(target=lambda: service.run(sockets=[listener]), daemon=True)
    def get(path):
        with httpx.Client(trust_env=False, timeout=10) as client:
            response = client.get(f'http://127.0.0.1:{port}/' + path)
            response.raise_for_status()
            return response
    worker.start()
    try:
        wait_for(lambda: service.started, 'private library HTTP startup', timeout=10)
        with ThreadPoolExecutor(max_workers=2) as clients:
            pending = clients.submit(get, 'api/library')
            try:
                assert entered.wait(5), 'Library response never reached encoding'
                static = clients.submit(get, 'catalog.html').result(timeout=3)
                assert static.status_code == 200 and not release.is_set()
            finally:
                release.set()
            response = pending.result(timeout=10)
        payload = response.json()
        assert response.headers['content-type'] == 'application/json'
        assert payload == get('api/library/export?format=json').json()
        assert payload['total_images'] == 1 and payload['total_exposure'] == 2.5
        group, = payload['objects']
        assert group['name'] == 'Literal Ω & <target>'
        assert 'content_sha256' not in group['images'][0]
    finally:
        release.set()
        service.should_exit = True
        worker.join(timeout=15)
        listener.close()
        assert not worker.is_alive(), 'Private library HTTP server did not shut down'
