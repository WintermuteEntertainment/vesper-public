"""The language Vesper hears and answers in, set by [language] code in config.toml.

One setting covers three things:
    hearing    Whisper is told the language; English-only models (base.en) switch to their
               multilingual twin (base), which is the same size to download.
    answering  Claude is told to answer in that language.
    speaking   with no [voice] voice set, the first Windows voice for that language is used.

Callouts and the short fixed commands ("quiet", "callouts on") are still English for now.
"""

from __future__ import annotations

# ISO 639-1 code -> the English name Windows uses in its voice names ("Microsoft Hortense - French").
# Elite's own languages first, then other common ones; any code Whisper knows still works without a name.
NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish", "ru": "Russian", "pt": "Portuguese",
    "it": "Italian", "nl": "Dutch", "pl": "Polish", "sv": "Swedish", "da": "Danish", "nb": "Norwegian",
    "fi": "Finnish", "cs": "Czech", "tr": "Turkish", "uk": "Ukrainian", "ja": "Japanese", "zh": "Chinese",
    "ko": "Korean",
}


def code(cfg: dict) -> str:
    """The configured language code, lower case; "en" when unset."""
    return (cfg.get("language", {}).get("code") or "en").strip().lower()


def name(lang: str) -> str:
    """The language's English name ("fr" -> "French"), or the code itself if it isn't in NAMES."""
    return NAMES.get(lang, lang)


def whisper_model(model: str, lang: str) -> str:
    """The Whisper model to load: an English-only model (".en") becomes its multilingual twin for any
    other language, since the English-only ones can't hear anything else."""
    return model.removesuffix(".en") if lang != "en" and model.endswith(".en") else model


def answer_rule(lang: str) -> str:
    """The line added to Claude's instructions, or "" for English."""
    if lang == "en":
        return ""
    return (f"Always answer in {name(lang)}, whatever language the tools return. Keep proper names from the "
            "game (systems, stations, ships, commodities) as the game spells them.\n")
