"""Microphone capture while push-to-talk is held, and local speech-to-text (faster-whisper).

Whisper runs on this PC, so what you say never leaves it until the text
is sent to Claude. The model downloads once (~150 MB for base.en) on first use.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)
RATE = 16000
SILENT_PEAK = 0.001  # loudest sample below this: the mic sent nothing (a muted or virtual mic reads 0.0000)


def is_silent(audio: np.ndarray) -> bool:
    """True for a real press (0.3 s or more) where the microphone sent pure silence, not just quiet: the
    sign of a muted mic or a virtual one (like Steam Streaming Microphone) as the Windows default."""
    return audio.size >= RATE * 0.3 and float(np.abs(audio).max()) < SILENT_PEAK


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
        self.mic = mic
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

    def reconnect(self) -> None:
        """Re-read Windows' list of microphones and find ours again.

        The audio library reads that list once, when Vesper starts. When a headset is unplugged and
        plugged back in (a VR restart does this), the old entry keeps "working" but records pure
        silence, until the list is read again.
        """
        import sounddevice as sd
        with self._lock:
            if self._stream is not None:  # never while recording
                return
            sd._terminate()
            sd._initialize()
            self.device = find_mic(self.mic) if self.mic else None
        log.info("microphone list re-read; now recording from %s", self.name())

    def name(self) -> str:
        """The name of the microphone being recorded (the Windows default unless [listen] mic is set)."""
        import sounddevice as sd
        try:
            return sd.query_devices(sd.default.device[0] if self.device is None else self.device)["name"]
        except Exception:  # noqa: BLE001 - only used in a spoken hint
            return "the default microphone"

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
    def __init__(self, model: str = "base.en", device: str = "cpu"):
        from faster_whisper import WhisperModel
        compute = "int8" if device == "cpu" else "float16"
        self.model = WhisperModel(model, device=device, compute_type=compute)

    def transcribe(self, audio: np.ndarray, hints: list[str] | None = None) -> str:
        """The text of one push-to-talk recording. Under 0.3 s returns "" (a button bump).

        `hints` are names the commander is likely to say right now (systems, stations); they go in Whisper's
        prompt with a list of game words, which makes it far likelier to spell them right.
        """
        if audio.size < RATE * 0.3:  # under 0.3 s: a button bump, not speech
            return ""
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            # Biases recognition toward game words it would otherwise mangle.
            initial_prompt=("Elite Dangerous. Commander, Vesper, Sol, interdict, interdiction, supercruise, "
                            "Interstellar Factors, material trader, Spansh, neutron, FSD, limpets, tritium, Onionhead, "
                            "Imperial Slaves, sell market, buy market, Anaconda, Cutter. "
                            + ", ".join(dict.fromkeys(h for h in (hints or []) if h))[:600]),
        )
        return " ".join(s.text.strip() for s in segments).strip()
