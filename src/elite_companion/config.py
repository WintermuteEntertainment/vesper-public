"""User settings, from %USERPROFILE%\\.vesper\\config.toml.

Every setting has a default, so the file is optional; copy
config.example.toml from the repo to start one. Unknown keys are ignored.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

HOME = Path.home() / ".vesper"
CONFIG_PATH = HOME / "config.toml"

DEFAULTS: dict = {
    "voice": {
        "voice": "",            # part of an installed Windows voice name, e.g. "Hazel"; blank = system default
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


def load(path: Path | None = None) -> dict:
    """The settings: DEFAULTS merged with config.toml if it exists. Always returns every section and key."""
    path = path or CONFIG_PATH
    if path.exists():
        with open(path, "rb") as f:
            return _merge(DEFAULTS, tomllib.load(f))
    return copy.deepcopy(DEFAULTS)
