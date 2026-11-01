"""Notes and reminders that survive between sessions.

Stored in ~/.vesper/memory.json and shared by the copilot and the
MCP server (each operation re-reads the file, so both stay in sync).

A note can be plain memory ("Mike Dailly hunts me around Col 285") or a
reminder with a trigger:
    dock     when you dock at a station (or anywhere in a system)
    arrive   when you jump into a system
    startup  next time the copilot starts
    time     at a clock time (fires at the next copilot start if it was off)
Reminders fire once and are then marked done; plain notes stay until forgotten.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import HOME

MEMORY_PATH = HOME / "memory.json"
TRIGGERS = ("none", "dock", "arrive", "startup", "time")


class NoteError(ValueError):
    """A note or reminder that can't be saved or found; the message is safe to speak."""
    pass


class MemoryStore:
    """Notes, reminders and the commander's profile, in one JSON file.

    Every call re-reads the file, so the copilot and the MCP server can share it. Writes go through
    a lock and an atomic replace.
    """
    def __init__(self, path: Path = MEMORY_PATH):
        self.path = path
        self._lock = threading.Lock()

    # ---- file ----

    def _read(self) -> dict:
        """The whole file as a dict ({} if missing or unreadable)."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        """Write the whole file atomically (temp file, then replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash never leaves half a file

    def _load(self) -> list[dict]:
        """Every note, done or not."""
        return self._read().get("notes", [])

    def _save(self, notes: list[dict]) -> None:
        """Replace the note list, keeping the profile."""
        self._write(self._read() | {"notes": notes})

    # ---- the copilot's identity and the commander's standing preferences ----

    def profile(self) -> dict:
        """The copilot's name and the commander's standing preferences, home base and shopping list."""
        return self._read().get("profile", {"name": None, "preferences": []})

    def set_name(self, name: str) -> None:
        """Rename the copilot ("call yourself Vesper")."""
        with self._lock:
            data = self._read()
            data["profile"] = data.get("profile", {"preferences": []}) | {"name": name.strip()}
            self._write(data)

    def set_home(self, station: str, system: str) -> None:
        """Remember the commander's home base, used by home trade rankings."""
        with self._lock:
            data = self._read()
            data["profile"] = data.get("profile", {"name": None, "preferences": []}) | {
                "home": {"station": station.strip(), "system": system.strip()}}
            self._write(data)

    def home(self) -> dict | None:
        """The home base ({"station", "system"}) set with "set my home base", or None if not set yet."""
        return self.profile().get("home")

    def shopping(self) -> dict[str, int]:
        """The shopping list: material -> count wanted (0 means "some")."""
        return dict(self.profile().get("shopping") or {})

    def shop(self, item: str, count: int | None = None, remove: bool = False) -> dict[str, int]:
        """Add (or with remove=True, drop) a material on their shopping list; count 0/None means 'some'."""
        with self._lock:
            data = self._read()
            prof = data.get("profile", {"name": None, "preferences": []})
            items = {k: v for k, v in (prof.get("shopping") or {}).items() if k.lower() != item.strip().lower()}
            if not remove:
                items[item.strip().title()] = int(count or 0)
            data["profile"] = prof | {"shopping": items}
            self._write(data)
        return items

    def add_preference(self, text: str) -> list[str]:
        """Add a standing preference ("keep answers short"); a repeat moves to the end."""
        with self._lock:
            data = self._read()
            prof = data.get("profile", {"name": None, "preferences": []})
            prefs = [p for p in prof.get("preferences", []) if p.lower() != text.strip().lower()] + [text.strip()]
            data["profile"] = prof | {"preferences": prefs}
            self._write(data)
        return prefs

    def remove_preference(self, search: str) -> list[str]:
        """Drop every preference containing `search`."""
        with self._lock:
            data = self._read()
            prof = data.get("profile", {"name": None, "preferences": []})
            prefs = [p for p in prof.get("preferences", []) if search.lower() not in p.lower()]
            data["profile"] = prof | {"preferences": prefs}
            self._write(data)
        return prefs

    # ---- operations ----

    def add(self, text: str, when: str = "none", place: str | None = None, minutes_from_now: int | None = None,
            at_local_time: str | None = None) -> dict:
        """Save a note or reminder. `when` is one of TRIGGERS; dock reminders default to anywhere,
        arrive reminders need a system, time reminders need minutes_from_now or at_local_time.
        """
        text = (text or "").strip()
        if not text:
            raise NoteError("Nothing to remember.")
        if when not in TRIGGERS:
            raise NoteError(f"when must be one of {', '.join(TRIGGERS)}.")
        if when == "dock" and not place:
            place = "anywhere"  # "remind me when I next land": the next dock, wherever it is
        if when == "arrive" and not place:
            raise NoteError("An arrive reminder needs a place (a system).")
        due = None
        if when == "time":
            due = _due_time(minutes_from_now, at_local_time)
        with self._lock:
            notes = self._load()
            note = {"id": max((n["id"] for n in notes), default=0) + 1, "text": text, "when": when,
                    "place": place, "due": due, "created": datetime.now().isoformat(timespec="seconds"),
                    "done": False}
            note = {k: v for k, v in note.items() if v is not None}
            notes.append(note)
            self._save(notes)
        return note

    def list(self, search: str | None = None, include_done: bool = False) -> list[dict]:
        """Notes not yet done (or all with include_done), best keyword matches first when searching."""
        notes = self._load()
        if not include_done:
            notes = [n for n in notes if not n.get("done")]
        if search:
            # Keyword match on the note and its place, best first, so they never need a note number.
            from .history import _words
            want = _words(search)
            scored = [(len(want & _words(n["text"] + " " + (n.get("place") or ""))), n) for n in notes]
            if not want:  # a query of only small words: fall back to a plain substring match
                scored = [(1, n) for n in notes if search.lower() in n["text"].lower()]
            notes = [n for score, n in sorted(scored, key=lambda sn: -sn[0]) if score]
        return notes

    def forget(self, note_id: int | None = None, search: str | None = None) -> list[dict]:
        """Delete notes by id or by text; returns what was deleted."""
        with self._lock:
            notes = self._load()
            gone = [n for n in notes if (note_id is not None and n["id"] == note_id)
                    or (search and search.lower() in n["text"].lower())]
            if not gone:
                raise NoteError("No matching note.")
            ids = {n["id"] for n in gone}
            self._save([n for n in notes if n["id"] not in ids])
        return gone

    def mark_done(self, ids: set[int]) -> None:
        """Mark fired reminders done, with the time they fired."""
        with self._lock:
            notes = self._load()
            for n in notes:
                if n["id"] in ids:
                    n["done"] = True
                    n["fired"] = datetime.now().isoformat(timespec="seconds")
            self._save(notes)

    def due(self, now: datetime | None = None) -> list[dict]:
        """Time reminders whose moment has passed."""
        now = now or datetime.now()
        return [n for n in self.list() if n["when"] == "time" and n.get("due") and datetime.fromisoformat(n["due"]) <= now]


def _due_time(minutes_from_now: int | None, at_local_time: str | None) -> str:
    """The ISO due time for a time reminder: minutes from now, or the next HH:MM (tomorrow if passed)."""
    now = datetime.now()
    if minutes_from_now:
        return (now + timedelta(minutes=minutes_from_now)).isoformat(timespec="seconds")
    if at_local_time:
        try:
            hh, mm = (int(x) for x in at_local_time.strip().split(":")[:2])
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError as e:
            raise NoteError("at_local_time must look like 21:30.") from e
        if due <= now:
            due += timedelta(days=1)  # "at 9" when it's already 10 means tomorrow
        return due.isoformat(timespec="seconds")
    raise NoteError("A time reminder needs minutes_from_now or at_local_time.")


def _matches(place: str | None, *names: str | None) -> bool:
    """Whether a reminder's place fits where the commander is: "anywhere", or a station or
    system name that contains it (or is contained in it).
    """
    place = (place or "").lower().strip()
    if place in ("anywhere", "any", "any station"):
        return True
    return bool(place) and any(n and (place in n.lower() or n.lower() in place) for n in names)


class Reminders:
    """Speaks reminders when their trigger happens. Subscribe it to a Watcher."""

    def __init__(self, watcher, store: MemoryStore, say: Callable[[str], None], poll_s: float = 15):
        self.watcher, self.store, self.say = watcher, store, say
        self.poll_s = poll_s
        watcher.on("Docked", self._on_docked)
        watcher.on("FSDJump", self._on_arrived)
        watcher.on("CarrierJump", self._on_arrived)
        self._stop = threading.Event()

    def _fire(self, notes: list[dict]) -> None:
        """Mark these reminders done and speak each one."""
        if not notes:
            return
        self.store.mark_done({n["id"] for n in notes})
        for n in notes:
            self.say(f"Reminder. {n['text']}")

    def _on_docked(self, ev) -> None:
        """Dock reminders for this station or system (or anywhere)."""
        if ev.replay:
            return
        station, system = ev.data.get("StationName"), ev.data.get("StarSystem")
        self._fire([n for n in self.store.list() if n["when"] == "dock" and _matches(n.get("place"), station, system)])

    def _on_arrived(self, ev) -> None:
        """Arrive reminders for the system just jumped into."""
        if ev.replay:
            return
        system = ev.data.get("StarSystem")
        self._fire([n for n in self.store.list() if n["when"] == "arrive" and _matches(n.get("place"), system)])

    def startup(self) -> None:
        """Speak startup reminders, and any timed ones that came due while the copilot was off."""
        pending = [n for n in self.store.list() if n["when"] == "startup"] + self.store.due()
        self._fire(pending)

    def start_clock(self) -> None:
        """Check time reminders every poll_s seconds in a background thread."""
        def loop():
            """The clock: a bad file is skipped and the next tick tries again."""
            while not self._stop.wait(self.poll_s):
                try:
                    self._fire(self.store.due())
                except Exception:  # never let a bad file kill the clock
                    time.sleep(self.poll_s)
        threading.Thread(target=loop, name="reminder-clock", daemon=True).start()

    def stop(self) -> None:
        """Stop the reminder clock."""
        self._stop.set()


def describe_note(n: dict) -> str:
    """One note as a short line: "#3 (when you dock at Jameson Memorial): Sell the gold"."""
    when = n.get("when", "none")
    trigger = {"none": "saved note", "dock": "next time you dock" if n.get("place") == "anywhere" else f"when you dock at {n.get('place')}", "startup": "next co-pilot start",
               "arrive": f"when you arrive in {n.get('place')}",
               "time": f"at {(n.get('due') or '')[11:16]} on {(n.get('due') or '')[:10]}"}.get(when, when)
    return f"#{n['id']} ({trigger}): {n['text']}"
