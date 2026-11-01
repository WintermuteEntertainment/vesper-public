"""The voice copilot: callouts, plus push-to-talk questions answered by Claude.

    vesper copilot                 # run it while you play
    vesper copilot --pick-button   # find your push-to-talk button
    vesper copilot --text          # type questions instead of speaking
    vesper copilot --say "Testing" # check the voice and output device

Pieces degrade independently: with no API key or no PTT button configured
you still get callouts; with callouts off you can still ask questions.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import winsound
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from .lookout import Lookout
from .screen import SCREEN_SPEC
from .mining import MiningCallouts
from .brain import Brain, has_credentials, spent_today, spoken_error
from .callouts import Callouts
from .speech import CRITICAL, NORMAL, Speaker
from .markets import MarketRecorder
from .memory import MemoryStore, Reminders
from .tools import GameTools
from .watcher import Watcher

log = logging.getLogger(__name__)

def paste_clipboard() -> str:
    """Press Ctrl+V for them (e.g. into the galaxy map search). The only key Vesper ever presses.
    Sent as hardware scan codes with SendInput: Elite reads keys by scan code, and ignored the virtual-key
    presses the first version sent (2026-10-26/27)."""
    import ctypes
    import time
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    SCANCODE, KEYUP, CTRL, V = 0x0008, 0x0002, 0x1D, 0x2F
    user32 = ctypes.windll.user32
    time.sleep(0.4)  # let go of push-to-talk first
    window = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(user32.GetForegroundWindow(), window, 256)
    keys = [(CTRL, 0), (V, 0), (V, KEYUP), (CTRL, KEYUP)]
    inputs = (INPUT * len(keys))(*[INPUT(type=1, ki=KEYBDINPUT(0, sc, SCANCODE | up, 0, 0)) for sc, up in keys])
    sent = user32.SendInput(len(keys), inputs, ctypes.sizeof(INPUT))
    log.info("paste: sent %s of 4 key events to %r", sent, window.value)
    if sent != len(keys):
        return "Windows blocked the paste. Press Control V yourself."
    return f"Pasted into {window.value or 'the active window'}."


def _look_at_screen(whole_screen: bool = False) -> dict:
    """The look_at_screen tool: a screenshot Claude can see (Elite's window, a VR mirror, or the
    whole screen).
    """
    from . import screen
    try:
        shot = screen.capture("screen" if whole_screen else "auto")
    except screen.ScreenError as e:
        return {"error": str(e)}
    return {"_image": shot["png_base64"], "source": shot["source"], "size": f"{shot['width']}x{shot['height']}"}


CONTACTS_SPEC = {
    "name": "read_contacts",
    "description": "Read the Contacts list on their left cockpit panel off the screen into a "
                   "list: names, ships, distances, wanted/clean, ranks. Use when they ask who's around, who's wanted, "
                   "or to read/sort/count their contacts. They must have the Contacts tab open. Answer what they asked "
                   "from the list; don't read it all back unless they want that.",
    "input_schema": {"type": "object", "properties": {}},
}
PASTE_SPEC = {
    "name": "paste_clipboard",
    "description": "Press Ctrl+V for them, pasting the clipboard into the text box they have selected in the game "
                   "(usually the galaxy map search after you copied a system). You CAN do this; never say you can't. "
                   "Afterwards say where it went.",
    "input_schema": {"type": "object", "properties": {}},
}
LOGBOOK_SPEC = {
    "name": "write_captains_log",
    "description": "Write up this session as a captain's log, with a YouTube title, description and chapters, saved "
                   "to their logbook folder. Use when they ask for their log, a write-up or stream chapters. Takes about "
                   "half a minute. Tell them the title it came up with.",
    "input_schema": {"type": "object", "properties": {
        "stream_start": {"type": "string", "description": "HH:MM local time the stream started, if they say."}}},
}


def _write_log(stream_start: str | None = None) -> dict:
    """The write_log tool: write today's captain's log and return where it went."""
    from . import logbook
    path = logbook.write(stream_start=stream_start)
    text = path.read_text(encoding="utf-8")
    title = text.split("## YouTube title", 1)[-1].strip().splitlines()[0] if "## YouTube title" in text else ""
    return {"saved_to": str(path), "youtube_title": title}

def _whole(body: str) -> re.Pattern:
    """Match only when the command is the whole utterance, give or take polite padding and punctuation.
    "Quiet." and "Okay, quiet mode please" match; "Is this system quiet?" does not (it's a question for Claude)."""
    pad = r"(?:okay|ok|hey|vesper|please|now|thanks|thank you)"
    return re.compile(rf"^\W*(?:{pad}\W+)*(?:{body})(?:\W+{pad})*\W*$")


# Spoken commands handled locally, without calling Claude. First match wins.
# Whisper often puts punctuation between words ("Go, Cloud."), hence the [\s,.!]+ separators.
COMMANDS = [
    # Callout modes: whole-utterance only, so "can you mute the music?" still reaches Claude.
    (_whole(r"(?:be )?(?:quiet|silent|mute)(?: mode| callouts)?|callouts? off"), "off"),
    (_whole(r"critical(?: only| mode| callouts)?|only critical(?: callouts)?"), "critical"),
    (_whole(r"callouts? on|unmute|normal mode|all callouts"), "all"),
    (_whole(r"new conversation|start over|forget that"), "reset"),
    (_whole(r"budget|what'?s the budget|how much have (?:you|we) spent(?: today)?"), "budget"),
    # "paste", "re-paste", "no, paste", "paste it for me" anywhere; Whisper's "paced" / "base" only on their own.
    (re.compile(r"\b(re-?)?paste\b|^\W*(paced|paced it|base)\W*$"), "paste"),
    (re.compile(r"\b(go|switch to|use)[\s,.!]+(local|offline)\b|\blocal[\s,.]+(mode|only)\b"), "local"),
    (re.compile(r"\b(go|switch to|use|back to)[\s,.!]+(cloud|claude|clode|online)\b|\bcloud[\s,.]+mode\b"), "cloud"),
]


LOG_DIR = config_mod.HOME / "logs"
COMMAND_MAX_WORDS = 6  # longer utterances are questions, even if they contain "start over"


class Copilot:
    """The voice copilot: wires the watcher to the callouts, lookout, mining and reminder
    speakers, the brain, push-to-talk and speech, and handles each thing the commander says.
    """
    def __init__(self, cfg: dict | None = None, watcher: Watcher | None = None, speak: bool = True,
                 notes: MemoryStore | None = None, log_dir: Path | None = LOG_DIR):
        self.cfg = cfg or config_mod.load()
        self.watcher = watcher or Watcher()
        self.notes = notes or MemoryStore()
        self.tools = GameTools(self.watcher, self.notes)
        self.log_dir = log_dir
        v = self.cfg["voice"]
        self.speaker = Speaker(v["voice"], v["rate"], v["volume"], v["output_device"]) if speak else None
        self.callouts = Callouts(self.watcher, self.say, self.cfg["callouts"])
        self.reminders = Reminders(self.watcher, self.notes, self.say)
        self.lookout = Lookout(self.watcher, self.say, self.callouts, self.cfg["lookout"])
        self.lookout.notes = self.notes
        from .bindings import set_nicknames
        set_nicknames(self.cfg["controls"]["nicknames"])
        from . import screen
        screen.configure(self.cfg["screen"])
        self.mining = MiningCallouts(self.watcher, self.say, self.callouts, self.cfg["mining"])
        self.markets = MarketRecorder(self.watcher, eddn=bool(self.cfg["eddn"]["enabled"]))
        b = self.cfg["brain"]
        self.brain = (Brain(self.tools, b["model"], b["effort"], b["daily_budget_usd"], b["max_history_turns"],
                            idle_reset_minutes=b["idle_reset_minutes"], on_slow_tool=self.say,
                            extra_tools={"write_captains_log": (LOGBOOK_SPEC, _write_log),
                                         "paste_clipboard": (PASTE_SPEC, lambda: {"result": paste_clipboard()}),
                                         "look_at_screen": (SCREEN_SPEC, _look_at_screen),
                                         "read_contacts": (CONTACTS_SPEC, self._read_contacts)},
                            hard_model=b["hard_model"], hard_effort=b["hard_effort"], local=self.cfg["local"],
                            web_search=b["web_search"], search=self.cfg["search"])
                      if has_credentials() or self.cfg["local"]["start_local"] else None)
        self.recorder = self.transcriber = self.ptt = None
        self._busy = threading.Lock()

    def log(self, kind: str, text: str) -> None:
        """Append to today's transcript: ~/.vesper/logs/copilot-YYYY-MM-DD.log"""
        if not self.log_dir:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            with open(self.log_dir / f"copilot-{now:%Y-%m-%d}.log", "a", encoding="utf-8") as f:
                f.write(f"{now:%H:%M:%S} {kind} {text}\n")
        except OSError:
            log.warning("couldn't write the transcript log")

    def say(self, text: str, priority: int = NORMAL) -> None:
        """Print, log and speak a line."""
        mark = "!" if priority == CRITICAL else ">"
        print(f"{mark} {text}", flush=True)
        self.log(mark, text)
        if self.speaker:
            self.speaker.say(text, priority)

    def start_background(self) -> None:
        """Start the journal watcher (and the dashboard) and mark the session start in the transcript."""
        self.watcher.start()
        d = self.cfg["dashboard"]
        if d["enabled"]:
            try:
                from .dashboard import serve
                serve(self.tools, d["host"], int(d["port"]))
                print(f"Dashboard: http://localhost:{d['port']}", flush=True)
            except OSError as e:
                log.warning("dashboard didn't start: %s", e)
        self.log("--", f"co-pilot started (model {self.cfg['brain']['model'] if self.brain else 'none'})")

    # ---- questions ----

    def handle_text(self, text: str) -> str:
        """Answer one utterance: a fixed command if it is short and matches one, otherwise a
        question for the brain. Returns the reply (also spoken).
        """
        text = text.strip()
        if not text:
            return ""
        self.log("?", text)
        low = text.lower()
        if len(low.split()) <= COMMAND_MAX_WORDS:
            for pattern, action in COMMANDS:
                if pattern.search(low):
                    return self._command(action)
        if self.brain is None:
            reply = "Questions need a Claude API key. Set ANTHROPIC_API_KEY and restart me."
        else:
            try:
                reply = self.brain.ask(text)
            except Exception as e:  # spoken, never fatal
                reply = spoken_error(e)
        self.say(reply)
        return reply

    def _read_contacts(self) -> dict:
        """The read_contacts tool: screenshot the contacts panel and read it with vision."""
        from . import screen, vision
        try:
            shot = vision.grab()
            out = vision.extract(shot["png_base64"], vision.CONTACTS_PROMPT, self.brain, "contacts")
        except (screen.ScreenError, vision.VisionError, ValueError) as e:
            return {"error": str(e)}
        out["source"] = shot["source"]
        out["read_at"] = datetime.now().strftime("%H:%M:%S")
        self.tools.contacts_snapshot = out  # so a follow-up question doesn't need a second screenshot
        log.info("contacts read from %s: %s rows, visible=%s", shot["source"], len(out.get("contacts", [])),
                 out.get("visible"))
        return out

    def _warm_local(self) -> None:
        """Load the local model now so the first question isn't slow."""
        if self.brain.warm_local():
            self.say("Local model ready.")
        else:
            self.say("The local model isn't answering. Say go cloud to switch back to Claude.")

    def _command(self, action: str) -> str:
        """Carry out a fixed voice command (see COMMANDS) and return what to say."""
        if action in ("off", "critical", "all"):
            self.callouts.set_mode(action)
            reply = {"off": "Callouts off.", "critical": "Critical callouts only.", "all": "Callouts on."}[action]
        elif action == "paste":
            reply = paste_clipboard()
        elif action in ("local", "cloud"):
            if not self.brain:
                reply = "I have no brain to switch."
            elif action == "cloud":
                reply = self.brain.set_local(False)
            else:
                reply = self.brain.set_local(True)
                if self.brain.local_mode:
                    reply = "Going local. Give me up to half a minute to load the model."
                    threading.Thread(target=self._warm_local, daemon=True).start()
        elif action == "reset":
            if self.brain:
                self.brain.reset()
            reply = "Starting fresh."
        else:
            budget = self.cfg["brain"]["daily_budget_usd"]
            reply = f"About {spent_today() * 100:.0f} cents of today's {budget:.2f} dollar budget used."
        self.say(reply)
        return reply

    def name_hints(self) -> list[str]:
        """Names they're likely to say right now (where they are, where they're going, mission and route systems,
        names Vesper just said), so speech recognition hears "Bhado" rather than "battle"."""
        st = self.watcher.state
        names = [st.location.system, st.location.station, st.location.body]
        names += [s.get("StarSystem") for s in st.route[-5:]]
        for m in st.missions.values():
            names += [m.get("destination_system"), m.get("destination_station"), m.get("source_station")]
        if self.speaker:
            names += re.findall(r"\b[A-Z][A-Za-z'-]+(?: [A-Z0-9][A-Za-z0-9'-]+)*", " ".join(self.speaker.recent))
        return [n for n in names if n and len(n) > 2][:40]

    # ---- push-to-talk ----

    def _setup_voice_input(self) -> None:
        """Load the microphone and Whisper, then start push-to-talk on the configured key or
        joystick button.
        """
        from .listen import Recorder, Transcriber
        from .ptt import KeyPushToTalk, PushToTalk
        p, l = self.cfg["ptt"], self.cfg["listen"]
        self.recorder = Recorder(l["mic"])
        print("Loading speech recognition...", flush=True)
        self.transcriber = Transcriber(l["whisper_model"], l["whisper_device"])
        if p.get("key"):
            self.ptt = KeyPushToTalk(p["key"], self._on_press, self._on_release)
            print(f"Push-to-talk: {p['key']}", flush=True)
        else:
            self.ptt = PushToTalk(p["device"], int(p["button"]), self._on_press, self._on_release)
            print(f"Push-to-talk: {p['device']} button {p['button']}", flush=True)
        self.ptt.start()

    def _on_press(self) -> None:
        if not self._busy.acquire(blocking=False):
            return  # still answering the last one
        if self.speaker:
            self.speaker.stop()  # barge-in: stop talking so the mic doesn't hear us
        winsound.Beep(1200, 40)
        self.recorder.start()

    def _on_release(self) -> None:
        if not self._busy.locked() or self.recorder._stream is None:
            return
        audio = self.recorder.stop()
        winsound.Beep(800, 40)

        def work():
            try:
                heard = strip_echo(self.transcriber.transcribe(audio, self.name_hints()),
                                   self.speaker.recent if self.speaker else [])
                if heard:
                    print(f"? {heard}", flush=True)
                    self.handle_text(heard)
            finally:
                self._busy.release()
        threading.Thread(target=work, name="question", daemon=True).start()

    # ---- running ----

    def run(self) -> None:
        """Start everything and run until Ctrl+C."""
        self.start_background()
        mode = self.cfg["callouts"]["mode"]
        print(f"Co-pilot running. Callouts: {mode}.", flush=True)
        if self.brain is None:
            print("Questions: off (no ANTHROPIC_API_KEY).", flush=True)
        elif not self.cfg["ptt"].get("key") and not int(self.cfg["ptt"]["button"]):
            print("Push-to-talk: no button set. Run `vesper copilot --pick-button`, then add it to config.toml.", flush=True)
        else:
            self._setup_voice_input()
        self.say("Co-pilot online.")
        self.reminders.startup()
        self.lookout.startup_summary()
        self.reminders.start_clock()
        self.lookout.start_clock()
        stop = threading.Event()
        try:
            while not stop.wait(0.5):  # short waits: an untimed wait blocks Ctrl+C on Windows
                pass
        except KeyboardInterrupt:
            print("Co-pilot stopped.", flush=True)


def _words(text: str) -> list[str]:
    """Lower-case words of a sentence."""
    return re.findall(r"[a-z0-9']+", text.lower())


def strip_echo(heard: str, spoken: list[str], min_words: int = 4) -> str:
    """Remove the copilot's own words that the mic picked up at the start of a transcript.
    e.g. "Keep in mind its market data is from late July. Non-lethal weapons" -> "Non-lethal weapons"."""
    if not heard or not spoken:
        return heard
    heard_words = _words(heard)
    best = 0
    for line in spoken:
        said = _words(line)
        # Longest run of the transcript's opening words that appears consecutively in a spoken line.
        for start in range(len(said)):
            n = 0
            while n < len(heard_words) and start + n < len(said) and _close(heard_words[n], said[start + n]):
                n += 1
            best = max(best, n)
    if best < min_words:
        return heard
    # Cut the same number of words off the original text, keeping its punctuation.
    tokens = re.findall(r"\S+", heard)
    kept, seen = [], 0
    for tok in tokens:
        if seen < best and _words(tok):
            seen += len(_words(tok))
            continue
        kept.append(tok)
    return " ".join(kept).lstrip(" .,;:-").strip()


def _close(a: str, b: str) -> bool:
    """Same word, or a likely mishearing ("deep" for "keep")."""
    return a == b or (len(a) > 3 and len(b) > 3 and a[1:] == b[1:])


def pick_button_interactive() -> int:
    """`vesper copilot --pick-button`: press keys, mouse buttons or joystick buttons; each one shows whether Elite
    already uses it. Then offers to save the last free one to config.toml (only if the file has no [ptt]
    section yet; otherwise it prints the lines to paste)."""
    from .bindings import joystick_button_used, key_used
    from .ptt import pick_input
    print("Press the key, mouse button or joystick button you want for push-to-talk.\n"
          "Each press shows whether Elite already uses it. Stops after 20 s with no presses. Ctrl+C to quit.\n",
          flush=True)
    last_free = None
    while got := pick_input(timeout=20):
        if got[0] == "key":
            label, used, lines = got[1], key_used(got[1]), f'key = "{got[1]}"'
        else:
            _, name, button, usb_id = got
            label, used = f"{name} button {button}", joystick_button_used(usb_id, button)
            lines = f'key = ""\ndevice = "{name}"\nbutton = {button}'
        if used:
            print(f"  {label}: already bound in Elite to {', '.join(used)}", flush=True)
        else:
            print(f"  {label}: FREE", flush=True)
            last_free = lines
    if not last_free:
        print("\nNo free button was pressed. Run this again and try another.")
        return 0
    path = config_mod.CONFIG_PATH
    print(f"\nPush-to-talk:\n\n[ptt]\n{last_free}\n")
    if input(f"Save this to {path}? [Y/n] ").strip().lower() in ("", "y", "yes"):
        if config_mod.add_section("ptt", last_free):
            print("Saved. Start Vesper with: vesper copilot")
        else:
            print(f"{path} already has a [ptt] section, so I left it alone. Replace its lines with the ones above.")
    return 0


def main(argv: list[str]) -> int:
    """`vesper copilot`: run the copilot, or --pick-button, --voices, --text, --say."""
    import argparse
    parser = argparse.ArgumentParser(prog="vesper copilot")
    parser.add_argument("--pick-button", action="store_true", help="press a key, mouse button or joystick button to set push-to-talk")
    parser.add_argument("--voices", action="store_true", help="list the installed voices and audio outputs for [voice]")
    parser.add_argument("--text", action="store_true", help="type questions instead of using push-to-talk")
    parser.add_argument("--say", metavar="TEXT", help="speak TEXT with your configured voice and exit")
    parser.add_argument("--quiet", action="store_true", help="print only; don't speak")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    cfg = config_mod.load()

    if args.pick_button:
        return pick_button_interactive()
    if args.voices:
        from .speech import list_voices_and_outputs
        voices, outputs = list_voices_and_outputs()
        print("Voices ([voice] voice = part of a name):\n  " + "\n  ".join(voices))
        print("Audio outputs ([voice] output_device = part of a name):\n  " + "\n  ".join(outputs))
        return 0
    if args.say:
        v = cfg["voice"]
        s = Speaker(v["voice"], v["rate"], v["volume"], v["output_device"])
        s.say(args.say, CRITICAL)
        s.wait_idle()
        return 0

    pilot = Copilot(cfg, speak=not args.quiet)
    if args.text:
        pilot.start_background()
        pilot.reminders.startup()
        pilot.reminders.start_clock()
        pilot.lookout.start_clock()
        print("Type a question (or 'quiet mode', 'callouts on'...). Ctrl+C to quit.", flush=True)
        try:
            for line in sys.stdin:
                pilot.handle_text(line)
        except KeyboardInterrupt:
            pass
        if pilot.speaker:
            pilot.speaker.wait_idle()
        return 0
    pilot.run()
    return 0
