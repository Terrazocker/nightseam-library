"""Package the independent catalog without private library data."""
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from check_release import check,ROOT
sys.path.insert(0,str(ROOT))
from app.version import project_version,build_version

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-dirty",action="store_true")
    args=ap.parse_args()
    errors=check(allow_dirty=args.allow_dirty,include_stage=False)
    if errors: raise SystemExit("\n".join(errors))
    version=build_version()
    stage=ROOT/"dist/release"
    stage.mkdir(parents=True,exist_ok=True)
    target=stage/f"NightSeam-Catalog-{version}-windows-x64.zip"
    with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as archive:
        for relative in ["dist/NightSeam.exe","LICENSE","NOTICE"]:
            archive.write(ROOT/relative,Path(relative).name)
    payload={"version":version,"file":target.name,"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"size_bytes":target.stat().st_size}
    (stage/"release.json").write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    errors=check(allow_dirty=args.allow_dirty,include_stage=True)
    if errors: raise SystemExit("\n".join(errors))
    print(target)
if __name__=="__main__": main()
