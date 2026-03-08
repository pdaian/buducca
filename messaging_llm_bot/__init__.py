"""Minimal multi-frontend messaging bot framework backed by an OpenAI-compatible endpoint."""

from .bot import BotRunner
from .config import BotConfig, load_config

__all__ = ["BotRunner", "BotConfig", "load_config"]
