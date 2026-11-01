"""Notes search, transcript memory, material recognition, Ardent fallback (no network)."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import ardent, history  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.tools import material_kind  # noqa: E402


class NotesSearchTests(unittest.TestCase):
    def test_finds_by_topic_best_first(self):
        with tempfile.TemporaryDirectory() as d:
            m = MemoryStore(Path(d) / "memory.json")
            m.add("Sabine Platform has a bunch of trade missions available.")
            m.add("There's a black market at Jameson Memorial, check it later.")
            m.add("Buy limpets before the mining trip.")
            hits = m.list("where was that black market?")
            self.assertEqual(len(hits), 1)
            self.assertIn("black market", hits[0]["text"])
            self.assertIn("trade missions", m.list("trading missions")[0]["text"])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        today = datetime.now()
        (self.dir / f"copilot-{today:%Y-%m-%d}.log").write_text(
            f"{(today - timedelta(minutes=30)):%H:%M:%S} -- co-pilot started\n"
            f"{(today - timedelta(minutes=20)):%H:%M:%S} ? Where can I get Selenium?\n"
            f"{(today - timedelta(minutes=20)):%H:%M:%S} > Selenium is a raw material.\nIt comes from traders.\n"
            f"{(today - timedelta(minutes=5)):%H:%M:%S} ! Warning. Hostile hail from Beryl Jones.\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_recent_skips_system_lines_and_joins_wrapped_answers(self):
        lines = history.recent(60, 10, self.dir)
        self.assertEqual(len(lines), 3)
        self.assertIn("Vesper: Selenium is a raw material. It comes from traders.", lines[1])

    def test_search_pairs_question_with_answer(self):
        hits = history.search("where get selenium", 3, log_dir=self.dir)
        paired = [h for h in hits if "Commander: Where can I get Selenium?" in h]
        self.assertTrue(paired)
        self.assertIn("Vesper: Selenium is a raw material", paired[0])


class MaterialTests(unittest.TestCase):
    def test_kinds(self):
        held = {"Manufactured": {"shieldemitters": ["Shield Emitters", 111]}}
        self.assertEqual(material_kind("Shield Emitters", held), ("manufactured", "Shield Emitters", 111))
        self.assertEqual(material_kind("selenium", {})[0], "raw")
        self.assertEqual(material_kind("Polymer Capacitors", {})[0], "manufactured")
        self.assertIsNone(material_kind("Silver", {})[0])


class ArdentTests(unittest.TestCase):
    def test_filters_and_sorts(self):
        rows = [
            {"stationName": "Old", "systemName": "A", "distance": 5, "sellPrice": 60000, "demand": 100,
             "updatedAt": "2020-01-01T00:00:00Z", "maxLandingPadSize": 3},
            {"stationName": "Small", "systemName": "B", "distance": 6, "sellPrice": 55000, "demand": 100,
             "updatedAt": "2099-01-01T00:00:00Z", "maxLandingPadSize": 1},
            {"stationName": "Good", "systemName": "C", "distance": 7, "sellPrice": 50000, "demand": 100,
             "updatedAt": "2099-01-01T00:00:00Z", "maxLandingPadSize": 3},
        ]
        with mock.patch.object(ardent, "_get", return_value=rows):
            out = ardent.nearby("Silver", "Sol", True, 30, 10, large_pad=True, max_age_days=30)
        self.assertEqual([r["station"] for r in out], ["Good"])  # old and small-pad ones dropped
        self.assertEqual(out[0]["sell_price"], 50000)
        self.assertEqual(ardent.symbol("Evacuation Shelter"), "evacuationshelter")


if __name__ == "__main__":
    unittest.main()
