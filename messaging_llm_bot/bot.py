from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Deque

from assistant_framework import CollectorManager, SkillManager, Workspace
from assistant_framework.action_runtime import append_action_audit, decide_action, load_action_policy
from assistant_framework.config_files import load_named_config_map
from assistant_framework.ingestion import ingest_attachment
from assistant_framework.memory import list_records, mark_routine_run, mark_task_notified
from assistant_framework.retrieval import (
    append_sources,
    build_structured_memory_context,
    format_evidence_context,
    search_workspace,
)
from assistant_framework.reminders import REMINDERS_FILE, parse_unix_time, serialize_reminder_record
from assistant_framework.traces import write_trace

from .config import BotConfig, ContactConfig, WORKSPACE_CONTACT_MAP_FILES
from .http import HttpClient, RequestTimeoutError
from .interfaces import IncomingAttachment, IncomingMessage
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
_MAX_REMINDER_FILE_CHARS = 4_000
_MAX_REMINDER_TOTAL_FILE_CHARS = 12_000
_HOURLY_NO_ACTION_REPLY = "NO_ACTION"
_SKILL_PASSTHROUGH_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_CONTACT_HANDLE_RE = re.compile(r"@\w[\w.]*")
_CONTACT_ANGLE_RE = re.compile(r"<([^>]+)>")


