"""Minimal multi-frontend messaging bot framework backed by an OpenAI-compatible endpoint."""

__all__ = ["BotRunner", "BotConfig", "load_config"]


def __getattr__(name: str):
    if name == "BotRunner":
        from .bot import BotRunner

        return BotRunner
    if name == "BotConfig":
        from .config import BotConfig

        return BotConfig
    if name == "load_config":
        from .config import load_config

        return load_config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
