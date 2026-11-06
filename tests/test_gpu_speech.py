"""Speech recognition on a graphics card that can't do it, and a failure while hearing a question."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from elite_companion import copilot as copilot_mod, config as config_mod  # noqa: E402
from elite_companion.listen import Transcriber  # noqa: E402
from elite_companion.memory import MemoryStore  # noqa: E402
from elite_companion.watcher import Watcher  # noqa: E402


class FakeWhisper:
    """Loads anywhere, like the real one, then fails on the GPU when it's given work if `missing` is set."""
    made, missing = [], False

    def __init__(self, model, device, compute_type):
        self.device = device
        FakeWhisper.made.append((device, compute_type))

    def transcribe(self, audio, **kw):
        if self.device == "cuda" and FakeWhisper.missing:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return iter(()), None


class FallbackTests(unittest.TestCase):
    def make(self, device, missing):
        FakeWhisper.made, FakeWhisper.missing = [], missing
        with mock.patch.dict(sys.modules, {"faster_whisper": mock.Mock(WhisperModel=FakeWhisper)}):
            return Transcriber("base.en", device)

    def test_missing_libraries_fall_back_to_the_cpu(self):
        t = self.make("cuda", True)
        self.assertTrue(t.fell_back)
        self.assertEqual(FakeWhisper.made, [("cuda", "float16"), ("cpu", "int8")])

    def test_working_gpu_stays_on_the_gpu(self):
        t = self.make("cuda", False)
        self.assertFalse(t.fell_back)
        self.assertEqual(FakeWhisper.made, [("cuda", "float16")])

    def test_cpu_never_tries_the_gpu(self):
        t = self.make("cpu", False)
        self.assertFalse(t.fell_back)
        self.assertEqual(FakeWhisper.made, [("cpu", "int8")])


class SpeechFailureTests(unittest.TestCase):
    def test_a_failure_is_spoken_and_push_to_talk_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = copilot_mod.Copilot(config_mod.load(d / "none.toml"), watcher=Watcher(d), speak=False,
                                    notes=MemoryStore(d / "m.json"), log_dir=None)
            said = []
            p.say = lambda text, *a: said.append(text)
            p._silent_presses = 0
            p.recorder = mock.Mock(_stream=object(), stop=lambda: np.full(16000, 0.1, dtype="float32"))
            p.transcriber = mock.Mock(transcribe=mock.Mock(side_effect=RuntimeError("cublas64_12.dll")))
            p._busy.acquire()
            with mock.patch.object(copilot_mod, "winsound"):
                p._on_release()
            for t in threading.enumerate():
                if t.name == "question":
                    t.join(5)
        self.assertEqual(said, ["Speech recognition failed, so I didn't catch that."])
        self.assertFalse(p._busy.locked())


if __name__ == "__main__":
    unittest.main()
