"""Thin ctypes wrappers around the Win32 calls gkeystrokes needs.

Nothing here keeps state about what the user typed; it only turns OS events
into small Python callbacks (key id, button, wheel delta).
"""
import ctypes
import logging
import os
import struct
import sys
import threading
import time
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORENUMPROC, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID),
                                   ctypes.POINTER(wintypes.UINT)]

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK, RI_KEY_E0 = 0x01, 0x02
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183

# RAWINPUTHEADER is DWORD, DWORD, HANDLE, WPARAM -> 24 bytes on 64-bit, 16 on 32-bit.
_HDR = 8 + 2 * ctypes.sizeof(ctypes.c_void_p)
_U32 = struct.Struct("<I")                  # RAWINPUTHEADER.dwType
_MOUSE_BUTTONS = struct.Struct("<Hh")       # RAWMOUSE usButtonFlags, usButtonData (signed)
_KEYBOARD = struct.Struct("<HHHH")          # RAWKEYBOARD MakeCode, Flags, Reserved, VKey

# usButtonFlags bits -> button name, for "down" transitions only.
MOUSE_DOWN = ((0x0001, "left"), (0x0004, "right"), (0x0010, "middle"),
              (0x0040, "x1"), (0x0100, "x2"))
RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL = 0x0400, 0x0800


_CACHE_MAX = 256    # entries kept by each small cache in this module


def _remember(cache, key, value, cap=_CACHE_MAX):
    """Store into a dict used as a bounded cache. Dicts keep insertion order, so past `cap`
    the oldest entries are dropped first."""
    cache[key] = value
    if len(cache) > cap:
        for old in list(cache)[:len(cache) - cap]:
            cache.pop(old, None)


def set_dpi_aware():
    """Per-monitor DPI awareness, so cursor coordinates are real pixels."""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


class RawInputListener(threading.Thread):
    """Hidden window that receives keyboard + mouse Raw Input for the whole desktop.

    Raw Input is delivered through the message queue, so a slow Python callback
    never delays the user's real input (unlike low-level hooks).

    on_key(vk, scancode, e0, down) and on_mouse(button_flags, button_data)
    are called on this thread.
    """

    def __init__(self, on_key, on_mouse):
        super().__init__(name="raw-input", daemon=True)
        self.on_key = on_key
        self.on_mouse = on_mouse
        self.hwnd = None
        self.ready = threading.Event()
        self.error = None
        self._buf = ctypes.create_string_buffer(256)
        self._size = wintypes.UINT()
        self._size_ref = ctypes.byref(self._size)   # built once; byref() allocates
        self._logged_error = False
        self._wndproc = WNDPROC(self._proc)   # keep a reference or it gets GC'd

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            return self._input(hwnd, wparam, lparam)
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _input(self, hwnd, wparam, lparam):
        """WM_INPUT: read the packet, then DefWindowProc so the system can clean up the raw input handle.
        The message loop calls this directly, which skips DispatchMessageW and the ctypes callback."""
        try:
            self._on_raw_input(lparam)
        except Exception:
            if not self._logged_error:      # never let a bad event kill input capture, but say so once
                self._logged_error = True
                logging.getLogger("gk.input").exception("raw input handler failed")
        return user32.DefWindowProcW(hwnd, WM_INPUT, wparam, lparam)

    # NB: not called _handle - Python 3.13's Thread uses self._handle internally.
    def _on_raw_input(self, lparam):
        self._size.value = 256
        n = user32.GetRawInputData(lparam, RID_INPUT, self._buf, self._size_ref, _HDR)
        if n == 0xFFFFFFFF or n < _HDR:
            return
        buf = self._buf                     # parse in place: self._buf.raw would copy 256 bytes per event
        rtype = _U32.unpack_from(buf, 0)[0]
        if rtype == RIM_TYPEMOUSE:
            flags, data = _MOUSE_BUTTONS.unpack_from(buf, _HDR + 4)
            if flags:                       # pure movement has no button flags: skip fast
                self.on_mouse(flags, data)
        elif rtype == RIM_TYPEKEYBOARD:
            make, kflags, _res, vk = _KEYBOARD.unpack_from(buf, _HDR)
            self.on_key(vk, make, bool(kflags & RI_KEY_E0), not (kflags & RI_KEY_BREAK))

    def run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "gkeystrokes_rawinput"
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "gkeystrokes", 0,
                                               0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            devs = (RAWINPUTDEVICE * 2)(
                RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self.hwnd),   # keyboard
                RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, self.hwnd))   # mouse
            if not user32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE)):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception as e:
            self.error = e
            self.ready.set()
            return
        self.ready.set()
        msg = wintypes.MSG()
        msg_ref = ctypes.byref(msg)
        while user32.GetMessageW(msg_ref, None, 0, 0) > 0:
            if msg.message == WM_INPUT:     # TranslateMessage does nothing for WM_INPUT; skip it and the dispatch
                self._input(msg.hWnd, msg.wParam, msg.lParam)
                continue
            user32.TranslateMessage(msg_ref)
            user32.DispatchMessageW(msg_ref)

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- cursor / screen

