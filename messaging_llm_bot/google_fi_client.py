from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any

GOOGLE_MESSAGES_URL = "https://messages.google.com/web/conversations"
logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    update_id: int
    backend: str
    conversation_id: str
    sender_id: str
    text: str | None = None
    sender_name: str | None = None
    sender_contact: str | None = None
    event_type: str = "message"


class GoogleFiFrontendUnavailableError(RuntimeError):
    """Raised when Google Fi frontend is not runnable in current environment."""


class GoogleFiAutomationError(RuntimeError):
    """Raised when Google Fi web automation cannot complete."""


@dataclass
class BrowserOptions:
    workspace: Path
    headless: bool = True
    timeout_ms: int = 15000
    browser_channel: str | None = "chrome"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    @property
    def profile_dir(self) -> Path:
        return self.workspace / "google_fi_browser_profile"


class GoogleFiClient:
    def __init__(self, receive_command: list[str], send_command: list[str]) -> None:
        self.receive_command = receive_command
        self.send_command = send_command
        self._update_counter = 0

    def _validate(self, command: list[str], *, name: str) -> None:
        if not command:
            raise GoogleFiFrontendUnavailableError(f"Google Fi frontend disabled: {name} command is empty")
        executable = command[0]
        if "/" not in executable and which(executable) is None:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {executable!r} was not found in PATH"
            )

    def get_updates(self) -> list[IncomingMessage]:
        self._validate(self.receive_command, name="receive")
        try:
            proc = subprocess.run(self.receive_command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
            raise RuntimeError(f"Google Fi receive command failed: {stderr}")
        return self._parse_updates(proc.stdout)

    def send_message(self, recipient: str, text: str) -> None:
        self._validate(self.send_command, name="send")
        command = [part.replace("{recipient}", recipient).replace("{message}", text) for part in self.send_command]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
            raise RuntimeError(f"Google Fi send command failed: {stderr}")

    def _parse_updates(self, stdout: str) -> list[IncomingMessage]:
        text = stdout.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Google Fi receive command must output valid JSON") from exc

        if isinstance(payload, dict):
            raw_messages = payload.get("messages", [])
            raw_calls = payload.get("calls", [])
        elif isinstance(payload, list):
            raw_messages = payload
            raw_calls = []
        else:
            return []

        updates: list[IncomingMessage] = []
        for item in raw_messages:
            parsed = self._parse_message(item)
            if parsed:
                updates.append(parsed)
        for item in raw_calls:
            parsed = self._parse_call(item)
            if parsed:
                updates.append(parsed)
        return updates

    def _parse_message(self, item: Any) -> IncomingMessage | None:
        if not isinstance(item, dict):
            return None
        text_value = self._first_text(item.get("text"), item.get("body"), item.get("message"))
        conversation_id = self._first_text(item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId"))
        sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("sender"), item.get("number"))
        if not text_value or not conversation_id or not sender_id:
            return None
        sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
        sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
        return IncomingMessage(
            update_id=self._next_update_id(),
            backend="google_fi",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text_value,
            sender_name=sender_name,
            sender_contact=sender_contact,
            event_type="message",
        )

    def _parse_call(self, item: Any) -> IncomingMessage | None:
        if not isinstance(item, dict):
            return None
        conversation_id = self._first_text(item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId"))
        sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("caller"), item.get("number"))
        if not conversation_id or not sender_id:
            return None
        sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
        sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
        call_state = self._first_text(item.get("status"), item.get("state"), item.get("call_state")) or "received"
        return IncomingMessage(
            update_id=self._next_update_id(),
            backend="google_fi",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=f"[Call event] {call_state}",
            sender_name=sender_name,
            sender_contact=sender_contact,
            event_type="call",
        )

    def _next_update_id(self) -> int:
        self._update_counter += 1
        return self._update_counter

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


def _open_messages_page(options: BrowserOptions):
    from playwright.sync_api import sync_playwright  # type: ignore

    options.profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        p = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": options.headless,
            "user_agent": options.user_agent,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if options.browser_channel:
            launch_kwargs["channel"] = options.browser_channel
        try:
            context = p.chromium.launch_persistent_context(str(options.profile_dir), **launch_kwargs)
        except Exception as exc:
            if options.browser_channel and "channel" in str(exc).lower():
                launch_kwargs.pop("channel", None)
                context = p.chromium.launch_persistent_context(str(options.profile_dir), **launch_kwargs)
            else:
                raise
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(GOOGLE_MESSAGES_URL, wait_until="domcontentloaded", timeout=options.timeout_ms)
        return p, context, page
    except KeyError as exc:
        if exc.args and exc.args[0] == "deviceDescriptors":
            raise GoogleFiFrontendUnavailableError(
                "Playwright installation is mismatched (missing 'deviceDescriptors'). "
                "Reinstall Playwright for this Python environment and run 'python3 -m playwright install chromium'."
            ) from exc
        raise


def _ensure_logged_in(page, timeout_ms: int, *, headful: bool = False, signup_wait_ms: int = 300000) -> None:
    try:
        page.wait_for_selector(
            "mws-conversation-list-item, a[href*='/web/conversations/'], [aria-label*='Conversation']",
            timeout=timeout_ms,
        )
        return
    except Exception:
        if headful and signup_wait_ms > 0:
            print(
                "Google Messages is not logged in yet. "
                "Complete login in the open browser window; waiting for conversations to appear...",
                file=sys.stderr,
            )
            try:
                page.wait_for_selector(
                    "mws-conversation-list-item, a[href*='/web/conversations/'], [aria-label*='Conversation']",
                    timeout=signup_wait_ms,
                )
                return
            except Exception:
                pass
    blocked_by_browser_security = False
    try:
        blocked_by_browser_security = int(page.locator("text=This browser or app may not be secure").count()) > 0
    except Exception:
        blocked_by_browser_security = False
    if blocked_by_browser_security:
        raise GoogleFiAutomationError(
            "Google blocked this login as an insecure browser. "
            "Run in --headful mode and ensure Google Chrome is installed so Playwright can use a standard Chrome profile."
        )
    raise GoogleFiAutomationError("Google Messages is not logged in or UI changed. Run with --headful once and login.")


def _extract_conversation_id_from_href(href: str) -> str:
    match = re.search(r"/web/conversations/(\d+)", href)
    if match:
        return match.group(1)
    return href.strip() or "unknown"


def _extract_conversation_id_from_row(row: Any, idx: int) -> str | None:
    href = (row.get_attribute("href") or "").strip()
    if "/web/conversations/new" in href:
        logger.debug("Skipping row %s because href points to new-conversation placeholder: %r", idx, href)
        return None
    conversation_id = _extract_conversation_id_from_href(href)
    if conversation_id not in {"", "unknown", "/web/conversations/new"}:
        logger.debug("Resolved conversation id for row %s from direct href: %s", idx, conversation_id)
        return conversation_id

    try:
        nested = row.locator("a[href*='/web/conversations/']:not([href*='/web/conversations/new'])")
        if nested.count() > 0:
            nested_href = (nested.first.get_attribute("href") or "").strip()
            nested_id = _extract_conversation_id_from_href(nested_href)
            if nested_id not in {"", "unknown", "/web/conversations/new"}:
                logger.debug("Resolved conversation id for row %s from nested href: %s", idx, nested_id)
                return nested_id
    except Exception:
        logger.debug("Failed nested href extraction for row %s", idx, exc_info=True)

    for attr in ("data-thread-id", "data-conversation-id", "data-id", "id"):
        raw = (row.get_attribute(attr) or "").strip()
        if raw and raw.lower() not in {"new", "conversation-new"}:
            logger.debug("Resolved conversation id for row %s from attribute %s: %s", idx, attr, raw)
            return raw

    logger.debug("Could not resolve conversation id for row %s", idx)
    return None


def _parse_possible_call_state(text: str) -> str | None:
    lowered = text.lower()
    if "missed call" in lowered:
        return "missed"
    if "incoming call" in lowered or "received call" in lowered:
        return "received"
    if "outgoing call" in lowered:
        return "outgoing"
    if "call" in lowered:
        return "call"
    return None


def receive_events(
    *, workspace: str = "workspace", state_file: str = "google_fi_receive_state.json", headful: bool = False,
    max_conversations: int = 12, max_bubbles: int = 20, dry_run: bool = False, signup_wait_seconds: int = 300,
) -> dict[str, list[dict[str, str]]]:
    if dry_run:
        logger.info("google_fi receive running in dry-run mode")
        return {"messages": [], "calls": []}

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    state_path = workspace_path / state_file
    state = _load_state(state_path)
    seen: dict[str, str] = state.get("seen", {})
    logger.info(
        "google_fi receive start workspace=%s state_file=%s headful=%s max_conversations=%s max_bubbles=%s seen_entries=%s",
        workspace_path,
        state_file,
        headful,
        max_conversations,
        max_bubbles,
        len(seen),
    )

    p = context = page = None
    try:
        p, context, page = _open_messages_page(BrowserOptions(workspace=workspace_path, headless=not headful))
        _ensure_logged_in(page, 15000, headful=headful, signup_wait_ms=max(0, signup_wait_seconds) * 1000)

        rows = page.locator(
            "mws-conversation-list-item, "
            "a[href*='/web/conversations/']:not([href*='/web/conversations/new'])"
        )
        total = min(rows.count(), max_conversations)
        logger.info("google_fi receive conversation rows detected=%s scanning=%s", rows.count(), total)
        messages: list[dict[str, str]] = []
        calls: list[dict[str, str]] = []
        skipped_rows = 0

        for idx in range(total):
            row = rows.nth(idx)
            try:
                row.click(timeout=2000)
            except Exception:
                skipped_rows += 1
                logger.debug("Skipping row %s because click failed", idx, exc_info=True)
                continue
            conversation_id = _extract_conversation_id_from_row(row, idx)
            if not conversation_id:
                skipped_rows += 1
                continue
            title = (row.inner_text(timeout=800) or "").strip().split("\n", 1)[0]
            bubbles = page.locator(
                "mws-text-message-content, mws-message-part-content, "
                ".text-msg, [data-e2e-message-text]"
            )
            bubble_total = min(bubbles.count(), max_bubbles)
            logger.debug(
                "Row %s conversation_id=%s title=%r bubble_count=%s scan_tail=%s",
                idx,
                conversation_id,
                title,
                bubbles.count(),
                bubble_total,
            )
            for j in range(max(0, bubble_total - 4), bubble_total):
                text = (bubbles.nth(j).inner_text(timeout=800) or "").strip()
                if not text:
                    logger.debug("Skipping empty bubble row=%s bubble=%s", idx, j)
                    continue
                key = f"{conversation_id}::{text}"
                if seen.get(conversation_id) == key:
                    logger.debug("Skipping duplicate seen event conversation_id=%s key=%r", conversation_id, key)
                    continue
                seen[conversation_id] = key
                event = {
                    "conversation_id": conversation_id,
                    "sender_id": title or conversation_id,
                    "sender_name": title or "",
                    "text": text,
                }
                call_state = _parse_possible_call_state(text)
                if call_state:
                    logger.debug("Captured call event conversation_id=%s state=%s text=%r", conversation_id, call_state, text)
                    calls.append({**event, "status": call_state, "received_at": datetime.now(timezone.utc).isoformat()})
                else:
                    logger.debug("Captured message event conversation_id=%s text=%r", conversation_id, text)
                    messages.append(event)

        _save_state(state_path, {"seen": seen})
        logger.info(
            "google_fi receive complete messages=%s calls=%s skipped_rows=%s updated_seen_entries=%s",
            len(messages),
            len(calls),
            skipped_rows,
            len(seen),
        )
        return {"messages": messages, "calls": calls}
    finally:
        try:
            if context is not None:
                context.close()
        finally:
            if p is not None:
                p.stop()


def send_via_browser(
    *, recipient: str, message: str, workspace: str = "workspace", headful: bool = False, dry_run: bool = False, signup_wait_seconds: int = 300,
) -> dict[str, object]:
    if dry_run:
        return {"ok": True, "dry_run": True, "recipient": recipient}

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    p = context = page = None
    try:
        p, context, page = _open_messages_page(BrowserOptions(workspace=workspace_path, headless=not headful))
        _ensure_logged_in(page, 15000, headful=headful, signup_wait_ms=max(0, signup_wait_seconds) * 1000)
        for selector in ["input[type='search']", "input[aria-label*='To']", "input[aria-label*='Search']", "textarea[aria-label*='To']"]:
            loc = page.locator(selector)
            if loc.count() == 0:
                continue
            try:
                loc.first.fill(recipient, timeout=1500)
                page.keyboard.press("Enter")
                break
            except Exception:
                continue

        compose = None
        for selector in ["textarea[placeholder*='message']", "textarea[aria-label*='message']", "div[contenteditable='true']"]:
            loc = page.locator(selector)
            if loc.count() == 0:
                continue
            compose = loc.first
            break
        if compose is None:
            raise GoogleFiAutomationError("Could not find message composer in Google Messages UI.")

        try:
            compose.fill(message, timeout=2000)
        except Exception:
            compose.type(message, delay=10)

        for selector in ["button[aria-label*='Send']", "mws-send-button button", "button:has-text('Send')"]:
            loc = page.locator(selector)
            if loc.count() == 0:
                continue
            try:
                loc.first.click(timeout=1200)
                return {"ok": True, "recipient": recipient}
            except Exception:
                continue
        page.keyboard.press("Enter")
        return {"ok": True, "recipient": recipient}
    finally:
        try:
            if context is not None:
                context.close()
        finally:
            if p is not None:
                p.stop()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen": {}}
    seen = raw.get("seen") if isinstance(raw, dict) else None
    return {"seen": seen if isinstance(seen, dict) else {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-fi", description="Google Fi / Messages web CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    recv = sub.add_parser("receive")
    recv.add_argument("--workspace", default="workspace")
    recv.add_argument("--state-file", default="google_fi_receive_state.json")
    recv.add_argument("--headful", action="store_true")
    recv.add_argument("--max-conversations", type=int, default=12)
    recv.add_argument("--max-bubbles", type=int, default=20)
    recv.add_argument("--signup-wait-seconds", type=int, default=300)
    recv.add_argument("--dry-run", action="store_true")
    recv.add_argument("--verbose", action="store_true")

    send = sub.add_parser("send")
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", required=True)
    send.add_argument("--workspace", default="workspace")
    send.add_argument("--headful", action="store_true")
    send.add_argument("--signup-wait-seconds", type=int, default=300)
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--verbose", action="store_true")

    list_messages = sub.add_parser("list-messages")
    list_messages.add_argument("--workspace", default="workspace")
    list_messages.add_argument("--headful", action="store_true")
    list_messages.add_argument("--signup-wait-seconds", type=int, default=300)
    list_messages.add_argument("--dry-run", action="store_true")
    list_messages.add_argument("--verbose", action="store_true")
    return parser


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    try:
        if args.command == "receive":
            payload = receive_events(
                workspace=args.workspace,
                state_file=args.state_file,
                headful=args.headful,
                max_conversations=args.max_conversations,
                max_bubbles=args.max_bubbles,
                dry_run=args.dry_run,
                signup_wait_seconds=args.signup_wait_seconds,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        if args.command == "send":
            payload = send_via_browser(
                recipient=args.recipient,
                message=args.message,
                workspace=args.workspace,
                headful=args.headful,
                dry_run=args.dry_run,
                signup_wait_seconds=args.signup_wait_seconds,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        payload = receive_events(
            workspace=args.workspace,
            headful=args.headful,
            dry_run=args.dry_run,
            signup_wait_seconds=args.signup_wait_seconds,
        )
        for item in payload.get("messages", []):
            sender = item.get("sender_name") or item.get("sender_id") or "unknown"
            print(f"{sender}: {item.get('text', '')}")
        return 0
    except (GoogleFiFrontendUnavailableError, GoogleFiAutomationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
