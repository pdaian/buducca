from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any

GOOGLE_MESSAGES_URL = "https://messages.google.com/web/conversations"


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
        context = p.chromium.launch_persistent_context(str(options.profile_dir), headless=options.headless)
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


def _ensure_logged_in(page, timeout_ms: int) -> None:
    try:
        page.wait_for_selector(
            "mws-conversation-list-item, a[href*='/web/conversations/'], [aria-label*='Conversation']",
            timeout=timeout_ms,
        )
        return
    except Exception:
        pass
    raise GoogleFiAutomationError("Google Messages is not logged in or UI changed. Run with --headful once and login.")


def _extract_conversation_id_from_href(href: str) -> str:
    match = re.search(r"/web/conversations/(\d+)", href)
    if match:
        return match.group(1)
    return href.strip() or "unknown"


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
    max_conversations: int = 12, max_bubbles: int = 20, dry_run: bool = False,
) -> dict[str, list[dict[str, str]]]:
    if dry_run:
        return {"messages": [], "calls": []}

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    state_path = workspace_path / state_file
    state = _load_state(state_path)
    seen: dict[str, str] = state.get("seen", {})

    p = context = page = None
    try:
        p, context, page = _open_messages_page(BrowserOptions(workspace=workspace_path, headless=not headful))
        _ensure_logged_in(page, 15000)

        rows = page.locator("mws-conversation-list-item, a[href*='/web/conversations/'], [aria-label*='Conversation']")
        total = min(rows.count(), max_conversations)
        messages: list[dict[str, str]] = []
        calls: list[dict[str, str]] = []

        for idx in range(total):
            row = rows.nth(idx)
            try:
                row.click(timeout=2000)
            except Exception:
                continue
            href = row.get_attribute("href") or ""
            conversation_id = _extract_conversation_id_from_href(href) or f"row-{idx}"
            title = (row.inner_text(timeout=800) or "").strip().split("\n", 1)[0]
            bubbles = page.locator("mws-message-part-content, .text-msg, [data-e2e-message-text], [aria-label*='Message']")
            bubble_total = min(bubbles.count(), max_bubbles)
            for j in range(max(0, bubble_total - 4), bubble_total):
                text = (bubbles.nth(j).inner_text(timeout=800) or "").strip()
                if not text:
                    continue
                key = f"{conversation_id}::{text}"
                if seen.get(conversation_id) == key:
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
                    calls.append({**event, "status": call_state, "received_at": datetime.now(timezone.utc).isoformat()})
                else:
                    messages.append(event)

        _save_state(state_path, {"seen": seen})
        return {"messages": messages, "calls": calls}
    finally:
        try:
            if context is not None:
                context.close()
        finally:
            if p is not None:
                p.stop()


def send_via_browser(*, recipient: str, message: str, workspace: str = "workspace", headful: bool = False, dry_run: bool = False) -> dict[str, object]:
    if dry_run:
        return {"ok": True, "dry_run": True, "recipient": recipient}

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    p = context = page = None
    try:
        p, context, page = _open_messages_page(BrowserOptions(workspace=workspace_path, headless=not headful))
        _ensure_logged_in(page, 15000)
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
    recv.add_argument("--dry-run", action="store_true")

    send = sub.add_parser("send")
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", required=True)
    send.add_argument("--workspace", default="workspace")
    send.add_argument("--headful", action="store_true")
    send.add_argument("--dry-run", action="store_true")

    list_messages = sub.add_parser("list-messages")
    list_messages.add_argument("--workspace", default="workspace")
    list_messages.add_argument("--headful", action="store_true")
    list_messages.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "receive":
            payload = receive_events(
                workspace=args.workspace,
                state_file=args.state_file,
                headful=args.headful,
                max_conversations=args.max_conversations,
                max_bubbles=args.max_bubbles,
                dry_run=args.dry_run,
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
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        payload = receive_events(workspace=args.workspace, headful=args.headful, dry_run=args.dry_run)
        for item in payload.get("messages", []):
            sender = item.get("sender_name") or item.get("sender_id") or "unknown"
            print(f"{sender}: {item.get('text', '')}")
        return 0
    except (GoogleFiFrontendUnavailableError, GoogleFiAutomationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
