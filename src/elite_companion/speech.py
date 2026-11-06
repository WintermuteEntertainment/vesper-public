"""Text to speech on a background thread: Windows' built-in voices (SAPI), or Piper's offline voices played
through SAPI, so the output device and interruptions work the same for both.

Critical lines interrupt whatever is being said; ordinary lines queue, and
are dropped if they've waited too long to still be useful.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import logging
import queue
import threading
import time

from .config import HOME

log = logging.getLogger(__name__)

CRITICAL, NORMAL = 0, 1
STALE_AFTER_S = 8.0     # ordinary lines older than this are skipped
SVSF_ASYNC, SVSF_PURGE = 1, 2
# Windows' newer voices. SAPI works with them but doesn't list them unless asked.
ONECORE_VOICES = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
PIPER_DIR = HOME / "voices"
PIPER_PREFIX = "Piper "  # a voice setting like "Piper en_GB-vctk-medium p239": file name, then speaker if it has several
PIPER_THREADS = 2        # the default uses about 10 cores in bursts, which could stutter VR. 2 starts in about 0.4 s
SAPI_FORMATS = {16000: 18, 22050: 22, 24000: 26, 44100: 34, 48000: 38}  # sample rate -> SAFT..16BitMono


def _tokens(collection) -> list:
    return [collection.Item(i) for i in range(collection.Count)]


def _pick_token(tokens: list, wanted: str):
    """An exact name wins, so "Microsoft Hazel - ..." doesn't find "Microsoft Hazel Desktop"; then part of a name."""
    wanted = (wanted or "").lower()
    if not wanted:
        return None
    names = [tok.GetDescription().lower() for tok in tokens]
    for matches in (lambda n: n == wanted, lambda n: wanted in n):
        for tok, name in zip(tokens, names):
            if matches(name):
                return tok
    log.warning("no voice/device matching %r", wanted)
    return None


def _voice_tokens(voice) -> list:
    """The classic voices first, so a partial name saved before these were added still finds the same voice."""
    tokens = _tokens(voice.GetVoices())
    try:
        import win32com.client
        category = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
        category.SetId(ONECORE_VOICES, False)
        tokens += _tokens(category.EnumerateTokens())
    except Exception:  # noqa: BLE001 - older Windows: the classic voices still work
        log.warning("couldn't list the newer Windows voices", exc_info=True)
    return tokens


def piper_voices(directory=None) -> list[str]:
    """Every Piper voice downloaded to ~/.vesper/voices, if Piper is installed."""
    if importlib.util.find_spec("piper") is None:
        return []
    names = []
    for model in sorted((directory or PIPER_DIR).glob("*.onnx")):
        try:
            speakers = json.loads(model.with_name(model.name + ".json").read_text(encoding="utf-8")).get("speaker_id_map")
        except (OSError, ValueError):
            log.warning("skipping Piper voice %s: its settings file is missing or unreadable", model.name)
            continue
        names += [f"{PIPER_PREFIX}{model.stem} {s}" for s in speakers] if speakers else [PIPER_PREFIX + model.stem]
    return names


class PiperVoice:
    """One loaded Piper voice. Raises if it can't load, so the caller can fall back to a Windows voice."""

    def __init__(self, label: str, directory=None):
        import onnxruntime
        from piper import PiperVoice as Loader
        self.label = label
        key, _, speaker = label[len(PIPER_PREFIX):].partition(" ")
        path = str((directory or PIPER_DIR) / f"{key}.onnx")
        self.voice = Loader.load(path)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = PIPER_THREADS, 1
        self.voice.session = onnxruntime.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
        self.speaker_id = self.voice.config.speaker_id_map[speaker] if speaker else None

    def lines(self, text: str, rate: int, volume: int):
        """Each sentence's audio as it's ready: (16-bit mono samples, sample rate). The rate is on SAPI's scale,
        where +10 is 3 times as fast and -10 a third as fast."""
        from piper import SynthesisConfig
        config = SynthesisConfig(speaker_id=self.speaker_id, volume=volume / 100,
                                 length_scale=self.voice.config.length_scale / 3 ** (rate / 10))
        for chunk in self.voice.synthesize(text, syn_config=config):
            yield chunk.audio_int16_bytes, chunk.sample_rate


def _sapi_stream(samples: bytes, sample_rate: int):
    import win32com.client
    stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
    stream.Format.Type = SAPI_FORMATS[sample_rate]
    stream.SetData(samples)
    return stream


