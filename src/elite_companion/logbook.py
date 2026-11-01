"""Captain's log: turns a day's journal into a log entry in the commander's voice, plus a YouTube title,
description and chapters for that night's stream.

    vesper logbook [--date 2026-10-25] [--session 2] [--stream-start 19:05] [--facts-only]

Saved to ~/.vesper/logbook/<date>_<start>.md (and <date>.json with the facts it was written from).
The facts are gathered locally; only the summary of them goes to Claude to be written up.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import history
from .config import HOME
from .journal import journal_files, read_entries

LOGBOOK_DIR = HOME / "logbook"
SCOOP_VALUABLE = {"Earthlike body", "Water world", "Ammonia world"}


def _local(ts: str | None) -> datetime | None:
    """A journal timestamp in local time, or None."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return None


def day_entries(day: date, directory: Path) -> list[dict]:
    """Every journal entry whose local time falls on this day (journals can span midnight)."""
    out = []
    for path in journal_files(directory):
        try:
            stamp = datetime.strptime(path.name.split(".")[1][:10], "%Y-%m-%d").date()
        except (IndexError, ValueError):
            stamp = None
        if stamp and not (day - timedelta(days=1) <= stamp <= day):
            continue
        for e in read_entries(path):
            t = _local(e.get("timestamp"))
            if t and t.date() == day:
                e["_local"] = t
                out.append(e)
    out.sort(key=lambda e: e["_local"])
    return out


def split_sessions(entries: list[dict], gap_minutes: float = 90) -> list[list[dict]]:
    """A day's entries as separate play sessions, split wherever the game was quiet this long."""
    sessions: list[list[dict]] = []
    for e in entries:
        if not sessions or (e["_local"] - sessions[-1][-1]["_local"]).total_seconds() > gap_minutes * 60:
            sessions.append([])
        sessions[-1].append(e)
    return sessions


def session_facts(entries: list[dict], transcript: list[str] | None = None) -> dict:
    """The night in numbers and moments: what a log entry and stream chapters are written from."""
    if not entries:
        return {}
    f: dict = {"start": entries[0]["_local"].strftime("%H:%M"), "end": entries[-1]["_local"].strftime("%H:%M")}
    ships, systems, stations, moments = [], [], [], []
    dist = 0.0
    credits = [e["Credits"] for e in entries if e["event"] == "LoadGame" and "Credits" in e]
    money = Counter()
    accepted, completed, failed = [], [], []
    interdictions, deaths, discoveries, valuable = [], [], [], []
    scans = 0

    def moment(e, text):
        moments.append({"time": e["_local"].strftime("%H:%M"), "what": text})

    for e in entries:
        ev = e["event"]
        if ev == "LoadGame":
            ship = e.get("ShipName") or e.get("Ship_Localised") or e.get("Ship")
            if ship and ship not in ships:
                ships.append(ship)
        elif ev == "FSDJump":
            if not systems:
                moment(e, f"First jump, to {e.get('StarSystem')}")
            systems.append(e.get("StarSystem"))
            dist += e.get("JumpDist", 0)
        elif ev == "Docked":
            stations.append(f"{e.get('StationName')}, {e.get('StarSystem')}")
            moment(e, f"Docked at {e.get('StationName')}")
        elif ev == "MissionAccepted":
            accepted.append(e.get("LocalisedName"))
        elif ev == "MissionCompleted":
            completed.append({"mission": e.get("LocalisedName") or e.get("Name"), "reward": e.get("Reward", 0)})
            money["missions"] += e.get("Reward", 0)
            moment(e, f"Completed: {e.get('LocalisedName') or e.get('Name')}")
        elif ev in ("MissionFailed", "MissionAbandoned"):
            failed.append(e.get("LocalisedName") or e.get("Name"))
        elif ev == "Bounty":
            money["bounties"] += e.get("TotalReward", e.get("Reward", 0))
            moment(e, f"Killed {e.get('PilotName_Localised') or e.get('Target_Localised') or e.get('Target')}")
        elif ev == "RedeemVoucher":
            money[f"vouchers ({e.get('Type')})"] += e.get("Amount", 0)
        elif ev == "MarketSell":
            money["trade profit"] += e.get("TotalSale", 0) - e.get("AvgPricePaid", 0) * e.get("Count", 0)
        elif ev in ("SellExplorationData", "MultiSellExplorationData"):
            money["exploration data"] += e.get("TotalEarnings", e.get("BaseValue", 0))
        elif ev == "Interdicted":
            who = e.get("Interdictor_Localised") or e.get("Interdictor")
            interdictions.append({"by": who, "submitted": e.get("Submitted")})
            moment(e, f"Interdicted by {who}")
        elif ev == "Died":
            killer = e.get("KillerName_Localised") or e.get("KillerName") or \
                ", ".join(k.get("Name", "") for k in e.get("Killers", [])) or "unknown"
            deaths.append(killer)
            moment(e, f"Ship destroyed by {killer}")
        elif ev == "Scan" and e.get("ScanType") != "NavBeaconDetail":
            scans += 1
            if e.get("WasDiscovered") is False and e.get("PlanetClass"):
                discoveries.append(e.get("BodyName"))
            if e.get("PlanetClass") in SCOOP_VALUABLE or e.get("TerraformState") == "Terraformable":
                valuable.append(f"{e.get('BodyName')} ({e.get('PlanetClass')})")
        elif ev in ("ShipyardSwap", "ShipyardBuy"):
            moment(e, f"Switched to {e.get('ShipType_Localised') or e.get('ShipType')}")

    f.update({k: v for k, v in {
        "ships": ships, "jumps": len(systems), "light_years": round(dist), "systems": systems[:40],
        "docked_at": list(dict.fromkeys(stations)), "credits_start": credits[0] if credits else None,
        "credits_end": credits[-1] if len(credits) > 1 else None, "earned": dict(money),
        "missions_accepted": accepted, "missions_completed": completed, "missions_failed": failed,
        "interdictions": interdictions, "deaths": deaths, "bodies_scanned": scans,
        "first_discoveries": discoveries[:15], "valuable_bodies": valuable[:15],
        "moments": moments[:60], "co_pilot_conversation": (transcript or [])[-80:],
    }.items() if v not in (None, [], {}, 0)})
    return f


