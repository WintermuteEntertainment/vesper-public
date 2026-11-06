"""Vesper, an Elite Dangerous voice copilot: journal watcher, game state, game tools, voice and MCP."""

from .state import GameState
from .watcher import Event, Watcher

__all__ = ["Event", "GameState", "Watcher"]
__version__ = "0.1.0a1"
