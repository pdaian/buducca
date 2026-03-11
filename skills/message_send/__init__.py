from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace
from messaging_llm_bot.config import BotConfig, load_config
from messaging_llm_bot.google_fi_client import GoogleFiClient
from messaging_llm_bot.http import HttpClient
from messaging_llm_bot.signal_client import SignalClient
from messaging_llm_bot.telegram_client import TelegramClient
from messaging_llm_bot.telegram_user_client import TelegramUserClient
from messaging_llm_bot.whatsapp_client import WhatsAppClient

NAME = "message_send"
DESCRIPTION = (
    "Send outbound messages through configured messaging backends. "
    "Supports telegram (bot or user mode), signal, whatsapp, and google_fi/fi. "
    "Use args.backend for one backend or 'all', args.message, and args.recipient for single-backend sends "
    "or args.recipients for per-backend fanout."
)
ARGS_SCHEMA = """
{
  backend: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi" | "all" | string[];
  message: string;
  recipient?: string | number;
  recipients?: Partial<Record<"telegram" | "signal" | "whatsapp" | "google_fi" | "fi", string | number>>;
  config_path?: string;
}
""".strip()

_BACKEND_ALIASES = {
    "telegram": "telegram",
    "signal": "signal",
    "whatsapp": "whatsapp",
    "google_fi": "google_fi",
    "google-fi": "google_fi",
    "fi": "google_fi",
}
_BACKEND_ORDER = ["telegram", "signal", "whatsapp", "google_fi"]


def _resolve_config_path(workspace: Workspace, raw_path: Any) -> Path:
    config_path = str(raw_path or "config.json").strip()
    if not config_path:
        config_path = "config.json"
    return workspace.resolve(config_path)


def _normalize_backend_name(value: Any) -> str:
    backend = str(value or "").strip().lower()
    if not backend:
        raise ValueError("Missing required arg `backend`.")
    normalized = _BACKEND_ALIASES.get(backend)
    if not normalized:
        supported = ", ".join(_BACKEND_ORDER)
        raise ValueError(f"Unsupported backend `{value}`. Use one of: {supported}, all.")
    return normalized


def _normalize_backend_targets(raw_backend: Any, configured: Iterable[str]) -> list[str]:
    configured_list = [name for name in _BACKEND_ORDER if name in set(configured)]

    if isinstance(raw_backend, list):
        if not raw_backend:
            raise ValueError("Missing required arg `backend`.")
        result: list[str] = []
        for item in raw_backend:
            normalized = _normalize_backend_name(item)
            if normalized not in result:
                result.append(normalized)
        return result

    backend_text = str(raw_backend or "").strip()
    if not backend_text:
        raise ValueError("Missing required arg `backend`.")
    if backend_text.lower() == "all":
        if not configured_list:
            raise ValueError("No messaging backends are configured in the selected config file.")
        return configured_list
    return [_normalize_backend_name(backend_text)]


def _coerce_message(args: dict[str, Any]) -> str:
    message = str(args.get("message") or args.get("text") or "").strip()
    if not message:
        raise ValueError("Missing required arg `message`.")
    return message


def _normalize_recipients(raw_recipients: Any) -> dict[str, str | int]:
    if not isinstance(raw_recipients, dict):
        return {}
    normalized: dict[str, str | int] = {}
    for key, value in raw_recipients.items():
        try:
            backend = _normalize_backend_name(key)
        except ValueError:
            continue
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            normalized[backend] = int(value)
            continue
        text = str(value).strip()
        if text:
            normalized[backend] = text
    return normalized


def _resolve_recipient_for_backend(args: dict[str, Any], backend: str, target_count: int) -> str | int:
    recipients = _normalize_recipients(args.get("recipients"))
    if backend in recipients:
        return recipients[backend]

    if target_count == 1:
        recipient = args.get("recipient")
        if recipient is None and backend == "telegram":
            recipient = args.get("chat_id")
        if isinstance(recipient, bool) or recipient is None:
            raise ValueError(f"Missing required recipient for backend `{backend}`.")
        if isinstance(recipient, (int, float)):
            return int(recipient)
        text = str(recipient).strip()
        if text:
            return text

    raise ValueError(f"Missing required recipient for backend `{backend}`.")


