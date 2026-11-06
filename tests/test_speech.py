"""Choosing a voice, using the real Windows speech system where a test needs one."""

import importlib.util
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion.speech import (  # noqa: E402
    CRITICAL, PiperVoice, Speaker, _pick_token, list_voices_and_outputs, piper_voices, speak_to_wav)

REAL_VOICE = next(iter(piper_voices()), None)


class Token:
    def __init__(self, name):
        self.name = name

    def GetDescription(self):
        return self.name


DESKTOP = Token("Microsoft Hazel Desktop - English (Great Britain)")
ONECORE = Token("Microsoft Hazel - English (United Kingdom)")


class PickTokenTests(unittest.TestCase):
    def test_exact_name_beats_an_earlier_partial_match(self):
        self.assertIs(_pick_token([DESKTOP, ONECORE], "microsoft hazel - english (united kingdom)"), ONECORE)

    def test_partial_name_finds_the_first_match(self):
        self.assertIs(_pick_token([DESKTOP, ONECORE], "Hazel"), DESKTOP)

    def test_no_name_and_no_match_pick_nothing(self):
        self.assertIsNone(_pick_token([DESKTOP], ""))
        self.assertIsNone(_pick_token([DESKTOP], "George"))


@unittest.skipUnless(sys.platform == "win32", "Windows speech")
class RealVoiceTests(unittest.TestCase):
    def test_every_windows_voice_can_speak(self):
        voices = [v for v in list_voices_and_outputs()[0] if not v.startswith("Piper ")]
        self.assertTrue(voices)
        with tempfile.TemporaryDirectory() as tmp:
            for name in voices:
                path = str(Path(tmp) / "out.wav")
                speak_to_wav("Fuel is at sixteen tons.", path, name)
                with wave.open(path) as w:
                    self.assertGreater(w.getnframes(), 0, name)


class PiperListTests(unittest.TestCase):
    @unittest.skipIf(importlib.util.find_spec("piper") is None, "Piper not installed")
    def test_lists_files_and_each_speaker_and_skips_broken_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, settings in (("one", "{}"), ("many", '{"speaker_id_map": {"p1": 0, "p2": 1}}')):
                (d / f"{name}.onnx").touch()
                (d / f"{name}.onnx.json").write_text(settings, encoding="utf-8")
            (d / "broken.onnx").touch()
            self.assertEqual(piper_voices(d), ["Piper many p1", "Piper many p2", "Piper one"])


@unittest.skipUnless(sys.platform == "win32", "Windows speech")
class SpeakerTests(unittest.TestCase):
    def test_missing_piper_voice_still_speaks_in_a_windows_voice(self):
        s = Speaker("Piper not-a-real-voice", 0, 0)
        self.assertIsNone(s._piper)
        s.say("Fuel is at sixteen tons.", CRITICAL)
        time.sleep(0.2)
        s.wait_idle(10)
        self.assertFalse(s._busy)


@unittest.skipUnless(REAL_VOICE, "needs Piper and a downloaded voice")
class RealPiperTests(unittest.TestCase):
    """Real voice, real playback through the Windows output, at volume 0 so nothing is heard."""
    LINE = ("Fuel is at sixteen tons. Your hold is worth four million credits. "
            "The best place to sell silver near you is about twenty light years away.")

    def test_rate_changes_the_length_and_volume_zero_is_silent(self):
        voice = PiperVoice(REAL_VOICE)
        normal = b"".join(a for a, _ in voice.lines(self.LINE, 0, 100))
        fast = b"".join(a for a, _ in voice.lines(self.LINE, 10, 100))
        self.assertLess(len(fast), len(normal) * 0.5)
        self.assertTrue(any(normal))
        self.assertFalse(any(b"".join(a for a, _ in voice.lines(self.LINE, 0, 0))))

    def test_plays_for_as_long_as_the_speech_lasts(self):
        s = Speaker(REAL_VOICE, 0, 0)
        self.assertIsNotNone(s._piper)
        start = time.monotonic()
        s.say(self.LINE, CRITICAL)
        time.sleep(0.2)
        s.wait_idle(30)
        self.assertGreater(time.monotonic() - start, 3)

    def test_critical_line_and_stop_cut_it_off(self):
        s = Speaker(REAL_VOICE, 0, 0)
        for interrupt in (lambda: s.say("Interdiction.", CRITICAL), s.stop):
            start = time.monotonic()
            s.say(self.LINE)
            time.sleep(1)
            self.assertTrue(s._busy)
            interrupt()
            time.sleep(0.2)
            s.wait_idle(30)
            self.assertLess(time.monotonic() - start, 6)  # the full line takes about 9 s


if __name__ == "__main__":
    unittest.main()
