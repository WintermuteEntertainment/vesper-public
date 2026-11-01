"""'Low Temp. Diamonds', selling the hold at one station, restart mishearings, large-pad module search."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import mining, spansh  # noqa: E402
from elite_companion.copilot import COMMANDS  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools, market_name  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402

GAME = [{"event": "LoadGame", "Commander": "Jameson", "Ship": "type9_military", "Credits": 1},
        {"event": "Location", "StarSystem": "T'u Tie"}]


def row(station, price, ly=10.0):
    return {"station": station, "system": "S", "distance_ly": ly, "distance_to_arrival_ls": 100,
            "sell_price": price, "demand": 9999}


class DiamondNameTests(unittest.TestCase):
    def test_prospector_and_refined_names(self):
        w = Watcher(Path("."))
        w.state.apply({"timestamp": "2099-01-01T00:00:00Z", "event": "ProspectedAsteroid", "Content_Localised": "Low",
                       "Remaining": 100.0, "Materials": [
                           {"Name": "LowTemperatureDiamond", "Name_Localised": "Low Temp. Diamonds", "Proportion": 28.0}]})
        p = w.state.mining["last_prospect"]
        self.assertEqual(p["materials"], {"Low Temperature Diamonds": 28.0})
        self.assertEqual(mining.verdict(p), "Mine it")  # was "Skip" on 2026-10-27
        w.state.apply({"timestamp": "2099-01-01T00:01:00Z", "event": "MiningRefined",
                       "Type": "$lowtemperaturediamond_name;", "Type_Localised": "Low Temp. Diamonds"})
        self.assertEqual(w.state.mining["refined"], {"Low Temperature Diamonds": 1})
        self.assertEqual(market_name("Low Temp. Diamonds"), "Low Temperature Diamonds")


class OneStationTests(unittest.TestCase):
    def test_best_single_station_and_its_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            journal(d, GAME + [{"event": "Cargo", "Vessel": "Ship", "Inventory": [
                {"Name": "bromellite", "Count": 10}, {"Name": "lowtemperaturediamond",
                                                      "Name_Localised": "Low Temp. Diamonds", "Count": 10}]}])
            w = Watcher(d)
            w.catch_up()
            t = GameTools(w, MemoryStore(d / "m.json"))
            prices = {"Bromellite": [row("Far", 100), row("Hub", 80)],
                      "Low Temperature Diamonds": [row("Hub", 300), row("Far", 200)]}
            with mock.patch.object(spansh, "find_best_price", side_effect=lambda c, *a, **k: prices[c]):
                out = t.hold_value(one_station=True)
            self.assertEqual(out["sell_all_at"], "Hub, S")
            self.assertEqual(out["total_value"], 10 * 80 + 10 * 300)
            self.assertEqual(out["selling_each_at_its_best_station"], 10 * 100 + 10 * 300)
            self.assertEqual(out["cost_of_one_stop"], 200)
            self.assertIsNone(out["not_bought_there"])


class ModulePadTests(unittest.TestCase):
    def test_large_pad_only(self):
        stations = [{"name": "Black Sanctuary", "system_name": "Shinrarta Dezhra", "has_large_pad": False, "distance": 0,
                     "modules": [{"name": "Detailed Surface Scanner", "class": 1, "rating": "I", "price": 250000}]},
                    {"name": "Kestrel's Nest", "system_name": "Shinrarta Dezhra", "has_large_pad": True, "distance": 0,
                     "modules": [{"name": "Detailed Surface Scanner", "class": 1, "rating": "I", "price": 250000}]}]
        with mock.patch.object(spansh, "_search_raw", return_value=stations) as raw:
            rows = spansh.find_module("Detailed Surface Scanner", "Shinrarta Dezhra", large_pad=True)
        self.assertEqual([r["station"] for r in rows], ["Kestrel's Nest"])
        self.assertEqual(raw.call_args[0][0]["has_large_pad"], {"value": True})


if __name__ == "__main__":
    unittest.main()
