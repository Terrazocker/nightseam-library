"""Catalog provenance sidecar, export and cache-integrity contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.database import Database
from app.server import create_app


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app" / "data"


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provenance_sidecar_covers_the_catalog(tmp_path):
    desktop = (DESKTOP / "catalog_provenance.json").read_bytes()
    payload = json.loads(desktop)
    db = Database(tmp_path / "provenance.db")
    catalog = db.all_objects(advanced=True)
    objects = payload["objects"]
    ids = {row["id"] for row in catalog}
    assert payload["schema_version"] == 1
    assert payload["catalog"]["row_count"] == len(ids)
    assert set(objects) == ids
    csv_sha = hashlib.sha256(
        (DESKTOP / "openned.csv").read_bytes()).hexdigest()
    assert payload["catalog"]["sha256"] == csv_sha
    assert all(record["source_version"].startswith("sha256:")
               for record in objects.values())
    assert all(isinstance(record["aliases"], list)
               and isinstance(record["designation_history"], list)
               for record in objects.values())
    assert objects["ngc1976"]["source"] == "OpenNGC"
    assert objects["ngc1976"]["source_version"].startswith("sha256:")
    assert objects["m40"]["source"] == "NightSeam supplement"




def test_catalog_provenance_api_returns_the_shipped_sidecar(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as client:
        response = client.get("/api/catalog/provenance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["objects"]["ngc1976"]["source"] == "OpenNGC"
