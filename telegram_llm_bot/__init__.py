"""Minimal Telegram bot framework backed by an OpenAI-compatible endpoint."""

from .bot import BotRunner, FrontendBotRunner
from .config import BotConfig, load_config

__all__ = ["FrontendBotRunner", "BotRunner", "BotConfig", "load_config"]
