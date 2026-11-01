"""Refit detection (including from older journals) and the ship locker (no network)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import GameTools  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402

from test_phase4 import journal  # noqa: E402


def loadout(modules, ts="2099-01-01T00:00:00Z"):
    return {"_ts": ts, "event": "Loadout", "Ship": "python", "ShipID": 7, "ShipName": "Kestrel",
            "Modules": [{"Slot": slot, "Item": item, **({"Engineering": eng} if eng else {})}
                        for slot, item, eng in modules]}


FSD = ("FrameShiftDrive", "Int_Hyperdrive_Size5_Class5", None)
SHIELD = ("Slot01_Size6", "Int_ShieldGenerator_Size6_Class5", None)


class RefitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tools(self):
        w = Watcher(self.d)
        w.catch_up()
        return GameTools(w, MemoryStore(self.d / "memory.json"))

    def test_first_loadout_compares_with_older_journal(self):
        journal(self.d, [loadout([FSD, SHIELD])], name="Journal.2098-12-31T000000.01.log")
        engineered = ("FrameShiftDrive", "Int_Hyperdrive_Size5_Class5",
                      {"BlueprintName": "FSD_LongRange", "Level": 5, "ExperimentalEffect_Localised": "Mass Manager"})
        journal(self.d, [loadout([engineered, ("Slot01_Size6", "Int_ShieldCellBank_Size6_Class5", None),
                                  ("Slot02_Size4", "Int_FuelScoop_Size4_Class5", None)])])
        refits = self.tools().recent_refits()
        self.assertEqual(len(refits), 1)
        changes = " | ".join(refits[0]["changes"])
        self.assertEqual(refits[0]["ship"], "Kestrel")
        self.assertIn("FrameShiftDrive", changes)
        self.assertIn("Mass Manager", changes)
        self.assertIn("Slot01_Size6", changes)
        self.assertIn("Slot02_Size4: added", changes)

    def test_same_fit_and_old_refits_are_not_reported(self):
        journal(self.d, [loadout([FSD]), loadout([FSD, SHIELD])], name="Journal.2098-12-31T000000.01.log")
        journal(self.d, [loadout([FSD, SHIELD])])
        self.assertEqual(self.tools().recent_refits(), [])


class ShipLockerTests(unittest.TestCase):
    def test_groups_counts_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            journal(d, [{"event": "ShipLocker",
                         "Items": [{"Name": "ionisedgas", "Name_Localised": "Ionised Gas", "Count": 2}],
                         "Components": [{"Name": "graphene", "Name_Localised": "Graphene", "Count": 5}],
                         "Consumables": [{"Name": "healthpack", "Name_Localised": "Medkit", "Count": 3}],
                         "Data": []}])
            w = Watcher(d)
            w.catch_up()
            t = GameTools(w, MemoryStore(d / "memory.json"))
            out = t.ship_locker()
            self.assertEqual(out["ship_locker"]["items"], {"Ionised Gas": 2})
            self.assertEqual(out["ship_locker"]["components"], {"Graphene": 5})
            self.assertEqual(t.ship_locker("med")["ship_locker"]["consumables"], {"Medkit": 3})
            self.assertEqual(t.ship_locker("med")["ship_locker"]["items"], {})


if __name__ == "__main__":
    unittest.main()
