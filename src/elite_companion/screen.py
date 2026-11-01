"""Screenshots for Claude: the Elite window, a VR mirror window, or the whole main screen, as a PNG.

Uses only pywin32 and numpy (no Pillow). PrintWindow with PW_RENDERFULLCONTENT captures DirectX windows
that a plain screen copy shows as black. A minimised window can't be captured, which is why VR players
need a mirror window (see MIRROR_TITLES).
"""

from __future__ import annotations

import base64
import struct
import zlib

import numpy as np

ELITE_TITLE = "Elite - Dangerous (CLIENT)"
MAX_WIDTH = 1280  # about 1,600 image tokens at most: cheap, and enough to read HUD text


class ScreenError(RuntimeError):
    """Nothing could be captured; the message is safe to speak."""
    pass


def _png(rgb: np.ndarray) -> bytes:
    """Encode an RGB array as PNG with only the standard library (no Pillow needed)."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# In VR, players usually minimise Elite's desktop window, so Vesper looks for a VR mirror window instead:
# a desktop window showing what the headset sees. Titles start with one of these; set your own with
# [screen] mirror_windows in config.toml. ("Oculus Mirror" is Meta's OculusMirror.exe; the SteamVR ones are
# its "Display VR View" window, whose title has varied between versions.)
MIRROR_TITLES = ["Oculus Mirror", "Headset Window", "VR View"]
MIRROR_COMMAND = ""  # optional program that opens a mirror when none is showing ([screen] mirror_command)


def configure(cfg: dict) -> None:
    """Apply the [screen] settings."""
    global MIRROR_TITLES, MIRROR_COMMAND
    MIRROR_TITLES = list(cfg.get("mirror_windows") or MIRROR_TITLES)
    MIRROR_COMMAND = cfg.get("mirror_command") or ""


def _visible(hwnd: int) -> bool:
    """Whether a window exists, isn't minimised and has some size."""
    import win32gui
    return bool(hwnd) and not win32gui.IsIconic(hwnd) and win32gui.GetClientRect(hwnd)[2] > 0


def mirror_window() -> int:
    """A visible VR mirror window, if there is one. Meta's says "Waiting for VR session" until the game
    is in VR; that one shows nothing yet, so it's skipped."""
    import win32gui
    found = []

    def check(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if any(title.startswith(t) for t in MIRROR_TITLES) and "Waiting" not in title and _visible(hwnd):
            found.append(hwnd)
    win32gui.EnumWindows(check, None)
    return found[0] if found else 0


def start_mirror(wait_s: float = 5) -> bool:
    """Run [screen] mirror_command if no mirror is showing, and wait for its window. True if one is showing."""
    import shlex
    import subprocess
    import time
    if mirror_window():
        return True
    if not MIRROR_COMMAND:
        return False
    try:
        subprocess.Popen(shlex.split(MIRROR_COMMAND, posix=False), creationflags=0x00000008)  # DETACHED_PROCESS
    except OSError:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if mirror_window():
            return True
        time.sleep(0.5)
    return False


def capture(source: str = "auto", max_width: int | None = MAX_WIDTH) -> dict:
    """{"png_base64", "width", "height", "source"}. source: "auto" (Elite's window if it's showing, else a VR
    mirror window, else the main screen), "elite", "mirror" or "screen". max_width None keeps full
    resolution, for reading small text."""
    import win32con
    import win32gui
    import win32ui
    from ctypes import windll

    try:
        windll.user32.SetProcessDPIAware()  # real pixels, not the scaled-down size Windows reports otherwise
    except OSError:
        pass
    hwnd, name = 0, "main screen"
    elite = win32gui.FindWindow(None, ELITE_TITLE)
    if source in ("auto", "elite") and _visible(elite):
        hwnd, name = elite, ELITE_TITLE
    elif source in ("auto", "mirror") and (m := mirror_window()):
        hwnd, name = m, f"VR mirror ({win32gui.GetWindowText(m)})"
    elif source == "elite":
        raise ScreenError("The Elite window is minimised.")
    elif source == "mirror":
        raise ScreenError("No VR mirror window is open. Open your headset's mirror view "
                          "(Oculus Mirror, or SteamVR's Display VR View) and try again.")
    source = name
    if hwnd:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
    else:
        hwnd = win32gui.GetDesktopWindow()
        left, top = 0, 0
        right, bottom = windll.user32.GetSystemMetrics(0), windll.user32.GetSystemMetrics(1)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise ScreenError("The Elite window is minimised; restore it and try again.")
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        if source == "main screen" or not windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 3):  # 3 = client + full
            mem.BitBlt((0, 0), (w, h), src, (0, 0), win32con.SRCCOPY)
        bgra = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
    step = max(1, -(-w // max_width)) if max_width else 1  # downscale by whole steps, keeping it simple
    rgb = np.ascontiguousarray(bgra[::step, ::step, 2::-1])
    if not rgb.any():
        raise ScreenError("The capture came out black; the game may be in exclusive fullscreen.")
    return {"png_base64": base64.b64encode(_png(rgb)).decode(), "width": rgb.shape[1], "height": rgb.shape[0],
            "source": source}


SCREEN_SPEC = {
    "name": "look_at_screen",
    "description": "Take a screenshot of the Elite window and look at it: galaxy map, "
                   "system map, engineering or outfitting screens, HUD, messages. Use when they ask you to look, read "
                   "something on screen, or check what they're seeing. Say what you see briefly.",
    "input_schema": {"type": "object", "properties": {
        "whole_screen": {"type": "boolean", "description": "The whole main monitor instead of the Elite window."}}},
}
