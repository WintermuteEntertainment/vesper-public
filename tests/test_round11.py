"""Mining-run planner, hold value, and the menu-journal fallback (no network)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import spansh  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools, market_name  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402

GAME = [{"event": "LoadGame", "Commander": "Jameson", "Ship": "type9_military", "ShipName": "Fat Man", "Credits": 1},
        {"event": "Location", "StarSystem": "Shinrarta Dezhra", "Docked": True, "StationName": "Jameson Memorial"},
        {"event": "Loadout", "Ship": "type9_military", "ShipID": 1, "ShipName": "Fat Man", "CargoCapacity": 256,
         "MaxJumpRange": 20.0, "Modules": []}]
RING = {"system": "Elboong", "body": "Elboong 3", "ring": "Elboong 3 A Ring", "ring_type": "Metal Rich",
        "hotspots": 1, "overlap": False, "other_hotspots": {}, "reserve": "Pristine", "distance_ly": 11.2,
        "distance_to_arrival_ls": 1834}


def sell(price, ly=20.0):
    return [{"station": "Ferguson Station", "system": "Ngera", "distance_ly": ly, "distance_to_arrival_ls": 2555,
             "sell_price": price, "demand": 5000, "market_updated": "2099-01-01"}]


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self, extra=()):
        journal(self.d, GAME + list(extra))
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "m.json"))

    def test_compares_targets_best_per_hour_first(self):
        t = self.tools()
        prices = {"Platinum": 270000, "Painite": 200000, "Low Temperature Diamonds": 380000}
        with mock.patch.object(spansh, "find_ring_hotspots", side_effect=lambda c, *a, **k: [] if "Diamond" in c else [RING]), \
             mock.patch.object(spansh, "find_best_price", side_effect=lambda c, *a, **k: sell(prices[c])):
            out = t.plan_mining_run(tons_per_hour=100)
        names = [p["commodity"] for p in out["plans"]]
        self.assertEqual(names, ["Platinum", "Painite", "Low Temperature Diamonds"])
        best = out["plans"][0]
        self.assertEqual(out["hold_tons"], 256)  # the whole hold: limpets get used up while mining
        self.assertEqual(best["run_value"], 256 * 270000)
        self.assertEqual(best["minutes"]["mining"], round(256 / 100 * 60))
        self.assertIn("error", out["plans"][2])
        self.assertEqual(out["rate_from"], "you gave it")

    def test_uses_own_session_rate(self):
        refined = [{"_ts": f"2099-01-01T00:{i:02d}:00Z", "event": "MiningRefined", "Type_Localised": "Platinum"}
                   for i in range(0, 60, 5)]  # 12 tons in 55 minutes
        t = self.tools([{"_ts": "2099-01-01T00:00:00Z", "event": "LaunchDrone", "Type": "Prospector"}] + refined)
        with mock.patch.object(spansh, "find_ring_hotspots", return_value=[RING]), \
             mock.patch.object(spansh, "find_best_price", return_value=sell(270000)):
            out = t.plan_mining_run("Platinum", tons=100)
        self.assertEqual(out["rate_from"], "your current session")
        self.assertEqual(out["mining_rate_tph"], 13)

    def test_hold_value(self):
        t = self.tools([{"event": "Cargo", "Vessel": "Ship", "Inventory": [
            {"Name": "platinum", "Count": 100}, {"Name": "lowtemperaturediamond", "Count": 10},
            {"Name": "drones", "Name_Localised": "Limpet", "Count": 20}]}])
        seen = []
        with mock.patch.object(spansh, "find_best_price",
                               side_effect=lambda c, *a, **k: seen.append(c) or sell(1000 if c == "Platinum" else 5000)):
            out = t.hold_value()
        self.assertEqual(sorted(seen), ["Low Temperature Diamonds", "Platinum"])
        self.assertEqual(out["total_value"], 100 * 1000 + 10 * 5000)
        self.assertEqual(market_name("opal"), "Void Opals")


class MenuJournalTests(unittest.TestCase):
    def test_new_journal_at_the_menu_uses_the_previous_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            journal(d, GAME, name="Journal.2099-01-01T000000.01.log")
            journal(d, [{"event": "Fileheader", "part": 1}], name="Journal.2099-01-02T000000.01.log")
            w = Watcher(d)
            w.catch_up()
            self.assertEqual(w.state.location.system, "Shinrarta Dezhra")
            self.assertEqual(w.state.ship.name, "Fat Man")


if __name__ == "__main__":
    unittest.main()
