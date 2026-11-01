"""Microphone capture while push-to-talk is held, and local speech-to-text (faster-whisper).

Whisper runs on this PC, so what you say never leaves it until the text
is sent to Claude. The model downloads once on first use (141 MB for base.en or base).
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)
RATE = 16000
GAME_NAMES = "Elite Dangerous. Vesper, Sol, Spansh, FSD, Onionhead, Anaconda, Cutter. "
ENGLISH_GAME_WORDS = ("Commander, interdict, interdiction, supercruise, Interstellar Factors, material trader, "
                      "neutron, limpets, tritium, Imperial Slaves, sell market, buy market. ")


def find_mic(name: str) -> int | None:
    """The sounddevice index of the first input device whose name contains `name`, or None."""
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    return None


class Recorder:
    """Records the microphone between start() and stop(), at 16 kHz mono (what Whisper expects)."""
    def __init__(self, mic: str = ""):
        self.device = find_mic(mic) if mic else None
        if mic and self.device is None:
            log.warning("no microphone matching %r; using the Windows default", mic)
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Open the microphone and start collecting audio."""
        import sounddevice as sd
        with self._lock:
            self._chunks = []
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=self.device,
                                          callback=lambda data, frames, t, status: self._chunks.append(data.copy()))
            self._stream.start()

    def stop(self) -> np.ndarray:
        """Close the microphone and return everything recorded since start() as one float array."""
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            return np.concatenate(self._chunks)[:, 0] if self._chunks else np.zeros(0, dtype="float32")


class Transcriber:
    """Local speech-to-text with faster-whisper. int8 on the CPU, float16 on an NVIDIA GPU."""
    def __init__(self, model: str = "base.en", device: str = "cpu", language: str = "en"):
        from faster_whisper import WhisperModel
        self.language = language
        compute = "int8" if device == "cpu" else "float16"
        self.model = WhisperModel(model, device=device, compute_type=compute)

    def transcribe(self, audio: np.ndarray, hints: list[str] | None = None) -> str:
        """The text of one push-to-talk recording. Under 0.3 s returns "" (a button bump).

        `hints` are names the commander is likely to say right now (systems, stations); they go in Whisper's
        prompt with a list of game words, which makes it far likelier to spell them right.
        """
        if audio.size < RATE * 0.3:  # under 0.3 s: a button bump, not speech
            return ""
        # Biases recognition toward game words it would otherwise mangle. In other languages only the names
        # go in: a prompt full of English phrases nudges Whisper to hear (or translate into) English.
        words = GAME_NAMES + (ENGLISH_GAME_WORDS if self.language == "en" else "")
        segments, _ = self.model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True,
            initial_prompt=words + ", ".join(dict.fromkeys(h for h in (hints or []) if h))[:600],
        )
        return " ".join(s.text.strip() for s in segments).strip()
