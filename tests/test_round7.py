"""Lookout callouts, hard-question routing and local-mode commands (no audio, no network)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.brain import HARD_PREFIX, _plain  # noqa: E402
from elite_companion.copilot import COMMANDS  # noqa: E402
from elite_companion.lookout import Lookout  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402


def lookout():
    w = Watcher(Path("."))
    said = []
    return w, Lookout(w, lambda text, prio: said.append(text)), said


def ev(name, **data):
    return Event(name, {"timestamp": "2099-01-01T00:00:00Z", "event": name, **data}, "journal")


class LookoutTests(unittest.TestCase):
    def test_mission_expiry_warns_once_per_threshold(self):
        w, lk, said = lookout()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)
        w.state.missions[1] = {"id": 1, "name": "Assassinate Ray Skinner", "destination_system": "Chi Nongoi",
                               "expiry": (now + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.assertEqual(len(lk.check_missions(now)), 1)
        self.assertIn("Chi Nongoi expires in 50 minutes", said[0])
        self.assertEqual(lk.check_missions(now + timedelta(minutes=5)), [])
        self.assertEqual(len(lk.check_missions(now + timedelta(minutes=40))), 1)  # the 15-minute warning
        self.assertEqual(lk.check_missions(now + timedelta(minutes=45)), [])

    def test_late_start_gives_one_warning_not_two(self):
        w, lk, said = lookout()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)
        w.state.missions[2] = {"id": 2, "name": "Deliver", "expiry": (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        lk.check_missions(now)
        lk.check_missions(now)
        self.assertEqual(len(said), 1)

    def test_valuable_scan_once(self):
        w, lk, said = lookout()
        scan = ev("Scan", BodyName="Sol 3", StarSystem="Sol", PlanetClass="Earthlike body", WasDiscovered=False)
        w.dispatch(scan)
        w.dispatch(scan)
        w.dispatch(ev("Scan", BodyName="Sol 5", StarSystem="Sol", PlanetClass="Gas giant with water based life"))
        self.assertEqual(said, ["Valuable scan: 3 is an Earth-like world, and nobody's discovered it yet. Worth mapping."])

    def test_scoop_ahead(self):
        w, lk, said = lookout()
        w.state.ship.fuel_capacity = 32
        w.state.route = [{"StarSystem": "A", "SystemAddress": 1, "StarClass": "M"},
                         {"StarSystem": "B", "SystemAddress": 2, "StarClass": "DA"},
                         {"StarSystem": "C", "SystemAddress": 3, "StarClass": "N"},
                         {"StarSystem": "D", "SystemAddress": 4, "StarClass": "K"}]
        w.state.location.system_address = 2
        w.dispatch(ev("FSDJump", StarSystem="B", FuelLevel=12))
        self.assertEqual(said, ["Fuel at 38 percent. Next scoopable star is 2 jumps out."])
        w.dispatch(ev("FSDJump", StarSystem="B", FuelLevel=20))  # over half a tank: quiet
        self.assertEqual(len(said), 1)

    def test_repeat_interdictions(self):
        w, lk, said = lookout()
        for _ in range(3):
            w.state.interdictions.append({"time": "2099-01-01T00:00:00Z", "by": "Mike Dailly", "faction": "Pirates"})
        w.dispatch(ev("Interdicted", Interdictor_Localised="Mike Dailly", Faction="Pirates"))
        self.assertEqual(said, ["That's the third interdiction tonight, Mike Dailly again."])

    def test_replay_is_silent(self):
        w, lk, said = lookout()
        e = ev("Scan", BodyName="X 1", StarSystem="X", PlanetClass="Water world")
        e.replay = True
        w.dispatch(e)
        self.assertEqual(said, [])


class BrainRoutingTests(unittest.TestCase):
    def test_hard_prefix(self):
        m = HARD_PREFIX.match("Think hard about whether I should engineer my shields")
        self.assertTrue(m)
        self.assertEqual("Think hard about whether"[m.end():], "about whether")
        self.assertIsNone(HARD_PREFIX.match("How much fuel do I have?"))

    def test_plain_drops_tool_turns(self):
        hist = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "{}"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Hello."}]}]
        self.assertEqual(_plain(hist), [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."}])

    def test_local_commands(self):
        def action(text):
            return next((a for p, a in COMMANDS if p.search(text)), None)
        self.assertEqual(action("go local"), "local")
        self.assertEqual(action("local mode"), "local")
        self.assertEqual(action("go cloud"), "cloud")
        self.assertEqual(action("back to claude"), "cloud")


if __name__ == "__main__":
    unittest.main()
