"""Turns the game's files into a stream of events and keeps GameState current.

    watcher = Watcher()
    watcher.on("FSDJump", lambda ev: print("Jumped to", ev.data["StarSystem"]))
    watcher.on("StatusFlag", lambda ev: ...)   # ev.data = {"flag": "LowFuel", "on": True}
    watcher.run()                               # or watcher.start() for a background thread

Event sources:
    journal  one per journal line, named after its "event" field
    status   "Status" whenever Status.json changes (ignoring its timestamp)
    derived  "StatusFlag" when a Status.json flag turns on or off
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .journal import SIDE_FILES, JournalTail, default_journal_dir, journal_files, read_entries, read_side_file
from .state import GameState, mission_from_accepted
from .status import flag_changes

log = logging.getLogger(__name__)


@dataclass
class Event:
    """One thing that happened: a journal line, a Status.json change, or a derived flag change."""
    name: str
    data: dict
    source: str  # "journal", "status" or "derived"
    replay: bool = False  # True while catching up on events that already happened
    received: float = field(default_factory=time.time)

    @property
    def timestamp(self) -> str | None:
        """The game's own ISO timestamp, if the event has one."""
        return self.data.get("timestamp")

    @property
    def time(self) -> float:
        """When it happened in game (UTC epoch seconds), falling back to arrival time."""
        ts = self.timestamp
        if ts:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return self.received


Handler = Callable[[Event], None]