def _add_stream_offsets(facts: dict, stream_start: str | None, day: date) -> None:
    """YouTube chapter times from the stream start on each moment, worked out here rather than by the
    model, which gets the arithmetic wrong."""
    start = datetime.combine(day, datetime.strptime(stream_start or facts["start"], "%H:%M").time())
    for m in facts.get("moments", []):
        secs = (datetime.combine(day, datetime.strptime(m["time"], "%H:%M").time()) - start).total_seconds()
        if secs >= 0:
            h, rem = divmod(int(secs), 3600)
            m["stream"] = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


PROMPT = """\
Below are the facts of one Elite Dangerous session by Commander {commander}, taken from their game journal and \
their voice co-pilot's transcript. Write three things.

1. A captain's log entry, first person, as {commander}. 150 to 300 words. Lean and grounded: what they set out \
to do, what happened, the close calls, how it ended. Use real names of systems, stations and pilots from \
the facts and invent nothing. Style rules: almost no em dashes, no run-on sentences, no purple prose.
2. A YouTube title (under 70 characters) and a 2 to 4 sentence description for the stream of this session.
3. YouTube chapters. Each moment has a "stream" offset already worked out: copy it exactly. The first \
chapter must be 0:00. Pick 5 to 12 moments worth jumping to, written as "<stream offset> Title". Skip \
moments with no stream offset (they happened before the stream started).

Answer in Markdown with exactly these headings: ## Captain's log, ## YouTube title, ## Description, ## Chapters

Facts (JSON):
{facts}
"""


def write(day: date | None = None, stream_start: str | None = None, directory: Path | None = None,
          client=None, model: str = "claude-sonnet-5-5", facts_only: bool = False,
          out_dir: Path = LOGBOOK_DIR, session: int = -1, whole_day: bool = False) -> Path:
    """Write a captain's log for one play session (or the whole day) to the logbook folder.
    Gathers the session's facts and conversation, then has Claude write the log, with chapter
    times from stream_start and a suggested video title. facts_only skips Claude.
    """
    from .watcher import Watcher
    day = day or date.today()
    directory = directory or Watcher().directory
    entries = day_entries(day, directory)
    if not entries:
        raise ValueError(f"No journal entries for {day}.")
    if not whole_day:
        entries = split_sessions(entries)[session]
    start, end = entries[0]["_local"].replace(tzinfo=None), entries[-1]["_local"].replace(tzinfo=None)
    transcript = [t for t in history.recent((end - start).total_seconds() / 60 + 5, 200, now=end)]
    facts = session_facts(entries, transcript)
    _add_stream_offsets(facts, stream_start, day)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{day}" if whole_day else f"{day}_{facts['start'].replace(':', '')}"
    (out_dir / f"{name}.json").write_text(json.dumps(facts, indent=1, default=str), encoding="utf-8")
    if facts_only:
        return out_dir / f"{name}.json"
    import anthropic
    from .brain import USAGE_LOG, has_credentials, request_cost
    has_credentials()  # loads the key from the Windows user variables if needed
    client = client or anthropic.Anthropic(max_retries=2, timeout=120)
    response = client.messages.create(
        model=model, max_tokens=3000, output_config={"effort": "medium"},
        messages=[{"role": "user", "content": PROMPT.format(facts=json.dumps(facts, default=str), commander=facts.get("commander") or "the commander")}])
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    cost = request_cost(model, response.usage)
    with open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date.today().isoformat(), "time": datetime.now().isoformat(timespec="seconds"),
                            "model": model, "input": response.usage.input_tokens,
                            "output": response.usage.output_tokens, "cost_usd": round(cost, 6),
                            "question": f"[logbook] {day}"}) + "\n")
    path = out_dir / f"{name}.md"
    path.write_text(f"# {day}: session {facts['start']} to {facts['end']}\n\n{text}\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    """`vesper logbook`: write a log from the command line."""
    import argparse
    p = argparse.ArgumentParser(prog="vesper logbook", description="Write a captain's log for a day")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.add_argument("--stream-start", help="HH:MM local time the stream started, for chapters (default: first event)")
    p.add_argument("--facts-only", action="store_true", help="just gather the facts (no Claude call)")
    p.add_argument("--session", type=int, default=-1,
                   help="which session of the day: 1 = first, -1 = latest (default). Sessions split at 90-minute gaps")
    p.add_argument("--whole-day", action="store_true", help="one log for the whole day")
    p.add_argument("--model", default="claude-sonnet-5-5")
    a = p.parse_args(argv)
    path = write(date.fromisoformat(a.date) if a.date else None, a.stream_start, model=a.model,
                 facts_only=a.facts_only, session=a.session - 1 if a.session > 0 else a.session,
                 whole_day=a.whole_day)
    print(path.read_text(encoding="utf-8") if path.suffix == ".md" else f"Facts saved to {path}")
    return 0
