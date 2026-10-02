"""Validate independent catalog build/version integrity without website staging."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.version import project_version,build_version

def run_command(args,root=ROOT):
    return subprocess.run(args,cwd=root,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120)

def check_stage(root,expected,exe):
    """Only the tested executable and public notices may enter a distributable."""
    errors=[]
    manifest=root/"dist/release/release.json"
    if not manifest.is_file(): return ["release.json is missing; run make_release.py or use --no-stage"]
    try:
        payload=json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload,dict): return ["release.json must contain an object"]
        relative=payload.get("file","")
        if not isinstance(relative,str) or Path(relative).name!=relative or not relative.endswith(".zip"):
            return ["staged archive name is unsafe"]
        archive=root/"dist/release"/relative
        if payload.get("version")!=expected: errors.append("staged version is stale")
        if not archive.is_file(): return errors+["staged archive is missing"]
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=payload.get("sha256"): errors.append("staged archive checksum is stale")
        if archive.stat().st_size!=payload.get("size_bytes"): errors.append("staged archive size is stale")
        with zipfile.ZipFile(archive) as packed:
            expected_members={"NightSeam.exe","LICENSE","NOTICE"}
            if set(packed.namelist())!=expected_members or len(packed.namelist())!=3:
                errors.append("release archive must contain only NightSeam.exe, LICENSE and NOTICE")
            else:
                for member,source in [("NightSeam.exe",exe),("LICENSE",root/"LICENSE"),("NOTICE",root/"NOTICE")]:
                    if not source.is_file() or packed.read(member)!=source.read_bytes(): errors.append(f"staged {member} differs from its verified source")
    except (OSError,ValueError,zipfile.BadZipFile,KeyError) as exc:
        errors.append(f"invalid release package: {exc}")
    return errors

def check_seed_sources(root):
    """A fresh checkout must include seed data; root data/ remains private."""
    required = {"app/data/openned.csv", "app/data/supplement.csv", "app/data/catalog_provenance.json"}
    result = run_command(["git", "ls-files", "--", "app/data"], root)
    if result.returncode:
        return ["cannot verify tracked catalog seed data"]
    tracked = set(result.stdout.splitlines())
    return [f"required seed source is missing or untracked: {relative}"
            for relative in sorted(required)
            if relative not in tracked or not (root / relative).is_file()]

def check(root=ROOT,allow_dirty=False,include_stage=True,require_exe=True):
    errors=check_seed_sources(root)
    version=(root/"VERSION").read_text(encoding="utf-8").strip()
    status=run_command(["git","status","--porcelain"],root)
    tag=run_command(["git","tag","--points-at","HEAD"],root)
    dirty=bool(status.stdout.strip())
    if not allow_dirty:
        if status.returncode or dirty: errors.append("release requires a clean Git worktree")
        if tag.returncode or f"v{version}" not in tag.stdout.splitlines(): errors.append(f"release requires HEAD tagged v{version}")
    expected=build_version(root)
    bake=root/"app/_version_baked.py"
    if bake.exists() and require_exe:
        node=ast.parse(bake.read_text(encoding="utf-8-sig"))
        values=[ast.literal_eval(n.value) for n in node.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="BAKED_VERSION" for t in n.targets)]
        if values!=[expected]: errors.append(f"baked version must equal {expected}, found {values}")
    elif require_exe: errors.append("version bake is missing; run build.ps1")
    exe=root/"dist/NightSeam.exe"
    if require_exe:
        if not exe.exists(): errors.append("dist/NightSeam.exe is missing")
        else:
            result=run_command([str(exe),"--version"],root)
            if result.returncode or result.stdout.strip()!=f"NightSeam {expected}": errors.append("executable version differs from the declared build version")
            if bake.exists() and exe.stat().st_mtime_ns<bake.stat().st_mtime_ns: errors.append("executable is older than its version bake")
    result=run_command([sys.executable,str(root/"scripts/check_js.py")],root)
    if result.returncode: errors.append(f"check_js.py failed: {result.stdout[-1500:]}{result.stderr[-500:]}")
    if include_stage: errors.extend(check_stage(root,expected,exe))
    return errors

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-dirty",action="store_true")
    ap.add_argument("--no-stage",action="store_true")
    ap.add_argument("--source-only",action="store_true",help="check source before compilation")
    args=ap.parse_args()
    errors=check(allow_dirty=args.allow_dirty,include_stage=not args.no_stage,require_exe=not args.source_only)
    for e in errors: print("ERROR:",e)
    print(f"Catalog release {project_version()}: {len(errors)} error(s)")
    return int(bool(errors))
if __name__=="__main__": raise SystemExit(main())
