"""A recording that is pure silence is caught, so Vesper can say the mic isn't working."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import numpy as np
    from elite_companion.listen import RATE, is_silent
except ImportError:  # the voice extra isn't installed
    np = None


@unittest.skipIf(np is None, "voice extra not installed")
class SilenceTests(unittest.TestCase):
    def test_pure_silence_is_caught(self):
        self.assertTrue(is_silent(np.zeros(RATE, dtype="float32")))

    def test_any_real_signal_is_not(self):
        audio = np.zeros(RATE, dtype="float32")
        audio[100] = 0.01
        self.assertFalse(is_silent(audio))

    def test_a_button_bump_is_left_to_the_transcriber(self):
        self.assertFalse(is_silent(np.zeros(RATE // 10, dtype="float32")))


if __name__ == "__main__":
    unittest.main()
