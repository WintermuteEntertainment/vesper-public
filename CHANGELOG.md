# Changelog

## 0.1.0a1 (alpha, not yet released)

First alpha.

- A redesigned status page in black and orange, with a new Settings page: change voice, microphone,
  push-to-talk, spending cap, callouts and more without opening a file. Changes save straight away and keep your
  config.toml comments. Only the PC running Vesper can change settings unless you allow others.
- Other languages: Vesper can hear and answer in your language. Callouts, readouts and short commands are translated
  into French, German, Spanish, Russian and Brazilian Portuguese. Those translations are drafts by Claude, not yet
  checked by native speakers.
- More voices: Windows' newer voices now appear, and Piper's offline voices work as an optional extra.
- Local models: much faster follow-up questions, and they can't say they saved or copied something without
  calling a tool that does it. A clearer message when a local model is too slow.
- Joystick reads now run one at a time, as a precaution against crashes in Windows' joystick system.
