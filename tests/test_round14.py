"""Engineer lookup (bundled data, no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import engineers  # noqa: E402


class EngineerTests(unittest.TestCase):
    def test_not_engineerable(self):
        out = engineers.lookup("mining laser")
        self.assertFalse(out["engineerable"])

    def test_module_lookup_unlocked_first(self):
        here = engineers.data()["engineers"]["Colonel Bris Dekker"]["coords"]  # sitting in Sol
        out = engineers.lookup("fsd", here=here, progress={"Felicity Farseer": {"status": "Unlocked", "rank": 5}})
        self.assertEqual(out["module"], "Frame Shift Drive")
        rng = next(b for b in out["blueprints"] if "Range" in b["blueprint"])
        self.assertEqual(rng["max_grade"], 5)
        self.assertIn("Mass Manager", out["experimental_effects"])
        self.assertEqual(out["engineers"][0]["engineer"], "Felicity Farseer")  # unlocked beats nearer
        self.assertEqual(out["engineers"][1]["engineer"], "Colonel Bris Dekker")  # then nearest (0 ly)
        self.assertEqual(out["engineers"][1]["distance_ly"], 0.0)

    def test_engineer_lookup_and_names(self):
        self.assertEqual(engineers.engineer_name("Tod 'The Blaster' McQuinn"), "Tod McQuinn")
        self.assertEqual(engineers.module_type("shield boosters"), "Shield Booster")
        out = engineers.lookup(engineer="farseer")
        self.assertEqual(out["system"], "Deciat")
        self.assertIn("Frame Shift Drive", out["offers"])
        self.assertIn("error", engineers.lookup(engineer="Nobody Atall"))

    def test_every_engineer_has_a_base_with_coordinates(self):
        d = engineers.data()
        named = {n for bps in d["modules"].values() for grades in bps.values() for who in grades.values() for n in who}
        self.assertEqual(named - set(d["engineers"]), set())
        self.assertTrue(all(e["coords"] for e in d["engineers"].values()))


if __name__ == "__main__":
    unittest.main()
