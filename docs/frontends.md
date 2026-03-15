# Frontends

BUDUCCA frontends are bidirectional adapters: they receive messages and send replies on the same channel. Google Fi additionally emits call events that are logged.

## Telegram

Set `telegram.mode` in `config/telegram.json`:

- `"bot"` for bot-token mode.
- `"user"` for full-account mode via Telethon session.

User-mode note:

- If Telegram user mode feels slow, the main cost is usually chat/message scanning and reconnect churn, not MTProto itself.
- This repo keeps a persistent Telethon session now to reduce that overhead.
- If you need even lower latency or heavier full-account workloads, TDLib is the usual next step. Pyrogram is also viable, but it is still an MTProto wrapper in the same general class as Telethon.

Resetting Telegram user-mode sync:

- BUDUCCA stores the last seen Telegram message id per chat in a state file next to `telegram.session_path`.
- If `session_path` is `data/telegram_user`, the sync cursor is stored in `data/telegram_user.updates.json`.
- Delete only the `*.updates.json` file to force BUDUCCA to rescan and resync messages while keeping the existing Telegram login session.
- Delete the session file (`data/telegram_user.session`, or the exact file for your configured `session_path`) only if you also want to re-authenticate the Telegram account.

## Signal

Configure `signal` in `config/signal.json` and point BUDUCCA to your `signal-cli` setup.

Use a `receive_command` shaped like `["signal-cli", "-a", "<account>", "receive", "--timeout", "1"]`. The command is executed as configured.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.signal_signup --config config
```

## WhatsApp

Configure `whatsapp` in `config/whatsapp.json` with receive/send JSON commands. The example config now points at the in-repo bridge: `python3 -m messaging_llm_bot.whatsapp_bridge`.

Backend flow:

- `run_bot.py` loads `Bot`, which creates `WhatsAppClient` from `whatsapp.receive_command` and `whatsapp.send_command`.
- On each poll, `WhatsAppClient.get_updates()` runs `receive_command` as a subprocess and expects JSON on stdout.
- The JSON is normalized into internal `IncomingMessage` objects, then processed by the same bot pipeline used by the other frontends.
- When the bot replies, `WhatsAppClient.send_message()` runs `send_command` with `{recipient}` and `{message}` replaced.
- The concrete bridge in this repo uses Playwright against WhatsApp Web, persists browser state under your configured `--session` path, and opens the signup QR in a real browser window during pairing.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.whatsapp_signup --config config
```

Repo setup:

```bash
cp -R config.example config
pip install playwright
python3 -m playwright install chromium
python3 -m messaging_llm_bot.whatsapp_signup --config config
```

Default config shape:

```json
{
  "account": "personal",
  "poll_interval_seconds": 1.0,
  "allowed_sender_ids": [],
  "allowed_group_ids_when_sender_not_allowed": [],
  "receive_command": [
    "python3",
    "-m",
    "messaging_llm_bot.whatsapp_bridge",
    "receive",
    "--session",
    "data/whatsapp-personal"
  ],
  "send_command": [
    "python3",
    "-m",
    "messaging_llm_bot.whatsapp_bridge",
    "send",
    "--session",
    "data/whatsapp-personal",
    "--recipient",
    "{recipient}",
    "--message",
    "{message}",
    "--attachment",
    "{attachment}"
  ]
}
```

Signup sequence:

```bash
python3 -m messaging_llm_bot.whatsapp_bridge pair --session data/whatsapp-personal --headful
```

During `pair`, WhatsApp Web opens in a browser window. Scan the QR from WhatsApp on your phone:

```text
WhatsApp -> Settings -> Linked Devices -> Link a Device
```

After pairing, test the same commands BUDUCCA will use:

```bash
python3 -m messaging_llm_bot.whatsapp_bridge receive --session data/whatsapp-personal
python3 -m messaging_llm_bot.whatsapp_bridge send --session data/whatsapp-personal --recipient "+15550001111" --message "test"
python3 run_bot.py --config config
```

Recipient notes:

- Direct chats: pass either a phone number like `+15550001111` or a raw WhatsApp chat id like `15550001111@c.us`.
- Groups: use the exact `conversation_id` BUDUCCA sees, which is emitted as `group:<name>|<chat-id>`.
- The example config already includes `{attachment}`, so the attach-file skill works without extra command edits.

