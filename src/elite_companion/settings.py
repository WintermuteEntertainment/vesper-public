"""Vesper's settings for the dashboard: what can be changed, what's allowed, and applying a change.

Each change goes into config.toml (keeping your comments, see config.write_values) and, where the setting
can change while Vesper runs, into the running co-pilot as well. Settings that decide where data or commands
go (the SSH host, the local model's address, the dashboard's own address) aren't offered
at all and are refused if asked for: the page has no password, so anyone on the network can reach it.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config as config_mod

log = logging.getLogger(__name__)

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")  # the ones brain.py has prices for
EFFORTS = ("low", "medium", "high")
MAX_TEXT = 200


@dataclass(frozen=True)
class Field:
    key: str                    # section.name, as in config.toml
    label: str
    kind: str = "bool"          # bool | int | float | choice | text | buttons | device
    live: bool = True           # False: takes effect when Vesper restarts
    locked: bool = False        # shown, but only changeable in config.toml
    choices: tuple = ()
    low: float | None = None
    high: float | None = None
    help: str = ""
    default: object = None      # for settings with no entry in config.DEFAULTS


def _rule_fields() -> list[Field]:
    from .callouts import RULES
    return [Field(f"callouts.rules.{r.id}.enabled", r.id.replace("_", " ").capitalize(), default=r.enabled) for r in RULES]


def groups() -> list[tuple[str, list[Field]]]:
    return [
        ("Microphone", [
            Field("listen.mic", "Microphone", "device"),
            Field("listen.whisper_model", "Speech recognition model", "choice", live=False,
                  choices=("tiny.en", "base.en", "small.en"), help="tiny.en is faster, small.en more accurate."),
            Field("listen.whisper_device", "Run speech recognition on", "choice", live=False, choices=("cpu", "cuda"),
                  help="cuda is the graphics card: faster, but it competes with the game."),
        ]),
        ("Voice", [
            Field("voice.voice", "Voice", "device"),
            Field("voice.output_device", "Speak through", "device"),
            Field("voice.rate", "Speed", "int", low=-10, high=10),
            Field("voice.volume", "Volume", "int", low=0, high=100),
        ]),
        ("Push-to-talk", [
            Field("ptt.key", "Keyboard key or mouse button", "text", live=False,
                  help="Like F9 or Mouse 4. Blank uses the joystick."),
            Field("ptt.device", "Joystick", "text", live=False, help="Part of its name."),
            Field("ptt.button", "Joystick button", "int", live=False, low=0, high=128),
        ]),
        ("Claude", [
            Field("brain.model", "Everyday model", "choice", choices=MODELS),
            Field("brain.effort", "Everyday effort", "choice", choices=EFFORTS),
            Field("brain.hard_model", 'Model for "think hard"', "choice", choices=("",) + MODELS),
            Field("brain.hard_effort", 'Effort for "think hard"', "choice", choices=EFFORTS),
            Field("brain.daily_budget_usd", "Daily budget (US dollars)", "float", low=0, high=100),
            Field("brain.max_history_turns", "Questions remembered", "int", low=1, high=500),
            Field("brain.idle_reset_minutes", "Start fresh after (minutes idle)", "float", low=1, high=1440),
            Field("brain.web_search", "Web search", "choice", live=False, choices=("searxng", "anthropic", "off"),
                  help="anthropic costs about 1 cent a search."),
        ]),
        ("Callouts", [Field("callouts.mode", "Mode", "choice", choices=("all", "critical", "off"))] + _rule_fields()),
        ("Lookout", [
            Field("lookout.mission_expiry", "Mission about to expire"),
            Field("lookout.valuable_scan", "Valuable planet scanned"),
            Field("lookout.scoop_ahead", "Fuel star ahead on a route"),
            Field("lookout.patterns", 'Patterns ("third interdiction tonight")'),
            Field("lookout.limpets", "Leaving port short of limpets"),
            Field("lookout.fighter_crew", "Fighter pilot check on first undock"),
        ]),
        ("Other", [
            Field("mining.prospector", "Prospector readouts", "choice", choices=("full", "verdict", "good", "off"),
                  help="good = only rocks worth mining."),
            Field("eddn.enabled", "Upload market screens to EDDN"),
        ]),
        ("Local model", [
            Field("local.model", "Local model", "text"),
            Field("local.timeout_seconds", "Local model timeout (seconds)", "int", low=5, high=600),
            Field("local.start_local", "Start in local mode", live=False),
            Field("local.base_url", "Local model address", "text", locked=True),
            Field("search.searxng_url", "SearXNG address", "text", locked=True),
            Field("search.ssh_host", "SearXNG SSH host", "text", locked=True),
            Field("dashboard.enabled", "Dashboard on", locked=True),
            Field("dashboard.editable", "Settings can be changed here", locked=True),
            Field("dashboard.network_edit", "Settings can be changed from other devices", locked=True),
            Field("dashboard.host", "Dashboard address", "text", locked=True),
            Field("dashboard.port", "Dashboard port", "int", locked=True),
        ]),
    ]


class SettingError(ValueError):
    """A value the setting can't take; the message is shown on the page."""


def _text(value, what: str) -> str:
    if not isinstance(value, str):
        raise SettingError(f"{what} needs text.")
    value = value.strip()
    if len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
        raise SettingError(f"{what}: that text is too long or has control characters in it.")
    return value


