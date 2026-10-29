"""System tray icon (pystray). Optional: the app runs without it."""
import logging
import threading

from . import winapi

log = logging.getLogger("gk.tray")

_icons = {}     # variant ("normal" / "paused" / "gaming") -> PIL image, drawn on first use, then reused


def _variant(paused=False, gaming=False):
    return "paused" if paused else "gaming" if gaming else "normal"


def make_icon_image(paused=False, gaming=False):
    v = _variant(paused, gaming)
    img = _icons.get(v)
    if img is None:
        img = _icons[v] = _draw_icon(paused, gaming)
    return img


def _draw_icon(paused, gaming):
    from PIL import Image, ImageDraw
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    face = (110, 118, 129) if paused else (144, 133, 233) if gaming else (57, 135, 229)
    d.rounded_rectangle((4, 8, 60, 56), radius=12, fill=(22, 27, 34), outline=face, width=4)
    for y in (18, 30):
        for i in range(4):
            x = 13 + i * 10
            d.rounded_rectangle((x, y, x + 7, y + 7), radius=2, fill=face)
    d.rounded_rectangle((18, 42, 46, 48), radius=2, fill=face)
    return img


def run_tray(tracker, open_dashboard, on_quit, mini):
    """Blocks until the user picks Quit."""
    import pystray

    def title():
        t = tracker.today
        s = tracker.session
        state = " (paused)" if tracker.paused else (" - gaming: %s" % s.name)[:60] if s else ""
        return ("gkeystrokes - %s keys, %s clicks today%s" % (
            f"{int(t['keys']):,}", f"{int(t['lclicks'] + t['rclicks'] + t['mclicks'] + t['xclicks']):,}", state))[:127]

    def look():
        return (tracker.paused, tracker.session is not None)

    # What the tray shows now. Each icon assignment makes pystray write a temp ICO file and rebuild the
    # icon handle, so the icon is assigned only when its variant changes, and the title only when it changes.
    shown = {}
    lock = threading.Lock()

    def push(icon):
        with lock:
            t = title()
            if t != shown["title"]:
                shown["title"] = t
                icon.title = t
            paused, gaming = look()
            v = _variant(paused, gaming)
            if v != shown["icon"]:
                shown["icon"] = v
                icon.icon = make_icon_image(paused, gaming)

    def toggle_pause(icon, item):
        tracker.set_paused(not tracker.paused)
        push(icon)

    def toggle_autostart(icon, item):
        winapi.set_autostart(not winapi.get_autostart())

    def toggle_mini(icon, item):
        mini.toggle()

    def toggle_clickthrough(icon, item):
        mini.set_clickthrough(not mini.cfg()["clickthrough"])

    def quit_(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda icon, item: open_dashboard(), default=True),
        pystray.MenuItem("Mini display", toggle_mini, checked=lambda item: mini.is_open()),
        pystray.MenuItem("Mini click-through", toggle_clickthrough, checked=lambda item: mini.cfg()["clickthrough"]),
        pystray.MenuItem("Pause tracking", toggle_pause, checked=lambda item: tracker.paused),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: winapi.get_autostart()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_),
    )
    paused, gaming = look()
    shown["title"] = title()
    shown["icon"] = _variant(paused, gaming)
    icon = pystray.Icon("gkeystrokes", make_icon_image(paused, gaming), shown["title"], menu)

    stop = threading.Event()

    def refresh():
        # Every 3 s: the title shows live counts, so it changes on most ticks while typing.
        # The icon changes only when tracking is paused/resumed or a game starts/ends.
        while not stop.wait(3):
            try:
                push(icon)
            except Exception:
                pass
    threading.Thread(target=refresh, daemon=True).start()
    try:
        icon.run()
    finally:
        stop.set()
        on_quit()
