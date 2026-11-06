"""User settings, from %USERPROFILE%\\.vesper\\config.toml.

Every setting has a default, so the file is optional; copy
config.example.toml from the repo to start one. Unknown keys are ignored.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import tomllib
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".vesper"
CONFIG_PATH = HOME / "config.toml"
_MISSING = object()

DEFAULTS: dict = {
    "voice": {
        "voice": "",            # part of a Windows voice name, e.g. "Hazel", or a Piper voice; blank = system default
        "rate": 1,              # -10 (slow) .. 10 (fast)
        "volume": 100,          # 0..100
        "output_device": "",    # part of an audio output name, e.g. "Headphones"; blank = Windows default
    },
    "callouts": {
        "mode": "all",          # all | critical | off
        "rules": {},            # per-rule overrides: [callouts.rules.under_attack] enabled = false, cooldown = 60
    },
    "ptt": {                    # find yours with: vesper copilot --pick-button (it prints these lines for you)
        "key": "",              # a keyboard key or mouse button: "F9", "Right Ctrl", "Mouse 4"; wins over a joystick
        "device": "",           # or a joystick, by the name the picker shows (e.g. "T16000M") or its USB id
        "button": 0,            # ...and its button number. No key and button 0 = push-to-talk off
    },
    "controls": {
        "nicknames": {},        # what you call your devices, for binding answers: { T16000M = "right stick" }
    },
    "language": {
        "code": "en",           # what you speak and Vesper answers in: en, fr, de, es, ru, pt...
    },
    "listen": {
        "mic": "",              # part of the microphone name; blank = Windows default
        "whisper_model": "base.en",
        "whisper_device": "cpu",  # cpu leaves the GPU to the game; "cuda" is faster on NVIDIA cards
    },
    "lookout": {                # callouts that watch over time; set any to false to silence it
        "mission_expiry": True,   # an hour and 15 minutes before a mission expires
        "valuable_scan": True,    # Earth-like, water and ammonia worlds, and terraformables
        "scoop_ahead": True,      # under half a tank on a route: is this star scoopable, or how far to the next
        "patterns": True,         # "that's the third interdiction tonight"
        "limpets": True,          # leaving port in a mining ship with few or no limpets
        "fighter_crew": True,     # first undock of the day with a fighter hangar: is the pilot aboard?
    },
    "mining": {
        "prospector": "full",     # full | verdict | good (only rocks worth mining) | off
    },
    "search": {                 # used when [brain] web_search = "searxng": your own SearXNG instance
        "searxng_url": "",        # e.g. "http://localhost:8888" (its settings must allow format=json)
        "ssh_host": "",           # optional: run the search on this SSH host ("user@host") when SearXNG only
                                  # listens there; needs key login
    },
    "screen": {                 # for look_at_screen and read_contacts while Elite's own window is minimised (VR)
        "mirror_windows": ["Oculus Mirror", "Headset Window", "VR View"],  # title starts of VR mirror windows
        "mirror_command": "",     # optional: a program that opens your mirror, e.g. Meta's OculusMirror.exe
    },
    "dashboard": {              # live second-screen page: http://<this PC>:8770 on a tablet or phone
        "enabled": True,
        "host": "127.0.0.1",      # "0.0.0.0" lets other devices on your network open it
        "port": 8770,
        "editable": True,         # false = the page shows the settings but can't change them
        "network_edit": False,    # false = only the PC running Vesper can change settings
    },
    "eddn": {
        "enabled": False,       # upload the market screens you open to EDDN (the feed Spansh/Inara/EDSM use)
    },
    "brain": {
        "model": "claude-haiku-5-5",
        "effort": "low",          # low keeps spoken answers quick and cheap
        "daily_budget_usd": 1.00,  # hard stop for the day once estimated spend reaches this
        "max_history_turns": 40,   # questions remembered before the conversation starts fresh
        "idle_reset_minutes": 15,  # ...or after this long without a question
        "hard_model": "claude-sonnet-5-5",  # hard questions go here ("think hard about..."); "" = never
        "hard_effort": "medium",
        "web_search": "anthropic",  # anthropic (about 1 cent a search) | searxng (free, your own server) | off
    },
    "local": {
        # Optional: an OpenAI-compatible server for "go local" (no Claude), e.g. Ollama
        # ("http://localhost:11434/v1"), LM Studio ("http://localhost:1234/v1") or llama.cpp. Blank = off.
        "base_url": "",
        "model": "",
        "timeout_seconds": 120,
        "start_local": False,      # start in local mode
    },
}


def _merge(base: dict, override: dict) -> dict:
    """`base` with `override` laid over it, section by section (nested tables merge, values replace)."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def add_section(section: str, lines: str, path: Path | None = None) -> bool:
    """Append a [section] with these lines to config.toml, creating the file if needed.

    Never changes what's already there: if the file already has that section, nothing is written and
    the result is False, so the caller can tell the user to edit it by hand.
    """
    path = path or CONFIG_PATH
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() == f"[{section}]" for line in old.splitlines()):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    gap = "" if not old else "\n" if old.endswith("\n") else "\n\n"
    path.write_text(f"{old}{gap}[{section}]\n{lines}\n", encoding="utf-8")
    return True