## Common behavior flags

Per frontend (`telegram`, `signal`, `whatsapp`):

- `read_only: true` → receive-only mode, no outgoing replies.
- `store_unanswered_messages: true` → persist non-agent/unanswered messages into workspace files.

Unread-storage files by frontend:

- Handled incoming messages are recorded in `workspace/logs/agenta_queries.history`.
- Outgoing frontend messages are recorded in `workspace/logs/{backend}.history`.
- Unanswered incoming Telegram messages are stored in `workspace/telegram.recent`. `workspace/telegram.messages.recent` is legacy compatibility input and is no longer written.
- Unanswered incoming Signal messages are stored in `workspace/signal.messages.recent`.
- Unanswered incoming WhatsApp messages are stored in `workspace/whatsapp.messages.recent`.
- Unanswered incoming Google Fi messages are stored in `workspace/google_fi.messages.recent`. Google Fi call events are stored once in `workspace/google_fi.calls.recent`.

Global runtime:

- `runtime.max_reply_chunk_chars` → split long responses into smaller message chunks.

## Frontend commands

- `/status` returns bot uptime and collector status without calling the LLM.
- `/now` returns the 10 most recent non-empty lines from each frontend `.recent` file without calling the LLM.
- `/plan` shows the concrete plan-mode payload shapes injected into the agent for `update_plan` and `request_user_input`. These are exact typed payload shapes, not JSON Schema documents.
- `/skill` lists currently loaded skills.
- `/skill <skill_name>` shows the skill description, README-backed `What it does` section when available, and args schema.
- `/skill <skill_name> {"key":"value"}` runs a skill directly from the frontend with JSON object args.
- `/skill <skill_name> key:value` runs a skill with lightweight passthrough args when you want to avoid JSON braces and quotes.
- `/skill run <skill_name> {"key":"value"}` is the explicit passthrough form when you want docs and execution to stay unambiguous.

`/skill` reloads the skills directory on each command so newly added or removed skills are reflected without restarting the bot.

## Allowlist overrides

- Telegram chat allowlist: `telegram.allowed_chat_ids`
- Signal sender allowlist: `signal.allowed_sender_ids`
- Signal group allowlist override: `signal.allowed_group_ids_when_sender_not_allowed`

## Voice notes

When `runtime.enable_voice_notes` is `true`, configure `runtime.voice_transcribe_command` with placeholders:

- `{input}`: downloaded voice-note file path
- `{input_dir}`: temporary directory containing that file

Example:

```json
"voice_transcribe_command": [
  "whisper",
  "--model", "base.en",
  "--output_dir", "{input_dir}",
  "--output_format", "txt",
  "{input}"
]
```



## Google Fi

Google Fi support is upstreamed into `messaging_llm_bot/google_fi_client.py`.

Use module commands directly:

```bash
python3 -m messaging_llm_bot.google_fi_client receive
python3 -m messaging_llm_bot.google_fi_client send --recipient "+15550001111" --message "test"
python3 -m messaging_llm_bot.google_fi_client list-messages
```

Setup requirements:

```bash
pip install playwright
playwright install chromium
python3 -m messaging_llm_bot.google_fi_client receive --headful
```

The first headful run is used to complete Google Messages login and save browser/session state under `data/google_fi_browser_profile`.
The receive command stores its dedupe state under `workspace/data/google_fi_receive_state.json` by default, so separate workspaces do not share message dedupe history.
If login takes longer, increase the wait window with `--signup-wait-seconds` (default: `300`).

Per-frontend flags also apply: `read_only` and `store_unanswered_messages`.

Timestamp logging note:

- `logged_collected_match` in frontend debug logs compares `logged_at` and `collected_at` only.
- For Google Fi, incoming messages prefer the message timestamp recovered from Google Messages as `logged_at` and `sent_at`.
- `collected_at` is still set when BUDUCCA writes the record locally.
- Because of that, `logged_collected_match=False` is expected whenever Google Fi successfully recovers an older message timestamp from the DOM. It means "message time differs from ingestion time", not "timestamp parsing failed".
- If Google Fi cannot recover a parseable message timestamp, `logged_at` falls back to the current write time and `logged_collected_match` will usually be `True`.
