"""Microphone capture while push-to-talk is held, and local speech-to-text (faster-whisper).

Whisper runs on this PC, so what you say never leaves it until the text is sent to whichever model
answers. The model downloads once on first use (141 MB for base.en or base).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
RATE = 16000
GAME_NAMES = "Elite Dangerous. Vesper, Sol, Spansh, FSD, Onionhead, Anaconda, Cutter. "
ENGLISH_GAME_WORDS = ("Commander, interdict, interdiction, supercruise, Interstellar Factors, material trader, "
                      "neutron, limpets, tritium, Imperial Slaves, sell market, buy market. ")
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


def list_mics() -> list[str]:
    """The microphones Windows has active, for picking one. Each once: the same mic is listed under several
    audio systems, some with the name cut off at 31 characters. WDM-KS also lists inactive jacks, so it's left out."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0
             and "WDM-KS" not in apis[d["hostapi"]]["name"]
             and "Sound Mapper" not in d["name"] and "Primary Sound Capture" not in d["name"]]
    unique = list(dict.fromkeys(names))
    return [n for n in unique if not any(o != n and o.startswith(n) for o in unique)]


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

    def set_mic(self, mic: str) -> bool:
        """Switch microphones. False if push-to-talk is held right now (it switches at the next reconnect)."""
        self.mic = mic
        return self.reconnect()

    def reconnect(self) -> bool:
        """Re-read Windows' list of microphones and find ours again.

        The audio library reads that list once, when Vesper starts. When a headset is unplugged and
        plugged back in (a VR restart does this), the old entry keeps "working" but records pure
        silence, until the list is read again.
        """
        import sounddevice as sd
        with self._lock:
            if self._stream is not None:  # never while recording
                return False
            sd._terminate()
            sd._initialize()
            self.device = find_mic(self.mic) if self.mic else None
        log.info("microphone list re-read; now recording from %s", self.name())
        return True

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


def _find_gpu_libraries() -> None:
    """Point Windows at NVIDIA's libraries from the optional gpu install, which pip puts in site-packages.
    It has to be this call: on this setup, adding those folders to PATH didn't help."""
    for folder in (Path(sys.prefix) / "Lib" / "site-packages" / "nvidia").glob("*/bin"):
        os.add_dll_directory(str(folder))


class Transcriber:
    """Local speech-to-text with faster-whisper. int8 on the CPU, float16 on an NVIDIA GPU.

    If the GPU can't do the work, this uses the CPU instead and sets `fell_back`, so a bad setting
    can't leave push-to-talk failing on every press.
    """
    def __init__(self, model: str = "base.en", device: str = "cpu", language: str = "en"):
        from faster_whisper import WhisperModel
        self.language = language
        self.fell_back = False
        if device != "cpu":
            try:
                _find_gpu_libraries()
                self.model = WhisperModel(model, device=device, compute_type="float16")
                # It loads even without NVIDIA's libraries, which are only needed once it does real work.
                list(self.model.transcribe(np.zeros(RATE, dtype="float32"), beam_size=1, vad_filter=False)[0])
                return
            except Exception:  # noqa: BLE001 - whatever went wrong, the CPU still works
                log.warning("speech recognition can't use %s, using the CPU", device, exc_info=True)
                self.fell_back = True
        self.model = WhisperModel(model, device="cpu", compute_type="int8")

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
