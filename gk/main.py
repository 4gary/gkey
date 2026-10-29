"""Entry point: start tracker + server, then sit in the tray."""
import argparse
import logging
import os
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from logging.handlers import RotatingFileHandler

from . import privacy, winapi
from .store import Store, check_db, data_dir

log = logging.getLogger("gk")

EDGE_PATHS = (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe")


# The app window gets its own profile (under the data folder) so the user's normal Edge is untouched,
# and runs without extensions, GPU process, sync or background network traffic. Edge still calls Microsoft
# and looks for Cast devices with only those flags (measured), so the window also gets a dead-end proxy
# (127.0.0.1:9, nothing listens; Edge never proxies 127.0.0.1 itself), no name lookups, no QUIC and no
# Cast discovery: it can reach this PC and nothing else. Settings > Privacy proof shows its sockets.
EDGE_FLAGS = ("--disable-extensions", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
              "--disable-sync", "--disable-background-networking", "--disable-component-update",
              "--disable-default-apps", "--disable-domain-reliability", "--disable-breakpad",
              "--disable-client-side-phishing-detection", "--safebrowsing-disable-auto-update",
              "--metrics-recording-only", "--no-pings", "--disable-quic",
              "--disable-features=MediaRouter,DialMediaRouteProvider,CastMediaRouteProvider,OptimizationHints,"
              "Translate,msSmartScreenProtection",
              "--proxy-server=http://127.0.0.1:9",
              "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
              "--renderer-process-limit=1")


def open_dashboard(url, size="1440,960"):
    """Open as a chromeless Edge app window; fall back to the default browser."""
    profile = os.path.join(data_dir(), "edge-profile")
    for p in EDGE_PATHS:
        exe = os.path.expandvars(p)
        if os.path.isfile(exe):
            try:
                os.makedirs(profile, exist_ok=True)
                subprocess.Popen([exe, "--app=" + url, "--window-size=" + size, "--user-data-dir=" + profile,
                                  *EDGE_FLAGS],
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                return
            except OSError:
                pass
    webbrowser.open(url)


DASHBOARD_TITLE = "gkeystrokes"     # web/index.html <title>; Edge app windows take the page title


def show_dashboard(url):
    """Bring this data folder's open dashboard window to the front, or open one if there is none.
    The window is a pywebview (WebView2) process like the mini; the Edge app window is the fallback."""
    try:
        from .mini import show_dashboard_window
        if show_dashboard_window(urllib.parse.urlsplit(url).port, data_dir()):
            return
    except Exception:
        log.exception("dashboard window failed; opening it in Edge")
    try:
        wins = winapi.edge_windows(os.path.join(data_dir(), "edge-profile"), DASHBOARD_TITLE)
    except Exception:
        log.exception("looking for the dashboard window failed")
        wins = []
    if wins and winapi.focus_window(wins[0]):
        return
    open_dashboard(url)


RESTORE_HELP = ("To restore a backup: quit gkeystrokes from the tray, copy the backup over gkeystrokes.db, "
                "delete gkeystrokes.db-wal and gkeystrokes.db-shm, then start gkeystrokes again (README, Data).")


def refuse_broken_db(reason, backup):
    """Log why the database can't be used, tell the user, and leave every file as it is."""
    log.error("not starting: %s (newest backup: %s)", reason, backup or "none")
    text = reason + "\n\ngkeystrokes did not start, so nothing was overwritten."
    text += ("\n\nNewest backup:\n%s\n\n%s" % (backup, RESTORE_HELP)) if backup else "\n\nNo backups were found."
    try:
        winapi.message_box(text, "gkeystrokes can't open its database")
    except Exception:
        pass


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(os.path.join(data_dir(), "gkeystrokes.log"), maxBytes=1_000_000, backupCount=2,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    if sys.stderr is not None:              # pythonw has no console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gkeystrokes")
    ap.add_argument("--background", action="store_true", help="don't open the dashboard on start")
    ap.add_argument("--no-tray", action="store_true", help="run without a tray icon (Ctrl+C to quit)")
    ap.add_argument("--port", type=int, default=8413)
    args = ap.parse_args(argv)
    privacy.install_guard()             # from here on, any attempt to reach outside this PC is blocked

    if not os.environ.get("GK_DATA_DIR") and winapi.appdata_redirected(data_dir()):
        # Started from inside a packaged app (e.g. Claude desktop): our database would land in that
        # app's private copy of AppData. Hand off to Explorer so we run as a normal program.
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gkeystrokes.pyw")
        subprocess.Popen(["explorer.exe", script])
        return 0

    winapi.set_dpi_aware()
    setup_logging()
    port_file = os.path.join(data_dir(), "port.txt")

    # one tracker per data folder (so a preview folder can run next to the real one)
    if not winapi.acquire_single_instance(winapi.instance_name("gkeystrokes", data_dir())):
        if args.background:
            log.info("already running; nothing to do")
            return 0
        log.info("already running; showing its dashboard")
        try:
            port = int(open(port_file).read().strip())
        except (OSError, ValueError):
            port = args.port
        show_dashboard("http://127.0.0.1:%d/" % port)
        return 0

    ok, reason, backup = check_db(data_dir())
    if not ok:
        refuse_broken_db(reason, backup)
        return 1

    from .server import start_server
    from .tracker import Tracker

    store = Store()
    tracker = Tracker(store)
    tracker.start()
    server, port = start_server(tracker, args.port)
    with open(port_file, "w") as f:
        f.write(str(port))
    url = "http://127.0.0.1:%d/" % port
    log.info("tracking; dashboard at %s (data: %s)", url, store.path)

    from .server import remote_cfg, set_remote
    if remote_cfg(store)["enabled"]:
        set_remote(tracker, True)
    from .mini import MiniController
    mini = MiniController(port, store)
    if mini.cfg()["open"]:
        mini.start()

    if not args.background:
        show_dashboard(url)             # a window left open by the previous run is reused

    done = threading.Event()

    def shutdown():
        if done.is_set():
            return
        done.set()
        log.info("shutting down")
        mini.stop()
        set_remote(tracker, False)
        tracker.stop()
        server.shutdown()

    tray_ok = False
    if not args.no_tray:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            tray_ok = True
        except ImportError:
            log.warning("pystray/Pillow not installed; running without a tray icon")
    try:
        if tray_ok:
            from .tray import run_tray
            run_tray(tracker, lambda: show_dashboard(url), shutdown, mini)
        else:
            print("gkeystrokes running at %s - press Ctrl+C to stop" % url)
            while not done.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0