@dataclass
class FrontendWorkerState:
    name: str
    poll_interval_seconds: float
    thread: threading.Thread | None = None
    polls: int = 0
    updates_handled: int = 0
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    disabled: bool = False
    fatal_exception: BaseException | None = None


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
        self._recent_unanswered_keys: dict[str, set[str]] = {
            "telegram.recent": set(),
            "signal.messages.recent": set(),
            "whatsapp.messages.recent": set(),
            "google_fi.messages.recent": set(),
            "google_fi.calls.recent": set(),
        }
        self._load_unanswered_recent_keys()
        self._last_hourly_slot = self._load_last_hourly_slot()
        self._skills = self._load_runtime_skills()
        self._current_evidence = []
        self._recent_handled_queries: dict[tuple[str, str, str], str] = {}
        self._processing_lock = threading.RLock()
        self._frontend_state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frontend_workers = self._build_frontend_workers()

    @property
    def _debug_enabled(self) -> bool:
        return self.config.runtime.debug or self.config.runtime.log_level.upper() == "DEBUG"

    def _load_runtime_skills(self) -> dict[str, Any]:
        skills = SkillManager(self.config.runtime.skills_dir).load()
        if not self.config.runtime.enable_message_send_skill:
            skills.pop("message_send", None)
        return skills

    def _refresh_skills(self) -> None:
        self._skills = self._load_runtime_skills()

    @staticmethod
    def _read_skill_doc_section(readme_path: Path | None, heading: str) -> str:
        if readme_path is None or not readme_path.exists():
            return ""
        readme_text = readme_path.read_text(encoding="utf-8")
        match = re.search(
            rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
            readme_text,
            flags=re.MULTILINE | re.DOTALL,
        )
        if match is None:
            return ""
        section = match.group(1).strip()
        lines: list[str] = []
        in_code_block = False
        for raw_line in section.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or not stripped:
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()

    def _build_skill_command_help(self, skill_name: str, skill: Any) -> str:
        lines = [
            f"Skill: {skill_name}",
            f"- description: {skill.description or 'No description provided.'}",
        ]
        what_it_does = self._read_skill_doc_section(skill.readme_path, "What it does")
        if what_it_does:
            lines.append("- documentation:")
            lines.extend(f"  {line}" for line in what_it_does.splitlines())
        if skill.args_schema:
            lines.append("- args_schema:")
            lines.extend(f"  {line}" for line in skill.args_schema.splitlines())
        lines.extend(
            [
                f'- run: /skill {skill_name} {{"key":"value"}}',
                f"- passthrough: /skill {skill_name} key:value",
                f"- explicit_run: /skill run {skill_name} {{\"key\":\"value\"}}",
            ]
        )
        return "\n".join(lines)

    def _build_skill_command_overview(self) -> str:
        self._refresh_skills()
        lines = [
            "Skill command",
            "- usage: /skill",
            "- list: /skill list",
            "- docs: /skill <skill_name>",
            "- run: /skill <skill_name> {\"key\":\"value\"}",
            "- passthrough: /skill <skill_name> key:value",
            "- explicit_run: /skill run <skill_name> {\"key\":\"value\"}",
        ]
        if not self._skills:
            lines.append("- available_skills: none loaded")
            return "\n".join(lines)
        lines.append("- available_skills:")
        for name in sorted(self._skills):
            description = self._skills[name].description or "No description provided."
            lines.append(f"  - {name}: {description}")
        return "\n".join(lines)

    def _handle_skill_command(self, text: str) -> str:
        self._refresh_skills()
        payload = text.strip()[len("/skill"):].strip()
        if not payload or payload == "list":
            return self._build_skill_command_overview()

        if payload.startswith("help "):
            skill_name = payload[5:].strip()
            if not skill_name:
                return "Usage: /skill help <skill_name>"
            skill = self._skills.get(skill_name)
            if skill is None:
                available = ", ".join(sorted(self._skills)) or "(none)"
                return f"Unknown skill '{skill_name}'. Available skills: {available}"
            return self._build_skill_command_help(skill_name, skill)

        explicit_run = payload.startswith("run ")
        remainder = payload[4:].strip() if explicit_run else payload
        if not remainder:
            return "Usage: /skill run <skill_name> {\"key\":\"value\"}"

        skill_name, separator, raw_args = remainder.partition(" ")
        if not separator and not explicit_run:
            skill = self._skills.get(skill_name)
            if skill is None:
                available = ", ".join(sorted(self._skills)) or "(none)"
                return f"Unknown skill '{skill_name}'. Available skills: {available}"
            return self._build_skill_command_help(skill_name, skill)

        skill = self._skills.get(skill_name)
        if skill is None:
            available = ", ".join(sorted(self._skills)) or "(none)"
            return f"Unknown skill '{skill_name}'. Available skills: {available}"

        args_text = raw_args.strip()
        if not args_text:
            args: dict[str, Any] = {}
        else:
            try:
                parsed_args = json.loads(args_text)
            except json.JSONDecodeError as exc:
                if not args_text.startswith("{") and not args_text.startswith("["):
                    parsed_args = self._parse_skill_passthrough_args(args_text)
                else:
                    parsed_args = None
                if parsed_args is not None:
                    args = parsed_args
                    return self._run_skill_call(skill_name, args)
                return (
                    f"Invalid JSON args for skill '{skill_name}': {exc.msg} at line {exc.lineno} column {exc.colno}. "
                    f'Examples: /skill {skill_name} {{"key":"value"}} or /skill {skill_name} key:value'
                )
            if not isinstance(parsed_args, dict):
                return f"Invalid args for skill '{skill_name}': expected a JSON object."
            args = parsed_args

        return self._run_skill_call(skill_name, args)

    @classmethod
    def _parse_skill_passthrough_args(cls, text: str) -> dict[str, Any] | None:
        if "\n" in text:
            parts = [part.strip() for part in text.splitlines() if part.strip()]
        elif "," in text:
            parts = [part.strip() for part in text.split(",") if part.strip()]
        else:
            parts = [text.strip()]
        if not parts:
            return None

        parsed: dict[str, Any] = {}
        for part in parts:
            key, separator, raw_value = part.partition(":")
            key = key.strip()
            value_text = raw_value.strip()
            if not separator or not key or not cls._is_valid_passthrough_key(key):
                return None
            if not value_text:
                return None
            parsed[key] = cls._parse_skill_passthrough_value(value_text)
        return parsed

    @staticmethod
    def _is_valid_passthrough_key(key: str) -> bool:
        return bool(_SKILL_PASSTHROUGH_KEY_RE.fullmatch(key))

    @staticmethod
    def _parse_skill_passthrough_value(value_text: str) -> Any:
        if value_text[:1] in {'"', "[", "{"}:
            try:
                return json.loads(value_text)
            except json.JSONDecodeError:
                return value_text
        if value_text in {"true", "false", "null"}:
            return json.loads(value_text)
        try:
            return json.loads(value_text)
        except json.JSONDecodeError:
            return value_text

    def _build_system_prompt(self) -> str:
        self._refresh_skills()
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
            workspace_root = Path(self.config.runtime.workspace_dir)
            collectors_root = Path(self.config.runtime.collectors_dir).parent
            collector_lines = [
                "Loaded collector outputs available in the workspace:",
                "Only collectors that are enabled and loaded without errors are listed here.",
            ]
            for manifest in collector_manifests:
                generated_files = self._existing_nonempty_prompt_files(manifest.generated_files, base_dir=workspace_root)
                module_files = self._existing_nonempty_prompt_files(manifest.file_structure, base_dir=collectors_root)
                if not generated_files and not module_files:
                    continue
                description = manifest.description or "No description provided."
                collector_lines.append(f"- {manifest.name}: {description}")
                if generated_files:
                    collector_lines.append("  generated workspace files:")
                    for item in generated_files:
                        collector_lines.append(f"    {item}")
                if module_files:
                    collector_lines.append("  module files:")
                    for item in module_files:
                        collector_lines.append(f"    {item}")
            if len(collector_lines) > 2:
                sections.append("\n".join(collector_lines))

        if self._skills:
            skill_rules = [
                "When you decide to invoke a skill, output ONLY valid JSON with this shape:",
                '{"skill_call": {"name": "<skill_name>", "args": {"key": "value"}, "done": <true|false>}}',
                "Do not include any extra text before or after JSON.",
                "Use skill_call only when user explicitly requests a skill run or task execution.",
                "If done is omitted, it defaults to false.",
                "If done is false, the tool result will be provided back to you so you can choose the next step.",
                "If done is true, the tool result is usually sent to the user as the final answer.",
                "Some skills may require an additional LLM response before replying to the user.",
                "For research tasks, you may chain multiple skill calls (for example repeated web_search queries) before finalizing.",
                "If you discover durable user preferences or reusable facts, save them with the learn skill as a concise one-line learning.",
                "When workspace evidence is provided, prefer it over memory guesses and cite the source paths you used.",
                "Do not mention source paths unless you explicitly referenced them in the answer.",
                "Incoming attachments are saved under workspace/attachments/YYYY-MM-DD/.",
                "Saved filenames include the sending platform, sender name, and Unix timestamp; PDFs also get a sibling .ocr.txt file when local extraction or OCR succeeds.",
                "If attachment paths or OCR text are included in a user turn, use them as first-party workspace context.",
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

            if "message_send" in self._skills and self.config.contacts:
                skill_rules.extend(self._build_message_send_contact_prompt_lines())

            sections.append("\n".join(skill_rules))

        return f"{base_prompt}\n\n" + "\n\n".join(sections)

    def _build_message_send_contact_prompt_lines(self) -> list[str]:
        lines = [
            "When using the message_send skill, prefer these configured contacts instead of guessing recipient ids.",
            "Each contact entry already uses the same recipient format expected by message_send args.recipient or args.recipients.",
            "Use the exact recipient value for the matching platform. For Telegram use the numeric chat id. For WhatsApp groups use the listed conversation id.",
            "Configured contacts:",
        ]
        for contact in self.config.contacts:
            summary = f"- {contact.name} [{contact.platform}] -> {contact.recipient}"
            if contact.description:
                summary += f" ({contact.description})"
            lines.append(summary)
        return lines

    @staticmethod
    def _existing_nonempty_prompt_files(items: list[str], *, base_dir: Path) -> list[str]:
        present: list[str] = []
        for item in items:
            path_text = str(item).strip()
            if not path_text:
                continue
            path = Path(path_text)
            if not path.is_absolute():
                path = base_dir / path
            try:
                if not path.is_file() or path.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            present.append(path_text)
        return present

    def _load_collector_prompt_config(self) -> dict[str, Any]:
        try:
            return load_named_config_map(self.config.runtime.collector_config_path, section_name="collectors")
        except ValueError:
            logging.warning(
                "collector_prompt_config event=skipped reason=invalid_config path=%s",
                self.config.runtime.collector_config_path,
            )
            return {}

    def run_forever(self) -> None:
        logging.info("Bot started. Waiting for messages...")
        if self.signal and self.config.signal:
            logging.info(
                "Signal frontend configured: account=%s poll_interval_seconds=%s receive_command=%s",
                self.config.signal.account,
                self.config.signal.poll_interval_seconds,
                getattr(self.signal, "receive_command", "<unavailable>"),
            )
        self._stop_event.clear()
        self._start_frontend_workers()
        try:
            while True:
                self._raise_worker_failure_if_any()
                try:
                    with self._processing_lock:
                        self._poll_due_structured_schedule_once()
                        self._poll_due_reminders_once()
                        self._poll_due_hourly_once()
                except RequestTimeoutError:
                    logging.debug("Long-poll request timed out; retrying")
                except Exception:
                    logging.exception("Error while polling or handling message")
                    if self._stop_event.wait(2):
                        break
                    continue
                self._raise_worker_failure_if_any()
                if self._stop_event.wait(self._scheduler_poll_interval_seconds()):
                    break
        except KeyboardInterrupt:
            logging.info("Bot interrupted. Exiting.")
            return
        finally:
            self._stop_frontend_workers()

    def _build_frontend_workers(self) -> dict[str, FrontendWorkerState]:
        workers: dict[str, FrontendWorkerState] = {}
        if self.telegram and self.config.telegram:
            workers["telegram"] = FrontendWorkerState(name="telegram", poll_interval_seconds=self.config.telegram.poll_interval_seconds)
        if self.signal and self.config.signal:
            workers["signal"] = FrontendWorkerState(name="signal", poll_interval_seconds=self.config.signal.poll_interval_seconds)
        if self.whatsapp and self.config.whatsapp:
            workers["whatsapp"] = FrontendWorkerState(name="whatsapp", poll_interval_seconds=self.config.whatsapp.poll_interval_seconds)
        if self.google_fi and self.config.google_fi:
            workers["google_fi"] = FrontendWorkerState(name="google_fi", poll_interval_seconds=self.config.google_fi.poll_interval_seconds)
        return workers

    def _scheduler_poll_interval_seconds(self) -> float:
        intervals = [state.poll_interval_seconds for state in self._frontend_workers.values() if state.poll_interval_seconds > 0]
        return min(intervals, default=1.0)

    def _sync_frontend_workers(self) -> None:
        desired_workers = self._build_frontend_workers()
        for name, desired_state in desired_workers.items():
            existing = self._frontend_workers.get(name)
            if existing is None:
                self._frontend_workers[name] = desired_state
                continue
            existing.poll_interval_seconds = desired_state.poll_interval_seconds

    def _start_frontend_workers(self) -> None:
        self._sync_frontend_workers()
        for name, state in self._frontend_workers.items():
            if state.thread is not None and state.thread.is_alive():
                continue
            logging.debug(
                "Starting frontend worker: frontend=%s poll_interval_seconds=%s",
                name,
                state.poll_interval_seconds,
            )
            worker = threading.Thread(target=self._run_frontend_worker, args=(name,), daemon=True, name=f"{name}-frontend")
            state.thread = worker
            worker.start()

    def _stop_frontend_workers(self) -> None:
        self._stop_event.set()
        for state in self._frontend_workers.values():
            thread = state.thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def _raise_worker_failure_if_any(self) -> None:
        for state in self._frontend_workers.values():
            if state.fatal_exception is None:
                continue
            exc = state.fatal_exception
            state.fatal_exception = None
            raise exc

    def _run_frontend_worker(self, frontend: str) -> None:
        state = self._frontend_workers[frontend]
        while not self._stop_event.is_set():
            with self._frontend_state_lock:
                state.last_started_at = datetime.now(timezone.utc).isoformat()
            try:
                updates_handled = self._poll_single_frontend(frontend)
            except RequestTimeoutError:
                with self._frontend_state_lock:
                    state.last_error_at = datetime.now(timezone.utc).isoformat()
                    state.last_error = "request timeout"
                logging.debug("Long-poll request timed out; retrying")
            except BaseException as exc:
                with self._frontend_state_lock:
                    state.last_error_at = datetime.now(timezone.utc).isoformat()
                    state.last_error = str(exc) or exc.__class__.__name__
                    state.fatal_exception = exc
                self._stop_event.set()
                return
            else:
                finished_at = datetime.now(timezone.utc).isoformat()
                with self._frontend_state_lock:
                    state.polls += 1
                    state.updates_handled += updates_handled
                    state.last_finished_at = finished_at
                    state.last_success_at = finished_at
                    state.last_error = None
                if self._stop_event.wait(state.poll_interval_seconds):
                    return

    def _poll_single_frontend(self, frontend: str) -> int:
        if frontend == "telegram":
            return self._poll_telegram_once()
        if frontend == "signal":
            return self._poll_signal_once()
        if frontend == "whatsapp":
            return self._poll_whatsapp_once()
        if frontend == "google_fi":
            return self._poll_google_fi_once()
        raise ValueError(f"Unknown frontend: {frontend}")

    def _handle_updates_with_lock(self, updates: list[IncomingMessage]) -> None:
        with self._processing_lock:
            for update in updates:
                self._handle_update(update)

    def _set_frontend_disabled(self, frontend: str, *, disabled: bool = True, error: str | None = None) -> None:
        state = self._frontend_workers.get(frontend)
        if state is None:
            return
        with self._frontend_state_lock:
            state.disabled = disabled
            if error is not None:
                state.last_error_at = datetime.now(timezone.utc).isoformat()
                state.last_error = error

    def _frontend_status_lines(self) -> list[str]:
        if not self._frontend_workers:
            return ["- frontends: none configured"]

        lines = [f"- frontend_count: {len(self._frontend_workers)}"]
        with self._frontend_state_lock:
            for name in sorted(self._frontend_workers):
                state = self._frontend_workers[name]
                thread = state.thread
                lines.extend(
                    [
                        f"frontend:{name}",
                        f"  - thread_alive: {thread.is_alive() if thread is not None else False}",
                        f"  - disabled: {state.disabled}",
                        f"  - poll_interval_seconds: {state.poll_interval_seconds}",
                        f"  - polls: {state.polls}",
                        f"  - updates_handled: {state.updates_handled}",
                        f"  - last_started_at: {state.last_started_at or 'never'}",
                        f"  - last_finished_at: {state.last_finished_at or 'never'}",
                        f"  - last_success_at: {state.last_success_at or 'never'}",
                        f"  - last_error_at: {state.last_error_at or 'never'}",
                        f"  - last_error: {state.last_error or 'none'}",
                    ]
                )
                if name == "telegram":
                    retry_after = self._telegram_retry_after
                    lines.append(
                        f"  - retry_after: {datetime.fromtimestamp(retry_after, tz=timezone.utc).isoformat() if retry_after else 'none'}"
                    )
        return lines

    def _poll_frontends_once(self) -> None:
        with self._processing_lock:
            self._poll_due_structured_schedule_once()
            self._poll_due_reminders_once()
            self._poll_due_hourly_once()
            self._poll_telegram_once()
            self._poll_signal_once()
            self._poll_whatsapp_once()
            self._poll_google_fi_once()

    def _poll_telegram_once(self) -> int:
        if not self.telegram or not self.config.telegram:
            return 0
        if self._telegram_retry_after is not None and time.time() < self._telegram_retry_after:
            logging.debug("Telegram polling is in conflict backoff; skipping this cycle")
            return 0
        try:
            if (
                self._telegram_offset is None
                and self.config.telegram.mode == "bot"
                and not self.config.telegram.process_pending_updates_on_startup
            ):
                self._telegram_offset = self._offset
                pending_updates = self.telegram.get_updates(offset=None, timeout_seconds=0)
                if pending_updates:
                    self._telegram_offset = pending_updates[-1].update_id + 1
                    self._offset = self._telegram_offset
                    logging.info("Skipped %s pending telegram update(s) from before startup", len(pending_updates))

            updates = self.telegram.get_updates(
                offset=self._telegram_offset,
                timeout_seconds=self.config.telegram.long_poll_timeout_seconds,
            )
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
                self._set_frontend_disabled("telegram", disabled=False, error="telegram conflict backoff")
                return 0
            raise

        self._telegram_conflict_logged_at = None
        self._telegram_retry_after = None
        self._telegram_conflict_backoff_seconds = _TELEGRAM_CONFLICT_INITIAL_BACKOFF_SECONDS
        with self._processing_lock:
            for update in updates:
                self._telegram_offset = update.update_id + 1
                self._offset = self._telegram_offset
                self._handle_update(update)
        return len(updates)

    def _poll_signal_once(self) -> int:
        if not self.signal or self._signal_frontend_disabled:
            if self._debug_enabled and self.signal:
                logging.debug(
                    "Signal poll skipped: frontend_disabled=%s",
                    self._signal_frontend_disabled,
                )
            return 0
        if self._debug_enabled:
            logging.debug("Polling signal frontend")
        try:
            updates = self.signal.get_updates()
        except SignalFrontendUnavailableError as exc:
            self._signal_frontend_disabled = True
            self._set_frontend_disabled("signal", error=str(exc))
            logging.warning("%s; continuing with telegram-only frontend", exc)
            return 0
        except RuntimeError as exc:
            self._set_frontend_disabled("signal", disabled=False, error=str(exc))
            logging.warning("Signal polling failed: %s; will retry", exc)
            return 0
        if self._debug_enabled:
            logging.debug("Signal poll returned %s update(s)", len(updates))
        if self._debug_enabled:
            for update in updates:
                logging.debug(
                    "Handling signal update: update_id=%s conversation_id=%s sender_id=%s text_present=%s voice_present=%s attachments=%s",
                    update.update_id,
                    update.conversation_id,
                    update.sender_id,
                    bool(update.text),
                    bool(update.voice_file_path),
                    len(update.attachments),
                )
        self._handle_updates_with_lock(updates)
        return len(updates)

    def _poll_whatsapp_once(self) -> int:
        if not self.whatsapp or self._whatsapp_frontend_disabled:
            return 0
        try:
            updates = self.whatsapp.get_updates()
        except WhatsAppFrontendUnavailableError as exc:
            self._whatsapp_frontend_disabled = True
            self._set_frontend_disabled("whatsapp", error=str(exc))
            logging.warning("%s; continuing without whatsapp frontend", exc)
            return 0
        self._handle_updates_with_lock(updates)
        return len(updates)

    def _poll_google_fi_once(self) -> int:
        if not self.google_fi or self._google_fi_frontend_disabled:
            return 0
        try:
            updates = self.google_fi.get_updates()
        except GoogleFiFrontendUnavailableError as exc:
            self._google_fi_frontend_disabled = True
            self._set_frontend_disabled("google_fi", error=str(exc))
            logging.warning("%s; continuing without google_fi frontend", exc)
            return 0
        self._handle_updates_with_lock(updates)
        return len(updates)

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
        self._current_evidence = search_workspace(self._workspace, text)
        messages: list[dict[str, str]] = [{"role": "system", "content": self._build_system_prompt()}]
        messages.extend(self._history[conversation_key])
        structured_memory_context = build_structured_memory_context(self._workspace)

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

        user_parts = [structured_memory_context, sender_context, text]
        evidence_context = format_evidence_context(self._current_evidence)
        if evidence_context:
            user_parts.append(evidence_context)
        user_content = "\n\n".join(part for part in user_parts if part)
        messages.append({"role": "user", "content": user_content})
        return messages

    def _poll_due_structured_schedule_once(self) -> None:
        now = datetime.now(timezone.utc)
        for record in list_records(self._workspace, "tasks"):
            if str(record.get("status", "open")).strip().lower() != "open":
                continue
            due_at = str(record.get("remind_at") or record.get("due_at") or "").strip()
            if not due_at or record.get("last_notified_at"):
                continue
            try:
                due_time = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if due_time > now:
                continue
            target = self._task_notify_target(record)
            if not target:
                continue
            backend, conversation_id = target
            lines = [
                "[Scheduled task]",
                f"- task_id: {record.get('id', '')}",
                f"- kind: {record.get('kind', 'task')}",
                f"- title: {record.get('title', '')}",
            ]
            if record.get("details"):
                lines.extend(["", str(record["details"])])
            self._send_message(backend, conversation_id, "\n".join(lines))
            mark_task_notified(self._workspace, record, fired_at=now)

        for record in list_records(self._workspace, "routines"):
            if not bool(record.get("enabled", True)):
                continue
            next_run_at = str(record.get("next_run_at", "")).strip()
            if not next_run_at:
                continue
            try:
                next_run = datetime.fromisoformat(next_run_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if next_run > now:
                continue
            target = self._task_notify_target(record)
            if target:
                backend, conversation_id = target
                text = "\n".join(
                    [
                        "[Recurring routine]",
                        f"- routine_id: {record.get('id', '')}",
                        f"- title: {record.get('title', '')}",
                        str(record.get("instructions", "")).strip(),
                    ]
                ).strip()
                self._send_message(backend, conversation_id, text)
            mark_routine_run(self._workspace, record, ran_at=now)

    def _task_notify_target(self, record: dict[str, Any]) -> tuple[str, str] | None:
        target = record.get("notify_target")
        if isinstance(target, dict):
            backend = str(target.get("backend", "")).strip()
            conversation_id = str(target.get("conversation_id", "")).strip()
            if backend and conversation_id:
                return backend, conversation_id
        return self._resolve_hourly_target()

    def _history_key(self, backend: str, conversation_id: str) -> Any:
        if backend == "telegram" and conversation_id.lstrip("-").isdigit():
            return int(conversation_id)
        return f"{backend}:{conversation_id}"

    def _poll_due_reminders_once(self) -> None:
        reminders_text = self._workspace.read_text(REMINDERS_FILE, default="")
        if not reminders_text.strip():
            return

        now_unix_time = int(time.time())
        retained_lines: list[str] = []
        changed = False

        for raw_line in reminders_text.splitlines():
            line = raw_line.strip()
            if not line:
                changed = True
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logging.warning("Skipping malformed reminder entry")
                retained_lines.append(raw_line)
                continue

            if not isinstance(record, dict):
                logging.warning("Skipping non-object reminder entry")
                retained_lines.append(raw_line)
                continue

            unix_time = parse_unix_time(record.get("unix_time"))
            prompt = str(record.get("prompt", "")).strip()
            backend = str(record.get("backend", "")).strip()
            conversation_id = str(record.get("conversation_id", "")).strip()

            if unix_time is None or not prompt or not backend or not conversation_id:
                logging.warning("Skipping invalid reminder entry with missing required fields")
                retained_lines.append(raw_line)
                continue

            if unix_time > now_unix_time:
                retained_lines.append(serialize_reminder_record(record))
                continue

            if self._run_due_reminder(record):
                changed = True
                continue

            retained_lines.append(serialize_reminder_record(record))

        if changed:
            self._workspace.write_text(
                REMINDERS_FILE,
                "".join(f"{line}\n" for line in retained_lines if line.strip()),
            )

    def _run_due_reminder(self, record: dict[str, Any]) -> bool:
        backend = str(record["backend"]).strip()
        conversation_id = str(record["conversation_id"]).strip()
        sender_id = str(record.get("sender_id", conversation_id)).strip() or conversation_id
        sender_name = "Scheduled Reminder"
        sender_contact = f"scheduled-reminder:{record.get('id', '')}"
        reminder_text = self._build_due_reminder_text(record)
        conversation_key = self._history_key(backend, conversation_id)

        logging.info(
            "Running scheduled reminder id=%s backend=%s conversation=%s",
            record.get("id", ""),
            backend,
            conversation_id,
        )
        try:
            with self._typing_indicator(backend, conversation_id):
                prompt = self._build_messages(
                    conversation_key,
                    reminder_text,
                    backend=backend,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                )
                model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
                reply = self._resolve_llm_reply(prompt, model_reply)
        except RequestTimeoutError:
            logging.warning("Scheduled reminder timed out id=%s", record.get("id", ""))
            return False
        except Exception:
            logging.exception("Scheduled reminder failed id=%s", record.get("id", ""))
            return False

        self._history[conversation_key].append({"role": "user", "content": reminder_text})
        self._history[conversation_key].append(
            {
                "role": "assistant",
                "content": self._summarize_skill_result_for_context("web_search", reply),
            }
        )

        try:
            for chunk in self._split_reply(reply):
                self._send_message(backend, conversation_id, chunk)
        except Exception:
            logging.exception("Failed to send scheduled reminder reply id=%s", record.get("id", ""))
            return False

        self._append_agenta_query_log(
            backend=backend,
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=reminder_text,
            reply=reply,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        self._workspace.append_text(
            "logs/reminders.history",
            json.dumps(
                {
                    "logged_at": datetime.now(timezone.utc).isoformat(),
                    "reminder": record,
                    "reply": reply,
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        return True

    def _poll_due_hourly_once(self) -> None:
        hourly_path = self.config.runtime.hourly_file
        hourly_text = self._workspace.read_text(hourly_path, default="").strip()
        if not hourly_text:
            return

        slot = self._current_hourly_slot()
        slot_key = slot.isoformat()
        if self._last_hourly_slot == slot_key:
            return

        if self._run_hourly_task(hourly_text, slot):
            self._last_hourly_slot = slot_key
            self._workspace.write_text(
                self.config.runtime.hourly_status_file,
                json.dumps(
                    {
                        "last_hourly_slot": slot_key,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

    def _run_hourly_task(self, hourly_text: str, slot: datetime) -> bool:
        target = self._resolve_hourly_target()
        backend = target[0] if target else "hourly"
        conversation_id = target[1] if target else slot.isoformat()
        sender_id = "hourly-scheduler"
        sender_name = "Hourly Scheduler"
        sender_contact = self.config.runtime.hourly_file
        hourly_prompt = self._build_hourly_prompt(hourly_text, slot)
        conversation_key = self._history_key(backend, conversation_id)

        logging.info("Running hourly routine slot=%s target=%s", slot.isoformat(), target or "none")
        try:
            with self._typing_indicator(backend, conversation_id):
                prompt = self._build_messages(
                    conversation_key,
                    hourly_prompt,
                    backend=backend,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                )
                model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
                reply = self._resolve_llm_reply(prompt, model_reply)
        except RequestTimeoutError:
            logging.warning("Hourly routine timed out slot=%s", slot.isoformat())
            return False
        except Exception:
            logging.exception("Hourly routine failed slot=%s", slot.isoformat())
            return False

        self._history[conversation_key].append({"role": "user", "content": hourly_prompt})
        self._history[conversation_key].append(
            {
                "role": "assistant",
                "content": self._summarize_skill_result_for_context("web_search", reply),
            }
        )

        normalized_reply = reply.strip()
        if normalized_reply and normalized_reply != _HOURLY_NO_ACTION_REPLY and target:
            try:
                for chunk in self._split_reply(reply):
                    self._send_message(backend, conversation_id, chunk)
            except Exception:
                logging.exception("Failed to send hourly routine reply slot=%s", slot.isoformat())
                return False
        elif normalized_reply == _HOURLY_NO_ACTION_REPLY:
            logging.info("Hourly routine produced no action for slot=%s", slot.isoformat())
        elif normalized_reply and not target:
            logging.info("Hourly routine produced output with no delivery target slot=%s", slot.isoformat())

        self._append_agenta_query_log(
            backend=backend,
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=hourly_prompt,
            reply=reply,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        self._workspace.append_text(
            "logs/hourly.history",
            json.dumps(
                {
                    "logged_at": datetime.now(timezone.utc).isoformat(),
                    "slot": slot.isoformat(),
                    "target": {"backend": backend, "conversation_id": conversation_id} if target else None,
                    "hourly_file": self.config.runtime.hourly_file,
                    "prompt": hourly_prompt,
                    "reply": reply,
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        return True

    def _build_hourly_prompt(self, hourly_text: str, slot: datetime) -> str:
        timezone_name = self.config.llm.system_prompt_timezone
        lines = [
            "[Hourly routine]",
            f"- scheduled_for_local: {slot.isoformat()}",
            f"- timezone: {timezone_name}",
            f"- file: {self.config.runtime.hourly_file}",
            "",
            "You are running automatically at the top of the hour.",
            "Read workspace files as needed and take any actions required by the hourly instructions.",
            f"If nothing should happen for this hour, reply with exactly {_HOURLY_NO_ACTION_REPLY}.",
            "",
            f"Instructions from workspace/{self.config.runtime.hourly_file}:",
            hourly_text,
        ]
        return "\n".join(lines)

    def _current_hourly_slot(self) -> datetime:
        now = datetime.now(ZoneInfo(self.config.llm.system_prompt_timezone))
        return now.replace(minute=0, second=0, microsecond=0)

    def _load_last_hourly_slot(self) -> str:
        raw = self._workspace.read_text(self.config.runtime.hourly_status_file, default="").strip()
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning("Skipping malformed hourly status file path=%s", self.config.runtime.hourly_status_file)
            return ""
        if not isinstance(payload, dict):
            return ""
        value = payload.get("last_hourly_slot")
        return value.strip() if isinstance(value, str) else ""

    def _resolve_hourly_target(self) -> tuple[str, str] | None:
        latest_target = self._latest_logged_conversation_target()
        if latest_target:
            return latest_target

        if self.config.telegram and len(self.config.telegram.allowed_chat_ids) == 1:
            return "telegram", str(self.config.telegram.allowed_chat_ids[0])
        if self.config.signal and len(self.config.signal.allowed_sender_ids) == 1:
            return "signal", self.config.signal.allowed_sender_ids[0]
        if self.config.whatsapp and len(self.config.whatsapp.allowed_sender_ids) == 1:
            return "whatsapp", self.config.whatsapp.allowed_sender_ids[0]
        if self.config.google_fi and len(self.config.google_fi.allowed_sender_ids) == 1:
            return "google_fi", self.config.google_fi.allowed_sender_ids[0]
        return None

    def _latest_logged_conversation_target(self) -> tuple[str, str] | None:
        candidates: list[tuple[datetime, str, str]] = []
        for backend in ("telegram", "signal", "whatsapp", "google_fi"):
            file_text = self._workspace.read_text(f"logs/{backend}.history", default="")
            if not file_text.strip():
                continue
            for raw_line in reversed(file_text.splitlines()):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("direction") != "incoming":
                    continue
                conversation_id = payload.get("conversation_id")
                if not isinstance(conversation_id, str) or not conversation_id.strip():
                    continue
                logged_at = payload.get("logged_at")
                try:
                    timestamp = datetime.fromisoformat(str(logged_at))
                except ValueError:
                    timestamp = datetime.min.replace(tzinfo=timezone.utc)
                candidates.append((timestamp, backend, conversation_id))
                break
        if not candidates:
            return None
        _, backend, conversation_id = max(candidates, key=lambda item: item[0])
        return backend, conversation_id

    def _build_due_reminder_text(self, record: dict[str, Any]) -> str:
        unix_time = int(record["unix_time"])
        scheduled_at = datetime.fromtimestamp(unix_time, tz=timezone.utc).isoformat()
        lines = [
            "[Scheduled reminder]",
            f"- reminder_id: {record.get('id', '')}",
            f"- scheduled_unix_time: {unix_time}",
            f"- scheduled_at_utc: {scheduled_at}",
            "",
            "Predefined prompt:",
            str(record["prompt"]).strip(),
        ]

        file_context = self._build_reminder_file_context(record.get("files"))
        if file_context:
            lines.extend(["", "Workspace file context:", file_context])
        return "\n".join(lines)

    def _build_reminder_file_context(self, files: Any) -> str:
        if not isinstance(files, list):
            return ""

        snippets: list[str] = []
        remaining_chars = _MAX_REMINDER_TOTAL_FILE_CHARS

        for item in files:
            relative_path = str(item).strip()
            if not relative_path or remaining_chars <= 0:
                continue
            try:
                file_path = self._workspace.resolve(relative_path)
            except ValueError:
                snippets.append(f"File `{relative_path}` is unavailable because the path escapes the workspace.")
                continue
            if not file_path.exists():
                snippets.append(f"File `{relative_path}` is missing.")
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                snippets.append(f"File `{relative_path}` is not UTF-8 text.")
                continue

            limit = min(_MAX_REMINDER_FILE_CHARS, remaining_chars)
            excerpt = content[:limit]
            remaining_chars -= len(excerpt)
            if len(content) > len(excerpt):
                excerpt += "\n[truncated]"
            snippets.append(f"File: {relative_path}\n```text\n{excerpt}\n```")

        return "\n\n".join(snippets)

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

        done = skill_call_payload.get("done", payload.get("done", nested_done if nested_done is not None else False))
        if not isinstance(done, bool):
            done = False
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
        self._last_trace_steps: list[dict[str, Any]] = []
        for step_index in range(_MAX_SKILL_CHAIN_STEPS):
            skill_call = self._try_parse_skill_call(model_reply)
            if not skill_call:
                return model_reply

            done_was_explicit = self._done_flag_was_explicit(model_reply)

            raw_skill_result = self._strip_think_blocks(
                self._run_skill_call(skill_call["name"], skill_call["args"]), source="skill"
            )
            self._last_trace_steps.append(
                {
                    "step": step_index + 1,
                    "model_reply": model_reply,
                    "skill_call": skill_call,
                    "skill_result": raw_skill_result,
                }
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
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self._skills)) or "(none)"
            return f"Unknown skill '{name}'. Available skills: {available}"

        action = skill.build_action(args) if skill.build_action else None
        if action is not None:
            policy = load_action_policy(self._workspace, self.config.runtime.action_policy_file)
            decision = decide_action(policy, action)
            if decision == "deny":
                append_action_audit(self._workspace, action=action, decision=decision, status="denied")
                return f"Action denied by policy: {action.name}"
            if decision == "ask":
                append_action_audit(self._workspace, action=action, decision=decision, status="pending_approval")
                return (
                    f"Action requires approval: {action.name}. "
                    f"Set `{self.config.runtime.action_policy_file}` to allow it, then retry."
                )
        try:
            result = skill.run(self._workspace, args)
            if action is not None:
                append_action_audit(self._workspace, action=action, decision="allow", status="executed", result=result)
            return result
        except Exception as exc:
            logging.exception("Skill execution failed for %s", name)
            if action is not None:
                append_action_audit(self._workspace, action=action, decision="allow", status="failed", error=str(exc))
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
        lines.extend(self._frontend_status_lines())

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
        conversation_name: str | None = None,
        sender_id: str,
        text: str,
        event_id: str | None = None,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        logged_at: str | None = None,
    ) -> None:
        if not self._backend_stores_unanswered_messages(backend):
            return

        self._append_recent_frontend_message(
            backend=backend,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            sender_id=sender_id,
            text=text,
            event_id=event_id,
            sender_name=sender_name,
            sender_contact=sender_contact,
            logged_at=logged_at,
            sent_at=logged_at,
        )

    @staticmethod
    def _recent_workspace_paths_for_backend(backend: str) -> tuple[str, ...]:
        if backend == "telegram":
            return ("telegram.recent",)
        if backend == "signal":
            return ("signal.messages.recent",)
        if backend == "whatsapp":
            return ("whatsapp.messages.recent",)
        if backend == "google_fi":
            return ("google_fi.messages.recent",)
        return ()

    @staticmethod
    def _recent_workspace_load_paths(file_path: str) -> tuple[str, ...]:
        if file_path == "telegram.recent":
            return ("telegram.recent", "telegram.messages.recent")
        return (file_path,)

    def _append_recent_frontend_message(
        self,
        *,
        backend: str,
        conversation_id: str,
        conversation_name: str | None = None,
        sender_id: str,
        text: str,
        event_id: str | None = None,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        logged_at: str | None = None,
        sent_at: str | None = None,
    ) -> None:
        if not self._backend_stores_unanswered_messages(backend):
            return

        recent_paths = self._recent_workspace_paths_for_backend(backend)
        if not recent_paths:
            return

        payload = self._build_frontend_record(
            backend=backend,
            direction="incoming",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            sender_id=sender_id,
            text=text,
            event_id=event_id,
            sender_name=sender_name,
            sender_contact=sender_contact or sender_id,
            account=self._frontend_account_name(backend),
            source="frontend_log",
            logged_at=logged_at,
            sent_at=sent_at,
        )
        appended = False
        for file_path in recent_paths:
            if not self._should_append_unanswered_message(
                file_path,
                conversation_id,
                sender_id,
                text,
                event_id=event_id,
                sent_at=sent_at,
            ):
                continue
            self._append_sorted_recent_message(file_path, payload)
            appended = True
        if appended:
            return

    @staticmethod
    def _is_google_fi_call_event(update: IncomingMessage) -> bool:
        return getattr(update, "backend", None) == "google_fi" and getattr(update, "event_type", "message") == "call"

    def _frontend_account_name(self, backend: str) -> str:
        if backend == "whatsapp" and self.config.whatsapp:
            return self.config.whatsapp.account
        if backend == "google_fi" and self.config.google_fi:
            return self.config.google_fi.account
        return "default"

    def _append_sorted_recent_message(self, file_path: str, payload: dict[str, Any]) -> None:
        entries: list[tuple[datetime | None, int, str]] = []
        existing = self._workspace.read_text(file_path, default="")
        for index, line in enumerate(existing.splitlines()):
            line = line.strip()
            if not line:
                continue
            sort_key = self._recent_message_sort_key(line)
            entries.append((sort_key, index, line))

        payload_line = json.dumps(payload, ensure_ascii=False)
        entries.append((self._recent_message_sort_key(payload_line), len(entries), payload_line))
        entries.sort(key=lambda item: (item[0] is None, item[0] or datetime.max.replace(tzinfo=timezone.utc), item[1]))
        self._workspace.write_text(file_path, "\n".join(line for _, _, line in entries) + "\n")

    @staticmethod
    def _recent_message_sort_key(line: str) -> datetime | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        timestamp = payload.get("sent_at") or payload.get("logged_at") or payload.get("collected_at")
        if not isinstance(timestamp, str):
            return None
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _load_unanswered_recent_keys(self) -> None:
        for file_path in self._recent_unanswered_keys:
            for load_path in self._recent_workspace_load_paths(file_path):
                existing = self._workspace.read_text(load_path, default="")
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
                    event_id = self._coerce_event_id(payload.get("event_id"))
                    if not isinstance(sender_id, str):
                        sender_id = payload.get("sender")
                    if event_id is None:
                        event_id = self._coerce_event_id(payload.get("update_id"))
                    if not isinstance(text, str) or not isinstance(conversation_id, str) or not isinstance(sender_id, str):
                        continue
                    sent_at = payload.get("sent_at")
                    if not isinstance(sent_at, str):
                        sent_at = payload.get("logged_at")
                    if not isinstance(sent_at, str):
                        sent_at = payload.get("collected_at")
                    self._recent_unanswered_keys[file_path].update(
                        self._unanswered_message_keys(
                            file_path,
                            conversation_id,
                            sender_id,
                            text,
                            event_id=event_id,
                            sent_at=sent_at,
                        )
                    )

    @staticmethod
    def _unanswered_message_key(conversation_id: str, sender_id: str, text: str, event_id: str | None = None) -> str:
        if event_id:
            return f"event:{event_id}"
        return f"{conversation_id}\n{sender_id}\n{text}"

    @classmethod
    def _unanswered_message_keys(
        cls,
        file_path: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        *,
        event_id: str | None = None,
        sent_at: str | None = None,
    ) -> set[str]:
        keys = {cls._unanswered_message_key(conversation_id, sender_id, text, event_id=event_id)}
        if file_path == "google_fi.calls.recent":
            keys.add(f"call:{conversation_id}\n{sender_id}\n{sent_at or ''}\n{text}")
        return keys

    @staticmethod
    def _coerce_event_id(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        return None

    def _should_append_unanswered_message(
        self,
        file_path: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        *,
        event_id: str | None = None,
        sent_at: str | None = None,
    ) -> bool:
        keys = self._unanswered_message_keys(
            file_path,
            conversation_id,
            sender_id,
            text,
            event_id=event_id,
            sent_at=sent_at,
        )
        known = self._recent_unanswered_keys.get(file_path)
        if known is None:
            self._recent_unanswered_keys[file_path] = set(keys)
            return True
        if any(key in known for key in keys):
            return False
        known.update(keys)
        return True

    def _append_agenta_query_log(
        self,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        reply: str,
        event_id: str | None = None,
        sender_name: str | None = None,
        sender_contact: str | None = None,
    ) -> None:
        normalized_query = text.strip()
        if normalized_query:
            self._recent_handled_queries[(backend, conversation_id, sender_id)] = normalized_query
        payload = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "event_id": event_id,
            "sender_name": sender_name,
            "sender_contact": sender_contact,
            "query": text,
            "reply": reply,
        }
        self._workspace.append_text("logs/agenta_queries.history", json.dumps(payload, ensure_ascii=False) + "\n")

    def _iter_jsonl_reverse(self, relative_path: str) -> list[dict[str, Any]]:
        raw = self._workspace.read_text(relative_path, default="")
        if not raw.strip():
            return []
        payloads: list[dict[str, Any]] = []
        for raw_line in reversed(raw.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def _query_log_contains_query(self, backend: str, conversation_id: str, sender_id: str, text: str, event_id: str | None) -> bool:
        _ = event_id
        normalized_text = text.strip()
        if not normalized_text:
            return False
        return self._recent_handled_queries.get((backend, conversation_id, sender_id)) == normalized_text

    def _query_log_contains_reply_chunk(self, backend: str, conversation_id: str, text: str) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        for payload in self._iter_jsonl_reverse("logs/agenta_queries.history"):
            if payload.get("backend") != backend:
                continue
            if str(payload.get("conversation_id", "")).strip() != conversation_id:
                continue
            reply = payload.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                continue
            if any(chunk.strip() == normalized_text for chunk in self._split_reply(reply)):
                return True
        return False

    def _frontend_history_contains_message(
        self,
        backend: str,
        direction: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        event_id: str | None,
    ) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        for payload in self._iter_jsonl_reverse(f"logs/{backend}.history"):
            if payload.get("direction") != direction:
                continue
            if str(payload.get("conversation_id", "")).strip() != conversation_id:
                continue
            logged_event_id = self._coerce_event_id(payload.get("event_id"))
            if event_id and logged_event_id == event_id:
                return True
            if str(payload.get("sender_id", "")).strip() != sender_id:
                continue
            logged_text = payload.get("text")
            if isinstance(logged_text, str) and logged_text.strip() == normalized_text:
                return True
        return False

    def _is_duplicate_frontend_message(
        self,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        text: str,
        event_id: str | None,
        outgoing: bool,
    ) -> bool:
        if outgoing:
            seen_in_query_log = self._query_log_contains_reply_chunk(backend, conversation_id, text)
            seen_in_history = self._frontend_history_contains_message(
                backend,
                "outgoing",
                conversation_id,
                "bot",
                text,
                event_id,
            )
        else:
            seen_in_query_log = self._query_log_contains_query(backend, conversation_id, sender_id, text, event_id)
            seen_in_history = False
        if seen_in_query_log or seen_in_history:
            logging.info(
                "Skipping duplicate frontend message backend=%s conversation=%s outgoing=%s seen_in_query_log=%s "
                "seen_in_history=%s",
                backend,
                conversation_id,
                outgoing,
                seen_in_query_log,
                seen_in_history,
            )
            return True
        return False

    def _is_whatsapp_self_sender(self, sender_id: str) -> bool:
        if not self.config.whatsapp:
            return False
        account = self.config.whatsapp.account
        return bool(account and sender_id.strip() == account.strip())

    def _is_google_fi_self_sender(self, sender_id: str) -> bool:
        if not self.config.google_fi:
            return False
        account = self.config.google_fi.account
        return bool(account and sender_id.strip() == account.strip())

    def _is_frontend_outgoing_update(self, update: IncomingMessage, backend: str, sender_id: str) -> bool:
        if getattr(update, "is_outgoing", False):
            return True
        if backend == "signal":
            return self._is_signal_self_sender(sender_id)
        if backend == "whatsapp":
            return self._is_whatsapp_self_sender(sender_id)
        if backend == "google_fi":
            return self._is_google_fi_self_sender(sender_id)
        return False

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
        conversation_name: str | None = None,
        sender_id: str,
        text: str,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        logged_at: str | None = None,
        sent_at: str | None = None,
    ) -> None:
        history_file = f"logs/{backend}.history"
        payload = self._build_frontend_record(
            backend=backend,
            direction=direction,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            sender_id=sender_id,
            text=text,
            sender_name=sender_name,
            sender_contact=sender_contact,
            source="frontend_log",
            logged_at=logged_at or datetime.now(timezone.utc).isoformat(),
            sent_at=sent_at,
        )
        self._workspace.append_text(history_file, json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_frontend_record(
        self,
        *,
        backend: str,
        direction: str,
        conversation_id: str,
        conversation_name: str | None = None,
        sender_id: str,
        text: str,
        event_id: str | None = None,
        sender_name: str | None = None,
        sender_contact: str | None = None,
        account: str | None = None,
        source: str = "frontend_log",
        logged_at: str | None = None,
        collected_at: str | None = None,
        sent_at: str | None = None,
    ) -> dict[str, str | None]:
        logged_timestamp = logged_at or datetime.now(timezone.utc).isoformat()
        collected_timestamp = collected_at or datetime.now(timezone.utc).isoformat()
        payload = {
            "sent_at": sent_at,
            "logged_at": logged_timestamp,
            "collected_at": collected_timestamp,
            "source": source,
            "backend": backend,
            "account": account or "default",
            "direction": direction,
            "conversation_id": conversation_id,
            "conversation_name": conversation_name,
            "sender_id": sender_id,
            "event_id": event_id,
            "sender_name": sender_name,
            "sender_contact": sender_contact,
            "text": text,
        }
        logging.debug(
            "Built frontend record backend=%s conversation=%s sender=%s source=%s logged_at=%s collected_at=%s "
            "timestamps_match=%s",
            backend,
            conversation_id,
            sender_id,
            source,
            logged_timestamp,
            collected_timestamp,
            logged_timestamp == collected_timestamp,
        )
        return payload

    def _is_authorized_frontend_sender(self, backend: str, conversation_id: str, sender_id: str) -> bool:
        if backend == "telegram":
            chat_id = int(conversation_id)
            if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
                logging.warning("Blocked message from unauthorized telegram chat_id=%s", chat_id)
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

    def _remember_contact_mapping(
        self,
        *,
        backend: str,
        conversation_id: str,
        conversation_name: str | None,
        sender_id: str,
        sender_name: str | None,
        sender_contact: str | None,
        chat_id: int | None,
    ) -> None:
        contacts_path = WORKSPACE_CONTACT_MAP_FILES.get(backend)
        if not contacts_path:
            return

        recipient = self._contact_recipient_for_backend(
            backend=backend,
            conversation_id=conversation_id,
            sender_id=sender_id,
            chat_id=chat_id,
        )
        if recipient is None:
            return

        aliases = self._contact_aliases_for_update(
            backend=backend,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        if not aliases:
            return

        raw_contacts = self._workspace.read_text(contacts_path, default="").strip()
        try:
            existing = json.loads(raw_contacts) if raw_contacts else {}
        except json.JSONDecodeError:
            logging.warning("Ignoring invalid contact map file: %s", contacts_path)
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

        updated = False
        for alias in aliases:
            if existing.get(alias) == recipient:
                continue
            existing[alias] = recipient
            updated = True
            self._upsert_runtime_contact(alias=alias, platform=backend, recipient=recipient)

        if updated:
            self._workspace.write_text(contacts_path, json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _contact_recipient_for_backend(
        self,
        *,
        backend: str,
        conversation_id: str,
        sender_id: str,
        chat_id: int | None,
    ) -> str | int | None:
        if backend == "telegram":
            if chat_id is not None:
                return chat_id
            try:
                return int(conversation_id)
            except (TypeError, ValueError):
                return None
        if backend == "signal":
            return conversation_id if conversation_id.startswith(SignalClient.GROUP_CONVERSATION_PREFIX) else (sender_id or conversation_id)
        if backend == "whatsapp":
            return conversation_id or sender_id
        if backend == "google_fi":
            return sender_id or conversation_id
        return None

    def _contact_aliases_for_update(
        self,
        *,
        backend: str,
        conversation_id: str,
        conversation_name: str | None,
        sender_id: str,
        sender_name: str | None,
        sender_contact: str | None,
    ) -> list[str]:
        aliases: list[str] = []
        seen: set[str] = set()

        def add(value: str | None) -> None:
            if value is None:
                return
            alias = str(value).strip()
            if not alias or alias in seen:
                return
            seen.add(alias)
            aliases.append(alias)

        add(conversation_id)
        add(conversation_name)
        if backend in {"signal", "whatsapp"} and conversation_id.startswith("group:"):
            payload = conversation_id.split(":", 1)[1]
            if "|" in payload:
                add(payload.rsplit("|", 1)[0].strip())

        is_direct_conversation = backend == "google_fi" or conversation_id == sender_id
        if is_direct_conversation:
            add(sender_id)
            add(sender_name)
            add(sender_contact)

        for raw_value in (conversation_name, sender_name, sender_contact):
            if not raw_value:
                continue
            for match in _CONTACT_HANDLE_RE.findall(raw_value):
                add(match)
            angle_match = _CONTACT_ANGLE_RE.search(raw_value)
            if angle_match:
                add(angle_match.group(1).strip())

        return aliases

    def _upsert_runtime_contact(self, *, alias: str, platform: str, recipient: str | int) -> None:
        for index, contact in enumerate(self.config.contacts):
            if contact.platform == platform and contact.name == alias:
                if contact.recipient != recipient:
                    self.config.contacts[index] = ContactConfig(
                        name=alias,
                        platform=platform,
                        recipient=recipient,
                        description=contact.description,
                    )
                return
        self.config.contacts.append(ContactConfig(name=alias, platform=platform, recipient=recipient))

    def _handle_update(self, update: IncomingMessage) -> None:
        with self._processing_lock:
            self._handle_update_locked(update)

    def _handle_update_locked(self, update: IncomingMessage) -> None:
        backend = getattr(update, "backend", "telegram")
        update_id = getattr(update, "update_id", None)
        event_id = str(update_id) if update_id is not None else None
        conversation_id = getattr(update, "conversation_id", "") or str(getattr(update, "chat_id", ""))
        conversation_name = getattr(update, "conversation_name", None)
        sender_id = getattr(update, "sender_id", conversation_id)
        sender_name = getattr(update, "sender_name", None)
        sender_contact = getattr(update, "sender_contact", None)
        sent_at = getattr(update, "sent_at", None)
        chat_id = getattr(update, "chat_id", None)
        if getattr(update, "is_outgoing", False) and backend != "signal":
            logging.info("Ignoring outgoing %s echo conversation=%s sender=%s", backend, conversation_id, sender_id)
            return

        if not sender_contact:
            sender_contact = sender_id
            if backend == "signal" and sender_name:
                sender_contact = f"{sender_name} <{sender_id}>"

        self._remember_contact_mapping(
            backend=backend,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_contact=sender_contact,
            chat_id=chat_id,
        )

        attachment_context = self._save_incoming_attachments(update, backend=backend, sender_name=sender_name, sender_id=sender_id)
        is_google_fi_call_event = self._is_google_fi_call_event(update)
        is_outgoing_update = self._is_frontend_outgoing_update(update, backend, sender_id)

        if update.text:
            message_text = f"{update.text}\n\n{attachment_context}" if attachment_context else update.text
            if self._is_duplicate_frontend_message(
                backend=backend,
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=message_text,
                event_id=event_id,
                outgoing=is_outgoing_update,
            ):
                return
            if is_google_fi_call_event:
                if not self._should_append_unanswered_message(
                    "google_fi.calls.recent",
                    conversation_id,
                    sender_id,
                    message_text,
                    event_id=event_id,
                    sent_at=sent_at,
                ):
                    return
                payload = self._build_frontend_record(
                    backend=backend,
                    direction="incoming",
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    sender_id=sender_id,
                    text=message_text,
                    event_id=event_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact or sender_id,
                    account=self.config.google_fi.account if self.config.google_fi else "default",
                    source="frontend_log",
                    logged_at=sent_at,
                    sent_at=sent_at,
                )
                self._append_sorted_recent_message("google_fi.calls.recent", payload)
                return
            if not self._is_authorized_frontend_sender(backend, conversation_id, sender_id):
                self._append_unanswered_collector_log(
                    backend=backend,
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    sender_id=sender_id,
                    text=message_text,
                    event_id=event_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    logged_at=sent_at,
                )
                return
            was_handled = self._handle_message(
                backend,
                conversation_id,
                sender_id,
                message_text,
                sender_name,
                sender_contact,
                conversation_name=conversation_name,
                sent_at=sent_at,
                event_id=event_id,
            )
            if not was_handled:
                self._append_unanswered_collector_log(
                    backend=backend,
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    sender_id=sender_id,
                    text=message_text,
                    event_id=event_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    logged_at=sent_at,
                )
            return

        if attachment_context:
            if self._is_duplicate_frontend_message(
                backend=backend,
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=attachment_context,
                event_id=event_id,
                outgoing=is_outgoing_update,
            ):
                return
            if not self._is_authorized_frontend_sender(backend, conversation_id, sender_id):
                return
            was_handled = self._handle_message(
                backend,
                conversation_id,
                sender_id,
                attachment_context,
                sender_name,
                sender_contact,
                conversation_name=conversation_name,
                sent_at=sent_at,
                event_id=event_id,
            )
            if not was_handled:
                self._append_unanswered_collector_log(
                    backend=backend,
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    sender_id=sender_id,
                    text=attachment_context,
                    event_id=event_id,
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
        was_handled = self._handle_message(
            backend,
            conversation_id,
            sender_id,
            transcript_text,
            sender_name,
            sender_contact,
            conversation_name=conversation_name,
            event_id=event_id,
        )
        if not was_handled:
            self._append_unanswered_collector_log(
                backend=backend,
                conversation_id=conversation_id,
                conversation_name=conversation_name,
                sender_id=sender_id,
                text=transcript_text,
                event_id=event_id,
                sender_name=sender_name,
                sender_contact=sender_contact,
            )

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

    def _handle_message(
        self,
        *args: Any,
        conversation_name: str | None = None,
        sent_at: str | None = None,
        event_id: str | None = None,
    ) -> bool:
        with self._processing_lock:
            return self._handle_message_locked(
                *args,
                conversation_name=conversation_name,
                sent_at=sent_at,
                event_id=event_id,
            )

    def _handle_message_locked(
        self,
        *args: Any,
        conversation_name: str | None = None,
        sent_at: str | None = None,
        event_id: str | None = None,
    ) -> bool:
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
                conversation_name=conversation_name,
                sender_id=sender_id,
                text=text,
                event_id=event_id,
                sender_name=sender_name,
                sender_contact=sender_contact,
                logged_at=sent_at,
            )
            return False

        if self._backend_is_read_only(backend):
            self._append_unanswered_collector_log(
                backend=backend,
                conversation_id=conversation_id,
                conversation_name=conversation_name,
                sender_id=sender_id,
                text=text,
                event_id=event_id,
                sender_name=sender_name,
                sender_contact=sender_contact,
                logged_at=sent_at,
            )
            return False

        conversation_key = self._history_key(backend, conversation_id)

        self._handled_messages_count += 1
        logging.info("Incoming message from %s conversation=%s sender=%s", backend, conversation_id, sender_contact or sender_name or sender_id)

        trace_payload: dict[str, Any] = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "last_message": text,
            "evidence": [],
            "steps": [],
        }
        self._current_evidence = []
        self._last_trace_steps = []
        command_text = text.strip()
        self._refresh_skills()
        if command_text.lower() == "/status":
            reply = self._build_status_message()
        elif command_text.lower() == "/skill" or command_text.lower().startswith("/skill "):
            reply = self._handle_skill_command(command_text)
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
                trace_payload["last_prompt"] = prompt
                trace_payload["evidence"] = [
                    {"path": item.path, "snippet": item.snippet, "score": item.score}
                    for item in self._current_evidence
                ]
                try:
                    model_reply = self._strip_think_blocks(self.llm.generate_reply(prompt), source="llm")
                    trace_payload["initial_model_reply"] = model_reply
                    reply = self._resolve_llm_reply(prompt, model_reply)
                    trace_payload["steps"] = getattr(self, "_last_trace_steps", [])
                    if trace_payload["steps"]:
                        trace_payload["last_action"] = trace_payload["steps"][-1].get("skill_call")
                        trace_payload["last_skill_result"] = trace_payload["steps"][-1].get("skill_result")
                except RequestTimeoutError:
                    logging.warning("LLM request timed out for %s conversation=%s", backend, conversation_id)
                    trace_payload["error"] = "timeout"
                    write_trace(self._workspace, trace_payload)
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
                    trace_payload["error"] = "internal_error"
                    write_trace(self._workspace, trace_payload)
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

        if self.config.runtime.enable_reply_citations:
            reply = append_sources(reply, self._current_evidence)
        trace_payload["final_reply"] = reply
        write_trace(self._workspace, trace_payload)
        for chunk in self._split_reply(reply):
            self._send_message(backend, conversation_id, chunk)
        self._append_agenta_query_log(
            backend=backend,
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text,
            reply=reply,
            event_id=event_id,
            sender_name=sender_name,
            sender_contact=sender_contact,
        )
        logging.info("Replied to %s conversation=%s", backend, conversation_id)
        return True

    def _save_incoming_attachments(
        self,
        update: IncomingMessage,
        *,
        backend: str,
        sender_name: str | None,
        sender_id: str,
    ) -> str:
        attachments = getattr(update, "attachments", None) or []
        if not attachments:
            return ""

        sent_at = self._parse_sent_at(getattr(update, "sent_at", None)) or datetime.now(timezone.utc)
        if not self._should_persist_attachment(sent_at=sent_at, sent_at_raw=getattr(update, "sent_at", None)):
            logging.info("Skipping attachment download because message is older than 24 hours: sent_at=%s", update.sent_at)
            return ""
        date_dir = sent_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        unix_timestamp = int(sent_at.timestamp())
        sender_token = self._sanitize_attachment_name(sender_name or sender_id or "unknown")

        lines = [
            "[Attachments]",
            f"- storage_root: attachments/{date_dir}/",
        ]
        for index, attachment in enumerate(attachments, start=1):
            relative_path = self._persist_attachment_file(
                attachment,
                backend=backend,
                sender_token=sender_token,
                unix_timestamp=unix_timestamp,
                date_dir=date_dir,
                index=index,
            )
            if not relative_path:
                continue
            lines.append(f"- saved: {relative_path}")
            if relative_path.lower().endswith(".pdf"):
                ingested = ingest_attachment(self._workspace.resolve(relative_path))
                attachment_text = str(ingested.get("text") or "").strip()
                if attachment_text:
                    ocr_path = f"{relative_path}.ocr.txt"
                    self._workspace.write_text(ocr_path, attachment_text + "\n")
                    lines.append(f"- pdf_text: {ocr_path}")
                    lines.extend(self._format_attachment_text(attachment_text))
        return "\n".join(lines) if len(lines) > 2 else ""

    def _persist_attachment_file(
        self,
        attachment: IncomingAttachment,
        *,
        backend: str,
        sender_token: str,
        unix_timestamp: int,
        date_dir: str,
        index: int,
    ) -> str:
        suffix = self._attachment_suffix(attachment)
        file_name = f"{backend}_{sender_token}_{unix_timestamp}"
        if index > 1:
            file_name = f"{file_name}_{index:02d}"
        relative_path = f"attachments/{date_dir}/{file_name}{suffix}"

        if attachment.content is not None:
            self._workspace.write_bytes(relative_path, attachment.content)
            return relative_path

        if attachment.file_id and backend == "telegram" and self.telegram:
            upstream_path = self.telegram.get_file_path(attachment.file_id)
            try:
                payload = self.telegram.download_file(upstream_path)
            except Exception:
                logging.exception(
                    "Skipping telegram attachment after download failure: file_id=%s path=%s",
                    attachment.file_id,
                    upstream_path,
                )
                return ""
            self._workspace.write_bytes(relative_path, payload)
            return relative_path

        if attachment.file_path:
            source_path = Path(attachment.file_path)
            if not source_path.exists():
                logging.warning("Attachment source file is missing: %s", source_path)
                return ""
            self._workspace.write_bytes(relative_path, source_path.read_bytes())
            return relative_path

        logging.warning("Attachment skipped because no content source was available: %s", attachment)
        return ""

    @staticmethod
    def _sanitize_attachment_name(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
        return token or "unknown"

    @staticmethod
    def _parse_sent_at(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _should_persist_attachment(*, sent_at: datetime, sent_at_raw: str | None) -> bool:
        if not sent_at_raw:
            return True
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        return sent_at >= datetime.now(timezone.utc) - timedelta(hours=24)

    @staticmethod
    def _format_attachment_text(text: str, limit: int = 4000) -> list[str]:
        snippet = text[:limit].strip()
        if not snippet:
            return []
        return ["", "[Attachment text]", snippet]

    @staticmethod
    def _attachment_suffix(attachment: IncomingAttachment) -> str:
        filename = str(attachment.filename or "").strip()
        if filename:
            suffix = Path(filename).suffix
            if suffix:
                return suffix
        mime_type = str(attachment.mime_type or "").strip()
        if mime_type:
            guessed = mimetypes.guess_extension(mime_type)
            if guessed:
                return guessed
        file_path = str(attachment.file_path or "").strip()
        if file_path:
            return Path(file_path).suffix
        return ""

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
