# Vesper

A voice co-pilot for Elite Dangerous. Hold a button, ask a question, hear the answer. She also speaks up
by herself when something happens: hostile hails, interdictions, low fuel, missions about to expire,
prospector readouts while mining.

Vesper only reads the game's own journal files. She never touches the game's memory or sends inputs,
with one exception: when you say "paste", she presses Ctrl+V for you (for the galaxy map search).

## What you need

- Windows 10 or 11 and Elite Dangerous (Horizons or Odyssey).
- Python 3.11 or newer.
- An Anthropic API key for questions (`ANTHROPIC_API_KEY`). Callouts work without one.
  Everyday questions cost about a tenth of a cent each, with a daily budget cap (default $1).
- A microphone, and a key, mouse button or joystick button to hold while you talk.

## Install

```powershell
git clone <this repo> vesper
cd vesper
python -m venv .venv
.venv\Scripts\pip install -e ".[voice,mcp]"
```

## Set up your push-to-talk button

```powershell
.venv\Scripts\python -m elite_companion copilot --pick-button
```

Press the key, mouse button or joystick button you'd like to use. Vesper says whether Elite already uses
it and prints the lines to paste into `%USERPROFILE%\.vesper\config.toml`, for example:

```toml
[ptt]
key = "Mouse 4"
```

or, for a joystick:

```toml
[ptt]
device = "T16000M"
button = 5
```

Every other setting is optional. `config.example.toml` lists them all with their defaults.

## Run

```powershell
.venv\Scripts\python -m elite_companion copilot          # voice co-pilot
.venv\Scripts\python -m elite_companion copilot --text   # type instead of talking
.venv\Scripts\python -m elite_companion copilot --say "Testing"   # check the voice
```

The dashboard (a live status page for a second screen) opens at http://localhost:8770.

## Talking to her

Short commands are handled on your PC, with no AI and no cost:

| Say | What happens |
|---|---|
| "quiet" / "mute" / "callouts off" | Callouts stop |
| "critical only" | Only urgent callouts |
| "callouts on" / "unmute" | All callouts back |
| "start over" / "new conversation" | Forget the current conversation (notes stay) |
| "budget" / "how much have we spent" | Today's spend |
| "paste" | Ctrl+V into the focused window |
| "go local" / "go cloud" | Switch to your own local model and back (optional, see `[local]`) |

Start a question with "think hard about..." to send it to the stronger model.

Anything else goes to Claude, which can use about 60 tools. Some examples:
- Where am I? What's my fuel? What missions do I have left?
- Best place to sell my silver? Best trades from here? Where should I sell this hold?
- Nearest material trader? Where can I buy a Python?
- Who can engineer my FSD to grade 5? Can I afford grade 4 reinforced shields?
- Plan a mining run. Where's the nearest Platinum hotspot? How's the mining going?
- Remember that Sabine Platform has good missions. Remind me to buy limpets when I dock.
- What's bound to orbit lines? Look at my screen. Read my contacts.

## MCP server

The same tools are available to Claude Code or Claude Desktop through an MCP server:

```powershell
.venv\Scripts\python -m elite_companion.mcp_server
```

## Where your files live

Everything Vesper keeps is in `%USERPROFILE%\.vesper\`: `config.toml`, `memory.json` (notes, reminders,
her name, your preferences), `logs\` (daily transcripts), `usage.jsonl` (spending), `markets\` and `cache\`.

## Data sources

Your own journal for everything about your game. Spansh, Ardent, EDSM and GalNet for the galaxy.
Engineer data from EDEngineer. Station data is player-uploaded, so Vesper mentions when it's old.

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests
```
