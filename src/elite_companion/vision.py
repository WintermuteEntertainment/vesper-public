"""Read structured text off a screenshot: one Claude call that turns the image into JSON.

Vesper's look_at_screen describes; this extracts. Each reader (contacts now, mission board later) is a prompt
that asks for one JSON object, so the brain gets tidy data instead of a picture to re-read every turn.
"""

from __future__ import annotations

import json
import re

from . import screen

API_MAX_EDGE = 1568  # the API shrinks anything longer than this, so sending more only costs upload time

CONTACTS_PROMPT = """This is a screenshot from Elite Dangerous.
Find the CONTACTS list (the left cockpit panel, Contacts tab). Read every row exactly as shown.
Reply with ONLY a JSON object, no prose:
{"visible": true/false,
 "contacts": [{"name": "commander, NPC or ship name as written", "ship": "ship type if shown",
               "distance": "as shown with units", "status": "Wanted/Clean/Hostile/etc if shown",
               "rank": "combat rank if shown", "faction": "if shown", "selected": true if highlighted}],
 "notes": "anything else useful on that panel, else empty"}
Omit fields that aren't shown. If no contacts list is on screen, set visible false, contacts [], and say in
notes what IS on screen in a few words. Don't guess at text you can't read; write "?" for unreadable parts."""


class VisionError(RuntimeError):
    """The capture or the text extraction failed; the message is safe to speak."""
    pass


def parse_json(text: str) -> dict:
    """The model's reply as a dict, tolerating code fences or a sentence around the JSON."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise VisionError(f"No JSON in the reply: {text[:120]}")
        return json.loads(m.group(0))


def extract(image_b64: str, prompt: str, brain, label: str, model: str | None = None) -> dict:
    """Ask Claude to read the image into JSON; the cost goes in the usage log like any other question."""
    model = model or brain.model
    response = brain.client.messages.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            {"type": "text", "text": prompt}]}])
    brain._log_usage(response.usage, f"[vision] {label}", model)
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return parse_json(text)


def grab() -> dict:
    """A screenshot sharp enough to read: Elite's window, else a VR mirror (opened with [screen]
    mirror_command if one is set and none is showing), else the main screen."""
    import win32gui
    if not screen._visible(win32gui.FindWindow(None, screen.ELITE_TITLE)):
        screen.start_mirror()
    return screen.capture("auto", max_width=API_MAX_EDGE)
