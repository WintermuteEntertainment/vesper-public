"""Text to speech through Windows' built-in voices (SAPI), on a background thread.

Critical lines interrupt whatever is being said; ordinary lines queue, and
are dropped if they've waited too long to still be useful.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time

log = logging.getLogger(__name__)

CRITICAL, NORMAL = 0, 1
STALE_AFTER_S = 8.0     # ordinary lines older than this are skipped
SVSF_ASYNC, SVSF_PURGE = 1, 2


ONECORE_VOICES = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"


def _tokens(collection) -> list:
    """A SAPI token collection as a list."""
    return [collection.Item(i) for i in range(collection.Count)]


def _all_voices(v) -> list:
    """Every voice SAPI can use: its own desktop voices, then the newer ones Windows installs from
    Settings > Time & language > Speech (including other languages). SAPI doesn't list those by
    default, but they work once found."""
    import win32com.client
    voices = _tokens(v.GetVoices())
    try:
        cat = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
        cat.SetId(ONECORE_VOICES, False)
        voices += _tokens(cat.EnumerateTokens())
    except Exception:  # an older Windows without them: the desktop voices still work
        log.debug("no OneCore voices", exc_info=True)
    return voices


def _pick_token(tokens: list, wanted: str):
    """The first voice or audio output whose name contains `wanted`, or None (the default)."""
    wanted = (wanted or "").lower()
    if not wanted:
        return None
    for tok in tokens:
        if wanted in tok.GetDescription().lower():
            return tok
    log.warning("no voice/device matching %r", wanted)
    return None


def list_voices_and_outputs() -> tuple[list[str], list[str]]:
    """Installed voices and audio outputs, for `vesper copilot --voices`."""
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    voices = [t.GetDescription() for t in _all_voices(v)]
    outputs = [t.GetDescription() for t in _tokens(v.GetAudioOutputs())]
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
        self.recent: list[str] = []  # last few lines spoken, for echo filtering
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(10)

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
        if tok := _pick_token(_all_voices(v), name):
            v.Voice = tok
        if tok := _pick_token(_tokens(v.GetAudioOutputs()), device):
            v.AudioOutput = tok
        v.Rate, v.Volume = rate, volume
        return v

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
            priority, _, queued_at, text = item
            if priority != CRITICAL and time.monotonic() - queued_at > STALE_AFTER_S:
                continue
            self._busy = True
            self._stop_now.clear()
            voice.Speak(text, SVSF_ASYNC | SVSF_PURGE)
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
            self._busy = False


def speak_to_wav(text: str, path: str, voice: str = "") -> None:
    """Render speech to a WAV file (used to test speech recognition offline)."""
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    if tok := _pick_token(_all_voices(v), voice):
        v.Voice = tok
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(path, 3)  # SSFMCreateForWrite
    v.AudioOutputStream = stream
    v.Speak(text)
    stream.Close()
