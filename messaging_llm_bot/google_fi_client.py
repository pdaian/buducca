from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import which
from typing import Any

from .interfaces import IncomingAttachment, IncomingMessage

GOOGLE_MESSAGES_URL = "https://messages.google.com/web/conversations"
logger = logging.getLogger(__name__)
_PHONE_PATTERN = re.compile(r"\+?[\d\s().-]{7,}")
DEFAULT_GOOGLE_FI_STATE_FILE = "data/google_fi_receive_state.json"


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
        workspace_root = self.workspace.resolve()
        return workspace_root.parent / "data" / "google_fi_browser_profile"


def _runtime_root(workspace: Path) -> Path:
    return workspace.resolve().parent


def _resolve_google_fi_state_path(workspace: Path, state_file: str) -> Path:
    candidate = Path(state_file)
    if candidate.is_absolute():
        return candidate
    return _runtime_root(workspace) / candidate


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

    def send_file(self, recipient: str, file_path: str, caption: str | None = None) -> None:
        self._validate(self.send_command, name="send")
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"Google Fi attachment file not found: {file_path}")
        if all("{attachment}" not in part for part in self.send_command):
            raise RuntimeError("Google Fi send_command must include a {attachment} placeholder for file sends.")
        command = [
            part.replace("{recipient}", recipient).replace("{message}", caption or "").replace("{attachment}", str(path))
            for part in self.send_command
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
            raise RuntimeError(f"Google Fi send attachment command failed: {stderr}")

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
        text_value = self._first_text(item.get("text"), item.get("body"), item.get("message"), item.get("content"))
        attachments = self._extract_attachments(item)
        conversation_id = self._first_text(item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId"))
        sender_id = self._pick_sender_id(
            item.get("sender_id"), item.get("from"), item.get("number"), item.get("sender"), item.get("sender_contact")
        )
        if (not text_value and not attachments) or not conversation_id or not sender_id:
            return None
        sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
        sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
        sent_at = self._extract_sent_at(item)
        return IncomingMessage(
            update_id=self._next_update_id(),
            backend="google_fi",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text_value,
            sender_name=sender_name,
            sender_contact=sender_contact,
            sent_at=sent_at,
            event_type="message",
            attachments=attachments,
        )

    def _parse_call(self, item: Any) -> IncomingMessage | None:
        if not isinstance(item, dict):
            return None
        conversation_id = self._first_text(item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId"))
        sender_id = self._pick_sender_id(
            item.get("sender_id"), item.get("from"), item.get("number"), item.get("caller"), item.get("sender_contact")
        )
        if not conversation_id or not sender_id:
            return None
        sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
        sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
        call_state = self._first_text(item.get("status"), item.get("state"), item.get("call_state")) or "received"
        sent_at = self._extract_sent_at(item, include_received_at=True)
        return IncomingMessage(
            update_id=self._next_update_id(),
            backend="google_fi",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=f"[Call event] {call_state}",
            sender_name=sender_name,
            sender_contact=sender_contact,
            sent_at=sent_at,
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
            if isinstance(value, (int, float)):
                rendered = str(value).strip()
                if rendered:
                    return rendered
            if isinstance(value, dict):
                nested = GoogleFiClient._first_text(value.get("text"), value.get("body"), value.get("message"), value.get("content"))
                if nested:
                    return nested
        return None

    @staticmethod
    def _phone_like_or_original(value: str | None) -> str | None:
        if not value:
            return None
        match = _PHONE_PATTERN.search(value)
        if not match:
            return value
        cleaned = "".join(ch for ch in match.group(0) if ch == "+" or ch.isdigit())
        return cleaned or value

    @classmethod
    def _pick_sender_id(cls, *values: Any) -> str | None:
        first_text = cls._first_text(*values)
        for value in values:
            candidate = cls._first_text(value)
            normalized = cls._phone_like_or_original(candidate)
            if normalized and normalized != candidate:
                return normalized
        return cls._phone_like_or_original(first_text)

    @staticmethod
    def _extract_sent_at(item: dict[str, Any], *, include_received_at: bool = False) -> str | None:
        timestamp_keys = ["sent_at", "timestamp", "date", "time"]
        if include_received_at:
            timestamp_keys.append("received_at")
        for key in timestamp_keys:
            value = item.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (int, float)):
                timestamp = float(value)
                if timestamp > 1_000_000_000_000:
                    timestamp /= 1000.0
                parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                logger.debug("Google Fi extracted sent_at from numeric key=%s raw=%r parsed=%s", key, value, parsed)
                return parsed
            if isinstance(value, str):
                cleaned = value.strip()
                if not cleaned:
                    continue
                if cleaned.isdigit():
                    timestamp = float(cleaned)
                    if timestamp > 1_000_000_000_000:
                        timestamp /= 1000.0
                    parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                    logger.debug("Google Fi extracted sent_at from digit-string key=%s raw=%r parsed=%s", key, value, parsed)
                    return parsed
                try:
                    parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                except ValueError:
                    parsed = None
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    rendered = parsed.isoformat()
                    logger.debug("Google Fi extracted sent_at from iso key=%s raw=%r parsed=%s", key, value, rendered)
                    return rendered
                parsed_google_timestamp = _parse_google_messages_timestamp(cleaned)
                if parsed_google_timestamp:
                    logger.debug(
                        "Google Fi extracted sent_at from Google Messages timestamp key=%s raw=%r parsed=%s",
                        key,
                        value,
                        parsed_google_timestamp,
                    )
                    return parsed_google_timestamp
                logger.debug("Google Fi timestamp candidate was not parseable key=%s raw=%r", key, value)
        logger.debug("Google Fi item did not include a parseable sent_at; keys=%s item=%r", sorted(item.keys()), item)
        return None

    def _extract_attachments(self, item: dict[str, Any]) -> list[IncomingAttachment]:
        raw = item.get("attachments")
        if not isinstance(raw, list):
            return []
        attachments: list[IncomingAttachment] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            file_path = self._first_text(entry.get("path"), entry.get("file_path"))
            if not file_path:
                continue
            attachments.append(
                IncomingAttachment(
                    file_path=file_path,
                    filename=self._first_text(entry.get("name"), entry.get("filename")),
                    mime_type=self._first_text(entry.get("mime_type"), entry.get("content_type")),
                )
            )
        return attachments


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
    ready_selector = (
        "a[href*='/web/conversations/']:not([href*='/web/conversations/new']), "
        "mws-conversation-list-item, [data-e2e-conversation-id], [data-thread-id]"
    )
    try:
        page.wait_for_selector(ready_selector, timeout=timeout_ms)
        return
    except Exception:
        if headful and signup_wait_ms > 0:
            print(
                "Google Messages is not logged in yet. "
                "Complete login in the open browser window; waiting for conversations to appear...",
                file=sys.stderr,
            )
            try:
                page.wait_for_selector(ready_selector, timeout=signup_wait_ms)
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


def _conversation_row_selectors() -> list[str]:
    return [
        "mws-conversation-list-item",
        "[data-e2e-conversation-id]",
        "[data-thread-id]",
        "[role='listitem']:has(a[href*='/web/conversations/'])",
        "a[href*='/web/conversations/']:not([href*='/web/conversations/new'])",
    ]


def _find_conversation_rows(page: Any):
    for selector in _conversation_row_selectors():
        locator = page.locator(selector)
        try:
            if locator.count() > 0:
                return locator, selector
        except Exception:
            logger.debug("Failed counting rows for selector %r", selector, exc_info=True)
    return page.locator("a[href*='/web/conversations/']:not([href*='/web/conversations/new'])"), "fallback-anchor"


def _expand_conversation_rows(page: Any, rows: Any, *, max_conversations: int) -> int:
    """Attempt to scroll the conversation list so lazy-loaded threads are discoverable."""
    try:
        known_count = rows.count()
    except Exception:
        return 0
    stable_rounds = 0
    max_rounds = 40
    while stable_rounds < 3 and max_rounds > 0:
        if max_conversations > 0 and known_count >= max_conversations:
            break
        max_rounds -= 1
        try:
            if known_count > 0:
                rows.nth(known_count - 1).scroll_into_view_if_needed(timeout=800)
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(120)
            current_count = rows.count()
        except Exception:
            break
        if current_count <= known_count:
            stable_rounds += 1
            continue
        known_count = current_count
        stable_rounds = 0
    return known_count


def _message_bubble_selectors() -> list[str]:
    return [
        "mws-text-message-content",
        "mws-message-part-content",
        "mws-message-text-content",
        "[data-e2e-message-text]",
        "[data-e2e-message-body]",
        "[data-message-id] [dir='auto']",
        ".text-msg",
    ]


def _message_timestamp_selectors() -> list[str]:
    return [
        "mws-message-timestamp",
        "time",
        "[data-e2e-message-timestamp]",
        "[data-e2e-timestamp]",
        "[data-message-timestamp]",
        "[class*='timestamp']",
        "[class*='time']",
    ]


def _find_message_bubbles(page: Any):
    for selector in _message_bubble_selectors():
        locator = page.locator(selector)
        try:
            if locator.count() > 0:
                return locator, selector
        except Exception:
            logger.debug("Failed counting bubbles for selector %r", selector, exc_info=True)
    fallback = "mws-text-message-content, mws-message-part-content, .text-msg, [data-e2e-message-text]"
    return page.locator(fallback), "fallback-bubbles"


def _collect_bubble_entries(page: Any, bubble_selector: str) -> list[dict[str, str]]:
    timestamp_selector = ", ".join(_message_timestamp_selectors())
    try:
        raw_entries = page.evaluate(
            r"""
            ({ bubbleSelector, timestampSelector }) => {
              const bubbleElements = new Set(Array.from(document.querySelectorAll(bubbleSelector)));
              const hasTimestamps = Boolean(timestampSelector && timestampSelector.trim());
              const timestampRegexes = [
                /^(today|yesterday)\s+\d{1,2}:\d{2}\s*(am|pm)$/i,
                /^(sun|mon|tue|wed|thu|fri|sat),?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)/i,
                /^(january|february|march|april|may|june|july|august|september|october|november|december)/i,
                /^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(,\s+\d{4})?,?\s+\d{1,2}:\d{2}\s*(am|pm)$/i,
                /^\d{1,2}:\d{2}\s*(am|pm)$/i,
              ];
              const timestampAttributes = [
                "data-message-timestamp",
                "data-timestamp",
                "data-e2e-message-timestamp",
                "datetime",
                "aria-label",
                "title",
              ];
              const readNodeText = (node) => ((node && (node.innerText || node.textContent)) || "").trim();
              const looksLikeTimestamp = (text) => {
                if (!text) {
                  return false;
                }
                return timestampRegexes.some((pattern) => pattern.test(text));
              };
              const readTimestampCandidate = (node) => {
                let current = node;
                for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
                  if (!current.getAttribute) {
                    continue;
                  }
                  for (const attr of timestampAttributes) {
                    const value = (current.getAttribute(attr) || "").trim();
                    if (value) {
                      return value;
                    }
                  }
                }
                return "";
              };
              const readTimestampNode = (node) => {
                if (!node) {
                  return "";
                }
                const attrValue = readTimestampCandidate(node);
                if (attrValue) {
                  return attrValue;
                }
                const textValue = readNodeText(node);
                if (looksLikeTimestamp(textValue)) {
                  return textValue;
                }
                return "";
              };
              const queryTimestampWithin = (node) => {
                if (!hasTimestamps || !node || !node.querySelectorAll) {
                  return "";
                }
                const matches = node.querySelectorAll(timestampSelector);
                for (const match of matches) {
                  const value = readTimestampNode(match);
                  if (value) {
                    return value;
                  }
                }
                return "";
              };
              const findNearbyTimestampText = (node) => {
                let current = node;
                for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
                  const ownValue = readTimestampNode(current);
                  if (ownValue) {
                    return ownValue;
                  }
                  const withinValue = queryTimestampWithin(current);
                  if (withinValue) {
                    return withinValue;
                  }
                  for (const sibling of [current.previousElementSibling, current.nextElementSibling]) {
                    const siblingValue = readTimestampNode(sibling);
                    if (siblingValue) {
                      return siblingValue;
                    }
                    const nestedSiblingValue = queryTimestampWithin(sibling);
                    if (nestedSiblingValue) {
                      return nestedSiblingValue;
                    }
                  }
                }
                return "";
              };
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
              const entries = [];
              let currentTimestamp = "";
              let node = walker.currentNode;
              while (node) {
                const text = readNodeText(node);
                if (bubbleElements.has(node)) {
                  if (text) {
                    entries.push({
                      text,
                      timestamp_text: currentTimestamp,
                      timestamp_hint: readTimestampCandidate(node),
                      inline_timestamp_text: findNearbyTimestampText(node),
                    });
                  }
                } else if (hasTimestamps && node.matches && node.matches(timestampSelector)) {
                  const timestampValue = readTimestampNode(node);
                  if (timestampValue) {
                    currentTimestamp = timestampValue;
                  }
                }
                node = walker.nextNode();
              }
              return entries;
            }
            """,
            {"bubbleSelector": bubble_selector, "timestampSelector": timestamp_selector},
        )
    except Exception:
        logger.debug("Failed to collect inline timestamp metadata for message bubbles", exc_info=True)
        return []

    entries: list[dict[str, str]] = []
    if not isinstance(raw_entries, list):
        return entries
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        text = GoogleFiClient._first_text(item.get("text"))
        if not text:
            continue
        timestamp_text = _pick_google_messages_timestamp_text(item)
        entries.append({"text": text, "timestamp_text": timestamp_text})
    return entries


def _pick_google_messages_timestamp_text(item: dict[str, Any]) -> str:
    for key in ("timestamp_text", "inline_timestamp_text", "timestamp_hint", "timestamp", "aria_label", "title"):
        candidate = GoogleFiClient._first_text(item.get(key))
        if candidate and _parse_google_messages_timestamp(candidate):
            return candidate
    return GoogleFiClient._first_text(item.get("timestamp_text")) or ""


def _debug_dump_conversation_elements(page: Any, conversation_id: str, *, limit: int = 200) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        elements = page.evaluate(
            """
            (maxItems) => {
              const selectors = [
                "mws-message-wrapper",
                "mws-text-message-content",
                "mws-message-part-content",
                "mws-message-text-content",
                "mws-message-timestamp",
                "[data-e2e-message-text]",
                "[data-e2e-message-body]",
                "[data-e2e-message-timestamp]",
                "time",
                "[class*='timestamp']",
                "[class*='time']",
              ].join(", ");
              return Array.from(document.querySelectorAll(selectors)).slice(0, maxItems).map((node) => ({
                tag: (node.tagName || "").toLowerCase(),
                class_name: typeof node.className === "string" ? node.className : "",
                text: ((node.innerText || node.textContent || "").trim()).slice(0, 160),
                aria_label: node.getAttribute ? (node.getAttribute("aria-label") || "") : "",
                title: node.getAttribute ? (node.getAttribute("title") || "") : "",
                data_timestamp: node.getAttribute ? (node.getAttribute("data-message-timestamp") || node.getAttribute("data-timestamp") || "") : "",
              }));
            }
            """,
            limit,
        )
    except Exception:
        logger.debug("Failed to capture Google Fi conversation element dump conversation_id=%s", conversation_id, exc_info=True)
        return
    logger.debug("Google Fi conversation element dump conversation_id=%s elements=%r", conversation_id, elements)


def _parse_google_messages_timestamp(value: str | None, *, reference: datetime | None = None) -> str | None:
    if not value:
        return None

    cleaned = " ".join(value.replace("\n", " ").split())
    cleaned = re.sub(r"^(sent|received)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        logger.debug("Google Fi timestamp parser received empty cleaned value raw=%r", value)
        return None

    now = reference or datetime.now().astimezone()
    local_tz = now.tzinfo or timezone.utc

    relative_match = re.match(r"^(today|yesterday)\s+(.+)$", cleaned, flags=re.IGNORECASE)
    if relative_match:
        day_label = relative_match.group(1).lower()
        time_part = relative_match.group(2).strip()
        try:
            parsed_time = datetime.strptime(time_part, "%I:%M %p")
        except ValueError:
            logger.debug("Google Fi relative timestamp parse failed raw=%r cleaned=%r", value, cleaned)
            return None
        day = now.date()
        if day_label == "yesterday":
            day = day.fromordinal(day.toordinal() - 1)
        candidate = datetime.combine(day, parsed_time.time(), tzinfo=local_tz)
        parsed = candidate.isoformat()
        logger.debug("Google Fi parsed relative timestamp raw=%r cleaned=%r parsed=%s", value, cleaned, parsed)
        return parsed

    formats = [
        ("%a, %b %d, %Y, %I:%M %p", False),
        ("%A, %B %d, %Y, %I:%M %p", False),
        ("%b %d, %Y, %I:%M %p", False),
        ("%B %d, %Y, %I:%M %p", False),
        ("%a, %b %d, %I:%M %p", True),
        ("%A, %B %d, %I:%M %p", True),
        ("%b %d, %I:%M %p", True),
        ("%B %d, %I:%M %p", True),
        ("%I:%M %p", True),
    ]
    for fmt, needs_inference in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if fmt == "%I:%M %p":
            candidate = datetime.combine(now.date(), parsed.time(), tzinfo=local_tz)
            if candidate > now:
                candidate -= timedelta(days=1)
            rendered = candidate.isoformat()
            logger.debug("Google Fi parsed inferred same-day timestamp raw=%r cleaned=%r parsed=%s", value, cleaned, rendered)
            return rendered
        if needs_inference:
            candidate = parsed.replace(year=now.year, tzinfo=local_tz)
            if candidate > now:
                try:
                    candidate = candidate.replace(year=candidate.year - 1)
                except ValueError:
                    candidate -= timedelta(days=365)
            rendered = candidate.isoformat()
            logger.debug("Google Fi parsed inferred-year timestamp raw=%r cleaned=%r parsed=%s", value, cleaned, rendered)
            return rendered
        rendered = parsed.replace(tzinfo=local_tz).isoformat()
        logger.debug("Google Fi parsed explicit timestamp raw=%r cleaned=%r parsed=%s", value, cleaned, rendered)
        return rendered
    logger.debug("Google Fi timestamp parser could not parse raw=%r cleaned=%r", value, cleaned)
    return None


def _expand_message_bubbles(page: Any, bubbles: Any, *, max_bubbles: int) -> int:
    """Attempt to scroll message history so older lazy-loaded bubbles become available."""
    try:
        known_count = bubbles.count()
    except Exception:
        return 0
    stable_rounds = 0
    max_rounds = 50
    while stable_rounds < 3 and max_rounds > 0:
        if max_bubbles > 0 and known_count >= max_bubbles:
            break
        max_rounds -= 1
        try:
            if known_count > 0:
                bubbles.nth(0).scroll_into_view_if_needed(timeout=800)
            page.mouse.wheel(0, -2600)
            page.wait_for_timeout(120)
            current_count = bubbles.count()
        except Exception:
            break
        if current_count <= known_count:
            stable_rounds += 1
            continue
        known_count = current_count
        stable_rounds = 0
    return known_count


def _wait_for_background_hydration(page: Any, *, wait_ms: int) -> None:
    """Give Google Messages time to finish post-load async rendering."""
    if wait_ms <= 0:
        return
    logger.debug("Waiting %sms for post-load background hydration", wait_ms)
    page.wait_for_timeout(wait_ms)


def receive_events(
    *, workspace: str = "workspace", state_file: str = DEFAULT_GOOGLE_FI_STATE_FILE, headful: bool = False,
    max_conversations: int = 0, max_bubbles: int = 0, dry_run: bool = False, signup_wait_seconds: int = 300,
    post_load_wait_ms: int = 2000,
) -> dict[str, list[dict[str, str]]]:
    if dry_run:
        logger.info("google_fi receive running in dry-run mode")
        return {"messages": [], "calls": []}

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    state_path = _resolve_google_fi_state_path(workspace_path, state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
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
        _wait_for_background_hydration(page, wait_ms=max(0, post_load_wait_ms))

        rows, selector = _find_conversation_rows(page)
        row_count = _expand_conversation_rows(page, rows, max_conversations=max_conversations)
        if row_count <= 0:
            row_count = rows.count()
        total = row_count if max_conversations <= 0 else min(row_count, max_conversations)
        logger.info(
            "google_fi receive conversation rows detected=%s scanning=%s selector=%s",
            row_count,
            total,
            selector,
        )
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
            page.wait_for_timeout(250)
            bubbles, bubble_selector = _find_message_bubbles(page)
            bubble_count = _expand_message_bubbles(page, bubbles, max_bubbles=max_bubbles)
            if bubble_count <= 0:
                bubble_count = bubbles.count()
            bubble_total = bubble_count if max_bubbles <= 0 else min(bubble_count, max_bubbles)
            start_idx = max(0, bubble_count - bubble_total)
            logger.debug(
                "Row %s conversation_id=%s title=%r bubble_count=%s scan_count=%s bubble_selector=%s",
                idx,
                conversation_id,
                title,
                bubble_count,
                bubble_total,
                bubble_selector,
            )
            bubble_entries = _collect_bubble_entries(page, bubble_selector)
            if bubble_entries and not any(_parse_google_messages_timestamp(entry.get("timestamp_text")) for entry in bubble_entries):
                logger.debug(
                    "Google Fi conversation had message bubbles but no parseable inline timestamps conversation_id=%s title=%r",
                    conversation_id,
                    title,
                )
                _debug_dump_conversation_elements(page, conversation_id)
            if bubble_entries:
                iter_entries = bubble_entries[start_idx:bubble_count]
            else:
                iter_entries = []
                for j in range(start_idx, bubble_count):
                    text = (bubbles.nth(j).inner_text(timeout=800) or "").strip()
                    if not text:
                        logger.debug("Skipping empty bubble row=%s bubble=%s", idx, j)
                        continue
                    iter_entries.append({"text": text, "timestamp_text": ""})
            for entry in iter_entries:
                text = entry["text"]
                key = f"{conversation_id}::{text}"
                if seen.get(conversation_id) == key:
                    logger.debug("Skipping duplicate seen event conversation_id=%s key=%r", conversation_id, key)
                    continue
                seen[conversation_id] = key
                extracted_sender_id = GoogleFiClient._phone_like_or_original(title) or conversation_id
                sent_at = _parse_google_messages_timestamp(entry.get("timestamp_text"))
                event = {
                    "conversation_id": conversation_id,
                    "sender_id": extracted_sender_id,
                    "sender_name": title or "",
                    "sender_contact": title or extracted_sender_id,
                    "text": text,
                }
                if sent_at:
                    event["sent_at"] = sent_at
                call_state = _parse_possible_call_state(text)
                if call_state:
                    logger.debug("Captured call event conversation_id=%s state=%s text=%r", conversation_id, call_state, text)
                    calls.append({**event, "status": call_state, "received_at": sent_at or datetime.now(timezone.utc).isoformat()})
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
    recv.add_argument("--state-file", default=DEFAULT_GOOGLE_FI_STATE_FILE)
    recv.add_argument("--headful", action="store_true")
    recv.add_argument("--max-conversations", type=int, default=0)
    recv.add_argument("--max-bubbles", type=int, default=0)
    recv.add_argument("--post-load-wait-ms", type=int, default=2000)
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
    list_messages.add_argument("--post-load-wait-ms", type=int, default=2000)
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
                post_load_wait_ms=args.post_load_wait_ms,
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
            post_load_wait_ms=args.post_load_wait_ms,
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