def _configured_backend_names(config: BotConfig) -> list[str]:
    names: list[str] = []
    if config.telegram:
        names.append("telegram")
    if config.signal:
        names.append("signal")
    if config.whatsapp:
        names.append("whatsapp")
    if config.google_fi:
        names.append("google_fi")
    return names


def _build_clients(config: BotConfig) -> dict[str, tuple[Any, bool]]:
    clients: dict[str, tuple[Any, bool]] = {}

    if config.telegram:
        if config.telegram.mode == "user":
            telegram_client: Any = TelegramUserClient(
                api_id=int(config.telegram.api_id or 0),
                api_hash=config.telegram.api_hash,
                session_path=config.telegram.session_path,
            )
        else:
            telegram_client = TelegramClient(
                bot_token=config.telegram.bot_token,
                http_client=HttpClient(timeout_seconds=config.runtime.request_timeout_seconds),
            )
        clients["telegram"] = (telegram_client, bool(config.telegram.read_only))

    if config.signal:
        clients["signal"] = (
            SignalClient(
                account=config.signal.account,
                receive_command=config.signal.receive_command,
                send_command=config.signal.send_command,
                debug=config.runtime.debug or config.runtime.log_level.upper() == "DEBUG",
            ),
            bool(config.signal.read_only),
        )

    if config.whatsapp:
        clients["whatsapp"] = (
            WhatsAppClient(
                receive_command=config.whatsapp.receive_command,
                send_command=config.whatsapp.send_command,
            ),
            bool(config.whatsapp.read_only),
        )

    if config.google_fi:
        clients["google_fi"] = (
            GoogleFiClient(
                receive_command=config.google_fi.receive_command,
                send_command=config.google_fi.send_command,
            ),
            bool(config.google_fi.read_only),
        )

    return clients


def _send_via_backend(client: Any, backend: str, recipient: str | int, message: str) -> None:
    if backend == "telegram":
        try:
            chat_id = int(recipient)
        except (TypeError, ValueError) as exc:
            raise ValueError("Telegram recipient must be an integer chat id.") from exc
        client.send_message(chat_id, message)
        return

    rendered_recipient = str(recipient).strip()
    if not rendered_recipient:
        raise ValueError(f"Recipient for backend `{backend}` must not be empty.")
    client.send_message(rendered_recipient, message)


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    try:
        config_path = _resolve_config_path(workspace, args.get("config_path"))
        config = load_config(config_path)
        configured_backends = _configured_backend_names(config)
        targets = _normalize_backend_targets(args.get("backend"), configured_backends)
        message = _coerce_message(args)
        clients = _build_clients(config)
    except ValueError as exc:
        return str(exc)

    results: list[str] = []
    success_count = 0

    for backend in targets:
        recipient: str | int
        try:
            recipient = _resolve_recipient_for_backend(args, backend, len(targets))
        except ValueError as exc:
            results.append(f"{backend}: {exc}")
            continue

        client_entry = clients.get(backend)
        if client_entry is None:
            results.append(f"{backend}: backend is not configured in {config_path}.")
            continue

        client, read_only = client_entry
        if read_only:
            results.append(f"{backend}: backend is configured read-only; outbound sends are disabled.")
            continue

        try:
            _send_via_backend(client, backend, recipient, message)
        except Exception as exc:
            results.append(f"{backend}: send failed: {exc}")
            continue

        success_count += 1
        results.append(f"{backend}: sent to {recipient}.")

    if len(targets) == 1 and len(results) == 1:
        return results[0]

    header = f"Sent {success_count} of {len(targets)} requested message(s)."
    return "\n".join([header, *results])


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    backend = args.get("backend")
    if isinstance(backend, list):
        backend_summary = ",".join(str(item) for item in backend if str(item).strip()) or "multiple"
    else:
        backend_summary = str(backend or "unspecified").strip() or "unspecified"
    return ActionEnvelope(
        name="message_send.send",
        args=args,
        reason=f"Send outbound message via backend(s): {backend_summary}.",
        writes=[],
        requires_approval=True,
    )
