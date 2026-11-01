"""Push-to-talk: hold a keyboard key, mouse button or joystick button to talk to Vesper.

Joysticks are read through Windows' joystick API (winmm), the same way games see them. Keys and mouse buttons
are read with GetAsyncKeyState, which works while Elite has focus. Nothing is hooked or blocked: the game
still gets the press, so choose something Elite doesn't use (`vesper copilot --pick-button` checks for you).
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

_winmm = ctypes.WinDLL("winmm")


class _Caps(ctypes.Structure):
    """winmm JOYCAPSW: a joystick's name and capabilities."""
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("szPname", wintypes.WCHAR * 32),
                *[(n, wintypes.UINT) for n in ("wXmin", "wXmax", "wYmin", "wYmax", "wZmin", "wZmax", "wNumButtons",
                                                "wPeriodMin", "wPeriodMax", "wRmin", "wRmax", "wUmin", "wUmax",
                                                "wVmin", "wVmax", "wCaps", "wMaxAxes", "wNumAxes", "wMaxButtons")],
                ("szRegKey", wintypes.WCHAR * 32), ("szOEMVxD", wintypes.WCHAR * 260)]


class _Info(ctypes.Structure):
    """winmm JOYINFOEX: a joystick's axes and buttons right now."""
    _fields_ = [(n, wintypes.DWORD) for n in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos",
                                              "dwVpos", "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2")]


def _buttons(index: int) -> int | None:
    """The button bitmask of joystick `index`, or None if it's gone."""
    info = _Info(dwSize=ctypes.sizeof(_Info), dwFlags=0xFF)
    return info.dwButtons if _winmm.joyGetPosEx(index, ctypes.byref(info)) == 0 else None


def _product_name(vid: int, pid: int) -> str | None:
    """The stick's product name as its driver registered it ("T.16000M"). winmm itself only says
    "Microsoft PC-joystick driver" for everything."""
    import winreg
    path = (r"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM"
            rf"\VID_{vid:04X}&PID_{pid:04X}")
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, path) as key:
                return winreg.QueryValueEx(key, "OEMName")[0].strip() or None
        except OSError:
            continue
    return None


def joysticks() -> dict[int, tuple[str, str]]:
    """Connected joysticks: winmm index -> (a readable name, its USB id like "044FB10A").
    The name is Elite's own name for the stick if it knows it, else the product name, else the USB id."""
    from .bindings import device_mappings
    found = {}
    for i in range(16):
        caps = _Caps()
        if _winmm.joyGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0 and _buttons(i) is not None:
            usb_id = f"{caps.wMid:04X}{caps.wPid:04X}"
            name = device_mappings().get(usb_id) or _product_name(caps.wMid, caps.wPid) or usb_id
            found[i] = (name, usb_id)
    return found


def find_device(name: str) -> int | None:
    """The connected joystick with this name or USB id; failing an exact match, the first whose name contains it
    (exact first, so "T16000M" doesn't pick "T16000MTHROTTLE")."""
    sticks = joysticks()
    for i, (label, usb_id) in sticks.items():
        if name.lower() == label.lower() or name.upper() == usb_id:
            return i
    return next((i for i, (label, _) in sticks.items() if name.lower() in label.lower()), None)


# ---- keyboard and mouse push-to-talk ----
# Read with GetAsyncKeyState, which sees keys even while Elite has focus. Nothing is hooked or blocked:
# the game still gets the key, so pick one Elite doesn't use (the picker checks).

def _key_names() -> dict[str, int]:
    """Friendly key name -> Windows virtual-key code."""
    names = {"Mouse 3": 0x04, "Mouse 4": 0x05, "Mouse 5": 0x06, "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D,
             "Pause": 0x13, "Caps Lock": 0x14, "Space": 0x20, "Page Up": 0x21, "Page Down": 0x22, "End": 0x23,
             "Home": 0x24, "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E,
             "Num Lock": 0x90, "Scroll Lock": 0x91, "Left Shift": 0xA0, "Right Shift": 0xA1, "Left Ctrl": 0xA2,
             "Right Ctrl": 0xA3, "Left Alt": 0xA4, "Right Alt": 0xA5, "Grave": 0xC0, "Numpad *": 0x6A,
             "Numpad +": 0x6B, "Numpad -": 0x6D, "Numpad .": 0x6E, "Numpad /": 0x6F}
    names.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
    names.update({str(d): 0x30 + d for d in range(10)})
    names.update({f"Numpad {d}": 0x60 + d for d in range(10)})
    names.update({f"F{n}": 0x6F + n for n in range(1, 25)})
    return names


KEY_NAMES = _key_names()
VK_TO_NAME = {vk: name for name, vk in KEY_NAMES.items()}
# Left/right mouse buttons and the generic Shift/Ctrl/Alt codes (which double up the left/right ones) are
# never offered: clicking or a modifier shouldn't open the mic.
_SKIP_VK = {0x01, 0x02, 0x10, 0x11, 0x12}


