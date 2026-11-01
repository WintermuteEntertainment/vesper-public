"""Conversation memory: read back the co-pilot's own daily transcripts
(~/.vesper/logs/copilot-YYYY-MM-DD.log), so Vesper can pick up what was said before a
restart, or find when a topic last came up."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import HOME

LOG_DIR = HOME / "logs"
LINE = re.compile(r"^(\d\d:\d\d:\d\d) ([?>!-]+) (.*)$")
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "is", "it", "i", "my", "me", "what",
        "where", "when", "did", "do", "about", "that", "this", "we", "you", "was", "were", "with"}


def _entries(day: date, log_dir: Path = LOG_DIR):
    """One day's transcript as {"when", "who", "text"} entries. Answers that ran onto several lines
    are joined back into one entry.
    """
    path = log_dir / f"copilot-{day:%Y-%m-%d}.log"
    if not path.exists():
        return
    who = {"?": "Commander", ">": "Vesper", "!": "Callout", "--": "System"}
    current = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if m:
            if current:
                yield current
            t, mark, text = m.groups()
            current = {"when": datetime.combine(day, datetime.strptime(t, "%H:%M:%S").time()),
                       "who": who.get(mark, mark), "text": text}
        elif current and raw.strip():  # an answer that ran onto more lines
            current["text"] += " " + raw.strip()
    if current:
        yield current


def recent(minutes: int = 60, max_lines: int = 60, log_dir: Path = LOG_DIR, now: datetime | None = None) -> list[str]:
    """Transcript lines from the last `minutes` (crossing midnight if needed), newest last, system
    lines left out.
    """
    now = now or datetime.now()
    since = now - timedelta(minutes=minutes)
    lines = []
    for day in (since.date(), now.date()) if since.date() != now.date() else (now.date(),):
        for e in _entries(day, log_dir):
            if e["when"] >= since and e["who"] != "System":
                lines.append(f"{e['when']:%H:%M} {e['who']}: {e['text'][:300]}")
    return lines[-max_lines:]


def _words(text: str) -> set[str]:
    """The meaningful words of a text, for matching: lower-case, stop words dropped, simple endings
    (-ing, -ed, -es, -s) trimmed from longer words.
    """
    out = set()
    for w in re.findall(r"[a-z0-9']+", text.lower()):
        if w in STOP:
            continue
        out.add(re.sub(r"(ing|ed|es|s)$", "", w) if len(w) > 4 else w)
    return out


def search(query: str, days: int = 30, max_hits: int = 15, log_dir: Path = LOG_DIR) -> list[str]:
    """Lines (and the reply that followed) sharing words with the query, best matches first."""
    want = _words(query)
    if not want:
        return []
    scored = []
    today = date.today()
    for d in range(days):
        entries = list(_entries(today - timedelta(days=d), log_dir))
        for i, e in enumerate(entries):
            score = len(want & _words(e["text"]))
            if score:
                reply = entries[i + 1] if i + 1 < len(entries) and entries[i + 1]["who"] == "Vesper" and e["who"] == "Commander" else None
                text = f"{e['when']:%Y-%m-%d %H:%M} {e['who']}: {e['text'][:250]}"
                if reply:
                    text += f" / Vesper: {reply['text'][:250]}"
                scored.append((score, e["when"], text))
    scored.sort(key=lambda s: (-s[0], -s[1].timestamp()))
    return [t for _, _, t in scored[:max_hits]]
