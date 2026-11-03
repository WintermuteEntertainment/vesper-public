# Vesper

A voice co-pilot for Elite Dangerous. Hold a button, ask a question, hear the answer. She also speaks up
by herself when something happens: hostile hails, interdictions, low fuel, missions about to expire,
prospector readouts while mining.

Vesper only reads the game's own journal files. She never touches the game's memory or sends inputs,
with one exception: when you say "paste", she presses Ctrl+V for you (for the galaxy map search).

## What you need

- Windows 10 or 11 and Elite Dangerous (Horizons or Odyssey).
- [Python](https://www.python.org/downloads/) 3.11 or newer. In the installer, tick **Add python.exe to PATH**.
- [Git](https://git-scm.com/download/win), or use **Code > Download ZIP** on GitHub and unzip it instead.
- A microphone, and a key, mouse button or joystick button to hold while you talk.
- An Anthropic API key for questions. Callouts work without one.
  Everyday questions cost about a tenth of a cent each, with a daily budget cap (default $1).
- About 390 MB of disk space for Vesper and its packages, plus 141 MB for the speech recognition model.

## Quick start

Open **PowerShell** and run these one at a time.

**1. Download and install.** This takes a minute or two and downloads about 100 MB.

```powershell
git clone https://github.com/WintermuteEntertainment/vesper-public.git vesper
cd vesper
python -m venv .venv
.venv\Scripts\pip install -e ".[voice,mcp]"
```

**2. Add your API key.** Create one at [console.anthropic.com](https://console.anthropic.com/) (Settings >
API keys; the account needs some credit), then save it for your Windows user:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-your-key-here"
```

Skip this step to use callouts only.

**3. Choose your push-to-talk button.**

```powershell
.venv\Scripts\vesper copilot --pick-button
```

Press the buttons you're considering. Each one shows whether Elite already uses it; pick one marked FREE.
Vesper then offers to save it to your settings file. Press Enter to accept.

**4. Check the voice.**

```powershell
.venv\Scripts\vesper copilot --say "Testing"
```

**5. Fly.** Start Elite Dangerous, then:

```powershell
.venv\Scripts\vesper copilot
```

The first start downloads the speech recognition model (141 MB), so give it a minute. When you hear
"Co-pilot online", hold your button, ask "where am I?", and let go.

From then on, steps 4 and 5 are all you need: open PowerShell, `cd vesper`, `.venv\Scripts\vesper copilot`.

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
- Set my home base (while docked there), then: best trades to home?
- What's bound to orbit lines? Look at my screen. Read my contacts.

## Settings

Everything works with no settings file. To change something, open
`%USERPROFILE%\.vesper\config.toml` in Notepad (the button picker creates it) and add only the lines
you want to change. [`config.example.toml`](config.example.toml) lists every setting, its default and what
it does. Common ones:

| To change | Setting |
|---|---|
| Her voice, speed or volume | `[voice] voice`, `rate`, `volume`. List voices with `.venv\Scripts\vesper copilot --voices` |
| Which speakers or headset she uses | `[voice] output_device` (same list) |
| Which microphone she hears | `[listen] mic` |
| Daily spending cap | `[brain] daily_budget_usd` |
| Fewer callouts | `[callouts] mode = "critical"`, or turn single callouts off under `[callouts.rules]` and `[lookout]` |
| Mining readouts | `[mining] prospector` |

Restart Vesper after changing settings.

## Second screen

While Vesper runs, a live status page is at http://localhost:8770. To open it on a tablet or phone on your
network, set `[dashboard] host = "0.0.0.0"` and browse to `http://<your PC's name or IP>:8770`.

## VR

Questions like "look at my screen" and "read my contacts" need to see the game. In VR, Elite's own window is
often minimised, so Vesper captures a VR mirror window instead: Meta's Oculus Mirror, or SteamVR's
headset view. Keep one open while you play, or set `[screen] mirror_command` so Vesper can open it for you.

## Claude Desktop and Claude Code (MCP)

Vesper's tools also work as an MCP server, so you can ask Claude Desktop or Claude Code about your game.

Claude Desktop: open Settings > Developer > Edit Config, and add Vesper to `claude_desktop_config.json`
(use the folder you cloned into, with double backslashes):

```json
{
  "mcpServers": {
    "vesper": {
      "command": "C:\\Users\\you\\vesper\\.venv\\Scripts\\vesper-mcp.exe"
    }
  }
}
```

Restart Claude Desktop. Claude Code: `claude mcp add vesper -- C:\Users\you\vesper\.venv\Scripts\vesper-mcp.exe`

The MCP server needs no API key of its own. It reads your journal; only notes, reminders, the shopping list
and the home base can be changed through it, and those are shared with the voice co-pilot.

## Troubleshooting

| Problem | Try |
|---|---|
| "Questions need a Claude API key" | Do step 2, then close PowerShell and open a new one |
| Nothing happens when I hold the button | Run step 3 again. A button Elite also uses still works for Elite, so pick a FREE one |
| "I couldn't hear that, so I've reconnected" | Your headset or mic was unplugged and plugged back in (a VR restart does this). Just say it again |
| "I still can't hear anything. I'm listening to ..." | Windows' default mic is silent (often Steam Streaming Microphone). Set `[listen] mic` to part of your real mic's name, e.g. `mic = "Headset"` |
| She can't hear me, or hears the wrong thing | Set `[listen] mic` to part of your microphone's name. `whisper_model = "small.en"` is more accurate but slower |
| Wrong voice or wrong speakers | `.venv\Scripts\vesper copilot --voices`, then set `[voice] voice` and `output_device` |
| "No game data yet" | Play Elite once on this PC so it writes its journal, or check that Vesper is looking in your Saved Games folder |
| Type instead of talk, for testing | `.venv\Scripts\vesper copilot --text` |
| `vesper` isn't recognised | Run it as `.venv\Scripts\vesper` from the `vesper` folder |

## Where your files live

Everything Vesper keeps is in `%USERPROFILE%\.vesper\`: `config.toml`, `memory.json` (notes, reminders,
her name, your preferences), `logs\` (daily transcripts), `usage.jsonl` (spending), `markets\` and `cache\`.
Questions go to Anthropic's API, and galaxy lookups to Spansh, Ardent, EDSM and GalNet. Nothing else
leaves your PC, unless you turn on `[eddn] enabled` to share the markets you open.

## Data sources

Your own journal for everything about your game. Spansh, Ardent, EDSM and GalNet for the galaxy.
Engineer data from EDEngineer. Station data is player-uploaded, so Vesper mentions when it's old.

## Updating

```powershell
cd vesper
git pull
.venv\Scripts\pip install -e ".[voice,mcp]"
```

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests
```

## License

GPL-3.0. See [LICENSE](LICENSE). You may use, change and share Vesper; if you share a changed
version, share its source under the same license.