def key_code(name: str) -> int | None:
    """Virtual-key code for a friendly name ("F9", "right ctrl", "mouse 4"), case-insensitive."""
    wanted = " ".join(name.lower().replace("control", "ctrl").split())
    return next((vk for n, vk in KEY_NAMES.items() if n.lower() == wanted), None)


def key_down(vk: int) -> bool:
    """Whether this virtual key (keyboard key or mouse button) is held right now."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def elite_key(name: str) -> tuple[str, str] | None:
    """How Elite's .binds file writes this key or mouse button: ("Keyboard", "Key_F9"), ("Mouse", "Mouse_4")."""
    if name.startswith("Mouse "):
        return "Mouse", "Mouse_" + name.split()[1]
    special = {"Left Ctrl": "LeftControl", "Right Ctrl": "RightControl", "Left Shift": "LeftShift",
               "Right Shift": "RightShift", "Left Alt": "LeftAlt", "Right Alt": "RightAlt", "Caps Lock": "CapsLock",
               "Page Up": "PageUp", "Page Down": "PageDown", "Scroll Lock": "ScrollLock", "Num Lock": "NumLock",
               "Up": "UpArrow", "Down": "DownArrow", "Left": "LeftArrow", "Right": "RightArrow",
               "Enter": "Enter", "Numpad *": "Numpad_Multiply", "Numpad +": "Numpad_Add",
               "Numpad -": "Numpad_Subtract", "Numpad .": "Numpad_Decimal", "Numpad /": "Numpad_Divide"}
    if name in special:
        return "Keyboard", "Key_" + special[name]
    if name.startswith("Numpad "):
        return "Keyboard", "Key_Numpad_" + name.split()[1]
    return "Keyboard", "Key_" + name.replace(" ", "")


def pick_input(timeout: float = 30) -> tuple | None:
    """Wait for the next press of anything. Returns ("key", name) for a keyboard key or mouse button 3-5,
    or ("joy", windows_name, button, usb_id) for a joystick button. Keys already held at the start are ignored."""
    sticks = joysticks()
    before = {i: _buttons(i) or 0 for i in sticks}
    held = {vk for vk in VK_TO_NAME if key_down(vk)}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for vk, name in VK_TO_NAME.items():
            if vk in _SKIP_VK:
                continue
            if key_down(vk):
                if vk not in held:
                    while key_down(vk):  # wait for release so the key doesn't leak into the next prompt
                        time.sleep(0.02)
                    return "key", name
            else:
                held.discard(vk)
        for i, (label, usb_id) in sticks.items():
            now = _buttons(i) or 0
            pressed = now & ~before[i]
            if pressed:
                return "joy", label, (pressed & -pressed).bit_length(), usb_id
            before[i] = now
        time.sleep(0.02)
    return None


class KeyPushToTalk:
    """Push-to-talk on a keyboard key or mouse button; same interface as PushToTalk."""

    def __init__(self, key: str, on_press: Callable[[], None], on_release: Callable[[], None]):
        self.vk = key_code(key)
        if self.vk is None:
            raise ValueError(f"Unknown push-to-talk key {key!r}. Run `vesper copilot --pick-button` to find its name.")
        self.on_press, self.on_release = on_press, on_release
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="push-to-talk", daemon=True)

    def start(self) -> None:
        """Start watching the key in a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop watching."""
        self._stop.set()

    def _run(self) -> None:
        """Poll the key 50 times a second and call on_press / on_release on each change."""
        down = False
        while not self._stop.is_set():
            pressed = key_down(self.vk)
            if pressed != down:
                down = pressed
                (self.on_press if down else self.on_release)()
            time.sleep(0.02)


class PushToTalk:
    """Calls on_press / on_release when the configured button goes down / up."""

    def __init__(self, device: str, button: int, on_press: Callable[[], None], on_release: Callable[[], None]):
        self.device, self.button = device, button
        self.on_press, self.on_release = on_press, on_release
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="push-to-talk", daemon=True)

    def start(self) -> None:
        """Start watching the button in a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop watching."""
        self._stop.set()

    def _run(self) -> None:
        """Poll the button 50 times a second and call on_press / on_release on each change. If the
        device disappears, keep looking for it every 2 seconds.
        """
        mask = 1 << (self.button - 1)
        index, down = None, False
        while not self._stop.is_set():
            if index is None:
                index = find_device(self.device)
                if index is None:
                    time.sleep(2)  # unplugged or asleep; keep looking
                    continue
            state = _buttons(index)
            if state is None:
                index = None
                continue
            pressed = bool(state & mask)
            if pressed != down:
                down = pressed
                (self.on_press if down else self.on_release)()
            time.sleep(0.02)
