"""config.example.toml must list every setting with the same default as config.py."""

import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from elite_companion.config import DEFAULTS  # noqa: E402


class ExampleConfigTests(unittest.TestCase):
    def test_example_matches_defaults(self):
        example = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
        for section, values in DEFAULTS.items():
            self.assertIn(section, example, f"[{section}] missing from config.example.toml")
            for key, default in values.items():
                if key == "rules":  # per-rule overrides are shown as comments
                    continue
                self.assertIn(key, example[section], f"{section}.{key} missing")
                self.assertEqual(example[section][key], default, f"{section}.{key}")
        self.assertEqual(set(example) - set(DEFAULTS), set(), "sections in the example that config.py ignores")


if __name__ == "__main__":
    unittest.main()
