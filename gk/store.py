"""SQLite storage. Everything is aggregated counts in hourly (or daily) buckets.

`hour` columns are UTC epoch hours (int(ts // 3600)); grouping into local days
happens in SQL with 'localtime'. `day` columns are local 'YYYY-MM-DD' strings.

Keys, clicks and shortcuts are stored per app, so "gaming" vs "normal" is decided
at query time from the current game list (marking a game later re-classifies its
past data too).
"""
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
from datetime import datetime

log = logging.getLogger("gk.store")

SCHEMA_VERSION = 3

STAT_COLS = ("keys", "lclicks", "rclicks", "mclicks", "xclicks", "scroll",
             "mouse_mm", "active_s", "typed", "typing_ms")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stats (
    hour INTEGER NOT NULL, app TEXT NOT NULL,
    keys INTEGER DEFAULT 0, lclicks INTEGER DEFAULT 0, rclicks INTEGER DEFAULT 0,
    mclicks INTEGER DEFAULT 0, xclicks INTEGER DEFAULT 0, scroll REAL DEFAULT 0,
    mouse_mm REAL DEFAULT 0, active_s INTEGER DEFAULT 0,
    typed INTEGER DEFAULT 0, typing_ms REAL DEFAULT 0,
    PRIMARY KEY (hour, app));
CREATE TABLE IF NOT EXISTS keys (
    hour INTEGER NOT NULL, app TEXT NOT NULL, key TEXT NOT NULL, n INTEGER DEFAULT 0,
    PRIMARY KEY (hour, app, key));
CREATE TABLE IF NOT EXISTS net (
    hour INTEGER NOT NULL, iface TEXT NOT NULL, up INTEGER DEFAULT 0, down INTEGER DEFAULT 0,
    PRIMARY KEY (hour, iface));
