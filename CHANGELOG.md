# Changelog

## Unreleased

- No API key or subscription needed: the README now shows how to run on a free local model, and Vesper offers that
  route when there's no key.
- Speech recognition on an NVIDIA card is now an optional install, `.[gpu]`. If the card can't do it, Vesper uses the CPU
  and says so. A speech failure is now spoken, where it used to drop the question silently.

## 0.1.0a1 (2026-11-06)

First alpha.

- A redesigned status page in black and orange, with a new Settings page: change voice, microphone,
  push-to-talk, spending cap, callouts and more without opening a file. Changes save straight away and keep your
  config.toml comments. Only the PC running Vesper can change settings unless you allow others.
- Other languages: Vesper can hear and answer in your language. Callouts, readouts and short commands are translated
  into French, German, Spanish, Russian and Brazilian Portuguese. Those translations are drafts by Claude, not yet
  checked by native speakers.
- More voices: Windows' newer voices now appear, and Piper's offline voices work as an optional extra.
- Local models: follow-up questions can be much faster, and if one says it saved or copied something without calling
  a tool that does it, it's asked to do it or admit it didn't. A clearer message when a local model is too slow.
- A settings file saved with a byte-order mark now loads, and a typo gets a plain message instead of a crash.
- The status page explains itself when there's no game data yet.
- Joystick reads now run one at a time, as a precaution against crashes in Windows' joystick system.
