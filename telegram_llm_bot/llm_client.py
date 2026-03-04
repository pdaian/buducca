from __future__ import annotations

from typing import Iterable

from .config import LLMConfig
from .http import HttpClient


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig, http_client: HttpClient) -> None:
        self.config = config
        self.http_client = http_client

    def generate_reply(self, messages: Iterable[dict[str, str]]) -> str:
        payload = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        endpoint = self.config.endpoint_path
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = self.config.base_url.rstrip("/") + endpoint

        data = self.http_client.post_json(url, payload, headers=headers)
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as err:
            raise RuntimeError(f"Malformed response from LLM endpoint: {data}") from err
