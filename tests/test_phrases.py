"""The phrase table and the locale files: complete, well-formed, and used for fixed commands."""

import json
import string
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import phrases  # noqa: E402
from elite_companion.phrases import EN, LOCALES, plural_category, t  # noqa: E402

EXTRA_FIELDS = {"interdictions_tonight": {"n"}}  # passed by the code though English doesn't use it
ACTIONS = {"off", "critical", "all", "reset", "budget", "paste", "local", "cloud"}


def fields(text) -> set[str]:
    """The placeholder names in a phrase (all plural forms together)."""
    texts = text.values() if isinstance(text, dict) else [text]
    return {f for s in texts for _, f, _, _ in string.Formatter().parse(s) if f}


class LocaleFileTests(unittest.TestCase):
    def setUp(self):
        self.files = sorted(LOCALES.glob("*.json"))

    def tearDown(self):
        phrases.set_language("en")

    def test_the_drafts_exist(self):
        self.assertEqual({f.stem for f in self.files}, {"fr", "de", "es", "ru", "pt"})

    def test_every_file_is_complete_and_well_formed(self):
        for f in self.files:
            with self.subTest(locale=f.stem):
                data = json.loads(f.read_text(encoding="utf-8"))
                self.assertIn("Draft by Claude", data["_meta"]["status"])
                table = data["phrases"]
                self.assertEqual(set(table) - set(EN), set(), "keys that English doesn't have")
                missing = {k for k in EN if k not in table and not k.startswith("ordinal_")}
                self.assertEqual(missing, set(), "untranslated keys")
                for key, text in table.items():
                    self.assertLessEqual(fields(text), fields(EN[key]) | EXTRA_FIELDS.get(key, set()),
                                         f"{key}: unknown placeholder")
                    if isinstance(text, dict):  # every plural form the language needs
                        need = {plural_category(n, f.stem) for n in range(0, 112)}
                        self.assertLessEqual(need, set(text), f"{key}: plural forms")
                self.assertLessEqual(set(data["commands"]), ACTIONS)

    def test_every_phrase_renders_in_every_language(self):
        for code in ["en"] + [f.stem for f in self.files]:
            phrases.set_language(code)
            for key, text in EN.items():
                kw = {name: name for name in fields(text) | EXTRA_FIELDS.get(key, set())}
                for n in (0, 1, 2, 5, 21):
                    kw["n"] = n
                    with self.subTest(locale=code, key=key, n=n):
                        self.assertTrue(t(key, **kw))


class PhraseTests(unittest.TestCase):
    def tearDown(self):
        phrases.set_language("en")

    def test_english_by_default(self):
        self.assertEqual(t("hull", percent=40), "Hull 40 percent.")
        self.assertEqual(t("jumps_left", n=1), "1 jump remaining.")
        self.assertEqual(t("jumps_left", n=3), "3 jumps remaining.")

    def test_russian_plurals(self):
        phrases.set_language("ru")
        self.assertEqual([plural_category(n) for n in (1, 3, 5, 11, 21, 22, 25)],
                         ["one", "few", "many", "many", "one", "few", "many"])
        self.assertIn("прыжка", t("jumps_left", n=3))

    def test_missing_language_falls_back_to_english(self):
        phrases.set_language("eo")
        self.assertEqual(t("shields_down"), "Shields down.")

    def test_translation_is_used(self):
        phrases.set_language("fr")
        self.assertEqual(t("shields_down"), "Boucliers tombés.")


class TranslatedCommandTests(unittest.TestCase):
    def setUp(self):
        try:
            from elite_companion.copilot import translated_command
        except ImportError:
            self.skipTest("voice extra not installed")
        self.cmd = translated_command

    def tearDown(self):
        phrases.set_language("en")

    def test_whole_phrase_with_padding(self):
        phrases.set_language("fr")
        self.assertEqual(self.cmd("Silence."), "off")
        self.assertEqual(self.cmd("Vesper, mode silencieux s'il te plaît"), "off")
        self.assertEqual(self.cmd("Nouvelle conversation"), "reset")

    def test_a_question_containing_a_command_word_is_not_one(self):
        phrases.set_language("fr")
        self.assertIsNone(self.cmd("Est-ce que ce système est en silence ?"))

    def test_cyrillic(self):
        phrases.set_language("ru")
        self.assertEqual(self.cmd("Тишина, пожалуйста"), "off")

    def test_english_has_no_translated_commands(self):
        self.assertIsNone(self.cmd("silence"))


class CopilotLanguageTests(unittest.TestCase):
    """A French copilot: French fixed commands and replies, and the set_callouts tool."""
    def setUp(self):
        try:
            from elite_companion.copilot import Copilot
        except ImportError:
            self.skipTest("voice extra not installed")
        import tempfile
        from elite_companion import config
        from elite_companion.memory import MemoryStore
        from elite_companion.watcher import Watcher
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        cfg = config.load(d / "none.toml")
        cfg["language"]["code"] = "fr"
        self.pilot = Copilot(cfg, watcher=Watcher(d), speak=False, notes=MemoryStore(d / "memory.json"),
                             log_dir=d / "logs")

    def tearDown(self):
        self._tmp.cleanup()
        phrases.set_language("en")

    def test_french_command_and_reply(self):
        self.assertEqual(self.pilot.handle_text("Silence, s'il te plaît."), "Annonces désactivées.")
        self.assertEqual(self.pilot.callouts.mode, "off")

    def test_set_callouts_tool(self):
        self.assertEqual(self.pilot._set_callouts("critical"), {"callouts": "critical"})
        self.assertEqual(self.pilot.callouts.mode, "critical")
        self.assertIn("error", self.pilot._set_callouts("loud"))


if __name__ == "__main__":
    unittest.main()
