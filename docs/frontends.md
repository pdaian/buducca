# Frontends

BUDUCCA frontends are bidirectional adapters: they receive messages and send replies on the same channel. Google Fi additionally emits call events that are logged.

## Android

Android support is command-driven like Signal and WhatsApp, but the recommended device-side path is:

- `Termux` for the local Python runtime.
- `Termux:API` for SMS sending via `termux-sms-send`.
- Any visible notification/SMS automation on the device that can run a shell command and append JSON lines into `data/android-events.jsonl`.

The in-repo bridge is `python3 -m messaging_llm_bot.android_client`. It reads new JSONL events and sends SMS replies.

Concrete setup sequence on the Android device:

1. Install `Termux` and `Termux:API`.
2. In Termux, install Python and the Termux API package:

```bash
pkg update
pkg install python termux-api
```

3. Grant SMS permission to `Termux:API` in Android system settings. Without that, `termux-sms-send` cannot send replies.
4. From the repository root in Termux, create the inbox directory and file used by the bridge:

```bash
mkdir -p data
: > data/android-events.jsonl
```

5. Configure `config/android.json` with the example below and set `allowed_sender_ids` to the exact phone numbers that are allowed to trigger replies.
6. Set up a device-side automation rule for incoming SMS and any notifications you want to ingest. Each rule should run on the phone when the event arrives and append exactly one JSON object plus a trailing newline to `data/android-events.jsonl`. Use the repository root as the working directory or write to the file with an absolute path.
7. Verify the receive side by appending one test SMS event, then reading it through the bridge:

```bash
printf '%s\n' '{"type":"sms","sender_id":"+15551234567","body":"Ping","timestamp":"2026-03-18T09:00:00-04:00"}' >> data/android-events.jsonl
python3 -m messaging_llm_bot.android_client receive --inbox data/android-events.jsonl --state-file data/android-bridge-state.json
```

The command should print JSON with one message under `"messages"`.

8. Verify the send side:

```bash
python3 -m messaging_llm_bot.android_client send --recipient +15551234567 --message "BUDUCCA Android bridge test"
```

If this fails, fix `Termux:API` permissions before running the bot.
9. Start BUDUCCA:

```bash
python3 run_bot.py --config config
```

Example config:

```json
{
  "account": "android",
  "poll_interval_seconds": 1.0,
  "allowed_sender_ids": ["+15551234567"],
  "receive_command": [
    "python3",
    "-m",
    "messaging_llm_bot.android_client",
    "receive",
    "--inbox",
    "data/android-events.jsonl",
    "--state-file",
    "data/android-bridge-state.json"
  ],
  "send_command": [
    "python3",
    "-m",
    "messaging_llm_bot.android_client",
    "send",
    "--recipient",
    "{recipient}",
    "--message",
    "{message}"
  ],
  "read_only": false,
  "store_unanswered_messages": true
}
```

Recommended event shapes written by the Android side:

```json
{"type":"sms","sender_id":"+15551234567","body":"Ping","timestamp":"2026-03-18T09:00:00-04:00"}
{"type":"notification","package_name":"org.thoughtcrime.securesms","app_name":"Signal","title":"Alice","body":"Are you free?","timestamp":"2026-03-18T09:01:00-04:00"}
```

Minimum requirements for appended events:

- One valid JSON object per line in `data/android-events.jsonl`.
- The Android automation must append to the file; the bridge only reads new lines and does not fetch SMS or notifications from Android by itself.
- SMS events must include enough data for the bridge to derive `conversation_id`, `sender_id`, and message text. The example `type`, `sender_id`, and `body` fields are sufficient.
- Notification events should use `type: "notification"` and usually include `package_name`, `title`, and `body`.

Operational notes:

- Set `android.allowed_sender_ids` to the SMS numbers that may trigger agent replies.
- Leave `store_unanswered_messages` enabled if you want all notification traffic persisted to `workspace/android.messages.recent`.
- Notifications from non-allowlisted senders are still collected into the recent file, but they are not allowed to trigger replies.
- SMS replies use `termux-sms-send`, so the device must grant the required SMS permission to `Termux:API`.

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

Per frontend (`telegram`, `signal`, `whatsapp`, `google_fi`, `android`):

- `read_only: true` → receive-only mode, no outgoing replies.
- `store_unanswered_messages: true` → persist non-agent/unanswered messages into workspace files.

Unread-storage files by frontend:

- Handled incoming messages are recorded in `workspace/logs/agenta_queries.history`.
- Outgoing frontend messages are recorded in `workspace/logs/{backend}.history`.
- Unanswered incoming Telegram messages are stored in `workspace/telegram.recent`. `workspace/telegram.messages.recent` is legacy compatibility input and is no longer written.
- Unanswered incoming Signal messages are stored in `workspace/signal.messages.recent`.
- Unanswered incoming WhatsApp messages are stored in `workspace/whatsapp.messages.recent`.
- Unanswered incoming Google Fi messages are stored in `workspace/google_fi.messages.recent`. Google Fi call events are stored once in `workspace/google_fi.calls.recent`.
- Unanswered incoming Android events are stored in `workspace/android.messages.recent`.

Global runtime:

- `runtime.max_reply_chunk_chars` → split long responses into smaller message chunks.

## Frontend commands

- `/status` returns bot uptime and collector status without calling the LLM.
- `/now` returns one consolidated table with `platform`, `sender`, and `text`, using the 10 most recent non-empty entries from each frontend `.recent` file without calling the LLM.
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
- Android sender allowlist: `android.allowed_sender_ids`

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
