"""Every fixed voice command, and the short questions that must NOT trigger one (no network, no audio)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.copilot import COMMAND_MAX_WORDS, COMMANDS  # noqa: E402


def action(text: str) -> str | None:
    """What copilot.handle_text would do with this utterance: a command name, or None (goes to Claude)."""
    low = text.lower()
    if len(low.split()) > COMMAND_MAX_WORDS:
        return None
    return next((a for p, a in COMMANDS if p.search(low)), None)


TRIGGERS = {
    "off": ["Quiet.", "Okay, quiet mode please.", "mute", "Callouts off.", "be quiet", "Silent mode."],
    "critical": ["Critical only.", "critical", "Only critical callouts."],
    "all": ["Callouts on.", "unmute", "Normal mode.", "All callouts."],
    "reset": ["Start over.", "Forget that.", "New conversation, please."],
    "budget": ["Budget?", "How much have we spent today?", "What's the budget?"],
    "paste": ["paste", "Re-paste.", "No, paste.", "Paste it for me.", "paced.", "base,"],
    "local": ["Go local.", "Go, local.", "local mode", "use offline"],
    "cloud": ["Go, cloud.", "Back to Claude.", "cloud mode"],
}
NOT_COMMANDS = ["Is this system quiet?", "Is that critical?", "How is my budget looking?", "Forget that mission.",
                "Can you mute the music?", "Set my home base.", "Restart VR.", "Start over the route planning for Sol and Colonia please"]


class CommandTests(unittest.TestCase):
    def test_every_trigger(self):
        for want, phrases in TRIGGERS.items():
            for p in phrases:
                self.assertEqual(action(p), want, p)

    def test_questions_reach_claude(self):
        for p in NOT_COMMANDS:
            self.assertIsNone(action(p), p)


if __name__ == "__main__":
    unittest.main()
