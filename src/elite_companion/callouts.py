"""Spoken callouts: a short, curated set of rules that turn watcher events into speech.

Deliberately lean by default: only things worth hearing in VR are on, and
everything has a cooldown. Each rule has an id you can switch on/off or
re-time in config.toml under [callouts.rules.<id>].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .speech import CRITICAL, NORMAL
from .state import GameState
from .watcher import Event, Watcher

Match = Callable[[Event, GameState], "str | None"]


@dataclass
class Rule:
    """One callout: when `match` returns a phrase for an event, say it at `priority`.

    `cooldown` (seconds of game time) stops the same rule repeating; `key` splits the cooldown, so
    two different pilots hailing you are each announced.
    """
    id: str
    description: str
    match: Match
    priority: int = NORMAL
    cooldown: float = 0.0
    enabled: bool = True
    key: Callable[[Event], str] = field(default=lambda e: "")


def _flag_on(name: str) -> Callable[[Event], bool]:
    """A test for "this Status.json flag just turned on"."""
    return lambda e: e.name == "StatusFlag" and e.data.get("flag") == name and e.data.get("on")


def _hostile_hail(e: Event, s: GameState) -> str | None:
    """"Warning. Hostile hail from ..." when a pirate or hunter NPC messages you. Their messages are
    the ones whose text key starts with $Pirate (mission hunters use the same keys).
    """
    d = e.data
    if e.name != "ReceiveText" or d.get("Channel") != "npc":
        return None
    if not str(d.get("Message", "")).startswith("$Pirate"):  # pirates and the hunters missions send after you
        return None
    return f"Warning. Hostile hail from {d.get('From_Localised') or 'unknown pilot'}."


def _hull(e: Event, s: GameState) -> str | None:
    """"Hull N percent." on damage to your own ship (not your fighter, not while someone else flies it)."""
    d = e.data
    if e.name != "HullDamage" or d.get("Fighter") or d.get("PlayerPilot") is False:
        return None
    return f"Hull {round(d.get('Health', 0) * 100)} percent."


def _shields_down(e: Event, s: GameState) -> str | None:
    """"Shields down." when the shields collapse in flight (docked shield changes are ignored)."""
    if e.name == "ShieldState" and e.data.get("ShieldsUp") is False and not s.location.docked:
        return "Shields down."
    return None


def _destination(e: Event, s: GameState) -> str | None:
    # The watcher applies events before dispatching, so a final jump has already
    # moved us; the plotted route is still set until NavRouteClear arrives.
    """"Destination reached." on the jump into the last system of the plotted route."""
    if e.name == "FSDJump" and s.route and s.route[-1].get("SystemAddress") == e.data.get("SystemAddress"):
        return f"Destination reached. {e.data.get('StarSystem')}."
    return None


def _jumps_left(e: Event, s: GameState) -> str | None:
    """"N jumps remaining." after each jump along a plotted route."""
    if e.name == "FSDJump" and s.route_remaining:
        n = len(s.route_remaining)
        return f"{n} jump{'s' if n != 1 else ''} remaining."
    return None


def _when(test: Callable[[Event], bool], phrase: str | Callable[[Event], str]) -> Match:
    """Build a rule's match from a simple test and a fixed phrase (or a function making the phrase)."""
    return lambda e, s: (phrase(e) if callable(phrase) else phrase) if test(e) else None


