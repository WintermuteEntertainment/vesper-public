"""Captain's log facts and stream chapters, and the dashboard's JSON (no network)."""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import logbook  # noqa: E402
from elite_companion.dashboard import dashboard_state  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402


def at(hhmm, event, **data):
    return {"event": event, "_local": datetime(2099, 1, 1, *map(int, hhmm.split(":"))).astimezone(), **data}


class LogbookTests(unittest.TestCase):
    def test_sessions_split_on_gaps(self):
        entries = [at("00:10", "LoadGame"), at("01:00", "Docked"), at("09:00", "LoadGame"), at("09:30", "FSDJump")]
        sessions = logbook.split_sessions(entries)
        self.assertEqual([len(s) for s in sessions], [2, 2])

    def test_facts_and_chapter_offsets(self):
        entries = [at("19:00", "LoadGame", Credits=100, ShipName="Kestrel"),
                   at("19:05", "FSDJump", StarSystem="Sol", JumpDist=10.4),
                   at("19:30", "Interdicted", Interdictor_Localised="Mike Dailly", Submitted=True),
                   at("20:40", "MissionCompleted", LocalisedName="Deliver Tea", Reward=5000),
                   at("20:45", "Scan", BodyName="Sol 3", PlanetClass="Earthlike body", WasDiscovered=False)]
        f = logbook.session_facts(entries)
        self.assertEqual(f["jumps"], 1)
        self.assertEqual(f["earned"], {"missions": 5000})
        self.assertEqual(f["valuable_bodies"], ["Sol 3 (Earthlike body)"])
        logbook._add_stream_offsets(f, "19:02", date(2099, 1, 1))
        stream = {m["what"]: m.get("stream") for m in f["moments"]}
        self.assertEqual(stream["First jump, to Sol"], "3:00")
        self.assertEqual(stream["Completed: Deliver Tea"], "1:38:00")


class DashboardTests(unittest.TestCase):
    def test_state_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            journal(d, [{"event": "LoadGame", "Commander": "Jameson", "Ship": "python", "ShipName": "Kestrel",
                         "Credits": 5, "FuelLevel": 10, "FuelCapacity": 32}])
            w = Watcher(d)
            w.catch_up()
            out = json.loads(json.dumps(dashboard_state(GameTools(w, MemoryStore(d / "m.json"))), default=str))
            self.assertEqual(out["commander"], "Jameson")
            self.assertIn("route", out)
            self.assertIn("pips", out)


if __name__ == "__main__":
    unittest.main()
