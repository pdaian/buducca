from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Deque

from .config import BotConfig
from .http import HttpClient
from .llm_client import OpenAICompatibleClient
from .telegram_client import TelegramClient

_TELEGRAM_MAX_MESSAGE_LEN = 4096


class BotRunner:
    def __init__(self, config: BotConfig) -> None:
        self.config = config

        http_client = HttpClient(timeout_seconds=config.runtime.request_timeout_seconds)
        self.telegram = TelegramClient(bot_token=config.telegram.bot_token, http_client=http_client)
        self.llm = OpenAICompatibleClient(config=config.llm, http_client=http_client)

        self._allowed_chat_ids = set(config.telegram.allowed_chat_ids)
        self._offset: int | None = None
        self._history: dict[int, Deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.config.llm.history_messages * 2)
        )

    def run_forever(self) -> None:
        logging.info("Bot started. Waiting for messages...")
        while True:
            try:
                updates = self.telegram.get_updates(
                    offset=self._offset,
                    timeout_seconds=self.config.telegram.long_poll_timeout_seconds,
                )
                for update in updates:
                    self._offset = update.update_id + 1
                    self._handle_message(update.chat_id, update.text)
            except KeyboardInterrupt:
                logging.info("Bot interrupted. Exiting.")
                return
            except Exception:
                logging.exception("Error while polling or handling message")
                time.sleep(2)

            time.sleep(self.config.telegram.poll_interval_seconds)

    def _build_messages(self, chat_id: int, text: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self.config.llm.system_prompt}]
        messages.extend(self._history[chat_id])
        messages.append({"role": "user", "content": text})
        return messages

    def _split_for_telegram(self, text: str) -> list[str]:
        if len(text) <= _TELEGRAM_MAX_MESSAGE_LEN:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            chunk = text[start : start + _TELEGRAM_MAX_MESSAGE_LEN]
            chunks.append(chunk)
            start += _TELEGRAM_MAX_MESSAGE_LEN
        return chunks

    def _handle_message(self, chat_id: int, text: str) -> None:
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            logging.warning("Blocked message from unauthorized chat_id=%s", chat_id)
            return

        logging.info("Incoming message from chat_id=%s", chat_id)
        prompt = self._build_messages(chat_id, text)
        reply = self.llm.generate_reply(prompt)

        self._history[chat_id].append({"role": "user", "content": text})
        self._history[chat_id].append({"role": "assistant", "content": reply})

        for chunk in self._split_for_telegram(reply):
            self.telegram.send_message(chat_id, chunk)
        logging.info("Replied to chat_id=%s", chat_id)
