"""The packaged desktop defaults to the archive shell; source planner mode stays available."""

from pathlib import Path

from fastapi.testclient import TestClient

import main
from app.database import Database
from app.server import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_default_selects_catalog_shell_and_source_keeps_planner():
    assert main.catalog_only_for_launch(frozen=True) is True
    assert main.catalog_only_for_launch(frozen=False) is True
    assert main.catalog_only_for_launch(True, frozen=False) is True
    assert main.catalog_only_for_launch(False, frozen=True) is False


def test_single_instance_handoff_uses_the_requested_shell_url():
    base = "http://127.0.0.1:8407/"
    assert main.shell_url_for_launch(base, True) == \
        "http://127.0.0.1:8407/catalog.html"
    assert main.shell_url_for_launch(base, False) == \
        "http://127.0.0.1:8407/index.html"


def test_planner_handoff_to_catalog_singleton_shows_a_clear_notice():
    base = "http://127.0.0.1:8407/"
    assert main.handoff_url_for_launch(base, True, False) == \
        "http://127.0.0.1:8407/catalog.html"
    assert main.handoff_url_for_launch(base, False, False) == \
        "http://127.0.0.1:8407/index.html"
    for running_mode in (True, None):
        url = main.handoff_url_for_launch(base, False, running_mode)
        assert url == ("http://127.0.0.1:8407/catalog.html"
                       "?planner-unavailable=1")


def test_catalog_launch_skips_image_prefetch(tmp_path, monkeypatch):
    from app import server

    started = []
    monkeypatch.setattr(server, "start_image_prefetch",
                        lambda db: started.append(db))

    main.create_launch_app(Database(tmp_path / "catalog-prefetch.sqlite3"),
                           catalog_only=True)
    assert started == []

    create_app(Database(tmp_path / "catalog-direct-prefetch.sqlite3"),
               catalog_only=True)
    assert started == []

    main.create_launch_app(Database(tmp_path / "planner-prefetch.sqlite3"),
                           catalog_only=False)
    assert len(started) == 1


def test_catalog_server_disables_planner_network_routes(tmp_path, monkeypatch):
    from app import images, server

    calls = []

    def fetch_image(obj):
        calls.append(("image", obj["id"]))
        return b"test image", "image/png"

    monkeypatch.setattr(images, "fetch_image", fetch_image)
    monkeypatch.setattr(images, "fetch_image_cached", fetch_image)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *args, **kwargs: calls.append(("update", None)))

    catalog_db = Database(tmp_path / "catalog-remote-routes.sqlite3")
    with TestClient(create_app(catalog_db, prefetch=False,
                               catalog_only=True)) as client:
        assert client.get("/api/object/ngc224/image").status_code == 404
        assert client.get("/api/object/ngc224/image?thumb=1").status_code == 404
        assert client.post("/api/update-check").status_code == 404
    assert calls == []

    planner_db = Database(tmp_path / "planner-remote-routes.sqlite3")
    with TestClient(create_app(planner_db, prefetch=False)) as client:
        image = client.get("/api/object/ngc224/image")
        thumbnail = client.get("/api/object/ngc224/image?thumb=1")
        update = client.post("/api/update-check")
    assert image.status_code == 200
    assert image.content == b"test image"
    assert thumbnail.status_code == 200
    assert thumbnail.content == b"test image"
    assert update.status_code == 404
    assert "catalog release source" in update.json()["detail"]
    assert calls == [("image", "ngc224"), ("image", "ngc224")]


def test_catalog_server_exposes_only_the_archive_shell(tmp_path):
    db = Database(tmp_path / "catalog-mode.sqlite3")
    with TestClient(create_app(db, prefetch=False, catalog_only=True)) as client:
        for path in ("/", "/catalog.html"):
            response = client.get(path)
            assert response.status_code == 200
            assert "/js/catalog_main.js" in response.text
            assert 'id="catalogTabs"' in response.text
            assert 'data-tab="library"' in response.text
            assert 'data-tab="import"' in response.text
            assert 'data-tab="skymap"' in response.text
            assert 'id="btnArcReindex"' in response.text
            assert 'id="btnIngest"' in response.text
            assert 'id="fitFilesCal"' in response.text
            assert 'id="importHistory"' in response.text
            assert 'id="librarySessionActions"' in response.text
            assert 'data-tab="tonight"' not in response.text
            assert 'id="tab-sky"' not in response.text
            assert 'id="tab-horizon"' not in response.text
            assert 'id="wizardModal"' not in response.text
            assert '/js/main.js' not in response.text
        planner = client.get("/index.html")
        assert planner.status_code == 404
        assert "/js/main.js" not in planner.text
        assert 'data-tab="tonight"' not in planner.text


def test_source_server_keeps_the_full_desktop_planner_entry(tmp_path):
    db = Database(tmp_path / "planner-mode.sqlite3")
    with TestClient(create_app(db, prefetch=False)) as client:
        response = client.get("/")
        explicit_planner = client.get("/index.html")
        explicit_catalog = client.get("/catalog.html")
    assert response.status_code == 200
    assert '/js/main.js' in response.text
    assert 'data-tab="tonight"' in response.text
    assert 'data-tab="horizon"' in response.text
    assert '/js/main.js' in explicit_planner.text
    assert '/js/catalog_main.js' in explicit_catalog.text


def test_catalog_boot_and_map_do_not_statically_import_planner_detail():
    boot = (ROOT / "static" / "js" / "catalog_main.js").read_text(encoding="utf-8")
    sky_map = (ROOT / "static" / "js" / "skymap.js").read_text(encoding="utf-8")
    assert 'from "./detail.js"' not in boot
    assert 'from "./detail.js"' not in sky_map
    assert 'import("./detail.js")' in sky_map
    assert 'document.body.classList.contains("catalog-only")' in sky_map


def test_catalog_shell_has_a_user_visible_mode_mismatch_notice():
    shell = (ROOT / "static" / "catalog.html").read_text(encoding="utf-8")
    boot = (ROOT / "static" / "js" / "catalog_main.js").read_text(encoding="utf-8")
    assert "catalog-launch-notice" in shell or "catalog-launch-notice" in boot
    assert "planner-unavailable" in boot
    assert "Close that window" in boot