def list_voices_and_outputs() -> tuple[list[str], list[str]]:
    """Installed voices (Windows and Piper) and audio outputs, for `vesper copilot --voices` and the Settings page."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()  # this can run on any thread (the dashboard's, for one)
    v = win32com.client.Dispatch("SAPI.SpVoice")
    voices = [tok.GetDescription() for tok in _voice_tokens(v)] + piper_voices()
    outputs = [tok.GetDescription() for tok in _tokens(v.GetAudioOutputs())]
    return voices, outputs


class Speaker:
    """Speaks text with Windows SAPI on its own thread, in priority order.

    Critical lines interrupt whatever is being said; anything else waits its turn and is dropped
    if it has waited longer than STALE_AFTER_S. `recent` keeps the last few lines, so the
    listener can strip Vesper's own voice from what the microphone heard.
    """
    def __init__(self, voice: str = "", rate: int = 1, volume: int = 100, output_device: str = ""):
        self.settings = (voice, rate, volume, output_device)
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._busy = False
        self._stop_now = threading.Event()
        self._changed = threading.Event()
        self._piper: PiperVoice | None = None
        self.recent: list[str] = []  # last few lines spoken, for echo filtering
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(10)

    def update(self, voice: str, rate: int, volume: int, output_device: str) -> None:
        """New voice settings, used from the next line spoken."""
        self.settings = (voice, rate, volume, output_device)
        self._changed.set()

    def say(self, text: str, priority: int = NORMAL) -> None:
        """Queue text to speak at this priority (CRITICAL or NORMAL)."""
        self.recent.append(text)
        del self.recent[:-5]
        self._queue.put((priority, next(self._seq), time.monotonic(), text))

    def stop(self) -> None:
        """Cut off whatever is being said and drop anything queued (push-to-talk barge-in),
        so the mic doesn't hear the copilot's own voice."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._stop_now.set()

    def wait_idle(self, timeout: float = 30) -> None:
        """Block until the queue is empty and nothing is being spoken (or the timeout)."""
        deadline = time.monotonic() + timeout
        while (not self._queue.empty() or self._busy) and time.monotonic() < deadline:
            time.sleep(0.05)

    def _make_voice(self):
        """A SAPI voice with the configured voice, output device, rate and volume."""
        import win32com.client
        v = win32com.client.Dispatch("SAPI.SpVoice")
        name, rate, volume, device = self.settings
        if name.startswith(PIPER_PREFIX):
            if not self._piper or self._piper.label != name:
                self._piper = None
                try:
                    self._piper = PiperVoice(name)
                except Exception:  # noqa: BLE001 - alerts must still be spoken, in the Windows default voice
                    log.exception("couldn't load the voice %r, using the Windows default", name)
        else:
            self._piper = None
            if tok := _pick_token(_voice_tokens(v), name):
                v.Voice = tok
        if tok := _pick_token(_tokens(v.GetAudioOutputs()), device):
            v.AudioOutput = tok
        # Piper's rate and volume are set when it makes the audio
        v.Rate, v.Volume = (0, 100) if self._piper else (rate, volume)
        return v

    def _parts(self, text: str):
        """What to play for one line: SAPI streams of Piper audio, a sentence at a time, or just the text. If Piper
        fails before saying anything, the text goes to the Windows voice so the line isn't lost."""
        said = False
        if self._piper:
            try:
                _, rate, volume, _ = self.settings
                for samples, sample_rate in self._piper.lines(text, rate, volume):
                    yield _sapi_stream(samples, sample_rate)
                    said = True
                return
            except Exception:  # noqa: BLE001
                log.exception("Piper couldn't say a line")
        if not said:
            yield text

    def _run(self) -> None:
        """The speech thread: speak queued lines in order, skip stale ones, and cut the current line
        short when something more urgent arrives.
        """
        import pythoncom
        pythoncom.CoInitialize()
        self._busy = False
        voice = self._make_voice()
        self._ready.set()
        pending = None
        while True:
            item = pending or self._queue.get()
            pending = None
            self._busy = True  # from here, so waiting for quiet includes loading a new voice
            if self._changed.is_set():
                self._changed.clear()
                try:
                    voice = self._make_voice()
                except Exception:  # noqa: BLE001 - keep talking with the old settings
                    log.exception("couldn't switch to the new voice settings")
            priority, _, queued_at, text = item
            if priority != CRITICAL and time.monotonic() - queued_at > STALE_AFTER_S:
                self._busy = False
                continue
            self._stop_now.clear()
            for part in self._parts(text):
                if isinstance(part, str):
                    voice.Speak(part, SVSF_ASYNC | SVSF_PURGE)
                else:
                    voice.SpeakStream(part, SVSF_ASYNC | SVSF_PURGE)
                while not voice.WaitUntilDone(50):
                    if self._stop_now.is_set():
                        voice.Speak("", SVSF_ASYNC | SVSF_PURGE)  # silence immediately
                        break
                    try:  # a critical line cuts off anything less urgent
                        nxt = self._queue.get_nowait()
                    except queue.Empty:
                        continue
                    if nxt[0] == CRITICAL and priority != CRITICAL:
                        pending = nxt
                        break
                    self._queue.put(nxt)
                    time.sleep(0.05)
                if pending or self._stop_now.is_set():
                    break  # the rest of this line's sentences aren't wanted
            self._busy = False


def speak_to_wav(text: str, path: str, voice: str = "") -> None:
    """Render speech to a WAV file (used to test speech recognition offline)."""
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    if tok := _pick_token(_voice_tokens(v), voice):
        v.Voice = tok
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(path, 3)  # SSFMCreateForWrite
    v.AudioOutputStream = stream
    v.Speak(text)
    stream.Close()
