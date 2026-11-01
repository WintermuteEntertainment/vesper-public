"""Callouts that need more than one event or a clock: missions about to expire, valuable scans, fuel
planning along a plotted route, the material trader and shopping list on docking, the undock checklist
(limpets, fighter pilot), and patterns like repeat interdictions.

They speak only when callouts are on "all" (not in critical-only or off). Each can be switched off in
config.toml under [lookout]: mission_expiry, valuable_scan, scoop_ahead, patterns, limpets, fighter_crew.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from .phrases import t
from .speech import NORMAL

log = logging.getLogger(__name__)

SCOOPABLE = set("KGBFOAM")  # main-sequence stars a fuel scoop works on
VALUABLE = {"Earthlike body": "earthlike", "Water world": "water_world", "Ammonia world": "ammonia_world"}
EXPIRY_WARNINGS = (3600, 900)  # seconds before a mission expires: an hour, then 15 minutes
PATTERN_HOURS = 8              # "tonight", for counting repeat interdictions


def _parse(ts: str | None) -> datetime | None:
    """An ISO journal timestamp as an aware datetime, or None if missing or malformed."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _spoken_time(seconds: float) -> str:
    """Seconds as speech: "a minute", "12 minutes", "about an hour"."""
    minutes = round(seconds / 60)
    if minutes >= 55:
        return t("about_an_hour")
    return t("minutes", n=minutes) if minutes > 1 else t("a_minute")


