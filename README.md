# Vesper

A voice co-pilot for Elite Dangerous. Hold a button, ask a question, hear the answer. She also speaks up
by herself when something happens: hostile hails, interdictions, low fuel, missions about to expire,
prospector readouts while mining.

**This is an alpha.** Expect rough edges, and please [report what you find](https://github.com/WintermuteEntertainment/vesper-public/issues).

Vesper only reads the game's own journal files. She never touches the game's memory or sends inputs,
with one exception: when you say "paste", she presses Ctrl+V for you (for the galaxy map search).

## What you need

- Windows 10 or 11 and Elite Dangerous (Horizons or Odyssey).
- [Python](https://www.python.org/downloads/) 3.11 or newer. In the installer, tick **Add python.exe to PATH**.
- [Git](https://git-scm.com/download/win), or use **Code > Download ZIP** on GitHub and unzip it instead.
- A microphone, and a key, mouse button or joystick button to hold while you talk.
- For questions, either an Anthropic API key or [a free local model](#no-api-key-use-a-local-model). Callouts work with neither.
  With Claude, everyday questions cost about a tenth of a cent each, with a daily budget cap (default $1).
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

Skip this step if you'll [use a local model](#no-api-key-use-a-local-model), or just want callouts.

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
- Copy that system to my clipboard. Then click the galaxy map search and say "paste".
- Nearest material trader? Where can I buy a Python?
- Who can engineer my FSD to grade 5? Can I afford grade 4 reinforced shields?
- Plan a mining run. Where's the nearest Platinum hotspot? How's the mining going?
- Remember that Sabine Platform has good missions. Remind me to buy limpets when I dock.
- Set my home base (while docked there), then: best trades to home?
- What's bound to orbit lines? Look at my screen. Read my contacts.

## Settings

Everything works with no settings file. While Vesper runs, open http://localhost:8770 and press **Settings** to
change her voice, microphone, push-to-talk, spending cap, callouts and more. Each change saves as you make it.

Prefer a file? `%USERPROFILE%\.vesper\config.toml` holds the same settings and keeps your comments.
[`config.example.toml`](config.example.toml) lists them all, including the few that are file-only.

## No API key? Use a local model

Vesper doesn't need Claude, an API key or a subscription. She can use a model on your own graphics card, through any
OpenAI-compatible server such as [Ollama](https://ollama.com/) or [LM Studio](https://lmstudio.ai/). Start your server,
then add this to `%USERPROFILE%\.vesper\config.toml`:

```toml
[local]
base_url = "http://localhost:11434/v1"   # Ollama. LM Studio is http://localhost:1234/v1
model = "the-model-name-your-server-uses"
start_local = true
```

Once the address is in the file, you can change the model name, the timeout and "Start in local mode" under
**Settings > Local model**. Say "go local" or "go cloud" to switch while she runs.

- **The model needs to support tool calling**, because that's how she reads your game.
- **It competes with the game.** A model on your graphics card can cut your frame rate, especially in VR. Answers are
  also slower and less reliable than Claude's, depending on your model and hardware.
- **What still goes online:** the first download of the speech model, and galaxy lookups such as prices, routes and GalNet.
  These are free and need no account.
- **What needs Claude:** the captain's log, and asking her to look at your screen. Web search needs Claude or your own
  SearXNG server.

## More speech options

Speech recognition runs on your CPU by default, which leaves the graphics card to the game. A short question took under
a second on the CPU of the PC it was tested on, so you may not need more. To run it on an NVIDIA card instead, install NVIDIA's libraries
(about 1.3 GB to download, 2 GB on disk) and choose `cuda` under **Settings > Microphone**, then restart Vesper:

```powershell
.venv\Scripts\pip install -e ".[voice,mcp,gpu]"
```

If the graphics card can't do it, Vesper uses the CPU and tells you.

## More voices

The Voice list includes every Windows voice on your PC. For more natural voices that run offline, add
[Piper](https://github.com/OHF-Voice/piper1-gpl):

```powershell
.venv\Scripts\pip install -e ".[voice,mcp,piper]"
```

Then download a voice from [Piper's voice list](https://huggingface.co/rhasspy/piper-voices), both its `.onnx` file
and its `.onnx.json` file, into `%USERPROFILE%\.vesper\voices`. Restart Vesper and it appears in the Voice list.
Each voice has its own license, listed in its `MODEL_CARD`.

## Other languages

Vesper can hear you and answer in other languages. Add this to `config.toml`, with your language's
two-letter code (fr, de, es, ru, pt, it, nl, pl...):

```toml
[language]
code = "fr"
```

- **Hearing:** the speech recognition model switches to its multilingual version. It's the same size
  (141 MB), downloaded once on the next start.
- **Answers:** Claude answers in your language. Game names stay as the game spells them.
- **Voice:** with `[voice] voice` left blank, Vesper picks the first Windows voice for your language. Add one
  in Windows Settings > Time & language > Speech > Add voices, then check it shows up with
  `.venv\Scripts\vesper copilot --voices`.

- **Callouts and commands:** French (fr), German (de), Spanish (es), Russian (ru) and Brazilian Portuguese (pt)
  have translated callouts, readouts and short commands ("silence", "Ruhe", "тишина"...). In any language
  you can also just ask her to mute or restore the callouts. Other languages fall back to English callouts.

The translations are first drafts written with Claude and not yet checked by native speakers. Corrections
are very welcome.

### Improving a translation

Each language is one file in [`src/elite_companion/locales/`](src/elite_companion/locales). The English
originals, and what every line is for, are in [`phrases.py`](src/elite_companion/phrases.py). Keep the
words in braces (`{pilot}`, `{n}`) as they are, though you may leave some out. A phrase with "one" and "other"
(plus "few" and "many" in Russian and Polish) is chosen by the number. To add a language, copy a file, name it
after the language's two-letter code, and translate it. Run the tests to check your file.

## Second screen

While Vesper runs, a live status page is at http://localhost:8770. To open it on a tablet or phone on your
network, set `[dashboard] host = "0.0.0.0"` and browse to `http://<your PC's name or IP>:8770`. The page has no
password, so anyone on your network can then see it. Settings can only be changed from the PC itself unless you also
set `[dashboard] network_edit = true`.

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
| "Questions need a Claude API key or a local model" | Do step 2 and open a new PowerShell, or [set up a local model](#no-api-key-use-a-local-model) |
| Nothing happens when I hold the button | Run step 3 again. A button Elite also uses still works for Elite, so pick a FREE one |
| "I couldn't hear that, so I've reconnected" | Your headset or mic was unplugged and plugged back in (a VR restart does this). Just say it again |
| "I still can't hear anything. I'm listening to ..." | Windows' default mic is silent (often Steam Streaming Microphone). Pick another under Settings, or set `[listen] mic` to part of your real mic's name, e.g. `mic = "Headset"` |
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
