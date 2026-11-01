"""The button picker saves its result with config.add_section, which never changes existing settings."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import config  # noqa: E402


class AddSectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "vesper" / "config.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_the_file(self):
        self.assertTrue(config.add_section("ptt", 'key = "F9"', self.path))
        self.assertEqual(config.load(self.path)["ptt"]["key"], "F9")

    def test_appends_to_an_existing_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('[voice]\nvoice = "Zira"', encoding="utf-8")
        self.assertTrue(config.add_section("ptt", 'key = "F9"', self.path))
        cfg = config.load(self.path)
        self.assertEqual((cfg["voice"]["voice"], cfg["ptt"]["key"]), ("Zira", "F9"))

    def test_leaves_an_existing_section_alone(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('[ptt]\nkey = "Mouse 4"\n', encoding="utf-8")
        self.assertFalse(config.add_section("ptt", 'key = "F9"', self.path))
        self.assertEqual(config.load(self.path)["ptt"]["key"], "Mouse 4")


if __name__ == "__main__":
    unittest.main()