class Lookout:
    """The watch-over-time callouts. Each listens for its own events; mission expiry also runs on a
    once-a-minute clock (start_clock), since nothing in the journal marks time passing.
    """
    def __init__(self, watcher, say: Callable[[str, int], None], callouts=None, config: dict | None = None,
                 poll_s: float = 60):
        cfg = config or {}
        self.watcher, self.say, self.callouts, self.poll_s = watcher, say, callouts, poll_s
        self.enabled = {k: bool(cfg.get(k, True)) for k in ("mission_expiry", "valuable_scan", "scoop_ahead", "patterns")}
        self.warned: set = set()
        watcher.on("Scan", self._scan)
        watcher.on("FSDJump", self._jump)
        watcher.on("Interdicted", self._interdicted)
        watcher.on("Undocked", self._undocked)
        watcher.on("Docked", self._docked)
        self.notes = None  # set by the co-pilot: their shopping list lives in the notes store
        self.checklist = {k: bool(cfg.get(k, True)) for k in ("limpets", "fighter_crew")}

    def _speak(self, text: str) -> None:
        """Say it only when callouts are on "all": these are useful, never urgent."""
        mode = self.callouts.mode if self.callouts else "all"
        if mode == "all":
            self.say(text, NORMAL)

    # ---- missions about to expire ----

    def start_clock(self) -> None:
        """Check mission expiry once a minute in a background thread."""
        def loop():
            """The clock: an error in one check is logged and the next minute tries again."""
            while True:
                try:
                    self.check_missions()
                except Exception:  # never let the clock die
                    log.exception("mission expiry check failed")
                time.sleep(self.poll_s)
        threading.Thread(target=loop, daemon=True, name="lookout").start()

    def check_missions(self, now: datetime | None = None) -> list[str]:
        """Warn about missions expiring within an hour, then within 15 minutes. Each warning is
        given once; a mission first seen with 10 minutes left gets only the 15-minute one.
        Missions whose objective is done are skipped: they don't fail at expiry.
        """
        if not self.enabled["mission_expiry"]:
            return []
        now = now or datetime.now(timezone.utc)
        said = []
        with self.watcher.lock:
            missions = list(self.watcher.state.missions.values())
        for m in missions:
            expiry = _parse(m.get("expiry"))
            if m.get("objective_complete") or not expiry:
                continue
            left = (expiry - now).total_seconds()
            due = [t for t in EXPIRY_WARNINGS if 0 < left <= t and (m.get("id"), t) not in self.warned]
            if not due:
                continue
            self.warned.update((m.get("id"), t) for t in EXPIRY_WARNINGS if t >= left)  # one warning, not two
            where = t("in_system", system=m["destination_system"]) if m.get("destination_system") else ""
            text = t("mission_expiring", mission=m.get("name") or t("a_mission"), where=where, time=_spoken_time(left))
            said.append(text)
            self._speak(text)
        return said

    # ---- valuable scans ----

    def _scan(self, ev) -> None:
        """A valuable body was scanned: Earth-like, water or ammonia world, or terraformable."""
        if ev.replay or not self.enabled["valuable_scan"]:
            return
        e = ev.data
        kind = VALUABLE.get(e.get("PlanetClass"))
        terraformable = e.get("TerraformState") == "Terraformable"
        body = e.get("BodyName", "")
        if not (kind or terraformable) or ("scan", body) in self.warned:
            return
        self.warned.add(("scan", body))
        short = body.removeprefix(e.get("StarSystem", "")).strip() or body
        what = t(kind) if kind else t("terraformable_planet", planet=str(e.get("PlanetClass", "planet")).lower())
        if kind and terraformable:
            what += t("and_terraformable")
        first = t("undiscovered") if e.get("WasDiscovered") is False else ""
        self._speak(t("valuable_scan", body=short, what=what, first=first))

    # ---- fuel along a plotted route ----

    def _jump(self, ev) -> None:
        """Under half a tank on a plotted route: is this star scoopable, or how many jumps to the next one?
        Main-sequence classes KGBFOAM are scoopable.
        """
        if ev.replay or not self.enabled["scoop_ahead"]:
            return
        s = self.watcher.state
        cap, fuel = s.ship.fuel_capacity, ev.data.get("FuelLevel", s.fuel_main)
        remaining = s.route_remaining
        if not cap or fuel is None or not remaining or fuel / cap >= 0.5:
            return
        pct = round(fuel / cap * 100)
        here = ev.data.get("StarSystem")
        star = next((r.get("StarClass") for r in s.route if r.get("StarSystem") == here), None)
        if star and star[0] in SCOOPABLE:
            self._speak(t("fuel_scoopable_here", pct=pct))
            return
        ahead = next((i + 1 for i, r in enumerate(remaining) if (r.get("StarClass") or " ")[0] in SCOOPABLE), None)
        if ahead is None:
            self._speak(t("fuel_no_scoopable", pct=pct))
        else:
            self._speak(t("fuel_scoopable_ahead", pct=pct, n=ahead))

    # ---- arriving: a material trader and their shopping list ----

    TRADER_KIND = {"Extraction": "raw", "Refinery": "raw", "Industrial": "manufactured", "High Tech": "encoded",
                   "Military": "encoded"}

    def _docked(self, ev) -> None:
        """At a station with a material trader, read out the shopping-list items that trader sells.
        The trader's kind follows the station economy (Extraction/Refinery = raw, Industrial =
        manufactured, High Tech/Military = encoded); an unknown economy reads the whole list.
        """
        if ev.replay or not self.notes:
            return
        services = [s.lower() for s in ev.data.get("StationServices", [])]
        items = self.notes.shopping()
        if "materialtrader" not in services or not items:
            return
        from .tools import material_kind
        economy = (ev.data.get("StationEconomy_Localised") or "").strip()
        kind = self.TRADER_KIND.get(economy)
        held = self.watcher.state.materials
        wanted = [n for n in items if not kind or material_kind(n, held)[0] in (kind, None)]
        if wanted:
            self._speak(t("trader_here", trader=t(f"trader_{kind or 'any'}"), items=", ".join(wanted)))

    def startup_summary(self, now: datetime | None = None) -> str | None:
        """At start-up: missions that expire within three hours, soonest first."""
        if not self.enabled["mission_expiry"]:
            return None
        now = now or datetime.now(timezone.utc)
        soon = []
        with self.watcher.lock:
            missions = list(self.watcher.state.missions.values())
        for m in missions:
            expiry = _parse(m.get("expiry"))
            if m.get("objective_complete") or not expiry:
                continue
            left = (expiry - now).total_seconds()
            if 0 < left <= 3 * 3600:
                soon.append((left, m.get("name") or t("a_mission")))
        if not soon:
            return None
        soon.sort()
        parts = [f"{name}, {_spoken_time(left) if left < 3600 else t('hours', n=f'{left / 3600:.1f}')}"
                 for left, name in soon[:4]]
        text = t("missions_expiring", n=len(soon), list="; ".join(parts))
        self._speak(text)
        return text

    # ---- leaving port ----

    def _undocked(self, ev) -> None:
        """The undock checklist: few or no limpets in a mining ship (refinery plus limpet controller),
        and once a day, a fighter hangar with no word on the pilot.
        """
        if ev.replay:
            return
        s = self.watcher.state
        items = " | ".join(m.get("Item", "").lower() for m in (s.records.get("Loadout") or {}).get("Modules", []))
        said = []
        if self.checklist["limpets"] and "refinery" in items and "dronecontrol" in items:
            limpets = s.cargo.get("Limpet", 0)
            if limpets == 0:
                said.append(t("no_limpets"))
            elif limpets < 20:
                said.append(t("few_limpets", n=limpets))
        day = (ev.data.get("timestamp") or "")[:10]
        if self.checklist["fighter_crew"] and "fighterbay" in items and ("crew", day) not in self.warned:
            self.warned.add(("crew", day))
            said.append(t("fighter_pilot"))
        if said:
            self._speak(" ".join(said))

    # ---- patterns ----

    def _interdicted(self, ev) -> None:
        """On the second interdiction or more within PATTERN_HOURS: say the count, and whether it's
        the same pilot again or the same faction.
        """
        if ev.replay or not self.enabled["patterns"]:
            return
        now = _parse(ev.data.get("timestamp")) or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=PATTERN_HOURS)
        recent = [i for i in self.watcher.state.interdictions if (_parse(i.get("time")) or now) >= cutoff]
        if len(recent) < 2:
            return
        who, faction = ev.data.get("Interdictor_Localised") or ev.data.get("Interdictor"), ev.data.get("Faction")
        n = len(recent)
        ordinal = t(f"ordinal_{n}") if 2 <= n <= 5 else t("ordinal_n", n=n)
        parts = [t("interdictions_tonight", ordinal=ordinal, n=n)]
        if who and sum(1 for i in recent if i.get("by") == who) > 1:
            parts.append(t("same_pilot", pilot=who))
        elif faction and sum(1 for i in recent if i.get("faction") == faction) > 1:
            parts.append(t("same_faction", faction=faction))
        self._speak(", ".join(parts) + ".")
