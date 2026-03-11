from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"


class WhatsAppBridgeError(RuntimeError):
    """Raised when WhatsApp Web automation cannot complete."""


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _default_state_file(session_path: str) -> str:
    return f"{_resolve_path(session_path)}.receive-state.json"


def _default_media_dir(session_path: str) -> str:
    return f"{_resolve_path(session_path)}-media"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_message_ids": []}
    if not isinstance(raw, dict):
        return {"seen_message_ids": []}
    seen = raw.get("seen_message_ids")
    if not isinstance(seen, list):
        seen = []
    return {"seen_message_ids": [str(item) for item in seen]}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _runtime_imports():
    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local runtime
        raise WhatsAppBridgeError(
            "Playwright is required for the pure-Python WhatsApp bridge. "
            "Install it in this Python environment and run 'python3 -m playwright install chromium'."
        ) from exc
    return sync_playwright, PlaywrightError, PlaywrightTimeoutError


def _launch_context(
    *,
    session_dir: Path,
    headless: bool,
    browser_path: str,
    timeout_ms: int,
):
    sync_playwright, PlaywrightError, _ = _runtime_imports()
    _ensure_dir(session_dir)
    playwright = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if browser_path:
        launch_kwargs["executable_path"] = str(_resolve_path(browser_path))
    try:
        context = playwright.chromium.launch_persistent_context(str(session_dir), **launch_kwargs)
    except PlaywrightError as exc:  # pragma: no cover - depends on local runtime
        playwright.stop()
        raise WhatsAppBridgeError(f"Failed to launch Chromium for WhatsApp: {exc}") from exc
    context.set_default_timeout(timeout_ms)
    page = context.pages[0] if context.pages else context.new_page()
    return playwright, context, page


def _open_whatsapp(page: Any, timeout_ms: int) -> None:
    page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded", timeout=timeout_ms)


def _wait_for_login(page: Any, timeout_ms: int, *, interactive_wait_ms: int = 0) -> None:
    _, _, PlaywrightTimeoutError = _runtime_imports()
    ready_selectors = [
        "div[aria-label='Chat list']",
        "div[data-testid='chat-list']",
        "div[role='grid']",
        "div[data-testid='pane-side']",
    ]
    ready_selector = ", ".join(ready_selectors)
    try:
        page.wait_for_selector(ready_selector, timeout=timeout_ms)
        return
    except PlaywrightTimeoutError:
        if interactive_wait_ms > 0:
            print(
                "WhatsApp Web is not linked yet. Scan the QR in the open browser window:",
                file=sys.stderr,
            )
            print("WhatsApp -> Settings -> Linked Devices -> Link a Device", file=sys.stderr)
            page.wait_for_selector(ready_selector, timeout=interactive_wait_ms)
            return
        raise WhatsAppBridgeError(
            "WhatsApp Web is not ready. Run the pair command in headful mode and complete Linked Devices login."
        )


def _conversation_lookup_label(recipient: str) -> str:
    if recipient.startswith("group:"):
        _, _, tail = recipient.partition(":")
        name, _, raw_id = tail.partition("|")
        return name.strip() or raw_id.strip()
    if recipient.endswith("@c.us") or recipient.endswith("@g.us"):
        return recipient.split("@", 1)[0]
    return recipient.strip()


def _phone_digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _install_store(page: Any) -> None:
    page.evaluate(
        """
        () => {
          if (window.__BUDUCCA_WA_STORE) {
            return true;
          }
          const chunkName = Object.keys(window).find((key) => key.startsWith("webpackChunk"));
          if (!chunkName || !Array.isArray(window[chunkName])) {
            throw new Error("WhatsApp webpack runtime not found");
          }
          const chunk = window[chunkName];
          let webpackRequire = null;
          chunk.push([[`buducca_${Date.now()}`], {}, (req) => { webpackRequire = req; }]);
          if (!webpackRequire) {
            throw new Error("WhatsApp webpack require is unavailable");
          }
          const modules = [];
          for (const moduleId of Object.keys(webpackRequire.m || {})) {
            try {
              modules.push(webpackRequire(moduleId));
            } catch (error) {
            }
          }
          const find = (predicate) => modules.find((mod) => {
            try {
              return Boolean(predicate(mod));
            } catch (error) {
              return false;
            }
          });
          const storeContainer = find((mod) => mod?.Chat?.models && mod?.Msg?.models);
          const widFactory = find((mod) => typeof mod?.createWid === "function");
          const sendTextModule = find((mod) => typeof mod?.sendTextMsgToChat === "function");
          window.__BUDUCCA_WA_STORE = {
            Chat: storeContainer?.Chat || null,
            Msg: storeContainer?.Msg || null,
            WidFactory: widFactory || null,
            sendTextMsgToChat: sendTextModule?.sendTextMsgToChat || null,
          };
          if (!window.__BUDUCCA_WA_STORE.Chat || !window.__BUDUCCA_WA_STORE.Msg) {
            throw new Error("WhatsApp data store is unavailable");
          }
          return true;
        }
        """
    )


