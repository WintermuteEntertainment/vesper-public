"""Local-data tools added in 0.4 (no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.tools import TOOL_SPECS, GameTools, _module_name, _rank_name, RANK_NAMES, call_tool  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

TS = "2026-10-25T01:00:00Z"
EVENTS = [
    {"event": "LoadGame", "Commander": "Testbot", "Ship": "cutter", "Ship_Localised": "Imperial Cutter", "ShipID": 5},
    {"event": "Location", "StarSystem": "Shinrarta Dezhra", "SystemAddress": 42, "Docked": True, "StationName": "Jameson Memorial",
     "MarketID": 7},
    {"event": "Loadout", "Ship": "cutter", "ShipID": 5, "ShipName": "Wrath", "MaxJumpRange": 30.0, "CargoCapacity": 352,
     "HullHealth": 1.0, "Rebuy": 100, "HullValue": 1000, "ModulesValue": 500, "Modules": [
         {"Slot": "Slot01_Size6", "Item": "int_fuelscoop_size6_class5", "Health": 0.5},
         {"Slot": "MediumHardpoint4", "Item": "hpt_multicannon_turret_medium",
          "Engineering": {"BlueprintName": "Weapon_Overcharged", "Level": 4, "ExperimentalEffect_Localised": "Incendiary Rounds"}}]},
    {"event": "Materials", "Raw": [{"Name": "iron", "Count": 10}], "Manufactured": [{"Name": "shieldemitters", "Name_Localised": "Shield Emitters", "Count": 3}], "Encoded": []},
    {"event": "MaterialCollected", "Category": "Raw", "Name": "iron", "Count": 5},
    {"event": "EngineerCraft", "Ingredients": [{"Name": "shieldemitters", "Count": 2}]},
    {"event": "Rank", "Combat": 4, "Trade": 10, "Explore": 6, "Empire": 13, "Federation": 13},
    {"event": "Progress", "Combat": 86, "Trade": 41},
    {"event": "Scan", "ScanType": "Detailed", "BodyName": "Shinrarta Dezhra 3", "SystemAddress": 42, "PlanetClass": "Earthlike body",
     "DistanceFromArrivalLS": 812.4, "TerraformState": "", "Landable": False, "WasDiscovered": False},
    {"event": "FSSBodySignals", "BodyName": "Shinrarta Dezhra 3", "SystemAddress": 42,
     "Signals": [{"Type": "$SAA_SignalType_Biological;", "Type_Localised": "Biological", "Count": 2}]},
    {"event": "Market", "MarketID": 7, "StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra"},
    {"event": "Cargo", "Vessel": "Ship", "Count": 4, "Inventory": [{"Name": "gold", "Name_Localised": "Gold", "Count": 4}]},
]
MARKET = {"timestamp": TS, "event": "Market", "MarketID": 7, "StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra", "Items": [
    {"Name_Localised": "Gold", "Category_Localised": "Metals", "BuyPrice": 0, "Stock": 0, "SellPrice": 50000, "Demand": 900, "MeanPrice": 47000},
    {"Name_Localised": "Cobalt", "Category_Localised": "Minerals", "BuyPrice": 2800, "Stock": 9000, "SellPrice": 2700, "Demand": 0, "MeanPrice": 3000},
]}


class LocalToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "Market.json").write_text(json.dumps(MARKET))
        (d / "Journal.2026-10-25T010000.01.log").write_text("".join(json.dumps({"timestamp": TS, **e}) + "\n" for e in EVENTS))
        w = Watcher(d)
        w.catch_up()
        self.t = GameTools(w)

    def tearDown(self):
        self._tmp.cleanup()

    def test_live_market_prices_your_cargo(self):
        m = self.t.market()
        self.assertEqual(m["source"], "your game (live market screen)")
        self.assertEqual(m["your_cargo_here"], [{"commodity": "Gold", "tons": 4, "sell_price": 50000, "value": 200000}])
        self.assertEqual(m["sells_to_you"], ["Cobalt: 2800 cr, stock 9000"])
        self.assertEqual(m["buys_from_you"], ["Gold: 50000 cr, demand 900", "Cobalt: 2700 cr, demand 0"][:1])
        self.assertEqual(self.t.market(commodity="gold")["commodities"][0]["sell_price"], 50000)

    def test_loadout(self):
        lo = self.t.loadout()
        self.assertEqual(lo["modules"][0], {"slot": "Slot01_Size6", "module": "6A Fuel Scoop", "health": "50%"})
        self.assertEqual(lo["modules"][1]["engineering"], "Weapon Overcharged G4 + Incendiary Rounds")
        self.assertEqual(lo["value_cr"], 1500)

    def test_materials_track_changes(self):
        self.assertEqual(self.t.materials()["materials"], {"Raw": {"Iron": 15}, "Manufactured": {"Shield Emitters": 1}})

    def test_profile_ranks(self):
        ranks = self.t.commander_profile()["ranks"]
        self.assertEqual(ranks["Combat"], {"rank": "Expert", "progress_to_next": "86%"})
        self.assertEqual(ranks["Trade"]["rank"], "Elite II")
        self.assertEqual(ranks["Empire"]["rank"], "Prince")

    def test_exploration_scans_and_signals(self):
        body = self.t.exploration()["bodies"][0]
        self.assertEqual(body["type"], "Earthlike body")
        self.assertEqual(body["signals"], {"Biological": 2})
        self.assertFalse(body["was_discovered"])

    def test_every_spec_has_a_handler(self):
        for spec in TOOL_SPECS:
            try:
                call_tool(self.t, spec["name"], {"__probe__": 1})
            except TypeError:
                pass  # handler exists; it just rejected the probe argument
            except Exception as e:  # noqa: BLE001
                self.assertNotIn("Unknown tool", str(e))


class HelperTests(unittest.TestCase):
    def test_module_names(self):
        self.assertEqual(_module_name("int_shieldgenerator_size7_class5_strong"), "7A Shield Generator Strong")
        self.assertEqual(_module_name("hpt_mrascanner_size0_class5"), "0A Pulse Wave Analyser")
        self.assertEqual(_module_name("hpt_beamlaser_gimbal_large"), "Beamlaser Gimbal Large")

    def test_rank_names(self):
        self.assertEqual(_rank_name(RANK_NAMES["Combat"], 8), "Elite")
        self.assertEqual(_rank_name(RANK_NAMES["Combat"], 13), "Elite V")


if __name__ == "__main__":
    unittest.main()
