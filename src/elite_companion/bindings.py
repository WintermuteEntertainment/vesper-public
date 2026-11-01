"""Elite's control bindings, read from the Custom.*.binds files.

Used two ways:
- the find_binding tool: what an action is bound to, and what a button does;
- the push-to-talk picker: is this key or button already used by Elite?

Elite names devices it knows ("T16000M", "LogitechExtreme3DPro") from DeviceMappings.xml in the game folder,
and writes anything else as its USB id ("231D0200"). Both are handled, so any stick works.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

BINDS_DIR = Path.home() / "AppData/Local/Frontier Developments/Elite Dangerous/Options/Bindings"
# Friendly names for the find_binding answers. Add your own sticks with [controls] nicknames in config.toml,
# e.g. nicknames = { T16000M = "right stick", T16000MTHROTTLE = "throttle" }.
DEVICES = {"Keyboard": "keyboard", "Mouse": "mouse", "GamePad": "gamepad"}
_ACTION = re.compile(r"<(\w+)>((?:(?!</\1>).)*)</\1>", re.S)  # one <Action>...</Action> block of a .binds file


def set_nicknames(nicknames: dict[str, str]) -> None:
    """Add the user's own device nicknames (Elite device name -> what they call it)."""
    DEVICES.update(nicknames or {})


def _binds_file(directory: Path = BINDS_DIR) -> Path | None:
    """The binds file Elite is using: the most recently saved Custom.*.binds."""
    files = sorted(directory.glob("Custom.*.binds"), key=lambda p: p.stat().st_mtime) if directory.is_dir() else []
    return files[-1] if files else None


def _squash(s: str) -> str:
    """Lower-case letters and digits only, for forgiving name matches."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def load(directory: Path = BINDS_DIR) -> dict[str, list[dict]]:
    """action -> [{"device", "key", "modifiers"}] for every bound (non-empty) slot."""
    path = _binds_file(directory)
    if not path:
        return {}
    xml = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, list[dict]] = {}
    for m in _ACTION.finditer(xml):
        action, body = m.group(1), m.group(2)
        for slot in re.finditer(r'<(Primary|Secondary|Binding)\s+Device="([^"]*)"\s+Key="([^"]*)"\s*(/>|>(.*?)</\1>)',
                                body, re.S):
            device, key = slot.group(2), slot.group(3)
            if not key or device == "{NoDevice}":
                continue
            mods = re.findall(r'<Modifier\s+Device="[^"]*"\s+Key="([^"]*)"', slot.group(5) or "")
            out.setdefault(action, []).append({"device": DEVICES.get(device, device),
                                               "key": key.replace("Joy_", "button ").replace("Key_", ""),
                                               "modifiers": mods or None})
    return out


def lookup(action: str | None = None, device: str | None = None, button: int | None = None,
           directory: Path = BINDS_DIR) -> dict:
    """The find_binding tool: by action words, or by device and button number."""
    binds = load(directory)
    if not binds:
        return {"error": "Couldn't read Elite's bindings file."}
    if action:
        words = [_squash(w) for w in re.split(r"\s+", action) if w]
        hits = {a: b for a, b in binds.items() if all(w in _squash(a) for w in words)}
        if not hits:
            return {"action": action, "bound": None,
                    "note": "No bound action matches those words (unbound actions aren't listed)."}
        return {"action": action, "bound": hits}
    if device and button:
        dev = next((v for k, v in DEVICES.items() if _squash(device) in (_squash(k), _squash(v))), device)
        key = f"button {button}"
        found = sorted(a for a, b in binds.items() for s in b if s["device"] == dev and s["key"] == key)
        return {"device": dev, "button": button, "bound_to": found or None}
    return {"error": "Give an action (e.g. 'orbit lines') or a device and button."}


# ---- which name Elite uses for a stick ----

def _game_folders() -> list[Path]:
    """Places Elite is usually installed: every Steam library, the Frontier launcher and Epic defaults."""
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Frontier" / "Products",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Epic Games" / "EliteDangerous" / "Products"]
    steam = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        pass
    libraries = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace"))]
    roots += [lib / "steamapps" / "common" / "Elite Dangerous" / "Products" for lib in libraries]
    return [r for r in roots if r.is_dir()]


@lru_cache(maxsize=1)
def device_mappings() -> dict[str, str]:
    """USB id ("044FB10A") -> the name Elite writes in .binds ("T16000M"), from the game's DeviceMappings.xml."""
    import xml.etree.ElementTree as ET
    out: dict[str, str] = {}
    for products in _game_folders():
        for xml_path in products.glob("*/ControlSchemes/DeviceMappings.xml"):
            try:
                root = ET.parse(xml_path).getroot()
            except ET.ParseError:
                continue
            for device in root:  # <T16000M><PID>B10A</PID><VID>044F</VID></T16000M>, plus <Alternative> ids
                for ids in [device, *device.findall("Alternative")]:
                    pid, vid = ids.findtext("PID"), ids.findtext("VID")
                    if pid and vid:
                        out.setdefault(f"{vid.strip().upper()}{pid.strip().upper()}", device.tag)
    return out


def elite_device_names(usb_id: str) -> list[str]:
    """Every name Elite might use for this stick: its known name if it has one, and its raw USB id."""
    known = device_mappings().get(usb_id.upper())
    return [n for n in (known, usb_id.upper()) if n]


# ---- conflict checks for the push-to-talk picker ----

def _bound_to(needles: list[str], directory: Path = BINDS_DIR) -> list[str]:
    """Elite actions whose binding contains any of these Device/Key strings, in any Custom.*.binds file."""
    if not needles or not directory.is_dir():
        return []
    actions: set[str] = set()
    for path in directory.glob("Custom.*.binds"):
        xml = path.read_text(encoding="utf-8", errors="replace")
        actions.update(m.group(1) for m in _ACTION.finditer(xml) if any(n in m.group(2) for n in needles))
    return sorted(actions)


def joystick_button_used(usb_id: str, button: int, directory: Path = BINDS_DIR) -> list[str]:
    """Elite actions bound to this joystick button (1-based)."""
    return _bound_to([f'Device="{n}" Key="Joy_{button}"' for n in elite_device_names(usb_id)], directory)


def key_used(key: str, directory: Path = BINDS_DIR) -> list[str]:
    """Elite actions bound to this keyboard key or mouse button ("F9", "Right Ctrl", "Mouse 4")."""
    from .ptt import elite_key
    dev, code = elite_key(key)
    return _bound_to([f'Device="{dev}" Key="{code}"'], directory)


BINDINGS_SPEC = {
    "name": "find_binding",
    "description": "The commander's Elite control bindings. With action (words like 'orbit lines', 'heat sink', 'boost'): what "
                   "each matching action is bound to (device, button or key, modifiers). With device (a nickname "
                   "like 'right stick', or Elite's device name) and button: what that button does.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string"}, "device": {"type": "string"}, "button": {"type": "integer"}}},
}
