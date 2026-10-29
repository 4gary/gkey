"""Which programs count as games.

A program is a game when (first match wins):
  1. you marked it as a game / not a game in the dashboard
  2. it's in KNOWN (the real game exe, never its launcher)
  3. auto-detect is on and it lives in a game library folder (Steam, Epic, Xbox, Riot...)
     and its name doesn't look like a launcher/updater/crash handler
"""
import json

KNOWN = {
    "league of legends.exe": "League of Legends",          # only exists during a match; client is LeagueClientUx.exe
    "valorant-win64-shipping.exe": "VALORANT",
    "cs2.exe": "Counter-Strike 2", "csgo.exe": "Counter-Strike: GO",
    "dota2.exe": "Dota 2",
    "fortniteclient-win64-shipping.exe": "Fortnite",
    "r5apex.exe": "Apex Legends", "r5apex_dx12.exe": "Apex Legends",
    "overwatch.exe": "Overwatch 2",
    "rocketleague.exe": "Rocket League",
    "gta5.exe": "GTA V", "gta5_enhanced.exe": "GTA V",
    "minecraft.windows.exe": "Minecraft",
    "robloxplayerbeta.exe": "Roblox",
    "tslgame.exe": "PUBG",
    "wow.exe": "World of Warcraft",
    "hearthstone.exe": "Hearthstone",
    "diablo iv.exe": "Diablo IV",
    "cod.exe": "Call of Duty",
    "rainbowsix.exe": "Rainbow Six Siege", "rainbowsix_vulkan.exe": "Rainbow Six Siege",
    "destiny2.exe": "Destiny 2",
    "eldenring.exe": "Elden Ring",
    "cyberpunk2077.exe": "Cyberpunk 2077",
    "bg3.exe": "Baldur's Gate 3", "bg3_dx11.exe": "Baldur's Gate 3",
    "helldivers2.exe": "Helldivers 2",
    "marvel-win64-shipping.exe": "Marvel Rivals",
    "project8.exe": "Deadlock",
    "osu!.exe": "osu!",
}

GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\xboxgames\\", "\\riot games\\",
             "\\gog galaxy\\games\\", "\\ea games\\", "\\ubisoft game launcher\\games\\")

NOT_GAME_HINTS = ("launcher", "client", "crash", "report", "updater", "update", "setup", "unins",
                  "redist", "vcredist", "dxsetup", "easyanticheat", "battleye", "helper", "service",
                  "installer", "cefprocess", "webview", "overlay")

NEVER = {"idle", "system", "unknown", ""}

CACHE_MAX = 512     # is_game() keeps one answer per exe; past this, the oldest answers are dropped


class GameRules:
    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self):
        self.added = set(json.loads(self.store.get_meta("games_add", "[]")))
        self.removed = set(json.loads(self.store.get_meta("games_remove", "[]")))
        self.auto = self.store.get_meta("games_auto", "1") == "1"
        self._cache = {}

    def classify(self, exe, path=""):
        """(is_game, reason) where reason is 'added' | 'removed' | 'known' | 'library' | ''."""
        exe = (exe or "").lower()
        if exe in NEVER:
            return False, ""
        if exe in self.added:
            return True, "added"
        if exe in self.removed:
            return False, "removed"
        if exe in KNOWN:
            return True, "known"
        if self.auto:
            low = (path or "").lower()
            if any(d in low for d in GAME_DIRS) and not any(h in exe for h in NOT_GAME_HINTS) \
                    and "\\launcher\\" not in low:
                return True, "library"
        return False, ""

    def is_game(self, exe, path=""):
        r = self._cache.get(exe)
        if r is None:
            r = self.classify(exe, path)[0]
            self._cache[exe] = r
            if len(self._cache) > CACHE_MAX:            # dicts keep insertion order: drop the oldest
                for old in list(self._cache)[:len(self._cache) - CACHE_MAX]:
                    self._cache.pop(old, None)
        return r

    def game_exes(self):
        """Every app seen so far that counts as a game (used to filter queries)."""
        rows = self.store.conn().execute("SELECT exe, path FROM apps").fetchall()
        return sorted(exe for exe, path in rows if self.classify(exe, path or "")[0])

    def set_game(self, exe, flag):
        exe = exe.lower()
        self.added.discard(exe)
        self.removed.discard(exe)
        default = GameRules.classify(self._defaults(), exe, self._path_of(exe))[0]
        if flag != default:                     # only store overrides that differ from the default
            (self.added if flag else self.removed).add(exe)
        self.store.set_meta("games_add", json.dumps(sorted(self.added)))
        self.store.set_meta("games_remove", json.dumps(sorted(self.removed)))
        self.reload()

    def set_auto(self, flag):
        self.store.set_meta("games_auto", "1" if flag else "0")
        self.reload()

    def _defaults(self):
        d = object.__new__(GameRules)
        d.added, d.removed, d.auto, d._cache = set(), set(), self.auto, {}
        return d

    def _path_of(self, exe):
        r = self.store.conn().execute("SELECT path FROM apps WHERE exe=?", (exe,)).fetchone()
        return (r[0] or "") if r else ""
