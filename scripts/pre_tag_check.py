"""Check catalog source before its independent release tag."""
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if __name__=="__main__":
    raise SystemExit(subprocess.call([sys.executable,str(ROOT/"scripts/check_release.py"),"--allow-dirty","--source-only","--no-stage"],cwd=ROOT))
