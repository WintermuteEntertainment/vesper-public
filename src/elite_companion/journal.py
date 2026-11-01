"""Find and read Elite Dangerous journal files.

The game appends one JSON object per line to Journal.<timestamp>.<part>.log
and starts a new file each session (and when a file gets too big). It also
keeps a few side files (Status.json, Cargo.json, NavRoute.json, ...) that it
rewrites in place, sometimes catching us mid-write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_DIR = "ED_JOURNAL_DIR"

# Side files the game rewrites, keyed by the journal event that announces it.
SIDE_FILES = {
    "Cargo": "Cargo.json",
    "NavRoute": "NavRoute.json",
    "NavRouteClear": "NavRoute.json",
    "Market": "Market.json",
    "Outfitting": "Outfitting.json",
    "Shipyard": "Shipyard.json",
    "ModuleInfo": "ModulesInfo.json",
    "ShipLocker": "ShipLocker.json",
    "Backpack": "Backpack.json",
    "FCMaterials": "FCMaterials.json",
}


def default_journal_dir() -> Path:
    """Where the game writes its journals: ED_JOURNAL_DIR if set, else
    Saved Games/Frontier Developments/Elite Dangerous in the user's folder.
    """
    override = os.environ.get(ENV_DIR)
    if override:
        return Path(override)
    return Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"


def journal_files(directory: Path) -> list[Path]:
    """Journal files oldest first. Current names sort by their timestamp."""
    return sorted(directory.glob("Journal.*.log"), key=lambda p: (p.name, p.stat().st_mtime))


def latest_journal(directory: Path) -> Path | None:
    """The newest journal file in the folder, or None if there are none."""
    files = journal_files(directory)
    return files[-1] if files else None


def parse_line(line: str) -> dict | None:
    """One journal line as a dict, or None for blank lines, broken JSON or lines with no "event"."""
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    return entry if isinstance(entry, dict) and "event" in entry else None


def read_entries(path: Path) -> list[dict]:
    """Every readable event in one journal file, in order."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return [e for e in map(parse_line, f) if e]


def read_side_file(path: Path, retries: int = 3) -> dict | None:
    """Read a side file the game may be writing right now. None if unreadable."""
    for _ in range(retries):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            continue  # caught mid-write; the next read usually works
    return None


class JournalTail:
    """Follows the newest journal file, returning complete new lines.

    Keeps a byte offset and only returns lines that end in a newline, so a
    line the game is halfway through writing waits for the next poll. When a
    newer journal appears, the rest of the old one is read first.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.path: Path | None = None
        self.offset = 0
        self._partial = b""

    def open_latest(self, from_start: bool = True) -> Path | None:
        """Start following the newest journal: from its first line, or (from_start=False) from its end."""
        self.path = latest_journal(self.directory)
        self._partial = b""
        if self.path is None:
            self.offset = 0
        else:
            self.offset = 0 if from_start else self.path.stat().st_size
        return self.path

    def poll(self) -> list[dict]:
        """Complete events written since the last poll. Switches to a newer journal when one appears,
        after reading what was left of the old one.
        """
        if self.path is None:
            if self.open_latest(from_start=True) is None:
                return []

        entries = self._read_new()
        newest = latest_journal(self.directory)
        if newest is not None and newest != self.path:
            entries += self._read_new()  # anything written just before the switch
            self.path, self.offset, self._partial = newest, 0, b""
            entries += self._read_new()
        return entries

    def _read_new(self) -> list[dict]:
        """Complete new lines from the current file. A half-written last line is kept for next time."""
        try:
            size = self.path.stat().st_size
            if size < self.offset:  # truncated or replaced; start over
                self.offset, self._partial = 0, b""
            if size == self.offset:
                return []
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        self.offset += len(chunk)

        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # empty if the chunk ended on a newline
        return [e for e in (parse_line(l.decode("utf-8", errors="replace")) for l in lines) if e]