def _get_unseen_messages(page: Any, seen_message_ids: list[str]) -> list[dict[str, Any]]:
    _install_store(page)
    result = page.evaluate(
        """
        (seenIds) => {
          const seen = new Set(seenIds || []);
          const store = window.__BUDUCCA_WA_STORE;
          const chats = Array.isArray(store.Chat?.models) ? store.Chat.models : [];
          const messages = [];
          for (const chat of chats) {
            const chatMessages = Array.isArray(chat?.msgs?.models) ? chat.msgs.models : [];
            for (const msg of chatMessages) {
              const id = msg?.id?._serialized || null;
              if (!id || seen.has(id) || msg?.fromMe || msg?.isNotification) {
                continue;
              }
              const chatId = chat?.id?._serialized || "";
              const chatName = String(chat?.formattedTitle || chat?.name || "chat").replaceAll("|", "/").trim() || "chat";
              const isGroup = Boolean(chat?.isGroup);
              const text = String(msg?.body || msg?.caption || "").trim();
              const type = String(msg?.type || "").trim();
              const normalizedText = text || (type && type !== "chat" ? `[Attachment: ${type}]` : "");
              if (!normalizedText) {
                continue;
              }
              messages.push({
                message_id: id,
                conversation_id: isGroup ? `group:${chatName}|${chatId}` : chatId,
                sender_id: msg?.author || msg?.from || chatId,
                sender_name: msg?._data?.notifyName || "",
                sender_contact: msg?._data?.notifyName || msg?.author || msg?.from || chatName,
                text: normalizedText,
                sent_at: msg?.t ? new Date(msg.t * 1000).toISOString() : null,
              });
            }
          }
          messages.sort((left, right) => {
            const leftTime = left.sent_at || "";
            const rightTime = right.sent_at || "";
            return leftTime.localeCompare(rightTime);
          });
          return messages;
        }
        """,
        seen_message_ids,
    )
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


def _open_chat_via_search(page: Any, recipient: str, timeout_ms: int) -> None:
    label = _conversation_lookup_label(recipient)
    phone = _phone_digits(label)
    if phone and recipient == label:
        page.goto(f"{WHATSAPP_WEB_URL}send?phone={quote(phone)}", wait_until="domcontentloaded", timeout=timeout_ms)
    search_box = page.locator("div[contenteditable='true'][role='textbox']").first
    search_box.wait_for(timeout=timeout_ms)
    search_box.click()
    search_box.fill("")
    search_box.type(label, delay=20)
    time.sleep(0.5)
    result = page.locator("[role='listitem'], [data-testid='cell-frame-container']").first
    result.wait_for(timeout=timeout_ms)
    result.click()


def _send_text(page: Any, message: str, timeout_ms: int) -> None:
    input_box = page.locator("footer div[contenteditable='true'][role='textbox']").last
    input_box.wait_for(timeout=timeout_ms)
    input_box.click()
    input_box.type(message, delay=10)
    input_box.press("Enter")


def _send_attachment(page: Any, attachment: Path, caption: str, timeout_ms: int) -> None:
    attach_input = page.locator("input[type='file']").first
    if attach_input.count() == 0:
        attach_button = page.locator("[data-testid='clip'], span[data-icon='plus-rounded']").first
        attach_button.wait_for(timeout=timeout_ms)
        attach_button.click()
        attach_input = page.locator("input[type='file']").first
    attach_input.set_input_files(str(attachment))
    caption_box = page.locator("div[contenteditable='true'][role='textbox']").last
    caption_box.wait_for(timeout=timeout_ms)
    if caption:
        caption_box.click()
        caption_box.type(caption, delay=10)
    send_button = page.locator("[data-testid='send'], span[data-icon='send']").last
    send_button.wait_for(timeout=timeout_ms)
    send_button.click()