def validate(field: Field, value):
    """Return the value to store, or raise SettingError."""
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise SettingError(f"{field.label} is on or off.")
        return value
    if field.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError(f"{field.label} needs a number.")
        if field.kind == "int":
            if value != int(value):
                raise SettingError(f"{field.label} needs a whole number.")
            value = int(value)
        if (field.low is not None and value < field.low) or (field.high is not None and value > field.high):
            raise SettingError(f"{field.label} goes from {field.low:g} to {field.high:g}.")
        return value
    if field.kind == "buttons":
        parts = [p.strip() for p in _text(value, field.label).split(",") if p.strip()]
        if len(parts) > 4 or not all(p.isdigit() and 1 <= int(p) <= 128 for p in parts):
            raise SettingError(f"{field.label}: up to 4 button numbers, separated by commas.")
        return [int(p) for p in parts]
    value = _text(value, field.label)
    if field.kind == "choice" and value not in field.choices:
        raise SettingError(f"{field.label} can't be {value!r}.")
    if field.key == "ptt.key" and value:
        from .ptt import key_code
        if key_code(value) is None:
            raise SettingError(f'I don\'t know the key "{value}". Names look like F9, Right Ctrl or Mouse 4.')
    return value


def _get(cfg: dict, field: Field):
    node = cfg
    for part in field.key.split("."):
        if not isinstance(node, dict) or part not in node:
            return field.default
        node = node[part]
    return node


def _set(cfg: dict, key: str, value) -> None:
    *parents, last = key.split(".")
    for part in parents:
        cfg = cfg.setdefault(part, {})
    cfg[last] = value


def device_choices(current: str, names: list[str], blank: str) -> list[dict]:
    """A dropdown for a setting that holds part of a name. A saved "USB" shows as the device it finds,
    "Headset Microphone (USB)", and keeps its value; if nothing matches, the page says it wasn't found."""
    out, found = [{"value": "", "label": blank}], not current
    for name in names:
        if not found and current.lower() in name.lower():
            out.append({"value": current, "label": name})
            found = True
        else:
            out.append({"value": name, "label": name})
    if not found:
        out.append({"value": current, "label": f"{current} (not found)"})
    return out


class Settings:
    """The dashboard's view of the settings.

    running: the co-pilot's live settings, or None when the dashboard runs without Vesper.
    apply(key, value): put a live setting into the running co-pilot.
    devices(field_key, refresh): the names to offer for a microphone, voice or output.
    live_value(key): what a setting is right now, if something other than this page can change it.
    note(key): a line of current status to show under a setting.
    """

    def __init__(self, running: dict | None = None, path: Path | None = None, apply: Callable | None = None,
                 devices: Callable | None = None, live_value: Callable | None = None, note: Callable | None = None):
        self.path = path or config_mod.CONFIG_PATH
        self.running, self.apply = running, apply
        self.devices = devices or default_devices
        self.live_value, self.note = live_value, note
        self.fields = {f.key: f for _, fs in groups() for f in fs}

    @property
    def editable(self) -> bool:
        return bool(config_mod.load(self.path)["dashboard"]["editable"])

    @property
    def network_edit(self) -> bool:
        return bool(config_mod.load(self.path)["dashboard"]["network_edit"])

    def _describe(self, field: Field, saved: dict, refresh: bool = False) -> dict:
        override = self.live_value(field.key) if self.live_value else None
        value = _get(saved, field) if override is None else override
        out = {"key": field.key, "label": field.label, "kind": field.kind, "value": value, "live": field.live,
               "locked": field.locked, "help": field.help, "min": field.low, "max": field.high}
        if field.kind == "buttons":
            out["value"] = ", ".join(str(b) for b in value)
        if field.kind == "choice":
            have = list(field.choices) + ([value] if value not in field.choices else [])
            out["choices"] = [{"value": c, "label": c or "Never"} for c in have]
        if field.kind == "device":
            out["choices"] = device_choices(value, self.devices(field.key, refresh), "Windows default")
        if self.running is not None and not field.locked and override is None:
            running = _get(self.running, field)
            if running != _get(saved, field):  # hand-edited, or waiting for a restart
                out["running"] = running
        if self.note and (line := self.note(field.key)):
            out["note"] = line
        return out

    def state(self, refresh: bool = False) -> dict:
        saved = config_mod.load(self.path)
        return {"editable": bool(saved["dashboard"]["editable"]), "vesper": self.running is not None,
                "groups": [{"title": title, "fields": [self._describe(f, saved, refresh) for f in fs if not f.locked]}
                           for title, fs in groups() if any(not f.locked for f in fs)]}

    def change(self, key: str, value) -> dict:
        """Save one setting and apply it if it can change live. Raises SettingError if it can't be changed."""
        field = self.fields.get(key)
        if field is None:
            raise SettingError(f"There's no setting called {key}.")
        if field.locked or not self.editable:
            raise SettingError(f"{field.label} can only be changed in config.toml.")
        value = validate(field, value)
        wrote = config_mod.write_values({key: value}, self.path)
        if self.running is None:
            message = "Saved. It takes effect when Vesper starts."
        elif field.live:
            _set(self.running, key, copy.deepcopy(value))
            if self.apply:
                self.apply(key, value)
            message = "Saved and in use now." if wrote else "In use now."
        else:
            message = "Saved. It takes effect when you restart Vesper."
        log.info("setting changed from the dashboard: %s = %r", key, value)
        return {"message": message, "field": self._describe(field, config_mod.load(self.path))}


def default_devices(key: str, refresh: bool = False) -> list[str]:
    """Names to offer for a device setting, without a running co-pilot."""
    try:
        if key == "listen.mic":
            from .listen import list_mics
            return list_mics()
        from .speech import list_voices_and_outputs
        voices, outputs = list_voices_and_outputs()
        return voices if key == "voice.voice" else outputs
    except Exception:  # noqa: BLE001 - no audio libraries or devices: the page still shows the saved value
        log.exception("couldn't list devices for %s", key)
        return []
