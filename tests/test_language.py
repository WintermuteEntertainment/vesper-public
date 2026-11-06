"""[language] code: which Whisper model, the answer-language rule, and the voice it picks."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import config, language  # noqa: E402


class LanguageTests(unittest.TestCase):
    def test_default_is_english(self):
        self.assertEqual(language.code(config.load(Path("no such file"))), "en")
        self.assertEqual(language.code({"language": {"code": " FR "}}), "fr")

    def test_english_only_models_switch_for_other_languages(self):
        self.assertEqual(language.whisper_model("base.en", "en"), "base.en")
        self.assertEqual(language.whisper_model("base.en", "fr"), "base")
        self.assertEqual(language.whisper_model("small", "de"), "small")

    def test_answer_rule(self):
        self.assertEqual(language.answer_rule("en"), "")
        self.assertIn("Always answer in German", language.answer_rule("de"))
        self.assertIn("Always answer in eo", language.answer_rule("eo"))  # a code with no name still works

    def test_voice_follows_language_when_none_is_set(self):
        try:
            from elite_companion.copilot import _language_voice
        except ImportError:  # the voice extra isn't installed
            self.skipTest("voice extra not installed")
        self.assertEqual(_language_voice({"language": {"code": "en"}}), "")
        self.assertEqual(_language_voice({"language": {"code": "es"}}), "Spanish")

    def test_brain_instructions_carry_the_rule(self):
        try:
            from elite_companion.brain import Brain
        except ImportError:
            self.skipTest("voice extra not installed")
        from elite_companion.memory import MemoryStore
        from elite_companion.tools import GameTools
        from elite_companion.watcher import Watcher
        with tempfile.TemporaryDirectory() as d:
            tools = GameTools(Watcher(Path(d)), MemoryStore(Path(d) / "memory.json"))
            french = Brain(tools, client=object(), usage_log=Path(d) / "usage.jsonl", language="fr")
            english = Brain(tools, client=object(), usage_log=Path(d) / "usage.jsonl")
            self.assertIn("Always answer in French", french._system())
            self.assertNotIn("Always answer in", english._system())


if __name__ == "__main__":
    unittest.main()
