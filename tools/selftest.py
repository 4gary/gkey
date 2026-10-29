"""Smoke test: key mapping, storage, and every API endpoint against a temp database.

    python tools\\selftest.py
"""
import json
import logging
import os
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["GK_DATA_DIR"] = tempfile.mkdtemp(prefix="gk-selftest-")

import sqlite3  # noqa: E402

from gk import privacy  # noqa: E402
from gk.games import GameRules  # noqa: E402
from gk.keymap import combo_label, key_id  # noqa: E402
from gk.mini import MiniController, has_webview  # noqa: E402
from gk.server import start_server  # noqa: E402
from gk.store import Store, _streaks, local_day  # noqa: E402

# Same rules as the app: talk to our own server directly (never via a system proxy), and block any attempt to
# reach outside this PC. Turned on before any server starts, so the checks below prove startup needs no lookups.
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
privacy.install_guard()
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> %s" % detail))
    if not cond:
        failures.append(name)


# ---- key mapping
check("letter", key_id(0x45, 0x12, False) == "E")
check("left/right shift by scancode", (key_id(0x10, 0x2A, False), key_id(0x10, 0x36, False)) == ("LShift", "RShift"))
check("fake shift ignored", key_id(0x10, 0x2A, True) is None)
check("right ctrl via E0", key_id(0x11, 0x1D, True) == "RCtrl")
check("numpad 7 with numlock off", key_id(0x24, 0x47, False) == "Num7")
check("real Home key", key_id(0x24, 0x47, True) == "Home")
check("numpad enter", key_id(0x0D, 0x1C, True) == "NumEnter")
check("combo label", combo_label({"LCtrl": 1, "LShift": 1, "T": 1}, "T") == "Ctrl+Shift+T")
check("shift alone is not a shortcut", combo_label({"LShift": 1}, "A") is None)
check("streaks", _streaks(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-05", "2026-01-06"], "2026-01-06") == (2, 3))

# ---- v1 -> v2 migration (keys/clicks/combos gain an app column; data kept; backup written)
mig = os.path.join(os.environ["GK_DATA_DIR"], "v1.db")
c = sqlite3.connect(mig)
c.executescript("""
CREATE TABLE keys (hour INTEGER, key TEXT, n INTEGER, PRIMARY KEY (hour, key));
CREATE TABLE clicks (day TEXT, gx INTEGER, gy INTEGER, n INTEGER, PRIMARY KEY (day, gx, gy));
CREATE TABLE combos (day TEXT, combo TEXT, n INTEGER, PRIMARY KEY (day, combo));
CREATE TABLE daily (day TEXT PRIMARY KEY, peak_wpm REAL, peak_kpm INTEGER, peak_down REAL, peak_up REAL);
INSERT INTO keys VALUES (1, 'E', 9); INSERT INTO clicks VALUES ('2026-01-01', 1, 2, 3);
INSERT INTO combos VALUES ('2026-01-01', 'Ctrl+C', 4); INSERT INTO daily VALUES ('2026-01-01', 50, 100, 0, 0);
""")
c.commit()
c.close()
m = Store(mig)
check("migration keeps key counts", m.conn().execute("SELECT app, key, n FROM keys").fetchall() == [("", "E", 9)])
check("migration keeps clicks/combos", m.conn().execute("SELECT n FROM clicks").fetchone()[0] == 3
      and m.conn().execute("SELECT n FROM combos").fetchone()[0] == 4)
check("migration adds peak_apm", m.conn().execute("SELECT peak_apm FROM daily").fetchone()[0] == 0)
check("migration writes a backup", os.path.exists(mig + ".v1-backup"))

# ---- storage
store = Store()
now = time.time()
hour = int(now // 3600)
day = local_day(now)
store.write({
    "stats": [(hour, "code.exe", 100, 10, 2, 1, 0, 5.0, 1234.5, 600, 80, 30000.0),
              (hour, "code.exe", 50, 0, 0, 0, 0, 0, 0, 60, 20, 6000.0)],     # second write must ADD
    "keys": [(hour, "code.exe", "E", 40), (hour, "code.exe", "Space", 20), (hour, "code.exe", "Backspace", 5),
             (hour, "league of legends.exe", "Q", 30)],
    "net": [(hour, "Ethernet", 1000, 50000)],
    "uptime": [(hour, 900)],
    "clicks": [(day, "code.exe", 10, 10, 7), (day, "league of legends.exe", 40, 30, 11)],
    "combos": [(day, "code.exe", "Ctrl+C", 3)],
    "daily": [(day, 88.0, 400, 1e6, 1e5, 140, 0, 0, 0)],
    "apps": [("code.exe", "Visual Studio Code", "C:\\code.exe"),
             ("league of legends.exe", "League of Legends", "C:\\Riot Games\\League of Legends\\Game\\League of Legends.exe"),
             ("leagueclientux.exe", "League of Legends (client)", "C:\\Riot Games\\League of Legends\\LeagueClientUx.exe"),
             ("marvel.exe", "Some Steam Game", "C:\\Program Files (x86)\\Steam\\steamapps\\common\\X\\marvel.exe"),
             ("unitycrashhandler64.exe", "Crash", "C:\\Program Files (x86)\\Steam\\steamapps\\common\\X\\UnityCrashHandler64.exe")],
})
store.write({"stats": [(hour, "league of legends.exe", 30, 1, 10, 0, 0, 0, 500.0, 120, 0, 0)]})
t = store.totals(now - 3600, now + 3600)
check("upsert adds counts", t["keys"] == 180 and t["active_s"] == 780, t)
check("network totals", (t["up"], t["down"]) == (1000, 50000), t)
check("backspaces from key table", t["backspaces"] == 5, t)
check("peak wpm / apm", t["peak_wpm"] == 88.0 and t["peak_apm"] == 140, t)

# ---- game detection
rules = GameRules(store)
check("League match exe is a game", rules.is_game("league of legends.exe"))
check("League launcher is not", not rules.classify("leagueclientux.exe", "C:\\Riot Games\\League of Legends\\LeagueClientUx.exe")[0])
check("Riot client is not", not rules.classify("riotclientservices.exe", "C:\\Riot Games\\Riot Client\\RiotClientServices.exe")[0])
check("Steam library exe auto-detected", rules.classify("marvel.exe", "D:\\SteamLibrary\\steamapps\\common\\X\\marvel.exe")[0])
check("crash handler in library is not", not rules.classify("unitycrashhandler64.exe", "C:\\x\\steamapps\\common\\X\\a.exe")[0])
check("browser is not", not rules.classify("chrome.exe", "C:\\Program Files\\Google\\Chrome\\chrome.exe")[0])
rules.set_game("code.exe", True)
check("manual mark as game", rules.is_game("code.exe") and "code.exe" in rules.game_exes())
rules.set_game("code.exe", False)
rules.set_game("league of legends.exe", False)
check("manual unmark built-in game", not rules.is_game("league of legends.exe"))
rules.set_game("league of legends.exe", True)
check("re-mark stores no override", rules.is_game("league of legends.exe") and not rules.added and not rules.removed)
games = rules.game_exes()
check("game list from seen apps", games == ["league of legends.exe", "marvel.exe"], games)
g = store.totals(now - 3600, now + 3600, "gaming", games)
n = store.totals(now - 3600, now + 3600, "normal", games)
check("gaming filter", g["keys"] == 30 and g["rclicks"] == 10, g)
check("normal filter", n["keys"] == 150 and n["backspaces"] == 5, n)
check("gaming keys", store.keys(now - 3600, now + 3600, "gaming", games) == [("Q", 30)])
check("gaming clicks", store.clicks(now - 3600, now + 3600, "gaming", games) == [(40, 30, 11)])
sid = store.start_session("league of legends.exe", now - 1800)
store.write({"sessions": [(now, 300, 200, 1500, 180, 210.5, sid)]})
ss = store.sessions(now - 3600, now + 3600)
check("session stored", ss and ss[0]["name"] == "League of Legends" and ss[0]["keys"] == 300, ss)
check("session per-game rollup", store.session_games(now - 3600, now + 3600)[0]["sessions"] == 1)
check("session energy stored", ss[0]["wh"] == 210.5, ss[0])

# ---- GPU & energy
store.write({"energy": [(hour, 3600, 300.0, 100.0, 60.0, 75.0)],
             "gpu": [(hour, 3600, 3600 * 40.0, 99.0, 3600 * 60.0, 71.0, 380.0, 9000.0)],
             "gpu_apps": [(hour, "league of legends.exe", 1800, 1440.0, 120.0, 4000.0),
                          (hour, "dwm.exe", 600, 30.0, 2.0, 300.0)],
             "daily": [(day, 0, 0, 0, 0, 0, 640.0, 72.0, 390.0)]})
store.write({"energy": [(hour, 0, 10.0, 0, 0, 0)]})                 # second write must ADD
pw = store.power(now - 3600, now + 3600, "hour")
t = pw["totals"]
check("energy totals add up", round(t["wh"], 1) == 545.0 and t["gpu_wh"] == 310.0, t)
check("gpu averages", round(t["gpu_util_avg"]) == 40 and round(t["gpu_temp_avg"]) == 60 and t["gpu_util_max"] == 99, t)
check("power peaks", t["peak_watts"] == 640.0 and t["gpu_temp_max"] == 72.0 and t["gpu_w_max"] == 390.0, t)
check("gpu busy time", t["gpu_busy_s"] == 1440.0, t)
check("energy series", pw["series"] and pw["series"][0]["b"] == hour and round(pw["series"][0]["gpu_util"]) == 40, pw["series"])
check("gpu apps ranked by GPU work", [a["exe"] for a in pw["apps"]] == ["league of legends.exe", "dwm.exe"]
      and pw["apps"][0]["name"] == "League of Legends", pw["apps"])
check("records: energy day + peaks", store.records(day)["best_wh"]["v"] > 500 and store.records(day)["peak_gpu_temp"]["v"] == 72)

# ---- system usage
store.write({"metrics": [(hour, "cpu.util", 3600 * 40.0, 95.0, 3600), (hour, "disk.read", 5e9, 2e8, 3600),
                         (hour, "gpu.vram_mb", 3600 * 8000.0, 12000.0, 3600)],
             "proc_usage": [(hour, "chrome.exe", 3600, 1800.0, 3600 * 900.0, 1400.0, 2e8, 1e8),
                            (hour, "vmmemwsl.exe", 3600, 36000.0, 3600 * 8000.0, 8000.0, 0, 0)]})
store.write({"metrics": [(hour, "cpu.util", 0, 99.0, 0)]})              # max must win, sums add
sy = store.system(now - 3600, now + 3600, "hour")
check("system metric averages", round(sy["totals"]["cpu.util"]["avg"]) == 40 and sy["totals"]["cpu.util"]["max"] == 99, sy["totals"])
check("system byte totals", sy["totals"]["disk.read"]["sum"] == 5e9, sy["totals"]["disk.read"])
check("system series", sy["series"][0]["b"] == hour and round(sy["series"][0]["gpu.vram_mb"][0]) == 8000, sy["series"])
check("programs ranked by CPU time", [a["exe"] for a in sy["apps"]] == ["vmmemwsl.exe", "chrome.exe"]
      and sy["apps"][1]["ram_avg"] == 900.0, sy["apps"])

# ---- any metric, any range (/api/series)
ms = store.metric_series(now - 7200, now + 3600, ["keys", "clicks", "down", "gpu_util", "m:cpu.util:avg",
                                                  "m:cpu.util:max", "m:disk.read:sum", "bogus", "keys"],
                         "hour", now=now + 3600)
p0, p1 = (ms["points"] + [{}, {}])[:2]
check("series: dense hourly points from the first tracked hour", [p.get("b") for p in ms["points"]] == [hour, hour + 1]
      and p0.get("t") == hour * 3600, ms["points"])
check("series: counts per hour", (p0.get("keys"), p0.get("clicks"), p0.get("down"), p1.get("keys")) == (180, 24, 50000, 0),
      (p0, p1))
check("series: sampled metrics", round(p0.get("gpu_util") or 0) == 40 and round(p0.get("m:cpu.util:avg") or 0) == 40
      and p0.get("m:cpu.util:max") == 99 and p0.get("m:disk.read:sum") == 5e9 and p1.get("gpu_util", 0) is None, (p0, p1))
check("series: unknown metric listed once, duplicates dropped",
      ms["unknown"] == ["bogus"] and ms["metrics"].count("keys") == 1, ms["metrics"])
check("series: gaming filter", store.metric_series(now - 3600, now + 3600, ["keys"], "hour", "gaming", games,
                                                   now=now + 3600)["points"][0]["keys"] == 30)
md = store.metric_series(now - 3600, now + 3600, ["keys"], "day", now=now + 3600)["points"]
check("series: day bucket starts at local midnight", md and md[0]["b"] == day
      and md[0]["t"] == int(time.mktime(time.strptime(day, "%Y-%m-%d"))), md)
try:
    store.metric_series(0, 1e12, ["keys"], "hour", now=hour * 3600 + 60000 * 3600)
    check("series: too many points refused", False)
except Exception as e:
    check("series: too many points refused", type(e).__name__ == "TooManyPoints", repr(e))


# ---- daily backups
p = store.backup("2026-01-01")
bk = sqlite3.connect(p)
check("backup is a working copy", p and bk.execute("SELECT SUM(keys) FROM stats").fetchone()[0] == 180, p)
bk.close()
check("one backup per day", store.backup("2026-01-01") is None)
for i in range(2, 10):
    store.backup("2026-01-%02d" % i)
check("keeps the newest 7 backups", store.backups()[0] == "gkeystrokes-2026-01-09.db" and len(store.backups()) == 7,
      store.backups())


# ---- privacy: the code itself can't reach the network
import ast  # noqa: E402
import re  # noqa: E402

PY_FILES = sorted(p for p in os.listdir(os.path.join(ROOT, "gk")) if p.endswith(".py"))
NET_MODULES = {"socket", "socketserver", "ssl", "asyncio", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib",
               "telnetlib", "nntplib", "xmlrpc", "webbrowser", "requests", "httpx", "aiohttp", "urllib3", "websocket",
               "websockets", "pycurl"}
NET_OK = {"urllib.parse"}                                         # parsing only
NET_ALLOWED = {"server.py": {"socket", "socketserver", "http.server"},   # the local server; socket for host names
               "mini.py": {"http.client", "urllib.request"},              # talks to that server on 127.0.0.1
               "main.py": {"webbrowser"}}                                 # opens http://127.0.0.1 in a browser
BANNED_DLLS = {"winhttp", "wininet", "urlmon", "ws2_32", "wsock32", "dnsapi", "httpapi", "mswsock", "winsock"}
BANNED_WORDS = ("curl", "wget", "invoke-webrequest", "invoke-restmethod", "bitsadmin", "certutil", "net.webclient",
                "start-bitstransfer", "iwr ", "msxml2.xmlhttp", "winhttprequest")
THIRD_PARTY = {"psutil": "psutil", "pystray": "pystray", "PIL": "pillow", "webview": "pywebview"}
bad = []
used_third = set()
for name in PY_FILES + ["../gkeystrokes.pyw"]:
    src = open(os.path.join(ROOT, "gk", name), encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {}                                       # node -> enclosing function name, for the bind rule
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                funcs.setdefault(sub, fn.name)
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mods = [node.module]
        for m in mods:
            top = m.split(".")[0]
            if top not in sys.stdlib_module_names and top != "gk":
                used_third.add(top)
            if top in NET_MODULES and m not in NET_OK and m not in NET_ALLOWED.get(name, ()):
                bad.append("%s:%d imports %s" % (name, node.lineno, m))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.lower()
            if any(not u.startswith("http://127.0.0.1") for u in re.findall(r"https?://\S*", s)) \
                    and not (name == "server.py" and s.startswith("http://%s:%d/")):     # phone link text shown in Settings
                bad.append("%s:%d URL %r" % (name, node.lineno, node.value[:60]))
            if any(w in s for w in BANNED_WORDS):
                bad.append("%s:%d network command %r" % (name, node.lineno, node.value[:60]))
            if s.split(".")[0] in BANNED_DLLS:
                bad.append("%s:%d network DLL %r" % (name, node.lineno, node.value))
        if isinstance(node, ast.Attribute):
            if node.attr in ("urlopen", "create_connection", "connect_ex", "sendto", "getaddrinfo", "gethostbyname",
                             "gethostbyaddr", "getfqdn") or node.attr.lower() in BANNED_DLLS:
                bad.append("%s:%d uses .%s" % (name, node.lineno, node.attr))
            if node.attr == "connect" and isinstance(node.value, ast.Name) and node.value.id in ("s", "sock", "socket"):
                bad.append("%s:%d socket connect" % (name, node.lineno))
        if isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if fn.endswith("HTTPServer") or fn == "TCPServer" or fn == "bind":
                host = None
                if node.args and isinstance(node.args[0], ast.Tuple) and isinstance(node.args[0].elts[0], ast.Constant):
                    host = node.args[0].elts[0].value
                if host != "127.0.0.1" and not (host == "0.0.0.0" and name == "server.py"
                                                and funcs.get(node) == "set_remote"):
                    bad.append("%s:%d server not bound to 127.0.0.1 (%r)" % (name, node.lineno, host))
check("no networking code outside the local server", not bad, bad)
check("servers bind 127.0.0.1 (phone link is the only exception)",
      not any("bound" in b for b in bad))
lock = open(os.path.join(ROOT, "requirements.lock"), encoding="utf-8").read()
pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==\S+((?:\s*\\\s*--hash=sha256:[0-9a-f]{64})+)", lock, re.M))
unhashed = [l.split("==")[0] for l in lock.splitlines() if "==" in l and l.split("==")[0] not in pins]
check("requirements.lock pins every package with sha256 hashes", pins and not unhashed, unhashed or lock[:80])
missing = sorted(THIRD_PARTY.get(m, m) for m in used_third if THIRD_PARTY.get(m, m).lower() not in
                 {k.lower() for k in pins})
check("every library the app imports is pinned", not missing, missing)

web = os.path.join(ROOT, "web")
wbad = []
for name in sorted(os.listdir(web)):
    if not name.endswith((".js", ".html", ".css")):
        continue
    src = open(os.path.join(web, name), encoding="utf-8").read()
    for m in re.finditer(r"(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}", src, re.I):
        if m.group(0) != "http://www.w3.org" or "/2000/svg" not in src[m.end():m.end() + 9]:
            wbad.append("%s: %s" % (name, m.group(0)))
    for word in ("WebSocket", "EventSource", "sendBeacon", "XMLHttpRequest", "importScripts", "new Function", "eval(",
                 "RTCPeerConnection", "@import"):
        if word in src:
            wbad.append("%s: %s" % (name, word))
    if not name.endswith(".js") and re.search(r"\burl\(", src):     # CSS url(): JS has its own .url() methods
        wbad.append("%s: url(" % name)
    for m in re.finditer(r"fetch\(\s*([^,)]*)", src):
        if not m.group(1).startswith('"api/'):
            wbad.append("%s: fetch(%s" % (name, m.group(1)[:30]))
    if name.endswith(".html"):
        for tag in re.findall(r"<script\b[^>]*>", src):
            if 'src="' not in tag:
                wbad.append("%s: inline <script>" % name)
        for m in re.finditer(r"\s(style|on[a-z]+)\s*=", src):
            wbad.append("%s: inline %s=" % (name, m.group(1)))
        for m in re.finditer(r'\s(?:src|href|action)="([^"#][^"]*)"', src):
            if ":" in m.group(1) or m.group(1).startswith("//"):
                wbad.append("%s: external %s" % (name, m.group(1)))
check("dashboard pages load and send nothing off this PC", not wbad, wbad)
app_js = open(os.path.join(web, "app.js"), encoding="utf-8").read()
check("dashboard asks /api/series and falls back to /api/summary on a 404",
      'fetch("api/series?' in app_js and "r.status !== 404" in app_js and "summary?${q}" in app_js)
index_html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
ws_js = open(os.path.join(web, "workspace.js"), encoding="utf-8").read()
check("Macros and Layouts tabs start hidden and show only when the engine answers /api/macros",
      '<button class="tab" data-tab="macros" hidden>' in index_html and '<button class="tab" data-tab="layouts" hidden>' in index_html
      and '<script src="workspace.js"></script>' in index_html and 'fetch("api/macros"' in ws_js
      and ".hidden = !ok || viewOnly" in ws_js and ws_js.count('fetch("api/') == 2)
na_js = open(os.path.join(web, "netapps.js"), encoding="utf-8").read()
check("per-program network parts start hidden and show only when the engine answers /api/net_apps",
      '<div id="naBox" hidden>' in index_html and '<div class="grid cols-2 mt" id="naCharts" hidden>' in index_html
      and '<div class="setting" id="setAdminRow" hidden>' in index_html and '<script src="netapps.js"></script>' in index_html
      and "NA.ok = !!r;" in na_js and "if (!r) return;" in na_js and "domain" not in na_js.lower())


# ---- API
class FakeTracker:
    paused = False

    def __init__(self, s):
        self.store = s
        self.games = GameRules(s)

    power_cfg = {"price": 0.2, "currency": "$", "other_w": 50.0, "display_w": 75.0, "psu_eff": 0.9, "cpu_tdp": 125.0}

    def set_power_cfg(self, cfg):
        self.power_cfg = dict(self.power_cfg, **cfg)

    def games_changed(self):
        self.games.reload()

    def live(self):
        return {"paused": False, "today": store.totals(now - 3600, now + 3600), "alltime": store.totals(),
                "now": {"wpm": 0, "kpm": 0, "up": 0, "down": 0}, "net_hist": [], "app": {"exe": "x", "name": "x"},
                "idle": True, "day": day, "first_day": day, "pc_uptime_s": 1}

    def flush(self):
        pass

    flush_if_stale = flush

    def set_paused(self, p):
        self.paused = p


srv, port = start_server(FakeTracker(store), 18413)
base = "http://127.0.0.1:%d" % port


def get(path, host=None):
    req = urllib.request.Request(base + path, headers={"Host": host or "127.0.0.1:%d" % port})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


q = "from=%f&to=%f" % (now - 86400, now + 3600)
for path, test in [
    ("/api/live", lambda d: d["today"]["keys"] == 180),
    ("/api/summary?%s&bucket=hour" % q, lambda d: d["totals"]["keys"] == 180 and d["series"][0]["b"] == hour),
    ("/api/summary?%s&bucket=hour&mode=gaming" % q, lambda d: d["totals"]["keys"] == 30),
    ("/api/summary?%s&bucket=hour&mode=normal" % q, lambda d: d["totals"]["keys"] == 150),
    ("/api/gaming?%s&bucket=day" % q, lambda d: d["totals"]["keys"] == 30 and d["sessions"][0]["peak_apm"] == 180
     and d["per_game"][0]["exe"] == "league of legends.exe"),
    ("/api/games", lambda d: {a["exe"]: a["game"] for a in d["apps"]}["leagueclientux.exe"] is False),
    ("/api/system?%s&bucket=hour" % q, lambda d: d["apps"][0]["exe"] == "vmmemwsl.exe" and d["cores"] >= 1),
    ("/api/power?%s&bucket=hour" % q, lambda d: round(d["totals"]["wh"], 1) == 545.0 and d["cfg"]["price"] == 0.2
     and d["apps"][0]["exe"] == "league of legends.exe"),
    ("/api/summary?%s&bucket=day" % q, lambda d: d["series"][0]["b"] == day),
    ("/api/keys?" + q, lambda d: d["keys"][0] == ["E", 40] and d["combos"][0] == ["Ctrl+C", 3]),
    ("/api/apps?" + q, lambda d: d["apps"][0]["name"] == "Visual Studio Code" and d["apps"][0]["keys"] == 150
     and d["apps"][0]["game"] is False and d["apps"][1]["game"] is True),
    ("/api/mouse?" + q, lambda d: sorted(d["grid"]["cells"]) == [[10, 10, 7], [40, 30, 11]] and d["screen"]["monitors"]),
    ("/api/mouse?%s&mode=normal" % q, lambda d: d["grid"]["cells"] == [[10, 10, 7]]),
    ("/api/net?" + q, lambda d: d["ifaces"] == [["Ethernet", 1000, 50000]]),
    ("/api/top_at?hours=%d,x" % hour, lambda d: d[str(hour)]["cpu"][0] == "vmmemwsl.exe"
     and d[str(hour)]["gpu"][0] == "league of legends.exe"),
    ("/api/net?%s&bucket=hour" % q, lambda d: d["series"] == [[hour, "Ethernet", 1000, 50000]]),
    ("/api/calendar?days=30", lambda d: d["days"][0]["keys"] == 180),
    ("/api/records", lambda d: d["best_keys"]["v"] == 180 and d["top_app"]["name"] == "Visual Studio Code"
     and d["top_game"]["name"] == "League of Legends" and d["peak_apm"]["v"] == 140),
    ("/api/settings", lambda d: "data_dir" in d and len(d["backups"]) == 7),
    ("/api/series?%s&metrics=keys,down&metrics=gpu_util" % q, lambda d: d["metrics"] == ["keys", "down", "gpu_util"]
     and any(p["keys"] == 180 for p in d["points"])),
]:
    code, body = get(path)
    try:
        ok = code == 200 and test(json.loads(body))
    except Exception as e:
        ok, body = False, repr(e)
    label = path.split("?")[0] + "".join(" " + p for p in path.split("&")[1:] if p.startswith(("bucket", "mode")))
    check("GET " + label, ok, body[:200])

code, _ = get("/api/macros")
check("this tracker has no /api/macros yet (404 keeps the Macros and Layouts tabs hidden)", code == 404, code)
code, _ = get("/api/net_apps")
check("this tracker has no /api/net_apps yet (404 keeps the Network tab as it was)", code == 404, code)

code, body = get("/api/export.csv")
check("CSV export", code == 200 and body.startswith(b"day,keys,clicks"), body[:80])
code, _ = get("/", )
check("dashboard page served", code == 200)
code, _ = get("/../gk/store.py")
check("no path traversal", code == 404, code)
code, _ = get("/api/live", host="evil.example:%d" % port)
check("foreign Host rejected", code == 403, code)
req = urllib.request.Request(base + "/api/settings", data=b'{"paused":true}', method="POST",
                             headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"})
try:
    urllib.request.urlopen(req)
    code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("POST without X-GK rejected", code == 403, code)
req = urllib.request.Request(base + "/api/games", data=b'{"exe":"code.exe","game":true}', method="POST",
                             headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json", "X-GK": "1"})
with urllib.request.urlopen(req) as r:
    d = json.loads(r.read())
check("POST /api/games marks a game", {a["exe"]: a["game"] for a in d["apps"]}["code.exe"] is True)
req = urllib.request.Request(base + "/api/settings", data=b'{"power":{"price":0.31}}', method="POST",
                             headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json", "X-GK": "1"})
with urllib.request.urlopen(req) as r:
    d = json.loads(r.read())
check("POST electricity price", d["power"]["price"] == 0.31, d.get("power"))
def post_settings(body):
    req = urllib.request.Request(base + "/api/settings", data=json.dumps(body).encode(), method="POST",
                                 headers={"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json", "X-GK": "1"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())
messy = [{"id": "live-keys", "visible": True}, {"id": "Bad_ID"}, {"id": "trends", "visible": 0},
         {"id": "live-keys", "visible": False}, {"id": "x" * 41}, {"id": "fun-facts"}, "junk", {"id": "calendar", "visible": True}]
d = post_settings({"layout": {"name": "main", "items": messy}})
check("POST main layout drops invalid and duplicate ids",
      d["layouts"]["main"] == [{"id": "live-keys", "visible": True}, {"id": "trends", "visible": False},
                               {"id": "fun-facts", "visible": True}, {"id": "calendar", "visible": True}], d["layouts"])
code, body = get("/api/settings")
check("main layout round-trips through GET /api/settings",
      code == 200 and json.loads(body)["layouts"]["main"] == d["layouts"]["main"])
d = post_settings({"layout": {"name": "other", "items": [{"id": "calendar", "visible": False}]}})
check("unknown layout name ignored", "other" not in d["layouts"] and d["layouts"]["main"][0]["id"] == "live-keys",
      d["layouts"])

code, body = get("/mini")
check("GET /mini serves the mini page", code == 200 and b"miniWidgets" in body, code)
d = post_settings({"mini": {"opacity": 5, "w": 50, "x": 100, "bogus": 1}})
check("mini settings clamped and unknown keys dropped", d["mini"]["opacity"] == 1.0 and d["mini"]["w"] == 140
      and d["mini"]["x"] == 100 and "bogus" not in d["mini"], d["mini"])
d = post_settings({"mini": {"open": 1, "y": None, "h": "tall"}})
check("mini flags, null position, bad number", d["mini"]["open"] is True and d["mini"]["y"] is None
      and d["mini"]["h"] == 200, d["mini"])
d = post_settings({"layout": {"name": "mini", "items": [{"id": "kpm", "visible": True}, {"id": "wpm", "visible": False},
                                                       {"id": "kpm"}]}})
check("mini layout round-trips", d["layouts"]["mini"] == [{"id": "kpm", "visible": True},
                                                        {"id": "wpm", "visible": False}], d["layouts"])
check("mini module imports without pywebview", isinstance(has_webview(), bool))
d = post_settings({})
check("dashboard window defaults to 1440x960, not maximized", d["dash"] == {"x": None, "y": None, "w": 1440, "h": 960,
      "max": False}, d["dash"])
d = post_settings({"dash": {"w": 10, "h": "big", "x": -5, "y": 40, "max": 1, "open": True}})
check("dashboard placement clamped, flags kept, unknown keys dropped", d["dash"]["w"] == 480 and d["dash"]["h"] == 960
      and d["dash"]["x"] == -5 and d["dash"]["max"] is True and "open" not in d["dash"], d["dash"])
post_settings({"dash": {"x": None, "y": None, "w": 1440, "h": 960, "max": False}})
mc = MiniController(port, store)
post_settings({"mini": {"open": False}})
check("mini controller closed", mc.is_open() is False)
mc.set_clickthrough(True)
check("tray click-through sets the saved flag", mc.cfg()["clickthrough"] is True)
mc.set_clickthrough(False)
check("click-through off again", mc.cfg()["clickthrough"] is False)

# ---- phone link (view-only server with a key)
import socket  # noqa: E402
import gk.server  # noqa: E402
from gk.server import remote_cfg, set_remote  # noqa: E402
# a free port, not 8414: the real tracker may hold 8414 with its own phone link on
_s = socket.socket()
_s.bind(("0.0.0.0", 0))
gk.server.REMOTE_PORT = REMOTE_PORT = _s.getsockname()[1]
_s.close()
ft = FakeTracker(store)
set_remote(ft, True)
key = remote_cfg(store)["key"]
rbase = "http://127.0.0.1:%d" % REMOTE_PORT


def rget(path, headers=None):
    req = urllib.request.Request(rbase + path, headers=dict({"Host": "192.168.1.5:%d" % REMOTE_PORT}, **(headers or {})))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


code, _, _ = rget("/api/live")
check("phone link needs the key", code == 403, code)
code, _, hd = rget("/?k=" + key)
check("phone link opens with the key and sets a cookie", code == 200 and "gk_k=" in (hd.get("Set-Cookie") or ""), code)
code, body, _ = rget("/api/settings", {"Cookie": "gk_k=" + key})
d = json.loads(body) if code == 200 else {}
check("phone link works with the cookie, hides settings", code == 200 and d.get("view_only") and "remote" not in d
      and d.get("data_dir") == "", code)
code, _, _ = rget("/api/live?k=wrong" + key)
check("phone link rejects a wrong key", code == 403, code)
req = urllib.request.Request(rbase + "/api/settings?k=" + key, data=b'{"paused":true}', method="POST",
                             headers={"Content-Type": "application/json", "X-GK": "1"})
try:
    urllib.request.urlopen(req)
    code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("phone link is view-only", code == 403, code)
code, body = get("/api/settings")
d = json.loads(body)
check("settings list phone links", d["remote"]["on"] and key in d["remote"]["urls"][0]["url"], d.get("remote"))
old = key
key = remote_cfg(store, {"new_key": True})["key"]
check("new key replaces the old one", key != old and rget("/?k=" + old)[0] == 403)
code, _, _ = rget("/api/privacy", {"Cookie": "gk_k=" + key})
check("phone link can't see the privacy panel", code == 404, code)
code, body = get("/api/privacy")
pv = json.loads(body) if code == 200 else {}
kinds = {(r["local"], r["kind"]) for r in pv.get("sockets", [])}
check("privacy panel lists the dashboard server as local", ("127.0.0.1:%d" % port, "local") in kinds, kinds)
check("privacy panel shows the phone link listening", ("0.0.0.0:%d" % REMOTE_PORT, "phone") in kinds, kinds)
check("privacy panel: all local, guard on, nothing blocked", pv.get("all_local") and pv["guard"]["on"]
      and pv["guard"]["blocked"] == 0, {k: pv.get(k) for k in ("all_local", "guard")})

# ---- lockdown mode keeps the phone link off, from every path
d = post_settings({"lockdown": True})
check("lockdown turns the phone link off", d["lockdown"] is True and not d["remote"]["on"], d["remote"])
d = post_settings({"remote": {"enabled": True}})
check("phone link can't be turned on in lockdown", not d["remote"]["on"] and "Lockdown" in d["remote"]["error"],
      d["remote"])
set_remote(ft, True)                                # the startup path
check("startup path respects lockdown", not json.loads(get("/api/settings")[1])["remote"]["on"])
d = post_settings({"lockdown": False})
check("leaving lockdown brings the phone link back", d["lockdown"] is False and d["remote"]["on"]
      and not d["remote"]["error"], d["remote"])
post_settings({"remote": {"enabled": False}})
set_remote(ft, False)

# ---- one server per port, and previews leave the real startup entry alone
import socket  # noqa: E402
from gk.server import LocalHTTPServer  # noqa: E402
try:
    LocalHTTPServer(("127.0.0.1", port), None).server_close()
    taken = False
except OSError:
    taken = True
check("a second server can't bind a port that's in use", taken)
from gk import server as gk_server  # noqa: E402
if gk_server.PREVIEW:           # never post this to a server that could really change the startup entry
    was = json.loads(get("/api/settings")[1])["autostart"]
    d = post_settings({"autostart": not was})
    check("preview can't change Start with Windows", d["autostart"] == was and d["autostart_fixed"] is True,
          d["autostart"])
else:
    check("preview can't change Start with Windows", False, "GK_DATA_DIR not seen as a preview")

# ---- the outgoing-network block (last: these attempts are meant to be blocked)

hdrs = urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Host": "127.0.0.1:%d" % port})).headers
csp = hdrs.get("Content-Security-Policy") or ""
check("pages send a strict Content-Security-Policy", "default-src 'self'" in csp and "script-src 'self'" in csp
      and "unsafe" not in csp, csp)
check("no lookups or outside connections so far", privacy.guard_state()["blocked"] == 0, privacy.guard_state())
check("is_local: loopback yes, others no", privacy.is_local("127.0.0.1") and privacy.is_local("::1")
      and privacy.is_local("localhost") and not privacy.is_local("192.168.1.5") and not privacy.is_local("example.com"))


def blocked(fn):
    try:
        fn()
    except PermissionError:
        return True
    except Exception as e:
        return "not blocked: %r" % e
    return False


logging.getLogger("gk.privacy").setLevel(logging.ERROR)    # the warnings below are expected
before = privacy.guard_state()["blocked"]
check("outside connect blocked", blocked(lambda: socket.create_connection(("192.0.2.1", 9), timeout=1)) is True)
check("UDP send outside blocked", blocked(lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.1", 9))) is True)
check("DNS lookup blocked", blocked(lambda: socket.getaddrinfo("example.com", 80)) is True)
check("reverse lookup blocked", blocked(lambda: socket.gethostbyaddr("192.0.2.1")) is True)
check("web request blocked", blocked(lambda: urllib.request.urlopen("http://example.com/", timeout=1)) is True)
check("blocked attempts are counted", privacy.guard_state()["blocked"] - before == 5, privacy.guard_state())
check("this PC still reachable under the block", get("/api/live")[0] == 200)

srv.shutdown()

# ---- one copy only: broken-database refusal, pinned folder, mutex names, window focus
import shutil  # noqa: E402

from gk import winapi  # noqa: E402
from gk.mini import mutex_name  # noqa: E402
from gk.store import STAT_COLS, check_db, data_dir  # noqa: E402

base = tempfile.mkdtemp(prefix="gk-selftest-copy-")
fresh = os.path.join(base, "fresh")
os.makedirs(fresh)
ok, why, bk = check_db(fresh)
check("new install (no db, no backups) may start", ok and bk is None, why)
check("db check creates no file", os.listdir(fresh) == [], os.listdir(fresh))

lost = os.path.join(base, "lost")
os.makedirs(os.path.join(lost, "backups"))
for day in ("2026-10-01", "2026-10-03", "2026-10-02"):
    open(os.path.join(lost, "backups", "gkeystrokes-%s.db" % day), "wb").close()
ok, why, bk = check_db(lost)
check("missing db with backups is refused", not ok and "missing" in why, why)
check("refusal names the newest backup", bk and bk.endswith("gkeystrokes-2026-10-03.db"), bk)
check("refused missing db is not created", not os.path.exists(os.path.join(lost, "gkeystrokes.db")))

junk = os.path.join(base, "junk")
os.makedirs(junk)
with open(os.path.join(junk, "gkeystrokes.db"), "wb") as f:
    f.write(b"this is not a database" * 100)
ok, why, bk = check_db(junk)
check("unreadable db is refused", not ok and "can't be opened" in why, why)

good = os.path.join(base, "good")
os.makedirs(good)
s1 = Store(os.path.join(good, "gkeystrokes.db"))
s1.write({"stats": [(500000 + i, "x.exe") + (1,) * (len(STAT_COLS)) for i in range(3000)]})
s1.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
check("store records its data folder on first run", s1.pinned_dir == os.path.abspath(good) and not s1.dir_moved,
      (s1.pinned_dir, s1.dir_moved))
s1.conn().close()
s1._local.c = None
ok, why, _ = check_db(good)
check("healthy db passes the check", ok, why)

moved = os.path.join(base, "moved")
shutil.copytree(good, moved)
s2 = Store(os.path.join(moved, "gkeystrokes.db"))
check("db opened from another folder is flagged", s2.dir_moved and s2.pinned_dir == os.path.abspath(good),
      (s2.pinned_dir, s2.dir_moved))
s2.conn().close()
s2._local.c = None

bad = os.path.join(base, "bad")
shutil.copytree(good, bad)
path = os.path.join(bad, "gkeystrokes.db")
size = os.path.getsize(path)
with open(path, "r+b") as f:                # scribble over the pages after the schema page
    f.seek(4096 * 2)
    f.write(b"\xff" * max(0, size - 4096 * 2))
ok, why, _ = check_db(bad)
check("db failing quick_check is refused", not ok, why)

check("data folder is absolute", os.path.isabs(data_dir()), data_dir())
a = winapi.instance_name("gkeystrokes", fresh)
check("mutex name is per folder", a == winapi.instance_name("gkeystrokes", fresh.upper() + os.sep)
      and a != winapi.instance_name("gkeystrokes", lost), a)
check("mini mutex differs from the tracker's", mutex_name(fresh) != a and mutex_name(fresh).startswith("Local\\"))
name = winapi.instance_name("gkeystrokes_selftest", base)
check("first copy gets the mutex", winapi.acquire_single_instance(name) and winapi.instance_running(name))
check("second copy is turned away", not winapi.acquire_single_instance(name))
check("no mutex, nothing running", not winapi.instance_running(winapi.instance_name("gkeystrokes_selftest", fresh)))
check("focus with no window is a no-op", winapi.focus_window(None) is False and winapi.focus_window(0) is False)
check("no dashboard window for an unused profile",
      winapi.edge_windows(os.path.join(base, "edge-profile"), "gkeystrokes") == [])
from gk.mini import dash_args, dash_cmd, dash_mutex, dash_procs  # noqa: E402
check("dashboard mutex differs from the tracker's and the mini's",
      dash_mutex(fresh) not in (a, mutex_name(fresh)) and dash_mutex(fresh) == dash_mutex(fresh.upper() + os.sep))
check("dashboard command names its folder and port",
      dash_args(dash_cmd(8413, fresh)) == (os.path.normcase(os.path.abspath(fresh)), 8413), dash_cmd(8413, fresh))
check("mini and tracker command lines are not dashboards", dash_args([sys.executable, "-m", "gk.mini", "--port", "8413"])
      is None and dash_args(["pythonw.exe", "gkeystrokes.pyw"]) is None and dash_args(None) is None)
check("no dashboard window process for an unused folder", dash_procs(fresh) == [])
check("no window for a process that has none", winapi.process_windows([os.getpid()], "gkeystrokes") == []
      and winapi.process_windows([], "gkeystrokes") == [])
shutil.rmtree(base, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
