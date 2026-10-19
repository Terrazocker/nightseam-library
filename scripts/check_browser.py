"""Real-browser gate for the independent packaged Image Catalog."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests/browser"))
import harness
import catalog_suite
import catalog_map_labels

def run_gate(**kwargs):
    try:
        return harness.run(**kwargs)
    except Exception as exc:
        print(f"check_browser FATAL: browser infrastructure could not start ({type(exc).__name__}: {exc}).")
        return 2

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all",action="store_true",help="every catalog check (default)")
    ap.add_argument("--desktop",action="store_true",help="packaged catalog checks")
    ap.add_argument("--channel",default="chrome")
    ap.add_argument("--exe",default=str(harness.DEFAULT_EXE))
    ap.add_argument("--only")
    ap.add_argument("--headed",action="store_true")
    ap.add_argument("--list",action="store_true")
    args=ap.parse_args()
    if args.list:
        for name,target,_ in harness.all_checks():
            if target=="catalog": print(f"{target:7s} {name}")
        return 0
    return run_gate(channel=args.channel,targets=("catalog",),exe=Path(args.exe),only=args.only,headed=args.headed)

if __name__=="__main__": raise SystemExit(main())
