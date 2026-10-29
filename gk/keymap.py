"""Virtual-key code -> stable key id (matches the dashboard keyboard layout)."""

_BASE = {
    0x08: "Backspace", 0x09: "Tab", 0x0C: "Num5", 0x0D: "Enter", 0x13: "Pause",
    0x14: "CapsLock", 0x1B: "Esc", 0x20: "Space",
    0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "PrtSc", 0x2D: "Ins", 0x2E: "Del",
    0x5B: "LWin", 0x5C: "RWin", 0x5D: "Menu",
    0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/",
    0x90: "NumLock", 0x91: "ScrLk",
    0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl", 0xA4: "LAlt", 0xA5: "RAlt",
    0xA6: "BrowserBack", 0xA7: "BrowserFwd", 0xA8: "BrowserRefresh", 0xAC: "BrowserHome",
    0xAD: "Mute", 0xAE: "VolDown", 0xAF: "VolUp",
    0xB0: "NextTrack", 0xB1: "PrevTrack", 0xB2: "Stop", 0xB3: "PlayPause",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0xE2: "IntlBackslash",
}
for _i in range(10):
    _BASE[0x30 + _i] = str(_i)
    _BASE[0x60 + _i] = "Num%d" % _i
for _i in range(26):
    _BASE[0x41 + _i] = chr(0x41 + _i)
for _i in range(24):
    _BASE[0x70 + _i] = "F%d" % (_i + 1)

# With NumLock off, numpad keys arrive as navigation VKs *without* the E0 flag.
_NUMPAD_NAV = {0x2D: "Num0", 0x23: "Num1", 0x28: "Num2", 0x22: "Num3", 0x25: "Num4",
               0x0C: "Num5", 0x27: "Num6", 0x24: "Num7", 0x26: "Num8", 0x21: "Num9", 0x2E: "Num."}

# vk to poll with GetAsyncKeyState to check a key is still physically held.
CHECK_VK = {"LShift": 0xA0, "RShift": 0xA1, "LCtrl": 0xA2, "RCtrl": 0xA3, "LAlt": 0xA4, "RAlt": 0xA5}

MODIFIERS = {"LShift", "RShift", "LCtrl", "RCtrl", "LAlt", "RAlt", "LWin", "RWin"}

# Keys that produce a character when typing (used for WPM).
PRINTABLE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") | set(";=,-./`[\\]'") | {
    "Space", "IntlBackslash", "Num*", "Num+", "Num-", "Num.", "Num/"} | {"Num%d" % i for i in range(10)}


def key_id(vk, scancode, e0):
    """Return the key id for a raw keyboard event, or None to ignore it."""
    if vk == 0xFF:                      # part of an escape sequence (Pause/PrtSc)
        return None
    if vk == 0x10:                      # Shift
        if e0:                          # fake shift injected around numpad nav keys
            return None
        return "RShift" if scancode == 0x36 else "LShift"
    if vk == 0x11:
        return "RCtrl" if e0 else "LCtrl"
    if vk == 0x12:
        return "RAlt" if e0 else "LAlt"
    if vk == 0x0D and e0:
        return "NumEnter"
    if not e0 and vk in _NUMPAD_NAV and scancode not in (0, 0x37):
        return _NUMPAD_NAV[vk]
    return _BASE.get(vk, "VK_%02X" % vk)


def check_vk(kid, vk):
    return CHECK_VK.get(kid, vk)


PRETTY = {"LCtrl": "Ctrl", "RCtrl": "Ctrl", "LAlt": "Alt", "RAlt": "Alt",
          "LShift": "Shift", "RShift": "Shift", "LWin": "Win", "RWin": "Win"}


def combo_label(held, kid):
    """'Ctrl+Shift+T' style label if a Ctrl/Alt/Win shortcut is being pressed."""
    mods = {PRETTY[k] for k in held if k in PRETTY}
    if not mods & {"Ctrl", "Alt", "Win"}:
        return None
    order = [m for m in ("Ctrl", "Alt", "Shift", "Win") if m in mods]
    return "+".join(order + [kid])