RULES: list[Rule] = [
    # ---- on by default: threats and things you can't see in VR ----
    Rule("hostile_hail", "A pirate or bounty hunter messages you (usually seconds before an interdiction or attack).",
         _hostile_hail, CRITICAL, cooldown=90, key=lambda e: e.data.get("From", "")),
    Rule("interdiction", "Someone starts interdicting you (several seconds before you're pulled out).",
         _when(_flag_on("BeingInterdicted"), "Interdiction!"), CRITICAL, cooldown=10),
    Rule("shields_down", "Your shields collapse (not counted while docked).", _shields_down, CRITICAL, cooldown=5),
    Rule("hull", "Hull damage, as the game reports it (about every 20%).", _hull, CRITICAL,
         key=lambda e: str(round(e.data.get("Health", 0) * 100))),
    Rule("under_attack", "You're being shot at.", _when(lambda e: e.name == "UnderAttack" and e.data.get("Target") == "You",
         "Under attack."), NORMAL, cooldown=30),
    Rule("low_fuel", "Fuel drops below 25%.", _when(_flag_on("LowFuel"), "Fuel low."), CRITICAL, cooldown=120),
    Rule("overheating", "Heat goes over 100%.", _when(_flag_on("OverHeating"), "Overheating!"), CRITICAL, cooldown=20),
    Rule("objective_done", "A mission objective is done and it's ready to turn in.",
         _when(lambda e: e.name == "MissionRedirected",
               lambda e: f"Objective complete. Return to {e.data.get('NewDestinationStation')}."), NORMAL),
    Rule("destination", "You arrive at the end of a plotted route.", _destination, NORMAL),

    # ---- off by default: switch on in config.toml if you want them ----
    Rule("jumps_left", "Jumps remaining after each jump on a route.", _jumps_left, NORMAL, enabled=False),
    Rule("escaped", "You escape an interdiction.", _when(lambda e: e.name == "EscapeInterdiction", "Escaped."),
         NORMAL, enabled=False),
    Rule("docking", "Docking granted or denied.", _when(lambda e: e.name in ("DockingGranted", "DockingDenied"),
         lambda e: "Docking granted." if e.name == "DockingGranted" else "Docking denied."), NORMAL, enabled=False),
    Rule("first_discovery", "You scan a body nobody has discovered before.",
         _when(lambda e: e.name == "Scan" and e.data.get("WasDiscovered") is False and e.data.get("ScanType") != "NavBeaconDetail",
               lambda e: f"First discovery. {e.data.get('BodyName')}."), NORMAL, enabled=False),
    Rule("crime", "You commit a crime.", _when(lambda e: e.name == "CommitCrime",
         lambda e: f"Crime: {e.data.get('CrimeType')}."), NORMAL, enabled=False, cooldown=30),
    Rule("fuel_full", "Fuel scooping fills the tank.", _when(lambda e: e.name == "FuelScoop" and e.data.get("Total", 0) >= 31.9,
         "Tank full."), NORMAL, enabled=False, cooldown=60),
]


class Callouts:
    """Subscribes to a Watcher and speaks matching events."""

    def __init__(self, watcher: Watcher, say: Callable[[str, int], None], config: dict | None = None):
        config = config or {}
        self.watcher, self.say = watcher, say
        self.mode = config.get("mode", "all")
        overrides = config.get("rules", {})
        self.rules = []
        for r in RULES:
            o = overrides.get(r.id, {})
            self.rules.append(Rule(r.id, r.description, r.match, r.priority, float(o.get("cooldown", r.cooldown)),
                                   bool(o.get("enabled", r.enabled)), r.key))
        self._last: dict[tuple[str, str], float] = {}
        watcher.on("*", self.handle)

    def handle(self, ev: Event) -> None:
        """Check one event against every enabled rule and speak the matches, honouring the mode
        (off / critical) and each rule's cooldown. Replayed events never speak.
        """
        if ev.replay or self.mode == "off":
            return
        state = self.watcher.state
        for rule in self.rules:
            if not rule.enabled or (self.mode == "critical" and rule.priority != CRITICAL):
                continue
            try:
                phrase = rule.match(ev, state)
            except Exception:  # a bad rule must never take the watcher down
                continue
            if not phrase:
                continue
            k = (rule.id, rule.key(ev))
            now = ev.time  # game time, so cooldowns also hold when replaying a journal
            if rule.cooldown and now - self._last.get(k, -1e9) < rule.cooldown:
                continue
            self._last[k] = now
            self.say(phrase, rule.priority)

    def set_mode(self, mode: str) -> None:
        """Switch between "all", "critical" and "off" (the voice commands use this)."""
        self.mode = mode