def cursor_pos():
    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return None


def key_is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def virtual_screen():
    """(x, y, w, h) of the bounding box of all monitors."""
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            max(1, user32.GetSystemMetrics(78)), max(1, user32.GetSystemMetrics(79)))


def monitors():
    """List of (x, y, w, h) for each monitor."""
    out = []

    def cb(hmon, hdc, rect, lparam):
        r = rect.contents
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


_dpi_cache = {}


def monitor_dpi_at(x, y):
    """Physical DPI (from the monitor's EDID) of the monitor under (x, y)."""
    hmon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    dpi = _dpi_cache.get(hmon)
    if dpi is None:
        dpi = 96.0
        try:
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if ctypes.WinDLL("shcore").GetDpiForMonitor(hmon, 2, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                if 50 <= dx.value <= 600:     # TVs/projectors sometimes report nonsense
                    dpi = float(dx.value)
        except Exception:
            pass
        _remember(_dpi_cache, hmon, dpi)
    return dpi


# ---------------------------------------------------------------- mini window

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
LWA_ALPHA = 0x2
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOACTIVATE = 0x0010


def find_window(title):
    return user32.FindWindowW(None, title) or None


def window_rect(hwnd):
    """(x, y, w, h) in physical pixels."""
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def move_window(hwnd, x, y, w, h, topmost=True):
    user32.SetWindowPos(hwnd, HWND_TOPMOST if topmost else HWND_NOTOPMOST, x, y, w, h, SWP_NOACTIVATE)


def set_window_alpha(hwnd, alpha):
    """Whole-window opacity. Makes the window layered, which click-through also needs."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(hwnd, 0, int(alpha * 255), LWA_ALPHA)


def set_click_through(hwnd, flag):
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TRANSPARENT) if flag else (ex & ~WS_EX_TRANSPARENT))


# ---------------------------------------------------------------- foreground app

def _pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_path(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
    finally:
        kernel32.CloseHandle(h)
    return None


_PATH_TTL_S = 600           # PIDs get reused by other programs, so an image path is re-read after this long
_pid_paths = {}             # pid -> (image path or "", time.monotonic() when read); bounded by _remember


def pid_path(pid):
    """process_path() with a per-PID cache. The foreground app is looked up every tick, so this
    keeps that from opening and closing the same process handle each time."""
    now = time.monotonic()
    hit = _pid_paths.get(pid)
    if hit is not None and now - hit[1] < _PATH_TTL_S:
        return hit[0]
    path = process_path(pid) or ""
    _remember(_pid_paths, pid, (path, now))
    return path


def foreground_pid():
    """PID of the app the user is looking at (resolves UWP frame hosts)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = _pid_of(hwnd)
    path = pid_path(pid)
    if path.lower().endswith("applicationframehost.exe"):
        found = []

        def cb(child, lparam):
            cpid = _pid_of(child)
            if cpid != pid:
                found.append(cpid)
                return False
            return True
        user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
        if found:
            return found[0]
    return pid


def file_description(path):
    """The 'FileDescription' from an exe's version info, e.g. 'Google Chrome'."""
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return None
        ptr, ln = wintypes.LPVOID(), wintypes.UINT()
        langs = []
        if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
            arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
            for i in range(0, ln.value // 2, 2):
                langs.append("%04x%04x" % (arr[i], arr[i + 1]))
        langs += ["040904b0", "040904e4", "04090000"]
        for lang in langs:
            if version.VerQueryValueW(data, "\\StringFileInfo\\%s\\FileDescription" % lang,
                                      ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                text = ctypes.wstring_at(ptr, ln.value).split("\x00", 1)[0].strip()   # some exes report a bogus length
                if text:
                    return text
    except Exception:
        pass
    return None


class AppResolver:
    """Maps the foreground window to (exe, friendly name), with caching."""

    def __init__(self):
        self._names = {}          # path -> friendly name (bounded; a miss re-reads the exe's version info)
        self.pid = None           # pid of the last resolved foreground app

    def current(self):
        pid = self.pid = foreground_pid()
        if not pid:
            return ("idle", "Desktop", "")
        path = pid_path(pid)
        if not path:
            return ("system", "System", "")
        exe = os.path.basename(path).lower()
        name = self._names.get(path)
        if name is None:
            name = file_description(path) or os.path.splitext(os.path.basename(path))[0]
            _remember(self._names, path, name, cap=512)
        return (exe, name, path)


# ---------------------------------------------------------------- misc

def appdata_redirected(folder):
    """True if this process's writes to `folder` (under %LOCALAPPDATA%) are being silently
    redirected into some packaged app's private Packages\\<app>\\LocalCache copy. That happens
    when we're started from inside a packaged app such as the Claude desktop app, even though
    the process itself reports no package identity. Programs started normally never see that
    copy, so stats saved there are effectively lost."""
    local = os.environ.get("LOCALAPPDATA", "")
    rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(local)) if local else ".."
    if rel.startswith(".."):
        return False
    marker = ".gk-redirect-probe-%d" % os.getpid()
    probe = os.path.join(folder, marker)
    try:
        open(probe, "w").close()
        packages = os.path.join(local, "Packages")
        for pkg in os.listdir(packages):
            if os.path.exists(os.path.join(packages, pkg, "LocalCache", "Local", rel, marker)):
                return True
        return False
    except OSError:
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


_mutexes = {}


def instance_name(kind, folder):
    """Mutex name for one `kind` of process ("gkeystrokes", "gkeystrokes_mini") per data folder,
    so a preview folder runs next to the real one without the two blocking each other."""
    key = zlib.crc32(os.path.normcase(os.path.abspath(folder)).encode()) & 0xFFFFFFFF
    return "Local\\%s_%08x" % (kind, key)


def acquire_single_instance(name="Local\\gkeystrokes_tracker"):
    """True if we're the only running copy. The mutex is held until this process exits."""
    h = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if h:
            kernel32.CloseHandle(h)
        return False
    _mutexes[name] = h
    return True


def instance_running(name):
    """True if some process holds the mutex `name` (doesn't take it)."""
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


# ---------------------------------------------------------------- bring a window to the front

SW_RESTORE = 9
SYNCHRONIZE = 0x00100000
MB_OK, MB_ICONERROR, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x10, 0x10000, 0x40000


def edge_windows(profile, title):
    """Visible top-level Edge windows titled `title` that belong to the Edge instance using `profile`
    (our own --user-data-dir, one per data folder). Matching on the profile keeps the hidden raw-input
    window (also titled "gkeystrokes") and a preview's dashboard out of it."""
    import psutil
    want = "--user-data-dir=" + os.path.normcase(os.path.abspath(profile))
    pids = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "msedge.exe" and any(
                    os.path.normcase(a) == want for a in (p.info["cmdline"] or ())):
                pids.add(p.pid)
        except (psutil.Error, OSError):
            pass
    if not pids:
        return []
    found = []
    buf = ctypes.create_unicode_buffer(256)

    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and _pid_of(hwnd) in pids:
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == "Chrome_WidgetWin_1":
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value == title:
                    found.append(hwnd)
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def process_windows(pids, title):
    """Visible top-level windows titled `title` owned by one of `pids` (the pywebview dashboard window)."""
    pids = set(pids)
    found = []
    buf = ctypes.create_unicode_buffer(256)

    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and _pid_of(hwnd) in pids:
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                found.append(hwnd)
        return True
    if pids:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


SW_MAXIMIZE = 3


def window_state(hwnd):
    """"min", "max" or "normal"."""
    return "min" if user32.IsIconic(hwnd) else "max" if user32.IsZoomed(hwnd) else "normal"


def maximize_window(hwnd):
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def focus_window(hwnd):
    """Restore `hwnd` if minimized and bring it to the front. False if there is no such window.
    No synthesized Alt press: this app counts key presses, so that would add a fake key."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return True
    # Not allowed to take the foreground: borrow the input queue of the window that has it.
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    if other and other != me and user32.AttachThreadInput(me, other, True):
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(me, other, False)
    return True


def message_box(text, title="gkeystrokes"):
    user32.MessageBoxW(None, text, title, MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gkeystrokes.pyw")
    return '"%s" "%s" --background' % (exe, script)


def get_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "gkeystrokes")
            return True
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "gkeystrokes", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "gkeystrokes")
            except OSError:
                pass
