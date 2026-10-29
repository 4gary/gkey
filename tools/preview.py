"""Run a preview copy of gkeystrokes against a read-only snapshot of your real data.

    python tools\\preview.py                 # fresh snapshot, port 8501
    python tools\\preview.py --port 8502
    python tools\\preview.py --data <folder> # reuse an earlier preview folder (no new snapshot)

The live database is read through a `mode=ro` connection and copied with SQLite's backup API into a
new temp folder, so the real file is never opened for writing. The preview runs with --no-tray
--background in the foreground of this console: Ctrl+C stops it. The real tracker keeps running.
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "gkeystrokes")


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def snapshot(src, dst):
    """Copy the database at `src` into `dst` without ever opening `src` read-write."""
    uri = "file:%s?mode=ro" % urllib.parse.quote(src.replace("\\", "/"))
    s = sqlite3.connect(uri, uri=True, timeout=10)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def main():
    ap = argparse.ArgumentParser(prog="preview")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--data", help="existing preview folder to reuse instead of a new snapshot")
    ap.add_argument("--open", action="store_true", help="also open the dashboard window")
    args = ap.parse_args()
    if args.port == 8413:
        sys.exit("Port 8413 belongs to the real tracker; pick another (default 8501).")

    folder = os.path.abspath(args.data) if args.data else tempfile.mkdtemp(prefix="gk-preview-")
    if same(folder, REAL):          # GK_DATA_DIR is set to `folder` below, so this is where it would resolve
        sys.exit("Refusing: %s is the real data folder." % folder)
    if not args.data:
        live = os.path.join(REAL, "gkeystrokes.db")
        if os.path.exists(live):
            snapshot(live, os.path.join(folder, "gkeystrokes.db"))
        else:
            print("No live database at %s; the preview starts empty." % live)

    env = dict(os.environ, GK_DATA_DIR=folder)
    cmd = [sys.executable, os.path.join(ROOT, "gkeystrokes.pyw"), "--no-tray", "--port", str(args.port)]
    if not args.open:
        cmd.append("--background")
    print("preview data: %s" % folder)
    print("preview URL:  http://127.0.0.1:%d/  (if the port is taken it moves to the next free one; see port.txt)"
          % args.port, flush=True)
    try:
        return subprocess.call(cmd, env=env, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
