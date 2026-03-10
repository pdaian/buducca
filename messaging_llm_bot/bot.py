from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from collections import defaultdict, deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Deque

from assistant_framework import CollectorManager, SkillManager, Workspace

from .config import BotConfig
from .http import HttpClient, RequestTimeoutError
from .interfaces import IncomingMessage
from .llm_client import OpenAICompatibleClient
from .signal_client import SignalClient, SignalFrontendUnavailableError
from .telegram_client import TelegramClient
from .telegram_user_client import TelegramUserClient
from .whatsapp_client import WhatsAppClient, WhatsAppFrontendUnavailableError
from .google_fi_client import GoogleFiClient, GoogleFiFrontendUnavailableError

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_MAX_SKILL_PARSE_CHARS = 20_000
_MAX_SKILL_PARSE_BRACE_ATTEMPTS = 100
_MAX_SKILL_CHAIN_STEPS = 12
_RESULT_HEADER_RE = re.compile(r"^\d+\.\s")
_TYPING_ACTION_INTERVAL_SECONDS = 4
_TELEGRAM_CONFLICT_INITIAL_BACKOFF_SECONDS = 5.0
_TELEGRAM_CONFLICT_MAX_BACKOFF_SECONDS = 60.0


class BotRunner:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        assert config.llm is not None

        http_client = HttpClient(timeout_seconds=config.runtime.request_timeout_seconds)
        self.telegram = None
        if config.telegram:
            if config.telegram.mode == "user":
                self.telegram = TelegramUserClient(
                    api_id=int(config.telegram.api_id or 0),
                    api_hash=config.telegram.api_hash,
                    session_path=config.telegram.session_path,
                )
            else:
                self.telegram = TelegramClient(bot_token=config.telegram.bot_token, http_client=http_client)
        self.signal = SignalClient(
            account=config.signal.account,
            receive_command=config.signal.receive_command,
            send_command=config.signal.send_command,
            debug=config.runtime.debug or config.runtime.log_level.upper() == "DEBUG",
        ) if config.signal else None
        self.whatsapp = WhatsAppClient(
            receive_command=config.whatsapp.receive_command,
            send_command=config.whatsapp.send_command,
        ) if config.whatsapp else None
        self.google_fi = GoogleFiClient(
            receive_command=config.google_fi.receive_command,
            send_command=config.google_fi.send_command,
        ) if config.google_fi else None
        self.llm = OpenAICompatibleClient(
            config=config.llm,
            http_client=http_client,
            debug=config.runtime.debug or config.runtime.log_level.upper() == "DEBUG",
        )

        self._allowed_chat_ids = set(config.telegram.allowed_chat_ids) if config.telegram else set()
        self._allowed_telegram_sender_ids = set(config.telegram.allowed_sender_ids) if config.telegram else set()
        self._allowed_telegram_group_ids_when_sender_not_allowed = set(config.telegram.allowed_group_ids_when_sender_not_allowed) if config.telegram else set()
        self._allowed_signal_sender_ids = set(config.signal.allowed_sender_ids) if config.signal else set()
        self._allowed_signal_sender_ids_normalized = {
            self._normalize_signal_identifier(sender_id)
            for sender_id in self._allowed_signal_sender_ids
            if self._normalize_signal_identifier(sender_id)
        }
        self._allowed_signal_group_ids_when_sender_not_allowed = (
            set(config.signal.allowed_group_ids_when_sender_not_allowed) if config.signal else set()
        )
        self._allowed_whatsapp_sender_ids = set(config.whatsapp.allowed_sender_ids) if config.whatsapp else set()
        self._allowed_whatsapp_group_ids_when_sender_not_allowed = (
            set(config.whatsapp.allowed_group_ids_when_sender_not_allowed) if config.whatsapp else set()
        )
        self._allowed_google_fi_sender_ids = set(config.google_fi.allowed_sender_ids) if config.google_fi else set()
        self._allowed_google_fi_sender_ids_normalized = {
            self._normalize_signal_identifier(sender_id)
            for sender_id in self._allowed_google_fi_sender_ids
            if self._normalize_signal_identifier(sender_id)
        }
        self._telegram_offset: int | None = None
        self._offset: int | None = None
        self._telegram_conflict_logged_at: float | None = None
        self._telegram_conflict_backoff_seconds = _TELEGRAM_CONFLICT_INITIAL_BACKOFF_SECONDS
        self._telegram_retry_after: float | None = None
        self._signal_frontend_disabled = False
        self._whatsapp_frontend_disabled = False
        self._google_fi_frontend_disabled = False
        self._started_at = datetime.now(timezone.utc)
        self._handled_messages_count = 0
        self._history: dict[Any, Deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.config.llm.history_messages * 2)
        )
        self._workspace = Workspace(self.config.runtime.workspace_dir)
        self._workspace.create_dir("logs")
        self._workspace.write_text("logs/telegram.history", self._workspace.read_text("logs/telegram.history", default=""))
        self._workspace.write_text("logs/signal.history", self._workspace.read_text("logs/signal.history", default=""))
        self._workspace.write_text("logs/whatsapp.history", self._workspace.read_text("logs/whatsapp.history", default=""))
        self._workspace.write_text("logs/google_fi.history", self._workspace.read_text("logs/google_fi.history", default=""))
        self._workspace.write_text("logs/agenta_queries.history", self._workspace.read_text("logs/agenta_queries.history", default=""))
        self._workspace.write_text("telegram.recent", self._workspace.read_text("telegram.recent", default=""))
        self._workspace.write_text("signal.messages.recent", self._workspace.read_text("signal.messages.recent", default=""))
        self._workspace.write_text("whatsapp.messages.recent", self._workspace.read_text("whatsapp.messages.recent", default=""))
        self._workspace.write_text("google_fi.messages.recent", self._workspace.read_text("google_fi.messages.recent", default=""))
        self._workspace.write_text("google_fi.calls.recent", self._workspace.read_text("google_fi.calls.recent", default=""))
        self._recent_unanswered_keys: dict[str, set[str]] = {
            "telegram.recent": set(),
            "signal.messages.recent": set(),
            "whatsapp.messages.recent": set(),
            "google_fi.messages.recent": set(),
            "google_fi.calls.recent": set(),
        }
        self._load_unanswered_recent_keys()
        self._skills = SkillManager(self.config.runtime.skills_dir).load()

    @property
    def _debug_enabled(self) -> bool:
        return self.config.runtime.debug or self.config.runtime.log_level.upper() == "DEBUG"

    def _build_system_prompt(self) -> str:
        base_prompt = self.config.llm.system_prompt.strip()
        configured_timezone = self.config.llm.system_prompt_timezone
        now_in_timezone = datetime.now(ZoneInfo(configured_timezone))
        sections: list[str] = [
            f"Current date/time ({configured_timezone}, accurate to the minute): "
            + now_in_timezone.strftime("%Y-%m-%d %H:%M %Z")
        ]

        if self._skills:
            skill_intro = [
                "You can call local skills if a user asks you to run one.",
                "Available skills:",
            ]
            for name in sorted(self._skills):
                skill = self._skills[name]
                description = skill.description or "No description provided."
                skill_intro.append(f"- {name}: {description}")
                if skill.args_schema:
                    skill_intro.append("  args schema:")
                    for line in skill.args_schema.splitlines():
                        skill_intro.append(f"    {line}")
            sections.append("\n".join(skill_intro))

        learnings_lines = [
            "Persistent learnings (from workspace/learnings):",
            "These are long-term learnings for future prompts. Reuse them whenever relevant.",
        ]
        learnings_text = self._workspace.read_text("learnings", default="")
        saved_learnings = [line.strip() for line in learnings_text.splitlines() if line.strip()]
        if saved_learnings:
            learnings_lines.extend(f"- {line}" for line in saved_learnings)
        else:
            learnings_lines.append("- No learnings recorded yet.")
        sections.append("\n".join(learnings_lines))

        collector_manifests = CollectorManager(
            self.config.runtime.collectors_dir,
            config=self._load_collector_prompt_config(),
        ).load_manifests()
        if collector_manifests:
            collector_lines = [
                "Loaded collector outputs available in the workspace:",
                "Only collectors that are enabled and loaded without errors are listed here.",
            ]
            for manifest in collector_manifests:
                description = manifest.description or "No description provided."
                collector_lines.append(f"- {manifest.name}: {description}")
                if manifest.generated_files:
                    collector_lines.append("  generated workspace files:")
                    for item in manifest.generated_files:
                        collector_lines.append(f"    {item}")
                if manifest.file_structure:
                    collector_lines.append("  module files:")
                    for item in manifest.file_structure:
                        collector_lines.append(f"    {item}")
            sections.append("\n".join(collector_lines))

        if self._skills:
            skill_rules = [
                "When you decide to invoke a skill, output ONLY valid JSON with this shape:",
                '{"skill_call": {"name": "<skill_name>", "args": {"key": "value"}, "done": <true|false>}}',
                "Do not include any extra text before or after JSON.",
                "Use skill_call only when user explicitly requests a skill run or task execution.",
                "If done is false, the tool result will be provided back to you so you can choose the next step.",
                "If done is true, the tool result is usually sent to the user as the final answer.",
                "Some skills may require an additional LLM response before replying to the user.",
                "For research tasks, you may chain multiple skill calls (for example repeated web_search queries) before finalizing.",
                "If you discover durable user preferences or reusable facts, save them with the learn skill as a concise one-line learning.",
            ]

            if "file" in self._skills:
                configured_actions = ", ".join(self.config.runtime.file_skill_actions)
                skill_rules.extend(
                    [
                        "For file-based personal assistant tasks, prefer the file skill instead of expecting task-specific skills.",
                        f"Configured file skill actions: {configured_actions}.",
                        "Use args.action exactly as configured and include concrete file paths under the workspace.",
                        f"File organization guidance: {self.config.llm.file_task_layout_prompt.strip()}",
                    ]
                )

            sections.append("\n".join(skill_rules))

        return f"{base_prompt}\n\n" + "\n\n".join(sections)

    def _load_collector_prompt_config(self) -> dict[str, Any]:
        config_path = Path(self.config.runtime.collector_config_path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            logging.warning("collector_prompt_config event=skipped reason=invalid_json path=%s", config_path)
            return {}
        collectors = raw.get("collectors", {})
        return collectors if isinstance(collectors, dict) else {}

    def run_forever(self) -> None:
        logging.info("Bot started. Waiting for messages...")
        while True:
            try:
                self._poll_frontends_once()
            except KeyboardInterrupt:
                logging.info("Bot interrupted. Exiting.")
                return
            except RequestTimeoutError:
                logging.debug("Long-poll request timed out; retrying")
            except Exception:
                logging.exception("Error while polling or handling message")
                time.sleep(2)

            poll_interval = 0.0
            if self.config.telegram:
                poll_interval = max(poll_interval, self.config.telegram.poll_interval_seconds)
            if self.config.signal:
                poll_interval = max(poll_interval, self.config.signal.poll_interval_seconds)
            if self.config.whatsapp:
                poll_interval = max(poll_interval, self.config.whatsapp.poll_interval_seconds)
            if self.config.google_fi:
                poll_interval = max(poll_interval, self.config.google_fi.poll_interval_seconds)
            time.sleep(poll_interval)

    def _poll_frontends_once(self) -> None:
        if self.telegram and self.config.telegram:
            if self._telegram_retry_after is not None and time.time() < self._telegram_retry_after:
                logging.debug("Telegram polling is in conflict backoff; skipping this cycle")
            else:
                try:
                    if self._telegram_offset is None and not self.config.telegram.process_pending_updates_on_startup:
                        self._telegram_offset = self._offset
                        pending_updates = self.telegram.get_updates(offset=None, timeout_seconds=0)
                        if pending_updates:
                            self._telegram_offset = pending_updates[-1].update_id + 1
                            self._offset = self._telegram_offset
                            logging.info("Skipped %s pending telegram update(s) from before startup", len(pending_updates))

                    telegram_timeout = self.config.telegram.long_poll_timeout_seconds if not self.signal else 0
                    updates = self.telegram.get_updates(offset=self._telegram_offset, timeout_seconds=telegram_timeout)
                except RuntimeError as exc:
                    if self._is_telegram_conflict_error(exc):
                        now = time.time()
                        if (
                            self._telegram_conflict_logged_at is None
                            or now - self._telegram_conflict_logged_at >= 60
                        ):
                            logging.warning(
                                "Telegram polling conflict (HTTP 409): another bot instance is already using getUpdates. "
                                "Will keep retrying with backoff."
                            )
                            self._telegram_conflict_logged_at = now
                        else:
                            logging.debug("Telegram polling conflict (HTTP 409); retrying with backoff")
                        self._telegram_retry_after = now + self._telegram_conflict_backoff_seconds
                        self._telegram_conflict_backoff_seconds = min(
                            self._telegram_conflict_backoff_seconds * 2,
                            _TELEGRAM_CONFLICT_MAX_BACKOFF_SECONDS,
                        )
                        return
                    raise

                self._telegram_conflict_logged_at = None
                self._telegram_retry_after = None
                self._telegram_conflict_backoff_seconds = _TELEGRAM_CONFLICT_INITIAL_BACKOFF_SECONDS
                for update in updates:
                    self._telegram_offset = update.update_id + 1
                    self._offset = self._telegram_offset
                    self._handle_update(update)

        if self.signal and not self._signal_frontend_disabled:
            try:
                for update in self.signal.get_updates():
                    self._handle_update(update)
            except SignalFrontendUnavailableError as exc:
                self._signal_frontend_disabled = True
                logging.warning("%s; continuing with telegram-only frontend", exc)

        if self.whatsapp and not self._whatsapp_frontend_disabled:
            try:
                for update in self.whatsapp.get_updates():
                    self._handle_update(update)
            except WhatsAppFrontendUnavailableError as exc:
                self._whatsapp_frontend_disabled = True
                logging.warning("%s; continuing without whatsapp frontend", exc)

        if self.google_fi and not self._google_fi_frontend_disabled:
            try:
                for update in self.google_fi.get_updates():
                    self._handle_update(update)
            except GoogleFiFrontendUnavailableError as exc:
                self._google_fi_frontend_disabled = True
                logging.warning("%s; continuing without google_fi frontend", exc)

    @staticmethod
    def _is_telegram_conflict_error(exc: RuntimeError) -> bool:
        message = str(exc)
        return "HTTP 409" in message and "/getUpdates" in message

    def _build_messages(
        self,
        conversation_key: str,
        text: str,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self._build_system_prompt()}]
        messages.extend(self._history[conversation_key])

        sender_identity = sender_contact or sender_name or sender_id
        if backend == "telegram":
            sender_context = (
                "[Sender context]\n"
                f"- frontend: telegram\n"
                f"- chat_id: {conversation_id}\n"
                f"- telegram_account: {sender_identity}"
            )
        elif backend == "signal":
            sender_context = (
                "[Sender context]\n"
                f"- frontend: signal\n"
                f"- conversation_id: {conversation_id}\n"
                f"- signal_identity: {sender_identity}"
            )
        else:
            sender_context = (
                "[Sender context]\n"
                f"- frontend: {backend}\n"
                f"- conversation_id: {conversation_id}\n"
                f"- sender: {sender_identity}"
            )

        messages.append({"role": "user", "content": f"{sender_context}\n\n{text}"})
        return messages

    def _history_key(self, backend: str, conversation_id: str) -> Any:
        if backend == "telegram" and conversation_id.lstrip("-").isdigit():
            return int(conversation_id)
        return f"{backend}:{conversation_id}"

    def _try_parse_skill_call(self, reply: str) -> dict[str, Any] | None:
        payload: Any | None = None
        payload_text = reply.strip()
        skill_key_markers = [f'"{name}"' for name in self._skills]
        if (
            "skill_call" not in payload_text
            and '"args"' not in payload_text
            and '"name"' not in payload_text
            and '"done"' not in payload_text
            and not any(marker in payload_text for marker in skill_key_markers)
        ):
            return None

        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", payload_text, re.DOTALL)
        if fenced:
            payload_text = fenced.group(1).strip()

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            if len(payload_text) > _MAX_SKILL_PARSE_CHARS:
                logging.warning(
                    "Skipping skill call recovery parse for oversized reply (%s chars)",
                    len(payload_text),
                )
                return None

            decoder = json.JSONDecoder()
            for idx, match in enumerate(re.finditer(r"\{", payload_text), start=1):
                if idx > _MAX_SKILL_PARSE_BRACE_ATTEMPTS:
                    logging.warning(
                        "Stopping skill call recovery parse after %s brace attempts",
                        _MAX_SKILL_PARSE_BRACE_ATTEMPTS,
                    )
                    break
                try:
                    parsed, _end = decoder.raw_decode(payload_text[match.start() :])
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if isinstance(parsed.get("skill_call"), dict):
                    payload = parsed
                    break
                if len(parsed) == 1:
                    [top_level_name] = parsed.keys()
                    top_level_payload = parsed.get(top_level_name)
                    if isinstance(top_level_payload, dict):
                        payload = parsed
                        break
            if payload is None:
                return None

        if not isinstance(payload, dict):
            return None
        skill_call_payload: Any | None = payload.get("skill_call")
        skill_name: str | None = None

        if isinstance(skill_call_payload, dict):
            skill_name = skill_call_payload.get("name")
        elif len(payload) == 1:
            [top_level_name] = payload.keys()
            top_level_payload = payload.get(top_level_name)
            if isinstance(top_level_payload, dict):
                skill_call_payload = top_level_payload
                declared_name = top_level_payload.get("name")
                skill_name = declared_name if isinstance(declared_name, str) else top_level_name

        if not isinstance(skill_call_payload, dict) or not isinstance(skill_name, str):
            return None

        raw_args = skill_call_payload.get("args")
        if raw_args is None:
            args = {
                key: value
                for key, value in skill_call_payload.items()
                if key not in {"name", "done"}
            }
        else:
            args = raw_args
        if not isinstance(args, dict):
            return None

        nested_done = args.get("done") if isinstance(args.get("done"), bool) else None
        if nested_done is not None:
            args = {key: value for key, value in args.items() if key != "done"}

        done = skill_call_payload.get("done", payload.get("done", nested_done if nested_done is not None else True))
        if not isinstance(done, bool):
            done = True
        return {"name": skill_name, "args": args, "done": done}


    def _skill_requires_llm_response(self, skill_name: str) -> bool:
        skill = self._skills.get(skill_name)
        if skill is None:
            return False
        return skill.requires_llm_response

    def _continue_skill_chain_prompt(self, skill_name: str, skill_result: str, *, allow_follow_up_skill: bool) -> str:
        if allow_follow_up_skill:
            guidance = (
                "If you need another skill call, respond with valid JSON skill_call and done=false. "
                "If no more skills are needed, respond normally to the user."
            )
        else:
            guidance = "Now reply to the user directly using this result. Do not return raw tool output or skill_call JSON."
        return f"Skill `{skill_name}` returned:\n{skill_result}\n\n{guidance}"

    def _summarize_skill_result_for_context(self, skill_name: str, skill_result: str) -> str:
        if skill_name != "web_search" or "DuckDuckGo results for:" not in skill_result:
            return skill_result

        lines = skill_result.splitlines()
        summarized: list[str] = []
        skipping_html = False

        for line in lines:
            stripped = line.strip()
            if stripped == "HTML:":
                skipping_html = True
                continue

            if skipping_html:
                if _RESULT_HEADER_RE.match(line) or line.startswith("DuckDuckGo results for:"):
                    skipping_html = False
                else:
                    continue

            summarized.append(line)

        return "\n".join(summarized).strip() or skill_result

    def _reply_would_split(self, text: str) -> bool:
        return len(text) > self.config.runtime.max_reply_chunk_chars

    def _done_flag_was_explicit(self, model_reply: str) -> bool:
        return bool(re.search(r'"done"\s*:', model_reply))

    def _resolve_llm_reply(self, prompt: list[dict[str, str]], initial_model_reply: str) -> str:
        model_reply = initial_model_reply
        for step_index in range(_MAX_SKILL_CHAIN_STEPS):
            skill_call = self._try_parse_skill_call(model_reply)
            if not skill_call:
                return model_reply

            done_was_explicit = self._done_flag_was_explicit(model_reply)

            raw_skill_result = self._strip_think_blocks(
                self._run_skill_call(skill_call["name"], skill_call["args"]), source="skill"
            )
            summarized_skill_result = self._summarize_skill_result_for_context(skill_call["name"], raw_skill_result)
            requires_llm_response = self._skill_requires_llm_response(skill_call["name"])
            long_skill_output = self._reply_would_split(raw_skill_result)
            should_force_follow_up_call = long_skill_output

            if skill_call["done"] and not requires_llm_response and not should_force_follow_up_call:
                return raw_skill_result

            prompt.append({"role": "assistant", "content": model_reply})
            prompt.append(
                {
                    "role": "user",
                    "content": self._continue_skill_chain_prompt(
                        skill_call["name"],
                        raw_skill_result,
                        allow_follow_up_skill=not skill_call["done"] or not done_was_explicit,
                    ),
                }
            )
            if self._debug_enabled:
                logging.debug(
                    "Skill chain step %s/%s prompt before intermediate LLM call: %s",
                    step_index + 1,
                    _MAX_SKILL_CHAIN_STEPS,
                    prompt,
                )
            model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
            if summarized_skill_result != raw_skill_result:
                prompt[-1]["content"] = self._continue_skill_chain_prompt(
                    skill_call["name"],
                    summarized_skill_result,
                    allow_follow_up_skill=not skill_call["done"] or not done_was_explicit,
                )
            if self._debug_enabled:
                logging.debug(
                    "Skill chain step %s/%s intermediate LLM response: %s",
                    step_index + 1,
                    _MAX_SKILL_CHAIN_STEPS,
                    model_reply,
                )

            if skill_call["done"] and requires_llm_response:
                return model_reply

        logging.warning("Skill chain exceeded max steps (%s)", _MAX_SKILL_CHAIN_STEPS)
        return "I stopped after too many chained skill calls. Please narrow the request and try again."

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
        return self._split_reply(text)

    def _split_reply(self, text: str) -> list[str]:
        max_len = self.config.runtime.max_reply_chunk_chars
        if len(text) <= max_len:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            chunk = text[start : start + max_len]
            chunks.append(chunk)
            start += max_len
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

    def _transcribe_voice_note(self, voice_file_id: str) -> str | None:
        if not self.config.runtime.enable_voice_notes:
            return None

        command_template = self.config.runtime.voice_transcribe_command
        file_path = self.telegram.get_file_path(voice_file_id)
        voice_bytes = self.telegram.download_file(file_path)

        if self._debug_enabled:
            logging.debug(
                "Voice transcription input (telegram): file_id=%s file_path=%s bytes=%s command_template=%s",
                voice_file_id,
                file_path,
                len(voice_bytes),
                command_template,
            )

        with tempfile.TemporaryDirectory() as td:
            input_path = Path(td) / "voice_note.ogg"
            input_path.write_bytes(voice_bytes)

            command = [
                part.replace("{input}", str(input_path)).replace("{input_dir}", str(input_path.parent))
                for part in command_template
            ]
            if not any("{input}" in part for part in command_template):
                command.append(str(input_path))

            proc = subprocess.run(command, capture_output=True, text=True, check=False)
            if self._debug_enabled:
                txt_candidates = sorted(str(candidate) for candidate in input_path.parent.glob("*.txt"))
                json_candidates = sorted(str(candidate) for candidate in input_path.parent.glob("*.json"))
                logging.debug(
                    "Voice transcription command finished (telegram): command=%s returncode=%s stdout_len=%s stderr=%s txt_candidates=%s json_candidates=%s",
                    command,
                    proc.returncode,
                    len(proc.stdout or ""),
                    (proc.stderr or "").strip(),
                    txt_candidates,
                    json_candidates,
                )
            if proc.returncode != 0:
                stderr = proc.stderr.strip() or "no stderr"
                raise RuntimeError(f"voice transcription command failed: {stderr}")

            transcript = proc.stdout.strip()
            if not transcript:
                transcript_path = input_path.with_suffix(".txt")
                if transcript_path.exists():
                    transcript = transcript_path.read_text(encoding="utf-8").strip()

            if not transcript:
                for candidate in sorted(input_path.parent.glob("*.txt")):
                    candidate_text = candidate.read_text(encoding="utf-8").strip()
                    if candidate_text:
                        transcript = candidate_text
                        break

            if not transcript:
                for candidate in sorted(input_path.parent.glob("*.json")):
                    try:
                        payload = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                        transcript = payload["text"].strip()
                        if transcript:
                            break
            if self._debug_enabled:
                logging.debug(
                    "Voice transcription parsed result (telegram): file_id=%s transcript_present=%s transcript_length=%s",
                    voice_file_id,
                    bool(transcript),
                    len(transcript) if transcript else 0,
                )
            return transcript or None

    def _transcribe_voice_file_path(self, voice_file_path: str) -> str | None:
        if not self.config.runtime.enable_voice_notes:
            return None

        input_path = Path(voice_file_path)
        if not input_path.exists():
            raise RuntimeError(f"Voice file does not exist: {voice_file_path}")

        if self._debug_enabled:
            logging.debug(
                "Voice transcription input (signal): voice_file_path=%s exists=%s size_bytes=%s suffix=%s",
                voice_file_path,
                input_path.exists(),
                input_path.stat().st_size if input_path.exists() else None,
                input_path.suffix,
            )

        transcription_input_path = input_path
        temp_input_dir: tempfile.TemporaryDirectory[str] | None = None
        if not input_path.suffix:
            inferred_suffix = ""
            try:
                header = input_path.read_bytes()[:16]
            except OSError:
                header = b""

            if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
                inferred_suffix = ".mp3"
            elif header.startswith(b"OggS"):
                inferred_suffix = ".ogg"
            elif header.startswith(b"fLaC"):
                inferred_suffix = ".flac"
            elif header.startswith(b"RIFF") and b"WAVE" in header:
                inferred_suffix = ".wav"

            if inferred_suffix:
                temp_input_dir = tempfile.TemporaryDirectory()
                transcription_input_path = Path(temp_input_dir.name) / f"signal_voice{inferred_suffix}"
                shutil.copy2(input_path, transcription_input_path)
                if self._debug_enabled:
                    logging.debug(
                        "Signal voice note had no extension; created temp transcription input: original=%s temp=%s inferred_suffix=%s",
                        input_path,
                        transcription_input_path,
                        inferred_suffix,
                    )

        command_template = self.config.runtime.voice_transcribe_command
        command = [
            part.replace("{input}", str(transcription_input_path)).replace("{input_dir}", str(transcription_input_path.parent))
            for part in command_template
        ]
        if not any("{input}" in part for part in command_template):
            command.append(str(transcription_input_path))
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if self._debug_enabled:
            logging.debug(
                "Voice transcription command finished (signal): command=%s returncode=%s stdout_len=%s stderr=%s",
                command,
                proc.returncode,
                len(proc.stdout or ""),
                (proc.stderr or "").strip(),
            )
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"voice transcription command failed: {stderr}")
        transcript = proc.stdout.strip()

        if not transcript:
            transcript_path = transcription_input_path.with_suffix(".txt")
            if transcript_path.exists():
                transcript = transcript_path.read_text(encoding="utf-8").strip()

        if not transcript:
            for candidate in sorted(transcription_input_path.parent.glob("*.txt")):
                candidate_text = candidate.read_text(encoding="utf-8").strip()
                if candidate_text:
                    transcript = candidate_text
                    break

        if not transcript:
            for candidate in sorted(transcription_input_path.parent.glob("*.json")):
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    transcript = payload["text"].strip()
                    if transcript:
                        break

        if temp_input_dir is not None:
            temp_input_dir.cleanup()

        if self._debug_enabled:
            logging.debug(
                "Voice transcription parsed result (signal): voice_file_path=%s transcript_present=%s transcript_length=%s",
                voice_file_path,
                bool(transcript),
                len(transcript) if transcript else 0,
            )
        return transcript or None

    def _backend_is_read_only(self, backend: str) -> bool:
        if backend == "telegram" and self.config.telegram:
            return bool(self.config.telegram.read_only)
        if backend == "signal" and self.config.signal:
            return bool(self.config.signal.read_only)
        if backend == "whatsapp" and self.config.whatsapp:
            return bool(self.config.whatsapp.read_only)
        if backend == "google_fi" and self.config.google_fi:
            return bool(self.config.google_fi.read_only)
        return False

    def _backend_stores_unanswered_messages(self, backend: str) -> bool:
        if backend == "telegram" and self.config.telegram:
            return bool(self.config.telegram.store_unanswered_messages)
        if backend == "signal" and self.config.signal:
            return bool(self.config.signal.store_unanswered_messages)
        if backend == "whatsapp" and self.config.whatsapp:
            return bool(self.config.whatsapp.store_unanswered_messages)
        if backend == "google_fi" and self.config.google_fi:
            return bool(self.config.google_fi.store_unanswered_messages)
        return False

    def _append_unanswered_collector_log(
        self,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        logged_at: str | None = None,
    ) -> None:
        if not self._backend_stores_unanswered_messages(backend):
            return

        if backend == "telegram":
            if not self._should_append_unanswered_message("telegram.recent", conversation_id, sender_id, text):
                return
            payload = self._build_frontend_record(
                backend=backend,
                direction="incoming",
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                account="default",
                source="frontend_log",
                logged_at=logged_at,
            )
            self._workspace.append_text("telegram.recent", json.dumps(payload, ensure_ascii=False) + "\n")
            return

        if backend == "signal":
            if not self._should_append_unanswered_message("signal.messages.recent", conversation_id, sender_id, text):
                return
            payload = self._build_frontend_record(
                backend=backend,
                direction="incoming",
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                sender_name=sender_name,
                sender_contact=sender_contact or sender_id,
                account="default",
                source="frontend_log",
                logged_at=logged_at,
            )
            self._workspace.append_text("signal.messages.recent", json.dumps(payload, ensure_ascii=False) + "\n")
            return

        if backend == "whatsapp":
            if not self._should_append_unanswered_message("whatsapp.messages.recent", conversation_id, sender_id, text):
                return
            payload = self._build_frontend_record(
                backend=backend,
                direction="incoming",
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                sender_name=sender_name,
                sender_contact=sender_contact or sender_id,
                account=self.config.whatsapp.account if self.config.whatsapp else "default",
                source="frontend_log",
                logged_at=logged_at,
            )
            self._workspace.append_text("whatsapp.messages.recent", json.dumps(payload, ensure_ascii=False) + "\n")
            return

        if backend == "google_fi":
            if not self._should_append_unanswered_message("google_fi.messages.recent", conversation_id, sender_id, text):
                return
            payload = self._build_frontend_record(
                backend=backend,
                direction="incoming",
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                sender_name=sender_name,
                sender_contact=sender_contact or sender_id,
                account=self.config.google_fi.account if self.config.google_fi else "default",
                source="frontend_log",
                logged_at=logged_at,
            )
            self._workspace.append_text("google_fi.messages.recent", json.dumps(payload, ensure_ascii=False) + "\n")

    def _load_unanswered_recent_keys(self) -> None:
        for file_path in self._recent_unanswered_keys:
            existing = self._workspace.read_text(file_path, default="")
            for line in existing.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                text = payload.get("text")
                conversation_id = payload.get("conversation_id")
                sender_id = payload.get("sender_id")
                if not isinstance(sender_id, str):
                    sender_id = payload.get("sender")
                if not isinstance(text, str) or not isinstance(conversation_id, str) or not isinstance(sender_id, str):
                    continue
                self._recent_unanswered_keys[file_path].add(self._unanswered_message_key(conversation_id, sender_id, text))

    @staticmethod
    def _unanswered_message_key(conversation_id: str, sender_id: str, text: str) -> str:
        return f"{conversation_id}\n{sender_id}\n{text}"

    def _should_append_unanswered_message(self, file_path: str, conversation_id: str, sender_id: str, text: str) -> bool:
        key = self._unanswered_message_key(conversation_id, sender_id, text)
        known = self._recent_unanswered_keys.get(file_path)
        if known is None:
            self._recent_unanswered_keys[file_path] = {key}
            return True
        if key in known:
            return False
        known.add(key)
        return True

    def _append_agenta_query_log(
        self,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        reply: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
    ) -> None:
        payload = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_contact": sender_contact,
            "query": text,
            "reply": reply,
        }
        self._workspace.append_text("logs/agenta_queries.history", json.dumps(payload, ensure_ascii=False) + "\n")

    def _send_message(self, backend: str, conversation_id: str, text: str) -> None:
        if not text.strip():
            logging.info("Skipping empty outgoing %s message for conversation=%s", backend, conversation_id)
            return
        if self._backend_is_read_only(backend):
            logging.info("Skipping outgoing %s message in read-only mode conversation=%s", backend, conversation_id)
            return
        if backend == "telegram":
            if not self.telegram:
                raise RuntimeError("Telegram frontend is not configured")
            self.telegram.send_message(int(conversation_id), text)
            self._append_frontend_log(
                backend="telegram",
                direction="outgoing",
                conversation_id=conversation_id,
                sender_id="bot",
                text=text,
            )
            return
        if backend == "signal":
            if not self.signal:
                raise RuntimeError("Signal frontend is not configured")
            self.signal.send_message(conversation_id, text)
            self._append_frontend_log(
                backend="signal",
                direction="outgoing",
                conversation_id=conversation_id,
                sender_id="bot",
                text=text,
            )
            return
        if backend == "whatsapp":
            if not self.whatsapp:
                raise RuntimeError("WhatsApp frontend is not configured")
            self.whatsapp.send_message(conversation_id, text)
            self._append_frontend_log(
                backend="whatsapp",
                direction="outgoing",
                conversation_id=conversation_id,
                sender_id="bot",
                text=text,
            )
            return
        if backend == "google_fi":
            if not self.google_fi:
                raise RuntimeError("Google Fi frontend is not configured")
            self.google_fi.send_message(conversation_id, text)
            self._append_frontend_log(
                backend="google_fi",
                direction="outgoing",
                conversation_id=conversation_id,
                sender_id="bot",
                text=text,
            )
            return
        raise RuntimeError(f"Unsupported backend: {backend}")

    def _append_frontend_log(
        self,
        *,
        backend: str,
        direction: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        logged_at: str | None = None,
    ) -> None:
        history_file = f"logs/{backend}.history"
        payload = self._build_frontend_record(
            backend=backend,
            direction=direction,
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text,
            sender_name=sender_name,
            sender_contact=sender_contact,
            source="frontend_log",
            logged_at=logged_at or datetime.now(timezone.utc).isoformat(),
        )
        self._workspace.append_text(history_file, json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_frontend_record(
        self,
        *,
        backend: str,
        direction: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        account: str | None = None,
        source: str = "frontend_log",
        logged_at: str | None = None,
    ) -> dict[str, str | None]:
        timestamp = logged_at or datetime.now(timezone.utc).isoformat()
        return {
            "logged_at": timestamp,
            "collected_at": timestamp,
            "source": source,
            "backend": backend,
            "account": account or "default",
            "direction": direction,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_contact": sender_contact,
            "text": text,
        }

    def _is_authorized_frontend_sender(self, backend: str, conversation_id: str, sender_id: str) -> bool:
        if backend == "telegram":
            chat_id = int(conversation_id)
            telegram_sender_id = int(sender_id)
            if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
                logging.warning("Blocked message from unauthorized telegram chat_id=%s", chat_id)
                return False
            if self._allowed_telegram_sender_ids:
                if telegram_sender_id in self._allowed_telegram_sender_ids:
                    return True
                if chat_id in self._allowed_telegram_group_ids_when_sender_not_allowed:
                    return True
                logging.warning(
                    "Blocked message from unauthorized telegram sender_id=%s conversation_id=%s",
                    sender_id,
                    conversation_id,
                )
                return False

        if backend == "signal" and self.config.signal:
            normalized_sender_id = self._normalize_signal_identifier(sender_id)
            sender_is_allowed = sender_id in self._allowed_signal_sender_ids or (
                bool(normalized_sender_id) and normalized_sender_id in self._allowed_signal_sender_ids_normalized
            )
            if sender_is_allowed:
                return True

            signal_group_id = self._extract_signal_group_id(conversation_id)
            if signal_group_id and signal_group_id in self._allowed_signal_group_ids_when_sender_not_allowed:
                return True

            if self._is_signal_self_sender(sender_id):
                if signal_group_id:
                    logging.warning(
                        "Blocked message from signal account sender_id=%s because it is not in signal.allowed_sender_ids group_id=%s",
                        sender_id,
                        signal_group_id,
                    )
                else:
                    logging.warning(
                        "Blocked message from signal account sender_id=%s because it is not in signal.allowed_sender_ids",
                        sender_id,
                    )
                return False

            logging.warning(
                "Blocked message from unauthorized signal sender_id=%s conversation_id=%s",
                sender_id,
                conversation_id,
            )
            return False

        if backend == "whatsapp" and self.config.whatsapp:
            if not self._allowed_whatsapp_sender_ids:
                return True
            if sender_id in self._allowed_whatsapp_sender_ids:
                return True
            whatsapp_group_id = self._extract_whatsapp_group_id(conversation_id)
            if whatsapp_group_id and whatsapp_group_id in self._allowed_whatsapp_group_ids_when_sender_not_allowed:
                return True
            logging.warning(
                "Blocked message from unauthorized whatsapp sender_id=%s conversation_id=%s",
                sender_id,
                conversation_id,
            )
            return False

        if backend == "google_fi" and self.config.google_fi:
            if not self._allowed_google_fi_sender_ids:
                return True
            normalized_sender_id = self._normalize_signal_identifier(sender_id)
            if sender_id in self._allowed_google_fi_sender_ids or (
                bool(normalized_sender_id) and normalized_sender_id in self._allowed_google_fi_sender_ids_normalized
            ):
                return True
            logging.warning(
                "Blocked message from unauthorized google_fi sender_id=%s conversation_id=%s",
                sender_id,
                conversation_id,
            )
            return False
        return True

    def _is_signal_self_sender(self, sender_id: str) -> bool:
        if not self.config.signal:
            return False
        account = self.config.signal.account
        if sender_id == account:
            return True
        normalized_sender = self._normalize_signal_identifier(sender_id)
        normalized_account = self._normalize_signal_identifier(account)
        return bool(normalized_sender and normalized_sender == normalized_account)

    @staticmethod
    def _normalize_signal_identifier(identifier: str) -> str:
        return "".join(ch for ch in identifier if ch == "+" or ch.isdigit())

    def _handle_update(self, update: IncomingMessage) -> None:
        backend = getattr(update, "backend", "telegram")
        conversation_id = getattr(update, "conversation_id", "") or str(getattr(update, "chat_id", ""))
        sender_id = getattr(update, "sender_id", conversation_id)
        sender_name = getattr(update, "sender_name", None)
        sender_contact = getattr(update, "sender_contact", None)
        sent_at = getattr(update, "sent_at", None)

        if not sender_contact:
            sender_contact = sender_id
            if backend == "signal" and sender_name:
                sender_contact = f"{sender_name} <{sender_id}>"

        if update.text:
            self._append_frontend_log(
                backend=backend,
                direction="incoming",
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=update.text,
                sender_name=sender_name,
                sender_contact=sender_contact,
                logged_at=sent_at,
            )
            if backend == "google_fi" and getattr(update, "event_type", "message") == "call":
                if not self._should_append_unanswered_message("google_fi.calls.recent", conversation_id, sender_id, update.text):
                    return
                payload = self._build_frontend_record(
                    backend=backend,
                    direction="incoming",
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=update.text,
                    sender_name=sender_name,
                    sender_contact=sender_contact or sender_id,
                    account=self.config.google_fi.account if self.config.google_fi else "default",
                    source="frontend_log",
                    logged_at=sent_at,
                )
                self._workspace.append_text("google_fi.calls.recent", json.dumps(payload, ensure_ascii=False) + "\n")
                return
            if not self._is_authorized_frontend_sender(backend, conversation_id, sender_id):
                self._append_unanswered_collector_log(
                    backend=backend,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=update.text,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    logged_at=sent_at,
                )
                return
            was_handled = self._handle_message(backend, conversation_id, sender_id, update.text, sender_name, sender_contact)
            if backend == "google_fi" and not was_handled:
                self._append_unanswered_collector_log(
                    backend=backend,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=update.text,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    logged_at=sent_at,
                )
            return

        voice_file_id = getattr(update, "voice_file_id", None)
        voice_file_path = getattr(update, "voice_file_path", None)
        if not voice_file_id and not voice_file_path:
            return

        if not self._is_authorized_frontend_sender(backend, conversation_id, sender_id):
            return

        if self._debug_enabled:
            logging.debug(
                "Voice update received: backend=%s conversation=%s sender=%s voice_file_id=%s voice_file_path=%s path_exists=%s",
                backend,
                conversation_id,
                sender_id,
                voice_file_id,
                voice_file_path,
                Path(voice_file_path).exists() if voice_file_path else None,
            )

        try:
            transcript = self._transcribe_voice_note(voice_file_id) if voice_file_id else self._transcribe_voice_file_path(voice_file_path)
        except Exception:
            logging.exception("Failed to transcribe voice note for backend=%s conversation=%s", backend, conversation_id)
            self._send_message(backend, conversation_id, "I could not transcribe that voice note locally.")
            return

        if not transcript:
            self._send_message(backend, conversation_id, "I received your voice note but could not extract text.")
            return

        transcript_text = f"[Voice note transcript]\n{transcript}"
        self._append_frontend_log(
            backend=backend,
            direction="incoming",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=transcript_text,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        self._handle_message(backend, conversation_id, sender_id, transcript_text, sender_name, sender_contact)

    def _extract_signal_group_id(self, conversation_id: str) -> str:
        if not conversation_id.startswith(SignalClient.GROUP_CONVERSATION_PREFIX):
            return ""
        group_part = conversation_id[len(SignalClient.GROUP_CONVERSATION_PREFIX):]
        if not group_part:
            return ""
        if SignalClient.GROUP_ID_DELIMITER in group_part:
            return group_part.rsplit(SignalClient.GROUP_ID_DELIMITER, 1)[-1]
        return group_part

    def _extract_whatsapp_group_id(self, conversation_id: str) -> str:
        if not conversation_id.startswith(WhatsAppClient.GROUP_CONVERSATION_PREFIX):
            return ""
        group_part = conversation_id[len(WhatsAppClient.GROUP_CONVERSATION_PREFIX):]
        if not group_part:
            return ""
        if WhatsAppClient.GROUP_ID_DELIMITER in group_part:
            return group_part.rsplit(WhatsAppClient.GROUP_ID_DELIMITER, 1)[-1]
        return group_part

    def _handle_message(self, *args: Any) -> bool:
        sender_name: str | None = None
        sender_contact: str | None = None
        if len(args) == 2:
            backend = "telegram"
            conversation_id = str(args[0])
            sender_id = str(args[0])
            text = args[1]
        elif len(args) == 4:
            backend = str(args[0])
            conversation_id = str(args[1])
            sender_id = str(args[2])
            text = str(args[3])
        elif len(args) == 6:
            backend = str(args[0])
            conversation_id = str(args[1])
            sender_id = str(args[2])
            text = str(args[3])
            sender_name = str(args[4]) if args[4] is not None else None
            sender_contact = str(args[5]) if args[5] is not None else None
        else:
            raise TypeError(
                "_handle_message expects (chat_id, text), (backend, conversation_id, sender_id, text), "
                "or (backend, conversation_id, sender_id, text, sender_name, sender_contact)"
            )
        if not self._is_authorized_frontend_sender(backend, conversation_id, sender_id):
            self._append_unanswered_collector_log(
                backend=backend,
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                sender_name=sender_name,
                sender_contact=sender_contact,
            )
            return False

        if self._backend_is_read_only(backend):
            self._append_unanswered_collector_log(
                backend=backend,
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                sender_name=sender_name,
                sender_contact=sender_contact,
            )
            return False

        conversation_key = self._history_key(backend, conversation_id)

        self._handled_messages_count += 1
        logging.info("Incoming message from %s conversation=%s sender=%s", backend, conversation_id, sender_contact or sender_name or sender_id)

        if text.strip().lower() == "/status":
            reply = self._build_status_message()
        else:
            with self._typing_indicator(backend, conversation_id):
                prompt = self._build_messages(
                    conversation_key,
                    text,
                    backend=backend,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                )
                try:
                    model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
                    reply = self._resolve_llm_reply(prompt, model_reply)
                except RequestTimeoutError:
                    logging.warning("LLM request timed out for %s conversation=%s", backend, conversation_id)
                    self._send_message(
                        backend,
                        conversation_id,
                        "The language model request timed out "
                        f"after {self.config.runtime.request_timeout_seconds:g}s. "
                        "Increase runtime.request_timeout_seconds in config.json if your model needs more time.",
                    )
                    return False
                except Exception:
                    logging.exception("Failed to generate or parse LLM response for %s conversation=%s", backend, conversation_id)
                    self._send_message(
                        backend,
                        conversation_id,
                        "I ran into an internal error while handling that request. "
                        "Please try again.",
                    )
                    return False
            self._history[conversation_key].append({"role": "user", "content": text})
            self._history[conversation_key].append(
                {
                    "role": "assistant",
                    "content": self._summarize_skill_result_for_context("web_search", reply),
                }
            )

        for chunk in self._split_reply(reply):
            self._send_message(backend, conversation_id, chunk)
        self._append_agenta_query_log(
            backend=backend,
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text,
            reply=reply,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        logging.info("Replied to %s conversation=%s", backend, conversation_id)
        return True

    @contextmanager
    def _typing_indicator(self, backend: str, conversation_id: str):
        if backend != "telegram" or not self.telegram:
            yield
            return
        stop_event = threading.Event()

        def _send_typing_actions() -> None:
            while not stop_event.is_set():
                try:
                    self.telegram.send_typing_action(int(conversation_id))
                except Exception:
                    logging.debug("Failed to send typing action for chat_id=%s", conversation_id, exc_info=True)
                    return
                stop_event.wait(_TYPING_ACTION_INTERVAL_SECONDS)

        worker = threading.Thread(target=_send_typing_actions, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop_event.set()
            worker.join(timeout=0.2)
