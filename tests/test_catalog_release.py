"""Independent packaging protects private data and the tested executable identity."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("catalog_release",ROOT/"scripts/check_release.py")
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

def _package(tmp_path,extra=None):
    stage=tmp_path/"dist/release"
    stage.mkdir(parents=True)
    exe=tmp_path/"dist/NightSeam.exe"
    exe.write_bytes(b"tested catalog executable")
    (tmp_path/"LICENSE").write_bytes(b"license")
    (tmp_path/"NOTICE").write_bytes(b"notice")
    archive=stage/"NightSeam-Catalog-0.1.0.zip"
    with zipfile.ZipFile(archive,"w") as packed:
        for name in ["NightSeam.exe","LICENSE","NOTICE"]:
            packed.write(exe if name=="NightSeam.exe" else tmp_path/name,name)
        if extra: packed.writestr(extra,b"private bytes")
    payload={"version":"0.1.0","file":archive.name,"sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),"size_bytes":archive.stat().st_size}
    (stage/"release.json").write_text(json.dumps(payload),encoding="utf-8")
    return exe,stage,payload

def test_missing_stage_is_rejected(tmp_path):
    assert release.check_stage(tmp_path,"0.1.0",tmp_path/"missing.exe")

def test_package_matches_verified_sources(tmp_path):
    exe,stage,payload=_package(tmp_path)
    assert release.check_stage(tmp_path,"0.1.0",exe)==[]

def test_private_library_can_never_enter_package(tmp_path):
    exe,stage,payload=_package(tmp_path,"data/nightseam.db")
    assert any("only NightSeam.exe" in e for e in release.check_stage(tmp_path,"0.1.0",exe))

def test_stale_executable_is_rejected(tmp_path):
    exe,stage,payload=_package(tmp_path)
    exe.write_bytes(b"new tested executable")
    assert any("NightSeam.exe differs" in e for e in release.check_stage(tmp_path,"0.1.0",exe))

def test_version_hash_size_and_unsafe_name_are_rejected(tmp_path):
    exe,stage,payload=_package(tmp_path)
    payload.update(version="0.6.34",sha256="bad",size_bytes=0)
    (stage/"release.json").write_text(json.dumps(payload),encoding="utf-8")
    assert len(release.check_stage(tmp_path,"0.1.0",exe))==3
    payload["file"]="../outside.zip"
    (stage/"release.json").write_text(json.dumps(payload),encoding="utf-8")
    assert release.check_stage(tmp_path,"0.1.0",exe)==["staged archive name is unsafe"]
