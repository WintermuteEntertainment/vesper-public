"""Mission go-to systems after a redirect, real shipyard lists, and Vesper knowing what she is (no network)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import spansh  # noqa: E402
from elite_companion.brain import SYSTEM  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402


class Round9Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self):
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "memory.json"))

    def test_redirected_mission_points_at_turn_in(self):
        journal(self.d, [
            {"event": "LoadGame", "Commander": "Jameson", "Ship": "type9", "Credits": 1},
            {"event": "MissionAccepted", "MissionID": 1, "LocalisedName": "Assassinate greedseed",
             "DestinationSystem": "Col 285 Sector RZ-F c11-18", "Expiry": "2099-01-02T00:00:00Z"},
            {"event": "MissionRedirected", "MissionID": 1, "NewDestinationStation": "Wales Reach",
             "NewDestinationSystem": "Col 285 Sector HB-D a42-3"},
        ])
        t = self.tools()
        m = t.missions()["missions"][0]
        self.assertEqual(m["go_to_system"], "Col 285 Sector HB-D a42-3")
        self.assertEqual(m["go_to_station"], "Wales Reach")
        self.assertEqual(m["objective_was_in"], "Col 285 Sector RZ-F c11-18")
        self.assertNotIn("destination_system", m)
        self.assertEqual(t.turn_ins(), ["Wales Reach (Col 285 Sector HB-D a42-3)"])

    def test_shipyard_seen_beats_spansh(self):
        (self.d / "Shipyard.json").write_text(
            '{"timestamp":"2099-01-01T00:00:00Z","event":"Shipyard","MarketID":5,"StationName":"Bondar Gateway",'
            '"StarSystem":"Kareldt","PriceList":[{"ShipType":"type9","ShipType_Localised":"Type-9 Heavy","ShipPrice":1}]}')
        journal(self.d, [{"event": "Location", "StarSystem": "Kareldt", "StationName": "Bondar Gateway", "Docked": True}])
        t = self.tools()
        rows = [{"station": "Bondar Gateway", "system": "Kareldt"}, {"station": "Elsewhere", "system": "X"}]
        with mock.patch.object(spansh, "find_ship", return_value=rows):
            out = t.find_ship("Caspian Explorer", "Kareldt")
        self.assertEqual(out["shipyards"][0]["station"], "Elsewhere")
        self.assertIn("NOT in the credit shipyard", out["shipyards"][1]["when_you_looked"])
        self.assertEqual(t.shipyard_here()["ships_for_credits"], {"Type-9 Heavy": 1})

    def test_vesper_knows_what_she_runs_on(self):
        self.assertIn("Claude API", SYSTEM)
        self.assertIn("never the current one", SYSTEM)


if __name__ == "__main__":
    unittest.main()
