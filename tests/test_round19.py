"""Reading text off screenshots: JSON parsing and the extract call (no network, no screen)."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import vision  # noqa: E402
from elite_companion.brain import LOCAL_BLOCKED  # noqa: E402


class FakeBrain:
    model = "claude-haiku-5-5"

    def __init__(self, reply):
        self.logged, self.sent = [], None
        def create(**kw):
            self.sent = kw
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        self.client = SimpleNamespace(messages=SimpleNamespace(create=create))

    def _log_usage(self, usage, question, model=None):
        self.logged.append(question)


class VisionTests(unittest.TestCase):
    def test_parse_tolerates_fences_and_prose(self):
        self.assertEqual(vision.parse_json('```json\n{"visible": false}\n```'), {"visible": False})
        self.assertEqual(vision.parse_json('Here you go: {"a": 1} hope that helps'), {"a": 1})
        with self.assertRaises(vision.VisionError):
            vision.parse_json("I can't see a contacts list.")

    def test_extract_sends_image_and_logs_cost(self):
        b = FakeBrain('{"visible": true, "contacts": [{"name": "CMDR Foo", "status": "Wanted"}]}')
        out = vision.extract("QUJD", vision.CONTACTS_PROMPT, b, "contacts")
        self.assertEqual(out["contacts"][0]["status"], "Wanted")
        self.assertEqual(b.sent["messages"][0]["content"][0]["source"]["data"], "QUJD")
        self.assertEqual(b.logged, ["[vision] contacts"])

    def test_blocked_in_local_mode(self):
        self.assertIn("read_contacts", LOCAL_BLOCKED)


if __name__ == "__main__":
    unittest.main()