CREATE TABLE IF NOT EXISTS uptime (
    hour INTEGER PRIMARY KEY, on_s INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS clicks (
    day TEXT NOT NULL, app TEXT NOT NULL, gx INTEGER NOT NULL, gy INTEGER NOT NULL, n INTEGER DEFAULT 0,
    PRIMARY KEY (day, app, gx, gy));
CREATE TABLE IF NOT EXISTS combos (
    day TEXT NOT NULL, app TEXT NOT NULL, combo TEXT NOT NULL, n INTEGER DEFAULT 0,
    PRIMARY KEY (day, app, combo));
CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY, peak_wpm REAL DEFAULT 0, peak_kpm INTEGER DEFAULT 0,
    peak_down REAL DEFAULT 0, peak_up REAL DEFAULT 0, peak_apm REAL DEFAULT 0,
    peak_watts REAL DEFAULT 0, peak_gpu_temp REAL DEFAULT 0, peak_gpu_w REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, exe TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
    keys INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, active_s INTEGER DEFAULT 0, peak_apm REAL DEFAULT 0,
    wh REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sessions_start ON sessions(start);
CREATE TABLE IF NOT EXISTS energy (
    hour INTEGER PRIMARY KEY, secs INTEGER DEFAULT 0,
    gpu_wh REAL DEFAULT 0, cpu_wh REAL DEFAULT 0, other_wh REAL DEFAULT 0, display_wh REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gpu (
    hour INTEGER PRIMARY KEY, secs INTEGER DEFAULT 0, util_sum REAL DEFAULT 0, util_max REAL DEFAULT 0,
    temp_sum REAL DEFAULT 0, temp_max REAL DEFAULT 0, power_max REAL DEFAULT 0, vram_max REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gpu_apps (
    hour INTEGER NOT NULL, app TEXT NOT NULL, secs INTEGER DEFAULT 0, gpu_s REAL DEFAULT 0,
    wh REAL DEFAULT 0, vram_max REAL DEFAULT 0, PRIMARY KEY (hour, app));
CREATE TABLE IF NOT EXISTS metrics (
    hour INTEGER NOT NULL, key TEXT NOT NULL, sum REAL DEFAULT 0, max REAL DEFAULT 0, secs REAL DEFAULT 0,
    PRIMARY KEY (hour, key));
CREATE TABLE IF NOT EXISTS proc_usage (
    hour INTEGER NOT NULL, app TEXT NOT NULL, secs REAL DEFAULT 0, cpu_s REAL DEFAULT 0, ram_mbs REAL DEFAULT 0,
    ram_max REAL DEFAULT 0, io_read REAL DEFAULT 0, io_write REAL DEFAULT 0, PRIMARY KEY (hour, app));
CREATE TABLE IF NOT EXISTS apps (
    exe TEXT PRIMARY KEY, name TEXT, path TEXT);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY, v TEXT);
"""

GRID_W, GRID_H = 96, 54          # click heatmap resolution over the virtual screen
BACKUP_KEEP = 7                  # daily database copies kept in <data>/backups
STREAK_MIN = 100                 # keys+clicks needed for a day to count toward a streak
FAR_FUTURE = 4102444800          # 2100-01-01; Windows' fromtimestamp rejects much larger/smaller values


def data_dir():
    d = os.environ.get("GK_DATA_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "gkeystrokes")
    d = os.path.abspath(d)                  # one absolute path, whatever the working folder
    os.makedirs(d, exist_ok=True)
    return d


def newest_backup(folder):
    """Full path of the newest daily backup in <folder>/backups, or None."""
    b = os.path.join(folder, "backups")
    try:
        names = sorted((f for f in os.listdir(b) if f.startswith("gkeystrokes-") and f.endswith(".db")), reverse=True)
    except OSError:
        return None
    return os.path.join(b, names[0]) if names else None


def check_db(folder):
    """Is it safe to open (or create) <folder>/gkeystrokes.db? Returns (ok, reason, newest backup).
    Never creates or changes a file: a broken or missing database must not be quietly replaced by an
    empty one. A brand-new install (no database, no backups) is ok."""
    path = os.path.join(folder, "gkeystrokes.db")
    backup = newest_backup(folder)
    if not os.path.exists(path):
        if backup:
            return False, "The database %s is missing, but the backups folder has copies." % path, backup
        return True, "", None
    try:
        c = sqlite3.connect("file:%s?mode=rw" % urllib.parse.quote(path.replace("\\", "/")), uri=True, timeout=10)
        try:
            res = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return False, "The database %s can't be opened: %s" % (path, e), backup
    if res != "ok":
        return False, "The database %s failed its integrity check: %s" % (path, res), backup
    return True, "", backup


def local_day(ts):
    return datetime.fromtimestamp(min(max(ts, 172800), FAR_FUTURE)).strftime("%Y-%m-%d")


def _upsert(table, keycols, valcols, agg="+"):
    cols = keycols + valcols
    if agg == "+":
        sets = ", ".join("%s=%s+excluded.%s" % (c, c, c) for c in valcols)
    else:
        sets = ", ".join("%s=max(%s,excluded.%s)" % (c, c, c) for c in valcols)
    return "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s" % (
        table, ",".join(cols), ",".join("?" * len(cols)), ",".join(keycols), sets)


PEAK_COLS = ["peak_wpm", "peak_kpm", "peak_down", "peak_up", "peak_apm", "peak_watts", "peak_gpu_temp", "peak_gpu_w"]
SQL_STATS = _upsert("stats", ["hour", "app"], list(STAT_COLS))
SQL_KEYS = _upsert("keys", ["hour", "app", "key"], ["n"])
SQL_NET = _upsert("net", ["hour", "iface"], ["up", "down"])
SQL_UPTIME = _upsert("uptime", ["hour"], ["on_s"])
SQL_CLICKS = _upsert("clicks", ["day", "app", "gx", "gy"], ["n"])
SQL_COMBOS = _upsert("combos", ["day", "app", "combo"], ["n"])
SQL_DAILY = _upsert("daily", ["day"], PEAK_COLS, agg="max")
SQL_SESSION = "UPDATE sessions SET end=?, keys=?, clicks=?, active_s=?, peak_apm=max(peak_apm, ?), wh=? WHERE id=?"
SQL_ENERGY = _upsert("energy", ["hour"], ["secs", "gpu_wh", "cpu_wh", "other_wh", "display_wh"])
SQL_GPU = ("INSERT INTO gpu(hour, secs, util_sum, util_max, temp_sum, temp_max, power_max, vram_max) "
           "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(hour) DO UPDATE SET secs=secs+excluded.secs, "
           "util_sum=util_sum+excluded.util_sum, util_max=max(util_max,excluded.util_max), "
           "temp_sum=temp_sum+excluded.temp_sum, temp_max=max(temp_max,excluded.temp_max), "
           "power_max=max(power_max,excluded.power_max), vram_max=max(vram_max,excluded.vram_max)")
SQL_METRICS = ("INSERT INTO metrics(hour, key, sum, max, secs) VALUES(?,?,?,?,?) ON CONFLICT(hour, key) DO UPDATE "
               "SET sum=sum+excluded.sum, max=max(max,excluded.max), secs=secs+excluded.secs")
SQL_PROC = ("INSERT INTO proc_usage(hour, app, secs, cpu_s, ram_mbs, ram_max, io_read, io_write) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(hour, app) DO UPDATE SET secs=secs+excluded.secs, cpu_s=cpu_s+excluded.cpu_s, "
            "ram_mbs=ram_mbs+excluded.ram_mbs, ram_max=max(ram_max,excluded.ram_max), "
            "io_read=io_read+excluded.io_read, io_write=io_write+excluded.io_write")
SQL_GPU_APPS = ("INSERT INTO gpu_apps(hour, app, secs, gpu_s, wh, vram_max) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(hour, app) DO UPDATE SET secs=secs+excluded.secs, gpu_s=gpu_s+excluded.gpu_s, "
                "wh=wh+excluded.wh, vram_max=max(vram_max,excluded.vram_max)")

DAY = "date(hour*3600,'unixepoch','localtime')"
BUCKETS = {
    "hour": "hour",
    "day": DAY,
    "week": "date(hour*3600,'unixepoch','localtime','-6 days','weekday 1')",
    "month": "strftime('%Y-%m',hour*3600,'unixepoch','localtime')",
}


# /api/series: start of each bucket in epoch seconds (local midnight for day/week/month)
BUCKET_T = {
    "hour": "h * 3600",
    "day": "CAST(strftime('%s', b, 'utc') AS INTEGER)",
    "week": "CAST(strftime('%s', b, 'utc') AS INTEGER)",
    "month": "CAST(strftime('%s', b || '-01', 'utc') AS INTEGER)",
}
BUCKET_HOURS = {"hour": 1, "day": 24, "week": 168, "month": 720}
SERIES_MAX_POINTS = 50000
# metric -> (table group, column index in that group's row). Sums are 0 where nothing was recorded.
SERIES_SUMS = {"keys": ("stats", 0), "clicks": ("stats", 1), "lclicks": ("stats", 2), "rclicks": ("stats", 3),
               "mclicks": ("stats", 4), "xclicks": ("stats", 5), "scroll": ("stats", 6), "mouse_mm": ("stats", 7),
               "active_s": ("stats", 8), "typed": ("stats", 9), "typing_ms": ("stats", 10),
               "backspaces": ("backspaces", 0), "up": ("net", 0), "down": ("net", 1), "uptime_s": ("uptime", 0),
               "wh": ("energy", 0), "gpu_wh": ("energy", 1), "cpu_wh": ("energy", 2), "other_wh": ("energy", 3),
               "display_wh": ("energy", 4)}
# Sampled metrics (averages and maxima) are None where there were no samples.
SERIES_GPU = ("gpu_util", "gpu_temp", "gpu_temp_max", "gpu_w_max", "vram_max")
SERIES_SQL = {
    "stats": "SUM(keys), SUM(lclicks+rclicks+mclicks+xclicks), SUM(lclicks), SUM(rclicks), SUM(mclicks), "
             "SUM(xclicks), SUM(scroll), SUM(mouse_mm), SUM(active_s), SUM(typed), SUM(typing_ms) FROM stats",
    "backspaces": "SUM(n) FROM keys",
    "net": "SUM(up), SUM(down) FROM net",
    "uptime": "SUM(on_s) FROM uptime",
    "energy": "SUM(gpu_wh+cpu_wh+other_wh+display_wh), SUM(gpu_wh), SUM(cpu_wh), SUM(other_wh), SUM(display_wh) "
              "FROM energy",
    "gpu": "SUM(secs), SUM(util_sum), SUM(temp_sum), MAX(temp_max), MAX(power_max), MAX(vram_max) FROM gpu",
}


class TooManyPoints(ValueError):
    pass


def app_filter(mode, games, col="app"):
    """SQL fragment limiting rows to games ('gaming'), non-games ('normal') or everything ('all')."""
    games = list(games or ())
    if mode == "gaming":
        if not games:
            return " AND 0", []
        return " AND %s IN (%s)" % (col, ",".join("?" * len(games))), games
    if mode == "normal" and games:
        return " AND %s NOT IN (%s)" % (col, ",".join("?" * len(games))), games
    return "", []


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "gkeystrokes.db")
        self._local = threading.local()
        self._wlock = threading.Lock()
        c = self.conn()
        self._migrate(c)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO meta(k,v) VALUES('schema',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  (str(SCHEMA_VERSION),))
        c.commit()
        self.pinned_dir, self.dir_moved = self._pin_dir()

    def _pin_dir(self):
        """The data folder is recorded on first run. A later start from a different folder (copied or
        moved database, or a redirected AppData) is logged and shown in Settings; the record is kept."""
        here = os.path.abspath(os.path.dirname(self.path))
        pinned = self.get_meta("data_dir")
        if pinned is None:
            self.set_meta("data_dir", here)
            return here, False
        if os.path.normcase(pinned) != os.path.normcase(here):
            log.warning("database was first used in %s, now opened from %s", pinned, here)
            return pinned, True
        return pinned, False

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA cache_size=-1024")                # page cache 1 MiB per connection (SQLite default 2 MiB)
            c.execute("PRAGMA journal_size_limit=16777216")     # WAL file is cut back to 16 MiB after checkpoints (disk only)
            self._local.c = c
        return c

    def _migrate(self, c):
        """v1 -> v2: keys/clicks/combos gain an `app` column; daily gains peak_apm."""
        def cols(table):
            return {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        for table, col, ddl in (("daily", "peak_watts", "REAL DEFAULT 0"), ("daily", "peak_gpu_temp", "REAL DEFAULT 0"),
                                ("daily", "peak_gpu_w", "REAL DEFAULT 0"), ("sessions", "wh", "REAL DEFAULT 0")):
            if cols(table) and col not in cols(table):          # v2 -> v3: plain column adds, no rebuild
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
                c.commit()
        need_app = {t for t in ("keys", "clicks", "combos") if cols(t) and "app" not in cols(t)}
        need_apm = bool(cols("daily")) and "peak_apm" not in cols("daily")
        if not (need_app or need_apm):
            return
        backup = self.path + ".v1-backup"
        log.info("migrating database to v%d (backup: %s)", SCHEMA_VERSION, backup)
        dst = sqlite3.connect(backup)
        c.backup(dst)
        dst.close()
        if need_apm:
            c.execute("ALTER TABLE daily ADD COLUMN peak_apm REAL DEFAULT 0")
            c.commit()
        copy = {"keys": ("hour, app, key, n", "hour, '', key, n"),
                "clicks": ("day, app, gx, gy, n", "day, '', gx, gy, n"),
                "combos": ("day, app, combo, n", "day, '', combo, n")}
        for t in sorted(need_app):
            c.execute("ALTER TABLE %s RENAME TO %s_v1" % (t, t))
        c.executescript(SCHEMA)                 # creates the new tables
        for t in sorted(need_app):
            new_cols, old_cols = copy[t]
            c.execute("INSERT INTO %s(%s) SELECT %s FROM %s_v1" % (t, new_cols, old_cols, t))
            c.execute("DROP TABLE %s_v1" % t)
        c.commit()

    # ------------------------------------------------------------ writes

    def write(self, b):
        """b: dict of lists of row tuples, keyed like the SQL_* statements."""
        with self._wlock:
            c = self.conn()
            with c:
                for key, sql in (("stats", SQL_STATS), ("keys", SQL_KEYS), ("net", SQL_NET),
                                 ("uptime", SQL_UPTIME), ("clicks", SQL_CLICKS),
                                 ("combos", SQL_COMBOS), ("daily", SQL_DAILY), ("sessions", SQL_SESSION),
                                 ("energy", SQL_ENERGY), ("gpu", SQL_GPU), ("gpu_apps", SQL_GPU_APPS),
                                 ("metrics", SQL_METRICS), ("proc_usage", SQL_PROC)):
                    rows = b.get(key)
                    if rows:
                        c.executemany(sql, rows)
                for exe, name, path in b.get("apps", ()):
                    c.execute("INSERT INTO apps(exe,name,path) VALUES(?,?,?) ON CONFLICT(exe) "
                              "DO UPDATE SET name=excluded.name, path=excluded.path", (exe, name, path))

    def backup(self, day):
        """Copy the database to backups/gkeystrokes-<day>.db (SQLite online backup, safe while tracking).
        Keeps the newest BACKUP_KEEP. A copy that fails its check is dropped and old ones are kept."""
        folder = os.path.join(os.path.dirname(self.path), "backups")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, "gkeystrokes-%s.db" % day)
        if os.path.exists(dest):
            return None
        tmp = dest + ".tmp"
        dst = sqlite3.connect(tmp)
        try:
            with self._wlock:
                self.conn().backup(dst)
            ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            dst.close()
        if not ok:
            os.remove(tmp)
            raise sqlite3.DatabaseError("backup failed its integrity check; older backups kept")
        os.replace(tmp, dest)
        for f in self.backups()[BACKUP_KEEP:]:
            os.remove(os.path.join(folder, f))
        return dest

    def backups(self):
        """Backup file names, newest first."""
        folder = os.path.join(os.path.dirname(self.path), "backups")
        if not os.path.isdir(folder):
            return []
        return sorted((f for f in os.listdir(folder) if f.startswith("gkeystrokes-") and f.endswith(".db")), reverse=True)

    def start_session(self, exe, start):
        with self._wlock:
            c = self.conn()
            with c:
                return c.execute("INSERT INTO sessions(exe, start, end) VALUES(?,?,?)", (exe, start, start)).lastrowid

    def get_meta(self, k, default=None):
        r = self.conn().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k, v):
        with self._wlock:
            c = self.conn()
            with c:
                c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                          (k, str(v)))

    # ------------------------------------------------------------ reads

    @staticmethod
    def _hours(t0, t1):
        return int(t0 // 3600), int(-(-t1 // 3600))

    @staticmethod
    def _days(t0, t1):
        return local_day(t0), local_day(max(t0, t1 - 1))

    def totals(self, t0=0, t1=None, mode="all", games=()):
        t1 = t1 or FAR_FUTURE
        h0, h1 = self._hours(t0, t1)
        w, p = app_filter(mode, games)
        c = self.conn()
        r = c.execute("SELECT %s FROM stats WHERE hour>=? AND hour<?%s" %
                      (",".join("COALESCE(SUM(%s),0)" % k for k in STAT_COLS), w), [h0, h1] + p).fetchone()
        out = dict(zip(STAT_COLS, r))
        out["up"], out["down"] = c.execute(
            "SELECT COALESCE(SUM(up),0), COALESCE(SUM(down),0) FROM net WHERE hour>=? AND hour<?", (h0, h1)).fetchone()
        out["uptime_s"] = c.execute(
            "SELECT COALESCE(SUM(on_s),0) FROM uptime WHERE hour>=? AND hour<?", (h0, h1)).fetchone()[0]
        out["backspaces"] = c.execute(
            "SELECT COALESCE(SUM(n),0) FROM keys WHERE key='Backspace' AND hour>=? AND hour<?%s" % w,
            [h0, h1] + p).fetchone()[0]
        d0, d1 = self._days(t0, t1)
        r = c.execute("SELECT %s FROM daily WHERE day>=? AND day<=?" %
                      ",".join("COALESCE(MAX(%s),0)" % k for k in PEAK_COLS), (d0, d1)).fetchone()
        out.update(zip(PEAK_COLS, r))
        return out

    def series(self, t0, t1, bucket="day", mode="all", games=()):
        """Per-bucket totals: list of dicts with 'b' = bucket label (hour int or date string)."""
        expr = BUCKETS.get(bucket, DAY)
        h0, h1 = self._hours(t0, t1)
        w, p = app_filter(mode, games)
        c = self.conn()
        rows = {}
        for r in c.execute(
                "SELECT %s b, SUM(keys), SUM(lclicks+rclicks+mclicks+xclicks), SUM(active_s), SUM(mouse_mm), "
                "SUM(scroll), SUM(typed), SUM(typing_ms) FROM stats WHERE hour>=? AND hour<?%s GROUP BY b" % (expr, w),
                [h0, h1] + p):
            rows[r[0]] = {"b": r[0], "keys": r[1], "clicks": r[2], "active_s": r[3], "mouse_mm": r[4],
                          "scroll": r[5], "typed": r[6], "typing_ms": r[7], "up": 0, "down": 0}
        for b, up, down in c.execute("SELECT %s b, SUM(up), SUM(down) FROM net WHERE hour>=? AND hour<? GROUP BY b"
                                     % expr, (h0, h1)):
            row = rows.setdefault(b, {"b": b, "keys": 0, "clicks": 0, "active_s": 0, "mouse_mm": 0,
                                      "scroll": 0, "typed": 0, "typing_ms": 0})
            row["up"], row["down"] = up, down
        return sorted(rows.values(), key=lambda r: r["b"])

    def punchcard(self, t0, t1, mode="all", games=()):
        """[[weekday 0=Sun, hour 0-23, keys+clicks], ...]"""
        h0, h1 = self._hours(t0, t1)
        w, p = app_filter(mode, games)
        return self.conn().execute(
            "SELECT CAST(strftime('%%w',hour*3600,'unixepoch','localtime') AS INT) wd, "
            "CAST(strftime('%%H',hour*3600,'unixepoch','localtime') AS INT) hr, "
            "SUM(keys+lclicks+rclicks+mclicks+xclicks) FROM stats WHERE hour>=? AND hour<?%s GROUP BY wd, hr" % w,
            [h0, h1] + p).fetchall()

    def keys(self, t0, t1, mode="all", games=()):
        h0, h1 = self._hours(t0, t1)
        w, p = app_filter(mode, games)
        return self.conn().execute(
            "SELECT key, SUM(n) s FROM keys WHERE hour>=? AND hour<?%s GROUP BY key ORDER BY s DESC" % w,
            [h0, h1] + p).fetchall()

    def combos(self, t0, t1, limit=25, mode="all", games=()):
        d0, d1 = self._days(t0, t1)
        w, p = app_filter(mode, games)
        return self.conn().execute(
            "SELECT combo, SUM(n) s FROM combos WHERE day>=? AND day<=?%s GROUP BY combo ORDER BY s DESC LIMIT ?" % w,
            [d0, d1] + p + [limit]).fetchall()

    def apps(self, t0, t1, mode="all", games=()):
        h0, h1 = self._hours(t0, t1)
        w, p = app_filter(mode, games, "s.app")
        rows = self.conn().execute(
            "SELECT s.app, COALESCE(a.name, s.app), SUM(active_s), SUM(keys), "
            "SUM(lclicks+rclicks+mclicks+xclicks), SUM(mouse_mm), SUM(typed), SUM(typing_ms), SUM(scroll) "
            "FROM stats s LEFT JOIN apps a ON a.exe = s.app WHERE hour>=? AND hour<?%s "
            "GROUP BY s.app ORDER BY SUM(active_s) DESC, SUM(keys) DESC" % w, [h0, h1] + p).fetchall()
        return [{"exe": r[0], "name": r[1], "active_s": r[2], "keys": r[3], "clicks": r[4],
                 "mouse_mm": r[5], "typed": r[6], "typing_ms": r[7], "scroll": r[8]} for r in rows]

    def clicks(self, t0, t1, mode="all", games=()):
        d0, d1 = self._days(t0, t1)
        w, p = app_filter(mode, games)
        return self.conn().execute(
            "SELECT gx, gy, SUM(n) FROM clicks WHERE day>=? AND day<=?%s GROUP BY gx, gy" % w,
            [d0, d1] + p).fetchall()

    def net_ifaces(self, t0, t1):
        h0, h1 = self._hours(t0, t1)
        return self.conn().execute(
            "SELECT iface, SUM(up), SUM(down) FROM net WHERE hour>=? AND hour<? GROUP BY iface "
            "ORDER BY SUM(down) DESC", (h0, h1)).fetchall()

    def net_series(self, t0, t1, bucket="day"):
        """[[bucket label, iface, up, down], ...] per bucket and adapter, for the per-adapter chart."""
        h0, h1 = self._hours(t0, t1)
        return self.conn().execute(
            "SELECT %s b, iface, SUM(up), SUM(down) FROM net WHERE hour>=? AND hour<? GROUP BY b, iface ORDER BY b"
            % BUCKETS.get(bucket, DAY), (h0, h1)).fetchall()

    def calendar(self, t0):
        h0 = int(t0 // 3600)
        c = self.conn()
        out = {}
        for d, k, cl, a in c.execute(
                "SELECT %s d, SUM(keys), SUM(lclicks+rclicks+mclicks+xclicks), SUM(active_s) FROM stats "
                "WHERE hour>=? GROUP BY d" % DAY, (h0,)):
            out[d] = {"day": d, "keys": k, "clicks": cl, "active_s": a, "down": 0}
        for d, down in c.execute("SELECT %s d, SUM(down) FROM net WHERE hour>=? GROUP BY d" % DAY, (h0,)):
            out.setdefault(d, {"day": d, "keys": 0, "clicks": 0, "active_s": 0})["down"] = down
        return sorted(out.values(), key=lambda r: r["day"])

    def metric_series(self, t0, t1, metrics, bucket="hour", mode="all", games=(), now=None):
        """Any metrics for any range: one point per bucket, dense (see /api/series). Points run from the later of
        t0 and the first tracked hour to the earlier of t1 and now. Raises TooManyPoints past SERIES_MAX_POINTS."""
        if bucket not in BUCKETS:
            bucket = "hour"
        want, unknown = [], []
        for m in metrics:
            if m in want or m in unknown:
                continue
            ok = m in SERIES_SUMS or m in SERIES_GPU or (
                m.startswith("m:") and m.count(":") >= 2 and m.rsplit(":", 1)[1] in ("avg", "max", "sum"))
            (want if ok else unknown).append(m)
        out = {"bucket": bucket, "from": t0, "to": t1, "metrics": want, "unknown": unknown, "points": []}
        c = self.conn()
        first = c.execute("SELECT MIN(m) FROM (SELECT MIN(hour) m FROM stats UNION ALL SELECT MIN(hour) FROM net "
                          "UNION ALL SELECT MIN(hour) FROM uptime UNION ALL SELECT MIN(hour) FROM energy)").fetchone()[0]
        if first is None:
            return out
        h0, h1 = self._hours(max(t0, first * 3600), min(t1, time.time() if now is None else now))
        if h1 <= h0:
            return out
        if (h1 - h0) / BUCKET_HOURS[bucket] > SERIES_MAX_POINTS:
            raise TooManyPoints("too many points; use a bigger bucket")
        expr = BUCKETS[bucket]
        buckets = c.execute(
            "WITH RECURSIVE hrs(hour) AS (SELECT ? UNION ALL SELECT hour + 1 FROM hrs WHERE hour + 1 < ?) "
            "SELECT b, %s FROM (SELECT %s b, MIN(hour) h FROM hrs GROUP BY b) ORDER BY h" % (BUCKET_T[bucket], expr),
            (h0, h1)).fetchall()
        groups = {SERIES_SUMS[m][0] for m in want if m in SERIES_SUMS}
        if any(m in SERIES_GPU for m in want):
            groups.add("gpu")
        w, p = app_filter(mode, games)
        data = {}
        for g in sorted(groups):
            where, params = " WHERE hour>=? AND hour<?", [h0, h1]
            if g == "backspaces":
                where += " AND key='Backspace'"
            if g in ("stats", "backspaces"):
                where, params = where + w, params + p
            for r in c.execute("SELECT %s b, %s%s GROUP BY b" % (expr, SERIES_SQL[g], where), params):
                data[(g, r[0])] = r[1:]
        mkeys = sorted({m[2:].rsplit(":", 1)[0] for m in want if m.startswith("m:")})
        if mkeys:
            for b, k, sm, mx, sc in c.execute(
                    "SELECT %s b, key, SUM(sum), MAX(max), SUM(secs) FROM metrics WHERE hour>=? AND hour<? "
                    "AND key IN (%s) GROUP BY b, key" % (expr, ",".join("?" * len(mkeys))), [h0, h1] + mkeys):
                data[("m:" + k, b)] = (sm, mx, sc)
        for b, t in buckets:
            pt = {"t": t, "b": b}
            for m in want:
                if m in SERIES_SUMS:
                    g, i = SERIES_SUMS[m]
                    r = data.get((g, b))
                    pt[m] = r[i] if r and r[i] is not None else 0
                elif m in SERIES_GPU:
                    r = data.get(("gpu", b))
                    if not (r and r[0]):
                        pt[m] = None
                    elif m == "gpu_util":
                        pt[m] = r[1] / r[0]
                    elif m == "gpu_temp":
                        pt[m] = r[2] / r[0]
                    else:
                        pt[m] = r[{"gpu_temp_max": 3, "gpu_w_max": 4, "vram_max": 5}[m]]
                else:
                    key, kind = m[2:].rsplit(":", 1)
                    r = data.get(("m:" + key, b))
                    if kind == "sum":
                        pt[m] = r[0] if r and r[0] is not None else 0
                    elif not (r and r[2]):
                        pt[m] = None
                    else:
                        pt[m] = r[0] / r[2] if kind == "avg" else r[1]
            out["points"].append(pt)
        return out

    # ------------------------------------------------------------ gaming sessions

    def sessions(self, t0, t1, limit=100):
        rows = self.conn().execute(
            "SELECT s.id, s.exe, COALESCE(a.name, s.exe), s.start, s.end, s.keys, s.clicks, s.active_s, s.peak_apm, s.wh "
            "FROM sessions s LEFT JOIN apps a ON a.exe = s.exe WHERE s.start < ? AND s.end >= ? "
            "ORDER BY s.start DESC LIMIT ?", (t1, t0, limit)).fetchall()
        return [{"id": r[0], "exe": r[1], "name": r[2], "start": r[3], "end": r[4], "keys": r[5], "clicks": r[6],
                 "active_s": r[7], "peak_apm": r[8], "wh": r[9]} for r in rows]

    def session_games(self, t0, t1):
        rows = self.conn().execute(
            "SELECT s.exe, COALESCE(a.name, s.exe), COUNT(*), SUM(s.end - s.start), MAX(s.end - s.start), "
            "MAX(s.peak_apm), SUM(s.wh) FROM sessions s LEFT JOIN apps a ON a.exe = s.exe "
            "WHERE s.start < ? AND s.end >= ? GROUP BY s.exe ORDER BY SUM(s.end - s.start) DESC", (t1, t0)).fetchall()
        return [{"exe": r[0], "name": r[1], "sessions": r[2], "play_s": r[3], "longest_s": r[4], "peak_apm": r[5],
                 "wh": r[6]} for r in rows]

    # ------------------------------------------------------------ system usage

    def system(self, t0, t1, bucket="day"):
        """Hardware metrics (avg/max/sum per key) for the range and per bucket, plus the hardest-working programs.
        For */s keys, `sum` is the total (e.g. bytes read)."""
        h0, h1 = self._hours(t0, t1)
        expr = BUCKETS.get(bucket, DAY)
        c = self.conn()
        totals = {k: {"avg": sm / sc if sc else 0, "max": mx, "sum": sm}
                  for k, sm, mx, sc in c.execute("SELECT key, SUM(sum), MAX(max), SUM(secs) FROM metrics "
                                                 "WHERE hour>=? AND hour<? GROUP BY key", (h0, h1))}
        series = {}
        for b, k, sm, mx, sc in c.execute("SELECT %s b, key, SUM(sum), MAX(max), SUM(secs) FROM metrics "
                                          "WHERE hour>=? AND hour<? GROUP BY b, key" % expr, (h0, h1)):
            series.setdefault(b, {"b": b})[k] = [sm / sc if sc else 0, mx, sm]
        apps = c.execute(
            "SELECT p.app, COALESCE(a.name, p.app), SUM(p.secs), SUM(p.cpu_s), SUM(p.ram_mbs), MAX(p.ram_max), "
            "SUM(p.io_read), SUM(p.io_write), (SELECT COALESCE(SUM(g.gpu_s),0) FROM gpu_apps g WHERE g.app=p.app "
            "AND g.hour>=? AND g.hour<?) FROM proc_usage p LEFT JOIN apps a ON a.exe=p.app WHERE p.hour>=? AND p.hour<? "
            "GROUP BY p.app ORDER BY SUM(p.cpu_s) DESC LIMIT 30", (h0, h1, h0, h1)).fetchall()
        return {"totals": totals, "series": sorted(series.values(), key=lambda r: r["b"]),
                "apps": [{"exe": r[0], "name": r[1], "secs": r[2], "cpu_s": r[3],
                          "ram_avg": r[4] / r[2] if r[2] else 0, "ram_max": r[5], "read": r[6], "write": r[7],
                          "gpu_s": r[8]} for r in apps]}

    def top_apps_at(self, hours):
        """For each hour (epoch hour int): the program with the most CPU time and the one with the most GPU work.
        {hour: {"cpu": [exe, name, cpu_s] or None, "gpu": [exe, name, gpu_s] or None}}"""
        hours = sorted({int(h) for h in hours})[:100]
        out = {h: {"cpu": None, "gpu": None} for h in hours}
        if not hours:
            return out
        marks = ",".join("?" * len(hours))
        c = self.conn()
        for kind, sql in (("cpu", "SELECT t.hour, t.app, COALESCE(a.name, t.app), t.cpu_s FROM proc_usage t "
                                  "LEFT JOIN apps a ON a.exe = t.app WHERE t.hour IN (%s) ORDER BY t.hour, t.cpu_s DESC"),
                          ("gpu", "SELECT t.hour, t.app, COALESCE(a.name, t.app), t.gpu_s FROM gpu_apps t "
                                  "LEFT JOIN apps a ON a.exe = t.app WHERE t.hour IN (%s) ORDER BY t.hour, t.gpu_s DESC")):
            for h, exe, name, v in c.execute(sql % marks, hours):
                if out[h][kind] is None and v and exe != "idle":
                    out[h][kind] = [exe, name, v]
        return out

    # ------------------------------------------------------------ GPU & power

    def power(self, t0, t1, bucket="day"):
        h0, h1 = self._hours(t0, t1)
        expr = BUCKETS.get(bucket, DAY)
        c = self.conn()
        e = c.execute("SELECT COALESCE(SUM(secs),0), COALESCE(SUM(gpu_wh),0), COALESCE(SUM(cpu_wh),0), "
                      "COALESCE(SUM(other_wh),0), COALESCE(SUM(display_wh),0) FROM energy WHERE hour>=? AND hour<?",
                      (h0, h1)).fetchone()
        g = c.execute("SELECT COALESCE(SUM(secs),0), COALESCE(SUM(util_sum),0), COALESCE(MAX(util_max),0), "
                      "COALESCE(SUM(temp_sum),0), COALESCE(MAX(temp_max),0), COALESCE(MAX(power_max),0), "
                      "COALESCE(MAX(vram_max),0) FROM gpu WHERE hour>=? AND hour<?", (h0, h1)).fetchone()
        d0, d1 = self._days(t0, t1)
        pk = c.execute("SELECT COALESCE(MAX(peak_watts),0), COALESCE(MAX(peak_gpu_temp),0), COALESCE(MAX(peak_gpu_w),0) "
                       "FROM daily WHERE day>=? AND day<=?", (d0, d1)).fetchone()
        days = c.execute("SELECT COUNT(DISTINCT %s) FROM energy WHERE hour>=? AND hour<? AND secs>0" % DAY,
                         (h0, h1)).fetchone()[0]
        totals = {"secs": e[0], "gpu_wh": e[1], "cpu_wh": e[2], "other_wh": e[3], "display_wh": e[4],
                  "wh": e[1] + e[2] + e[3] + e[4], "gpu_secs": g[0],
                  "gpu_util_avg": g[1] / g[0] if g[0] else 0, "gpu_util_max": g[2],
                  "gpu_temp_avg": g[3] / g[0] if g[0] else 0, "gpu_temp_max": max(g[4], pk[1]),
                  "gpu_w_max": max(g[5], pk[2]), "vram_max": g[6], "peak_watts": pk[0],
                  "gpu_busy_s": g[1] / 100.0, "days": days}
        series = {}
        for r in c.execute("SELECT %s b, SUM(secs), SUM(gpu_wh), SUM(cpu_wh), SUM(other_wh), SUM(display_wh) "
                           "FROM energy WHERE hour>=? AND hour<? GROUP BY b" % expr, (h0, h1)):
            series[r[0]] = {"b": r[0], "secs": r[1], "gpu_wh": r[2], "cpu_wh": r[3], "other_wh": r[4],
                            "display_wh": r[5], "gpu_util": 0, "gpu_temp_max": 0}
        for b, secs, us, tmax in c.execute("SELECT %s b, SUM(secs), SUM(util_sum), MAX(temp_max) FROM gpu "
                                           "WHERE hour>=? AND hour<? GROUP BY b" % expr, (h0, h1)):
            row = series.setdefault(b, {"b": b, "secs": 0, "gpu_wh": 0, "cpu_wh": 0, "other_wh": 0, "display_wh": 0})
            row["gpu_util"] = us / secs if secs else 0
            row["gpu_temp_max"] = tmax
        apps = c.execute(
            "SELECT g.app, COALESCE(a.name, g.app), SUM(g.secs), SUM(g.gpu_s), SUM(g.wh), MAX(g.vram_max) "
            "FROM gpu_apps g LEFT JOIN apps a ON a.exe = g.app WHERE hour>=? AND hour<? "
            "GROUP BY g.app HAVING SUM(g.gpu_s) >= 1 ORDER BY SUM(g.gpu_s) DESC LIMIT 25", (h0, h1)).fetchall()
        return {"totals": totals, "series": sorted(series.values(), key=lambda r: r["b"]),
                "apps": [{"exe": r[0], "name": r[1], "secs": r[2], "gpu_s": r[3], "wh": r[4], "vram_max": r[5]}
                         for r in apps]}

    # ------------------------------------------------------------ records / export

    def daily_rows(self):
        """Every tracked day, for records and CSV export."""
        c = self.conn()
        days = {r["day"]: r for r in self.calendar(0)}
        for row in c.execute("SELECT day, %s FROM daily" % ",".join(PEAK_COLS)):
            r = days.setdefault(row[0], {"day": row[0], "keys": 0, "clicks": 0, "active_s": 0, "down": 0})
            r.update(zip(PEAK_COLS, row[1:]))
        for d, up in c.execute("SELECT %s d, SUM(up) FROM net GROUP BY d" % DAY):
            if d in days:
                days[d]["up"] = up
        for d, mm, sc, ty in c.execute("SELECT %s d, SUM(mouse_mm), SUM(scroll), SUM(typed) FROM stats GROUP BY d" % DAY):
            if d in days:
                days[d].update(mouse_mm=mm, scroll=sc, typed=ty)
        for d, on in c.execute("SELECT %s d, SUM(on_s) FROM uptime GROUP BY d" % DAY):
            if d in days:
                days[d]["uptime_s"] = on
        for d, wh, gwh in c.execute("SELECT %s d, SUM(gpu_wh+cpu_wh+other_wh+display_wh), SUM(gpu_wh) FROM energy "
                                    "GROUP BY d" % DAY):
            row = days.setdefault(d, {"day": d, "keys": 0, "clicks": 0, "active_s": 0, "down": 0})
            row["wh"], row["gpu_wh"] = wh, gwh
        return sorted(days.values(), key=lambda r: r["day"])

    def records(self, today):
        rows = self.daily_rows()
        c = self.conn()

        def best(field):
            r = max(rows, key=lambda x: x.get(field) or 0, default=None)
            return {"day": r["day"], "v": r.get(field) or 0} if r and (r.get(field) or 0) > 0 else None

        bh = c.execute("SELECT hour, SUM(keys) s FROM stats GROUP BY hour ORDER BY s DESC LIMIT 1").fetchone()
        top_key = c.execute("SELECT key, SUM(n) s FROM keys GROUP BY key ORDER BY s DESC LIMIT 1").fetchone()
        top_app = c.execute("SELECT COALESCE(a.name, s.app), SUM(active_s) t FROM stats s LEFT JOIN apps a "
                            "ON a.exe=s.app GROUP BY s.app ORDER BY t DESC LIMIT 1").fetchone()
        longest = c.execute("SELECT COALESCE(a.name, s.exe), s.end - s.start, s.start FROM sessions s LEFT JOIN apps a "
                            "ON a.exe=s.exe ORDER BY s.end - s.start DESC LIMIT 1").fetchone()
        top_game = c.execute("SELECT COALESCE(a.name, s.exe), SUM(s.end - s.start) t FROM sessions s LEFT JOIN apps a "
                             "ON a.exe=s.exe GROUP BY s.exe ORDER BY t DESC LIMIT 1").fetchone()
        cur, longest_streak = _streaks([r["day"] for r in rows if (r["keys"] or 0) + (r["clicks"] or 0) >= STREAK_MIN],
                                       today)
        return {
            "first_day": rows[0]["day"] if rows else None,
            "days_tracked": len(rows),
            "best_keys": best("keys"), "best_clicks": best("clicks"), "best_active": best("active_s"),
            "best_down": best("down"), "best_up": best("up"), "best_mouse": best("mouse_mm"),
            "peak_wpm": best("peak_wpm"), "peak_kpm": best("peak_kpm"), "peak_apm": best("peak_apm"),
            "peak_down": best("peak_down"), "peak_up": best("peak_up"),
            "best_wh": best("wh"), "peak_watts": best("peak_watts"), "peak_gpu_temp": best("peak_gpu_temp"),
            "best_hour": {"hour": bh[0], "v": bh[1]} if bh and bh[1] else None,
            "top_key": {"key": top_key[0], "v": top_key[1]} if top_key else None,
            "top_app": {"name": top_app[0], "v": top_app[1]} if top_app and top_app[1] else None,
            "longest_session": {"name": longest[0], "v": longest[1], "start": longest[2]} if longest else None,
            "top_game": {"name": top_game[0], "v": top_game[1]} if top_game and top_game[1] else None,
            "streak": cur, "longest_streak": longest_streak, "streak_min": STREAK_MIN,
        }


def _streaks(days, today):
    """(current, longest) run of consecutive days. Today not being active yet doesn't break 'current'."""
    from datetime import date, timedelta
    ds = sorted({date.fromisoformat(d) for d in days})
    longest = run = 0
    prev = None
    for d in ds:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    t = date.fromisoformat(today)
    s = set(ds)
    cur = 0
    d = t if t in s else t - timedelta(days=1)
    while d in s:
        cur += 1
        d -= timedelta(days=1)
    return cur, longest
