"""Market cache, EDDN message format and trade rankings (no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import markets, spansh  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools, _trip_minutes  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

MARKET = {"timestamp": "2099-01-01T00:00:00Z", "event": "Market", "MarketID": 42, "StationName": "Here Port",
          "StarSystem": "Here", "Items": [
              {"Name": "$gold_name;", "Name_Localised": "Gold", "Category": "$MARKET_category_metals;",
               "Category_Localised": "Metals", "BuyPrice": 40000, "MeanPrice": 47000, "Stock": 500, "StockBracket": 2,
               "SellPrice": 39000, "Demand": 0, "DemandBracket": 0},
              {"Name": "$mission_name;", "Category": "$MARKET_category_nonmarketable;", "BuyPrice": 0, "MeanPrice": 0,
               "Stock": 0, "SellPrice": 0, "Demand": 0},
          ]}


class MarketTests(unittest.TestCase):
    def test_cache_round_trip_keeps_newest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            markets.save(MARKET, d)
            older = dict(MARKET, MarketID=43, timestamp="2098-01-01T00:00:00Z")
            markets.save(older, d)
            self.assertEqual(markets.load("here port", "Here", d)["MarketID"], 42)
            self.assertIsNone(markets.load("Elsewhere", None, d))

    def test_eddn_message(self):
        msg = markets.eddn_message(MARKET, "Jameson", "4.4.2.1", "r334925/r0 ", True, test=True)
        self.assertTrue(msg["$schemaRef"].endswith("/commodity/3/test"))
        self.assertEqual(msg["header"]["uploaderID"], "Jameson")
        c = msg["message"]["commodities"]
        self.assertEqual([x["name"] for x in c], ["gold"])  # non-marketable dropped, symbol cleaned
        self.assertEqual(c[0]["buyPrice"], 40000)
        self.assertTrue(msg["message"]["odyssey"])


class TradeTests(unittest.TestCase):
    def test_trip_minutes_penalises_far_stations(self):
        self.assertLess(_trip_minutes(20, 30, 10), _trip_minutes(20, 30, 343_000))
        self.assertGreater(_trip_minutes(20, 30, 343_000), 15)

    def test_best_trades_ranks_and_handles_full_hold(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "Market.json").write_text(json.dumps(MARKET))
            events = [{"event": "LoadGame", "Commander": "T", "Ship": "cutter", "ShipID": 1, "Credits": 10**9},
                      {"event": "Loadout", "Ship": "cutter", "ShipID": 1, "CargoCapacity": 100, "MaxJumpRange": 30},
                      {"event": "Location", "StarSystem": "Here", "SystemAddress": 1, "Docked": True, "StationName": "Here Port"},
                      {"event": "Market", "MarketID": 42, "StationName": "Here Port", "StarSystem": "Here"},
                      {"event": "Cargo", "Vessel": "Ship", "Count": 100, "Inventory": [{"Name": "x", "Name_Localised": "X", "Count": 100}]}]
            (d / "Journal.2099-01-01T000000.01.log").write_text(
                "".join(json.dumps({"timestamp": "2099-01-01T00:00:00Z", **e}) + "\n" for e in events))
            w = Watcher(d)
            w.catch_up()
            t = GameTools(w, MemoryStore(d / "memory.json"))
            buyers = [{"station": "Far", "system": "B", "distance_ly": 20, "distance_to_arrival_ls": 300_000,
                       "sell_price": 50000, "demand": 1000, "market_updated": "2099-01-01"}]
            with mock.patch.object(spansh, "find_best_price", return_value=buyers):
                r = t.best_trades()
            self.assertEqual(r["free_cargo_t"], 0)
            self.assertEqual(r["planned_for_t"], 100)  # full hold: plan for an empty one, and say so
            self.assertTrue(r["hold_note"])
            trade = r["trades"][0]
            self.assertEqual((trade["commodity"], trade["tons"], trade["trip_profit"]), ("Gold", 100, 1_000_000))


if __name__ == "__main__":
    unittest.main()
