#!/usr/bin/env python3

from qnapdisplay import QnapDisplay
import os
import socket
import time
import psutil
import threading


LCD_WIDTH = int(os.getenv("LCD_WIDTH", "16"))

REFRESH_SECONDS = float(os.getenv("LCD_REFRESH_SECONDS", "0.5"))
LCD_ON_SECONDS = float(os.getenv("LCD_ON_SECONDS", "30"))
LCD_ALWAYS_ON = os.getenv("LCD_ALWAYS_ON", "false").lower() in ("1", "true", "yes", "on")

IFACE = os.getenv("LCD_IFACE", "ens11")

DEFAULT_MOUNTS = "Media=/mnt/tank/media,Fast=/mnt/fast"
MOUNTS_ENV = os.getenv("LCD_MOUNTS", DEFAULT_MOUNTS)

BUTTON_UP = "Up"
BUTTON_DOWN = "Down"
BUTTON_ENTER = "Enter"
BUTTON_ESC = "Esc"


def parse_mounts(value):
    mounts = []

    for item in value.split(","):
        item = item.strip()

        if not item:
            continue

        if "=" not in item:
            mounts.append((item, item))
            continue

        label, mount = item.split("=", 1)
        mounts.append((label.strip(), mount.strip()))

    return mounts


MOUNTS = parse_mounts(MOUNTS_ENV)


state = {
    "screen_index": 0,
    "enabled": LCD_ALWAYS_ON,
    "last_activity": time.monotonic(),
    "dirty": True,
}

state_lock = threading.Lock()


def fit(text):
    return str(text)[:LCD_WIDTH].ljust(LCD_WIDTH)


def human_bytes(value):
    units = ["B", "K", "M", "G", "T", "P"]
    value = float(value)

    for unit in units:
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024

    return f"{value:.1f}E"


def get_ipv4(interface):
    addrs = psutil.net_if_addrs().get(interface, [])

    for addr in addrs:
        if getattr(addr.family, "name", "") == "AF_INET":
            return addr.address

    return "no ip"


def screen_host():
    return (
        fit(socket.gethostname()),
        fit(get_ipv4(IFACE)),
    )


def screen_ram():
    mem = psutil.virtual_memory()

    return (
        fit(f"RAM {mem.percent:.0f}%"),
        fit(f"{human_bytes(mem.used)}/{human_bytes(mem.total)}"),
    )


def make_mount_screen(label, mount):
    def mount_screen():
        try:
            usage = psutil.disk_usage(mount)

            return (
                fit(f"{label} {usage.percent:.0f}%"),
                fit(f"{human_bytes(usage.used)}/{human_bytes(usage.total)}"),
            )

        except Exception as error:
            return (
                fit(label),
                fit(f"ERR {type(error).__name__}"),
            )

    return mount_screen


def screen_load():
    load1, load5, load15 = os.getloadavg()

    return (
        fit("Load"),
        fit(f"{load1:.2f} {load5:.2f} {load15:.2f}"),
    )


def build_screens():
    screens = [
        screen_host,
        screen_ram,
    ]

    for label, mount in MOUNTS:
        screens.append(make_mount_screen(label, mount))

    screens.append(screen_load)

    return screens


SCREENS = build_screens()


def current_screen_lines():
    with state_lock:
        index = state["screen_index"] % len(SCREENS)

    return SCREENS[index]()


def enable_screen():
    with state_lock:
        state["enabled"] = True
        state["last_activity"] = time.monotonic()
        state["dirty"] = True


def disable_screen():
    with state_lock:
        state["enabled"] = False
        state["dirty"] = True


def move_screen(delta):
    with state_lock:
        state["screen_index"] = (state["screen_index"] + delta) % len(SCREENS)
        state["enabled"] = True
        state["last_activity"] = time.monotonic()
        state["dirty"] = True


def mark_activity():
    with state_lock:
        state["enabled"] = True
        state["last_activity"] = time.monotonic()
        state["dirty"] = True


def handle_button(button):
    handlers = {
        BUTTON_UP: lambda: move_screen(-1),
        BUTTON_DOWN: lambda: move_screen(1),
        BUTTON_ENTER: mark_activity,
        BUTTON_ESC: disable_screen,
    }

    handlers.get(button, mark_activity)()


def button_reader(lcd):
    while True:
        button = lcd.Read()
        handle_button(button)


def main():
    lcd = QnapDisplay()

    threading.Thread(target=button_reader, args=(lcd,), daemon=True).start()

    last_render = 0
    lcd_is_enabled = False

    while True:
        now = time.monotonic()

        with state_lock:
            enabled = state["enabled"]
            last_activity = state["last_activity"]
            dirty = state["dirty"]

        should_timeout = (
            not LCD_ALWAYS_ON
            and enabled
            and now - last_activity >= LCD_ON_SECONDS
        )

        if should_timeout:
            lcd.Disable()
            lcd_is_enabled = False

            with state_lock:
                state["enabled"] = False
                state["dirty"] = False

            time.sleep(REFRESH_SECONDS)
            continue

        if not enabled:
            if lcd_is_enabled:
                lcd.Disable()
                lcd_is_enabled = False

            time.sleep(REFRESH_SECONDS)
            continue

        should_render = dirty or now - last_render >= REFRESH_SECONDS

        if should_render:
            line0, line1 = current_screen_lines()

            if not lcd_is_enabled:
                lcd.Enable()
                lcd_is_enabled = True

            lcd.Write(0, line0)
            lcd.Write(1, line1)

            last_render = now

            with state_lock:
                state["dirty"] = False

        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        QnapDisplay().Disable()
