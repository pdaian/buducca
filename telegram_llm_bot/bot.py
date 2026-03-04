from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque

from assistant_framework import SkillManager, Workspace

from .config import BotConfig
from .http import HttpClient
from .llm_client import OpenAICompatibleClient
from .telegram_client import TelegramClient

_TELEGRAM_MAX_MESSAGE_LEN = 4096
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


class BotRunner:
    def __init__(self, config: BotConfig) -> None:
        self.config = config

        http_client = HttpClient(timeout_seconds=config.runtime.request_timeout_seconds)
        self.telegram = TelegramClient(bot_token=config.telegram.bot_token, http_client=http_client)
        self.llm = OpenAICompatibleClient(config=config.llm, http_client=http_client)

        self._allowed_chat_ids = set(config.telegram.allowed_chat_ids)
        self._offset: int | None = None
        self._started_at = datetime.now(timezone.utc)
        self._handled_messages_count = 0
        self._history: dict[int, Deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.config.llm.history_messages * 2)
        )
        self._workspace = Workspace(self.config.runtime.workspace_dir)
        self._skills = SkillManager(self.config.runtime.skills_dir).load()

    def _build_system_prompt(self) -> str:
        base_prompt = self.config.llm.system_prompt.strip()
        if not self._skills:
            return base_prompt

        skill_lines = [
            "You can call local skills if a user asks you to run one.",
            "Available skills:",
        ]
        for name in sorted(self._skills):
            description = self._skills[name].description or "No description provided."
            skill_lines.append(f"- {name}: {description}")

        skill_lines.extend(
            [
                "When you decide to invoke a skill, output ONLY valid JSON with this shape:",
                '{"skill_call": {"name": "<skill_name>", "args": {"key": "value"}}}',
                "Do not include any extra text before or after JSON.",
                "Use skill_call only when user explicitly requests a skill run or task execution.",
            ]
        )
        return f"{base_prompt}\n\n" + "\n".join(skill_lines)

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
        messages: list[dict[str, str]] = [{"role": "system", "content": self._build_system_prompt()}]
        messages.extend(self._history[chat_id])
        messages.append({"role": "user", "content": text})
        return messages

    def _try_parse_skill_call(self, reply: str) -> dict[str, Any] | None:
        payload_text = reply.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", payload_text, re.DOTALL)
        if fenced:
            payload_text = fenced.group(1).strip()

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None
        skill_call = payload.get("skill_call")
        if not isinstance(skill_call, dict):
            return None
        if not isinstance(skill_call.get("name"), str):
            return None
        args = skill_call.get("args", {})
        if not isinstance(args, dict):
            return None
        return {"name": skill_call["name"], "args": args}

    def _run_skill_call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self._skills:
            available = ", ".join(sorted(self._skills)) or "(none)"
            return f"Unknown skill '{name}'. Available skills: {available}"

        try:
            return self._skills[name].run(self._workspace, args)
        except Exception as exc:
            logging.exception("Skill execution failed for %s", name)
            return f"Skill '{name}' failed: {exc}"

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

    def _strip_think_blocks(self, text: str, *, source: str) -> str:
        if _THINK_BLOCK_RE.search(text):
            logging.debug("Filtered <think> block(s) from %s output", source)
        return _THINK_BLOCK_RE.sub("", text).strip()

    def _read_collector_status(self) -> dict:
        status_path = Path(self.config.runtime.workspace_dir) / self.config.runtime.collector_status_file
        if not status_path.exists():
            return {}
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            logging.exception("Failed to parse collector status file at %s", status_path)
            return {}

    def _build_status_message(self) -> str:
        now = datetime.now(timezone.utc)
        uptime_seconds = int((now - self._started_at).total_seconds())
        lines = [
            "Agent status",
            f"- now: {now.isoformat()}",
            f"- bot_started_at: {self._started_at.isoformat()}",
            f"- bot_uptime_seconds: {uptime_seconds}",
            f"- handled_messages: {self._handled_messages_count}",
            f"- active_chats_in_memory: {len(self._history)}",
        ]

        status = self._read_collector_status()
        if not status:
            lines.append("- collectors: no status data yet")
            return "\n".join(lines)

        lines.append(f"- collector_count: {status.get('collector_count', 0)}")
        lines.append(f"- collector_loop_count: {status.get('loop_count', 0)}")
        lines.append(f"- collector_status_updated_at: {status.get('updated_at', 'unknown')}")

        collectors = status.get("collectors", {})
        for name in sorted(collectors):
            c = collectors[name]
            lines.extend(
                [
                    f"collector:{name}",
                    f"  - runs: {c.get('runs', 0)}",
                    f"  - failures: {c.get('failures', 0)}",
                    f"  - last_success_at: {c.get('last_success_at', 'never')}",
                    f"  - last_error_at: {c.get('last_error_at', 'never')}",
                ]
            )
        return "\n".join(lines)

    def _handle_message(self, chat_id: int, text: str) -> None:
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            logging.warning("Blocked message from unauthorized chat_id=%s", chat_id)
            return

        self._handled_messages_count += 1
        logging.info("Incoming message from chat_id=%s", chat_id)

        if text.strip().lower() == "/status":
            reply = self._build_status_message()
        else:
            prompt = self._build_messages(chat_id, text)
            model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
            skill_call = self._try_parse_skill_call(model_reply)
            if skill_call:
                reply = self._strip_think_blocks(
                    self._run_skill_call(skill_call["name"], skill_call["args"]), source="skill"
                )
            else:
                reply = model_reply
            self._history[chat_id].append({"role": "user", "content": text})
            self._history[chat_id].append({"role": "assistant", "content": reply})

        for chunk in self._split_for_telegram(reply):
            self.telegram.send_message(chat_id, chunk)
        logging.info("Replied to chat_id=%s", chat_id)