def command_pair(args: argparse.Namespace) -> int:
    headless = args.headless
    if args.command == "pair" and not args.headless_explicit:
        headless = False
    playwright, context, page = _launch_context(
        session_dir=_resolve_path(args.session),
        headless=headless,
        browser_path=args.browser_path,
        timeout_ms=args.ready_timeout_seconds * 1000,
    )
    try:
        _open_whatsapp(page, args.ready_timeout_seconds * 1000)
        _wait_for_login(
            page,
            args.ready_timeout_seconds * 1000,
            interactive_wait_ms=args.signup_wait_seconds * 1000,
        )
        print("WhatsApp session is linked.", file=sys.stderr)
        return 0
    finally:
        context.close()
        playwright.stop()


def command_receive(args: argparse.Namespace) -> int:
    playwright, context, page = _launch_context(
        session_dir=_resolve_path(args.session),
        headless=args.headless,
        browser_path=args.browser_path,
        timeout_ms=args.ready_timeout_seconds * 1000,
    )
    try:
        _open_whatsapp(page, args.ready_timeout_seconds * 1000)
        _wait_for_login(page, args.ready_timeout_seconds * 1000)
        state_path = _resolve_path(args.state_file or _default_state_file(args.session))
        state = _load_state(state_path)
        raw_messages = _get_unseen_messages(page, state.get("seen_message_ids", []))
        seen_ids = set(state.get("seen_message_ids", []))
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            message_id = str(item.get("message_id") or "").strip()
            if message_id:
                seen_ids.add(message_id)
            payload = {key: value for key, value in item.items() if key != "message_id"}
            messages.append(payload)
        _save_state(state_path, {"seen_message_ids": sorted(seen_ids)})
        print(json.dumps({"messages": messages}))
        return 0
    finally:
        context.close()
        playwright.stop()


def command_send(args: argparse.Namespace) -> int:
    playwright, context, page = _launch_context(
        session_dir=_resolve_path(args.session),
        headless=args.headless,
        browser_path=args.browser_path,
        timeout_ms=args.ready_timeout_seconds * 1000,
    )
    try:
        _open_whatsapp(page, args.ready_timeout_seconds * 1000)
        _wait_for_login(page, args.ready_timeout_seconds * 1000)
        _open_chat_via_search(page, args.recipient, args.ready_timeout_seconds * 1000)
        attachment = Path(args.attachment).expanduser() if args.attachment else None
        if attachment:
            if not attachment.exists():
                raise WhatsAppBridgeError(f"Attachment file does not exist: {attachment}")
            _send_attachment(page, attachment.resolve(), args.message or "", args.ready_timeout_seconds * 1000)
        elif args.message:
            _send_text(page, args.message, args.ready_timeout_seconds * 1000)
        else:
            raise WhatsAppBridgeError("Either --message or --attachment must be provided")
        return 0
    finally:
        context.close()
        playwright.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-bridge",
        description="Pure-Python WhatsApp Web bridge for BUDUCCA",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--session", default="data/whatsapp-default")
        subparser.add_argument("--browser-path", default="")
        subparser.add_argument("--ready-timeout-seconds", type=int, default=45)
        subparser.add_argument("--signup-wait-seconds", type=int, default=300)
        subparser.add_argument("--headless", dest="headless", action="store_true")
        subparser.add_argument("--headful", dest="headless", action="store_false")
        subparser.set_defaults(headless=True, headless_explicit=False)

    pair = subparsers.add_parser("pair", help="Open WhatsApp Web and wait for device linking")
    add_common(pair)
    pair.set_defaults(func=command_pair)

    receive = subparsers.add_parser("receive", help="Emit unread WhatsApp messages as JSON")
    add_common(receive)
    receive.add_argument("--state-file", default="")
    receive.add_argument("--media-dir", default=_default_media_dir("data/whatsapp-default"))
    receive.set_defaults(func=command_receive)

    send = subparsers.add_parser("send", help="Send a WhatsApp message or attachment")
    add_common(send)
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", default="")
    send.add_argument("--attachment", default="")
    send.set_defaults(func=command_send)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_argv = argv if argv is not None else sys.argv[1:]
    args.headless_explicit = "--headless" in raw_argv or "--headful" in raw_argv
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except WhatsAppBridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