class ConfigError(ValueError):
    """config.toml can't be read; the message says which file and where."""


def load(path: Path | None = None) -> dict:
    """The settings: DEFAULTS merged with config.toml if it exists. Always returns every section and key."""
    path = path or CONFIG_PATH
    if path.exists():
        try:
            # utf-8-sig: some Windows tools start the file with a byte-order mark, which TOML doesn't allow
            return _merge(DEFAULTS, tomllib.loads(path.read_bytes().decode("utf-8-sig")))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(f"There's a mistake in your settings file, {path}: {e}") from e
    return copy.deepcopy(DEFAULTS)


# ---- changing settings from the dashboard ----

_SECTION = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_write_lock = threading.Lock()


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML string
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    raise ValueError(f"can't write {value!r} to config.toml")


def _value_end(text: str) -> int:
    """Where a TOML value ends in the text after "key = " (a # inside a string isn't a comment)."""
    if text[:1] in ('"', "'"):
        quote, i = text[0], 1
        while i < len(text) and text[i] != quote:
            i += 2 if quote == '"' and text[i] == "\\" else 1
        return min(i + 1, len(text))
    if text[:1] == "[":
        return text.find("]") + 1 or len(text)
    comment = text.find("#")
    return len((text if comment < 0 else text[:comment]).rstrip())


def _edit(text: str, section: str, key: str, value) -> str:
    """The file's text with one setting changed. Every other line, comment and blank stays as it was."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    assign = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*)$")
    current, last_in_section = None, None
    for i, line in enumerate(lines):
        if head := _SECTION.match(line):
            current = head.group(1).strip()
            if current == section:
                last_in_section = i
            continue
        if current != section:
            continue
        if line.strip():
            last_in_section = i
        if m := assign.match(line):
            rest = m.group(2)
            lines[i] = (m.group(1) + _toml(value) + rest[_value_end(rest):]).rstrip()
            return newline.join(lines)
    entry = f"{key} = {_toml(value)}"
    if last_in_section is not None:
        lines.insert(last_in_section + 1, entry)
        return newline.join(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return newline.join(lines + ([""] if lines else []) + [f"[{section}]", entry, ""])


def _backup(path: Path, backup_dir: Path) -> None:
    """Keep the file as it was, at most once a minute so a run of clicks doesn't leave a pile of copies."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"config-{datetime.now():%Y-%m-%d_%H%M}.toml"
    if not target.exists():
        shutil.copy2(path, target)


def write_values(changes: dict, path: Path | None = None) -> bool:
    """Save {"section.name": value} to config.toml; returns False if nothing needed changing.

    Reads the result back before replacing the file: if anything other than these settings would change
    (a layout this simple editor doesn't understand), it refuses and the file stays as it was.
    """
    path = path or CONFIG_PATH
    with _write_lock:
        text = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
        before = tomllib.loads(text)
        effective, expected, new_text = _merge(DEFAULTS, before), copy.deepcopy(before), text
        for dotted, value in changes.items():
            *parents, key = dotted.split(".")
            node = effective
            for part in parents + [key]:
                node = node.get(part, _MISSING) if isinstance(node, dict) else _MISSING
            if node == value and type(node) is type(value):
                continue
            new_text = _edit(new_text, ".".join(parents), key, value)
            target = expected
            for part in parents:
                target = target.setdefault(part, {})
            target[key] = value
        if new_text == text:
            return False
        try:
            good = tomllib.loads(new_text) == expected
        except tomllib.TOMLDecodeError:
            good = False
        if not good:
            raise ValueError("config.toml is laid out in a way I can't edit safely. Nothing was changed: edit it by hand.")
        if path.exists():
            _backup(path, path.parent / "config-backups")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".saving")
        temp.write_bytes(new_text.encode("utf-8"))
        os.replace(temp, path)
        return True
