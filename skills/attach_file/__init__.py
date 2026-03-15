from __future__ import annotations

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

NAME = "attach_file"
DESCRIPTION = (
    "Send a workspace file attachment through a configured messaging backend. "
    "Use args.backend, args.path, and args.recipient or args.recipients. "
    "Optional args.caption adds message text alongside the file."
)
ARGS_SCHEMA = """
{
  backend: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi" | string[];
  path: string;
  caption?: string;
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
    config_path = str(raw_path or "").strip()
    if config_path:
        return workspace.resolve(config_path)

    workspace_root = workspace.root.resolve()
    repo_root = workspace_root.parent if workspace_root.name == "workspace" else workspace_root
    for candidate in (repo_root / "config", repo_root / "config.json", workspace_root / "config.json"):
        if candidate.exists():
            return candidate
    return repo_root / "config"


def _normalize_backend_name(value: Any) -> str:
    backend = str(value or "").strip().lower()
    normalized = _BACKEND_ALIASES.get(backend)
    if not normalized:
        supported = ", ".join(_BACKEND_ORDER)
        raise ValueError(f"Unsupported backend `{value}`. Use one of: {supported}.")
    return normalized


def _normalize_backend_targets(raw_backend: Any) -> list[str]:
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
    return [_normalize_backend_name(backend_text)]


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


def _send_via_backend(client: Any, backend: str, recipient: str | int, file_path: str, caption: str) -> None:
    if backend == "telegram":
        try:
            chat_id = int(recipient)
        except (TypeError, ValueError) as exc:
            raise ValueError("Telegram recipient must be an integer chat id.") from exc
        client.send_file(chat_id, file_path, caption=caption)
        return

    rendered_recipient = str(recipient).strip()
    if not rendered_recipient:
        raise ValueError(f"Recipient for backend `{backend}` must not be empty.")
    client.send_file(rendered_recipient, file_path, caption=caption)


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    try:
        targets = _normalize_backend_targets(args.get("backend"))
        config = load_config(_resolve_config_path(workspace, args.get("config_path")))
        clients = _build_clients(config)
        caption = str(args.get("caption") or args.get("message") or "").strip()
        requested_path = str(args.get("path") or "").strip()
        if not requested_path:
            return "Missing required arg `path`."
        file_path = workspace.resolve(requested_path)
        if not file_path.exists() or not file_path.is_file():
            return f"File not found: {requested_path}"
    except ValueError as exc:
        return str(exc)

    results: list[str] = []
    sent_count = 0
    for backend in targets:
        client_info = clients.get(backend)
        if client_info is None:
            results.append(f"{backend}: backend is not configured.")
            continue
        client, read_only = client_info
        if read_only:
            results.append(f"{backend}: backend is configured read-only; outbound sends are disabled.")
            continue
        try:
            recipient = _resolve_recipient_for_backend(args, backend, len(targets))
            _send_via_backend(client, backend, recipient, str(file_path), caption)
            results.append(f"{backend}: attached {requested_path} to {recipient}.")
            sent_count += 1
        except Exception as exc:
            results.append(f"{backend}: send failed ({exc})")

    if len(targets) == 1:
        return results[0] if results else "No backend requested."
    return f"Sent {sent_count} of {len(targets)} requested attachment(s).\n" + "\n".join(results)


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    return ActionEnvelope(
        name="attach_file.send",
        args=args,
        reason="Send a workspace file attachment through a configured messaging backend.",
        writes=[],
        requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
