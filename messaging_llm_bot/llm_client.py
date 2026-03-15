from __future__ import annotations

import logging
import re
import time
from typing import Iterable

from .config import LLMConfig
from .http import HttpClient

_NOTHINK_RE = re.compile(r"(?i)(?<!\S)/nothink(?!\S)")


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig, http_client: HttpClient, *, debug: bool = False) -> None:
        self.config = config
        self.http_client = http_client
        self.debug = debug

    def generate_reply(self, messages: Iterable[dict[str, str]], *, disable_thinking: bool = False) -> str:
        materialized_messages = list(messages)
        payload = {
            "model": self.config.model,
            "messages": materialized_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if disable_thinking or self._messages_request_no_think(materialized_messages):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        endpoint = self.config.endpoint_path
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = self.config.base_url.rstrip("/") + endpoint
        started = time.perf_counter()

        if self.debug:
            logging.debug("LLM request URL: %s", url)
            logging.debug("LLM request payload: %s", payload)

        data = self.http_client.post_json(url, payload, headers=headers)
        duration_ms = (time.perf_counter() - started) * 1000
        if self.debug:
            logging.debug("LLM response payload: %s", data)
            logging.debug("LLM request completed in %.2fms", duration_ms)

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, AttributeError) as err:
            raise RuntimeError(f"Malformed response from LLM endpoint: {data}") from err
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            return "\n".join(parts).strip()
        refusal = message.get("refusal")
        if isinstance(refusal, str):
            return refusal.strip()
        return ""

    @staticmethod
    def _messages_request_no_think(messages: Iterable[dict[str, str]]) -> bool:
        for message in messages:
            content = message.get("content")
            if isinstance(content, str) and _NOTHINK_RE.search(content):
                return True
        return False