class Watcher:
    """Follows the journal and Status.json, keeps `state` (a GameState) current, and calls handlers.

    Thread safety: every change to `state` happens under `lock`. Read state under it too, or use snapshot().
    """
    def __init__(self, directory: Path | str | None = None, poll_interval: float = 0.25, history: int = 1000):
        self.directory = Path(directory) if directory else default_journal_dir()
        self.poll_interval = poll_interval
        self.state = GameState()
        self.lock = threading.RLock()
        self.tail = JournalTail(self.directory)
        self.history: deque[Event] = deque(maxlen=history)
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._status_sig: tuple[int, int] | None = None
        self._last_status: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- subscribing ----

    def on(self, name: str, handler: Handler) -> Handler:
        """Call handler for events with this name, or "*" for every event."""
        self._handlers[name].append(handler)
        return handler

    def dispatch(self, event: Event) -> None:
        """Call every handler for this event (and every "*" handler). A failing handler is logged and
        skipped, so one bad callout can't stop the others or the watcher.
        """
        for handler in self._handlers.get(event.name, []) + self._handlers.get("*", []):
            try:
                handler(event)
            except Exception:
                log.exception("handler %r failed on %s", handler, event.name)

    # ---- reading ----

    def catch_up(self, dispatch: bool = False) -> list[Event]:
        """Read the current journal from the top to rebuild state.

        These events already happened, so they are marked replay=True and
        only dispatched when asked; live callouts shouldn't fire for them.
        """
        self.tail.open_latest(from_start=True)
        self._seed_from_previous()
        self._backfill_fits()  # before today's replay, so today's first Loadout can be compared
        events = self.poll_once(replay=True)
        self._backfill_missions()
        self._backfill_history()
        self._backfill_records(("StoredShips", "StoredModules", "ShipLocker", "Loadout"))
        # The last outfitting and shipyard screens they opened, even in an older session: the files are the game's own record.
        outfitting = read_side_file(self.directory / SIDE_FILES["Outfitting"])
        if outfitting and outfitting.get("Items") is not None:
            with self.lock:
                self.state._on_Outfitting({**outfitting, "_file": outfitting})
        shipyard = read_side_file(self.directory / SIDE_FILES["Shipyard"])
        if shipyard and shipyard.get("PriceList") is not None:
            with self.lock:
                self.state._on_Shipyard({**shipyard, "_file": shipyard})
        if dispatch:
            for ev in events:
                self.dispatch(ev)
        return events

    def _seed_from_previous(self) -> None:
        """A new journal starts at the game's menu, before LoadGame. Until they load in, use the previous
        journal for ship, location and cargo, so the tools still work (and don't show an empty game)."""
        path = self.tail.path
        if not path or any(e["event"] == "LoadGame" for e in read_entries(path)):
            return
        older = [p for p in journal_files(self.directory) if p != path]
        for prev in reversed(older[-5:]):  # skip stub journals from a game that never loaded
            entries = read_entries(prev)
            if any(e["event"] == "LoadGame" for e in entries):
                with self.lock:
                    for e in entries:
                        self.state.apply(e)
                return

    def poll_once(self, replay: bool = False) -> list[Event]:
        """Read anything new, update state and return the new events (not dispatched)."""
        events = []
        with self.lock:
            for entry in self.tail.poll():
                side = SIDE_FILES.get(entry["event"])
                if side:
                    data = read_side_file(self.directory / side)
                    if data is not None:
                        entry["_file"] = data
                self.state.apply(entry)
                events.append(Event(entry["event"], entry, "journal", replay))
            events += self._poll_status(replay)
            self.history.extend(events)
        return events

    def _backfill_missions(self, max_files: int = 50) -> None:
        """Missions accepted in an earlier session only show up as bare IDs at
        login, so look back through older journals for their details."""
        with self.lock:
            missions = self.state.missions
            missing = self.state.missions_missing_details()
            if not missing:
                return
            older = [p for p in journal_files(self.directory) if p != self.tail.path]
            for path in reversed(older[-max_files:]):  # newest first
                docked_at = (None, None)
                for entry in read_entries(path):
                    if entry["event"] == "Docked" or (entry["event"] == "Location" and entry.get("Docked")):
                        docked_at = (entry.get("StationName"), entry.get("StarSystem"))
                    elif entry["event"] == "Undocked":
                        docked_at = (None, None)
                    mid = entry.get("MissionID")
                    if mid not in missing:
                        continue
                    if entry["event"] == "MissionRedirected":
                        self.state.apply(entry)
                    elif entry["event"] == "MissionAccepted":
                        done = {k: missions[mid][k] for k in ("objective_complete", "turn_in_system", "turn_in_station")
                                if k in missions[mid]}
                        missions[mid] = mission_from_accepted(entry) | done
                        if docked_at[0]:
                            missions[mid]["source_station"], missions[mid]["source_system"] = docked_at
                missing = self.state.missions_missing_details()
                if not missing:
                    return

    def _backfill_fits(self, max_files: int = 150) -> None:
        """Seed each ship's last known fit from older journals (oldest first), so the first Loadout of a
        session can already be compared. Recorded refits from old journals are discarded."""
        older = [p for p in journal_files(self.directory) if p != self.tail.path][-max_files:]
        with self.lock:
            known = dict(self.state.fits)
            seen: dict = {}
            for path in older:
                for e in read_entries(path):
                    if e["event"] == "Loadout" and e.get("ShipID") is not None:
                        seen[e["ShipID"]] = e
            for ship_id, e in seen.items():
                self.state._ship_profile(e)
                if ship_id not in known:
                    before = list(self.state.refits)
                    self.state._note_refit(e)
                    self.state.refits = before

    def _backfill_records(self, kinds: tuple[str, ...], max_files: int = 30) -> None:
        """Latest StoredShips / StoredModules from older journals, if today's has none."""
        missing = [k for k in kinds if k not in self.state.records]
        if not missing:
            return
        older = [p for p in journal_files(self.directory) if p != self.tail.path][-max_files:]
        with self.lock:
            for path in reversed(older):
                for e in reversed(read_entries(path)):
                    if e["event"] in missing:
                        self.state.records[e["event"]] = e
                        missing.remove(e["event"])
                if not missing:
                    return

    def _backfill_history(self, max_files: int = 12, days: int = 3) -> None:
        """Deaths, hostile pilots and mission cargo from the last few days of older
        journals, so a restart doesn't forget who killed you or what you lost."""
        st = self.state
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        older = [p for p in journal_files(self.directory) if p != self.tail.path][-max_files:]
        deaths, last_collect = [], {}
        with self.lock:
            for path in older:  # oldest first
                for e in read_entries(path):
                    ts, ev = e.get("timestamp", ""), e["event"]
                    if ts < cutoff:
                        continue
                    if ev == "Died":
                        deaths.append({"time": ts, "killer": e.get("KillerName_Localised") or e.get("KillerName"),
                                       "killer_ship": e.get("KillerShip"), "killer_rank": e.get("KillerRank")})
                    elif ev in ("ReceiveText", "Interdicted"):
                        name = e.get("From_Localised") if ev == "ReceiveText" else e.get("Interdictor")
                        if name and name in st.contacts and st.contacts[name].get("last_seen", "") > ts:
                            continue  # today's journal already has a newer sighting
                        st.apply(e)
                    elif ev == "CargoDepot" and e.get("MissionID") in st.missions:
                        mid = e["MissionID"]
                        if e.get("UpdateType") == "Collect":
                            last_collect[mid] = ts
                        st.missions[mid].setdefault("depot", {"collected": e.get("ItemsCollected"),
                                                              "delivered": e.get("ItemsDelivered"),
                                                              "total": e.get("TotalItemsToDeliver")})
            st.deaths = deaths + st.deaths
            for mid, collected_at in last_collect.items():
                m, depot = st.missions[mid], st.missions[mid].get("depot") or {}
                died_after = [d["time"] for d in st.deaths if d["time"] > collected_at]
                if died_after and mid not in st.mission_cargo and (depot.get("delivered") or 0) < (depot.get("total") or 1):
                    m.setdefault("cargo_lost", died_after[0])

    def _poll_status(self, replay: bool) -> list[Event]:
        """Status.json events: "Status" when anything but its timestamp changed, plus a "StatusFlag"
        event for each flag that turned on or off. The first read only sets the baseline.
        """
        path = self.directory / "Status.json"
        try:
            st = path.stat()
        except OSError:
            return []
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._status_sig:
            return []
        status = read_side_file(path)
        if status is None:
            return []  # mid-write; try again next poll
        self._status_sig = sig

        old = self._last_status
        if old is not None and _without_timestamp(old) == _without_timestamp(status):
            return []
        self._last_status = status
        self.state.apply_status(status)

        events = [Event("Status", status, "status", replay)]
        turned_on, turned_off = flag_changes(old, status)
        if old is not None:  # the first read only sets the baseline
            for flag in sorted(turned_on):
                events.append(Event("StatusFlag", {"flag": flag, "on": True, "timestamp": status.get("timestamp")}, "derived", replay))
            for flag in sorted(turned_off):
                events.append(Event("StatusFlag", {"flag": flag, "on": False, "timestamp": status.get("timestamp")}, "derived", replay))
        return events

    # ---- querying ----

    def snapshot(self) -> dict:
        """The whole game state as plain JSON-ready data (see GameState.snapshot)."""
        with self.lock:
            return self.state.snapshot()

    def recent(self, seconds: float | None = None, names: set[str] | None = None) -> list[Event]:
        """Events from the last N seconds of game time, optionally filtered by name."""
        cutoff = time.time() - seconds if seconds else 0
        with self.lock:
            return [e for e in self.history if e.time >= cutoff and (names is None or e.name in names)]

    # ---- running ----

    def run(self, catch_up: bool = True) -> None:
        """Poll forever (until stop()), dispatching events to handlers."""
        if catch_up:
            self.catch_up()
        while not self._stop.is_set():
            for ev in self.poll_once():
                self.dispatch(ev)
            self._stop.wait(self.poll_interval)

    def start(self, catch_up: bool = True) -> threading.Thread:
        """Run the watcher in a background thread (daemon, so it never keeps the program alive)."""
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, args=(catch_up,), name="elite-watcher", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Stop polling and wait up to 5 s for the thread to finish."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def _without_timestamp(d: dict) -> dict:
    """A Status.json dict minus its timestamp, so two reads can be compared for real changes."""
    return {k: v for k, v in d.items() if k != "timestamp"}
