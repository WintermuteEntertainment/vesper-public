"""Phase 4 fixes: threats, mission cargo, complete markets, echo filtering (no audio, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.copilot import strip_echo  # noqa: E402
from elite_companion.describe import is_noise  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Event, Watcher  # noqa: E402

TS = "2099-01-01T00:00:00Z"  # far future so "recent" filters always include these
HAIL = {"event": "ReceiveText", "From": "$npc_name_decorate:#name=Mike Dailly;", "From_Localised": "Mike Dailly",
        "Message": "$Pirate_HunterHostileSC_Relevant04;", "Message_Localised": "Big haul like that...", "Channel": "npc"}


def journal(d: Path, events, name="Journal.2099-01-01T000000.01.log"):
    (d / name).write_text("".join(json.dumps({"timestamp": e.pop("_ts", TS), **e}) + "\n" for e in events))


class ThreatAndCargoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self):
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "memory.json"))

    def test_mission_cargo_lost_on_death_and_bought_goods_dont_count(self):
        journal(self.d, [
            {"event": "LoadGame", "Commander": "T", "Ship": "type7", "ShipID": 1},
            {"event": "MissionAccepted", "MissionID": 9, "LocalisedName": "Deliver 196 shelters", "Commodity_Localised": "Evacuation Shelter",
             "Count": 196, "Expiry": "2099-01-02T00:00:00Z"},
            {"event": "CargoDepot", "MissionID": 9, "UpdateType": "Collect", "Count": 196, "ItemsCollected": 196, "ItemsDelivered": 0,
             "TotalItemsToDeliver": 196},
            {"event": "Cargo", "Vessel": "Ship", "Count": 196, "Inventory": [
                {"Name": "evacuationshelter", "Name_Localised": "Evacuation Shelter", "MissionID": 9, "Count": 196}]},
            {"event": "Died", "KillerName": "Mike Dailly", "KillerShip": "anaconda", "KillerRank": "Dangerous"},
            {"event": "Cargo", "Vessel": "Ship", "Count": 200, "Inventory": [
                {"Name": "evacuationshelter", "Name_Localised": "Evacuation Shelter", "Count": 200}]},
        ])
        t = self.tools()
        status = t.status()
        self.assertEqual(status["mission_cargo"], [])
        self.assertEqual(status["owned_cargo"], {"Evacuation Shelter": 200})
        m = t.missions()["missions"][0]
        self.assertTrue(m["status"].startswith("MISSION CARGO LOST"))
        self.assertEqual(m["depot"], {"collected": 196, "delivered": 0, "total": 196})

    def test_mission_cargo_split_when_carried(self):
        journal(self.d, [
            {"event": "LoadGame", "Commander": "T", "Ship": "type7", "ShipID": 1},
            {"event": "MissionAccepted", "MissionID": 9, "LocalisedName": "Deliver", "Expiry": "2099-01-02T00:00:00Z"},
            {"event": "Cargo", "Vessel": "Ship", "Count": 10, "Inventory": [
                {"Name": "gold", "Name_Localised": "Gold", "MissionID": 9, "Count": 4},
                {"Name": "gold", "Name_Localised": "Gold", "Count": 6}]},
        ])
        s = self.tools().status()
        self.assertEqual(s["cargo"], {"Gold": 10})
        self.assertEqual(s["owned_cargo"], {"Gold": 6})
        self.assertEqual(s["mission_cargo"], [{"commodity": "Gold", "count": 4}])

    def test_threats_and_summary(self):
        journal(self.d, [
            {"event": "LoadGame", "Commander": "T", "Ship": "cutter", "ShipID": 1},
            {"event": "Died", "KillerName": "Mike Dailly", "KillerShip": "anaconda"},
            dict(HAIL),
            {"event": "ShipTargeted", "TargetLocked": True, "Ship_Localised": "Anaconda", "ScanStage": 3,
             "PilotName_Localised": "Mike Dailly", "PilotRank": "Deadly", "ShieldHealth": 81.4, "HullHealth": 100.0,
             "LegalStatus": "Wanted", "Bounty": 672708},
        ])
        t = self.tools()
        threats = t.threats()
        self.assertEqual(threats["current_target"]["rank"], "Deadly")
        self.assertEqual(threats["current_target"]["shields"], 81)
        mike = threats["hostile_pilots"][0]
        self.assertEqual((mike["ship"], mike["bounty"]), ("Anaconda", 672708))
        self.assertIn("killed_you", mike)
        summary = t.threat_summary()
        self.assertIn("Mike Dailly", summary)
        self.assertIn("has killed you before", summary)
        self.assertIn("Target locked: Anaconda", summary)

    def test_hostile_events_are_not_noise(self):
        self.assertFalse(is_noise(Event("ReceiveText", HAIL, "journal")))
        self.assertTrue(is_noise(Event("ReceiveText", {"Message": "$STATION_NoFireZone_exited;"}, "journal")))
        self.assertFalse(is_noise(Event("ShipTargeted", {"TargetLocked": True, "ScanStage": 3}, "journal")))
        self.assertTrue(is_noise(Event("ShipTargeted", {"TargetLocked": True, "ScanStage": 1}, "journal")))

    def test_complete_market_lists(self):
        items = [{"Name_Localised": f"Item{i}", "Category_Localised": "Legal Drugs" if i < 3 else "Metals",
                  "BuyPrice": 100 + i, "Stock": 10, "SellPrice": 90 + i, "Demand": 5, "MeanPrice": 95} for i in range(40)]
        (self.d / "Market.json").write_text(json.dumps({"timestamp": TS, "event": "Market", "StationName": "X", "StarSystem": "Y",
                                                        "Items": items}))
        journal(self.d, [
            {"event": "LoadGame", "Commander": "T", "Ship": "cutter", "ShipID": 1},
            {"event": "Location", "StarSystem": "Y", "SystemAddress": 1, "Docked": True, "StationName": "X"},
            {"event": "Market", "StationName": "X", "StarSystem": "Y"},
        ])
        t = self.tools()
        m = t.market()
        self.assertEqual(len(m["sells_to_you"]), 40)
        self.assertTrue(m["complete_lists"])
        self.assertEqual(len(t.market(category="drugs")["sells_to_you"]), 3)


class EchoTests(unittest.TestCase):
    def test_strips_own_words_from_the_start(self):
        spoken = ["Go ahead, Commander. Tell me the commodity and I'll check what Jameson Memorial pays for it. "
                  "Keep in mind its market data is from late July."]
        self.assertEqual(strip_echo("Deep in mind its market data is from late July. Non-lethal weapons and reactive armor.", spoken),
                         "Non-lethal weapons and reactive armor.")

    def test_leaves_real_questions_alone(self):
        spoken = ["You're docked at Jameson Memorial."]
        self.assertEqual(strip_echo("What is the market at Jameson Memorial?", spoken), "What is the market at Jameson Memorial?")
        self.assertEqual(strip_echo("Hello there", []), "Hello there")


if __name__ == "__main__":
    unittest.main()
