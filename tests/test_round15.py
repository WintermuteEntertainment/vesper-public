"""Paste phrasing, sale planner, ship details, ranked hotspots, large pads, unlocks, web-search cost, button log."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import engineers, spansh  # noqa: E402
from elite_companion.brain import request_cost  # noqa: E402
from elite_companion.copilot import COMMANDS  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402


def loadout(ship_id, ship, name, jump, cargo, ts="2099-01-01T00:00:00Z"):
    return {"_ts": ts, "event": "Loadout", "ShipID": ship_id, "Ship": ship, "ShipName": name, "MaxJumpRange": jump,
            "CargoCapacity": cargo, "FuelCapacity": {"Main": 32, "Reserve": 0.6}, "Rebuy": 100, "HullValue": 1000,
            "ModulesValue": 500, "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size7_class5",
                                              "Engineering": {"BlueprintName": "FSD_LongRange", "Level": 5}}]}


def st(station, price, xyz):
    return {"station": station, "system": station + " sys", "distance_ly": 10.0, "distance_to_arrival_ls": 100,
            "sell_price": price, "demand": 9999, "_xyz": xyz}


class Tools(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self, events):
        journal(self.d, [{"event": "LoadGame", "Commander": "Jameson", "Ship": "type9_military", "Credits": 1},
                         {"event": "Location", "StarSystem": "Home", "StarPos": [0, 0, 0]}] + events)
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "m.json"))

    def test_ship_details_for_ships_not_flown(self):
        t = self.tools([loadout(1, "cutter", "HER MAJESTY'S WRATH", 29.8, 320),
                        loadout(2, "type9_military", "Fat Man", 19.6, 256, ts="2099-01-01T01:00:00Z")])
        c = t.ship_details("cutter")
        self.assertEqual((c["ship"], c["jump_range_ly"], c["cargo_capacity"]), ("Imperial Cutter 'HER MAJESTY'S WRATH'", 29.8, 320))
        self.assertIn("Frame Shift Drive", c["modules"][0])
        self.assertEqual(len(t.ship_details()["ships"]), 2)

    def test_two_stop_sale_beats_one_when_worth_it(self):
        t = self.tools([{"event": "Cargo", "Vessel": "Ship", "Inventory": [
            {"Name": "bromellite", "Count": 20}, {"Name": "lowtemperaturediamond", "Count": 10}]}])
        prices = {"Bromellite": [st("Near", 100_000, (5, 0, 0))],
                  "Low Temperature Diamonds": [st("Far", 500_000, (40, 0, 0)), st("Near", 130_000, (5, 0, 0))]}
        with mock.patch.object(spansh, "find_best_price", side_effect=lambda c, *a, **k: prices[c]):
            out = t.plan_sale()
        # best single stop: the diamonds at Far (5M), leaving the bromellite unsold, beats everything at Near (3.3M)
        self.assertEqual(out["one_stop"]["total_value"], 10 * 500_000)
        self.assertEqual(out["one_stop"]["unsold"], ["Bromellite"])
        self.assertEqual(out["two_stops"]["total_value"], 20 * 100_000 + 10 * 500_000)
        self.assertEqual(out["recommend"], "two stops")
        self.assertGreater(out["second_stop_extra_minutes"], 0)

    def test_large_pad_cannot_be_switched_off_for_a_large_ship(self):
        t = self.tools([])
        self.assertTrue(t._large_pad(False))


class HotspotRankTests(unittest.TestCase):
    def test_radius_and_most_hotspots(self):
        def body(name, ly, n):
            return {"system_name": name, "name": name + " 1", "distance": ly, "reserve_level": "Pristine",
                    "rings": [{"name": name + " A Ring", "type": "Icy",
                               "signals": [{"name": "Low Temperature Diamonds", "count": n}]}]}
        page = {"results": [body("A", 5, 1), body("B", 20, 6), body("C", 60, 9)]}
        with mock.patch.object(spansh, "_post", return_value=page):
            rows = spansh.find_ring_hotspots("LTDs", "Home", 5, max_distance_ly=50, rank="most_hotspots")
        self.assertEqual([r["system"] for r in rows], ["B", "A"])


class SmallTests(unittest.TestCase):
    def test_paste_phrasings(self):
        act = lambda t: next((a for p, a in COMMANDS if p.search(t)), None)
        for t in ("paste", "re-paste,", "paste it for me.", "no, paste.", "repaste"):
            self.assertEqual(act(t), "paste", t)
        self.assertIsNone(act("toothpaste"))

    def test_unlocks(self):
        out = engineers.lookup(engineer="The Sarge")
        self.assertIn("Midshipman", out["how_to_unlock"]["invitation"])
        out = engineers.lookup("collector", progress={"The Sarge": {"status": "Unlocked"}})
        sarge = next(e for e in out["engineers"] if e["engineer"] == "The Sarge")
        self.assertNotIn("how_to_unlock", sarge)  # already unlocked

    def test_web_search_cost(self):
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=2))
        self.assertAlmostEqual(request_cost("claude-haiku-5-5", usage), 0.02)

if __name__ == "__main__":
    unittest.main()
