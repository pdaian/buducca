# Frontends

BUDUCCA frontends are bidirectional adapters: they receive messages and send replies on the same channel. Google Fi additionally emits call events that are logged.

## Android

Android support is command-driven like Signal and WhatsApp. The production path for a remote Android device is:

- `Termux` for the local Python runtime.
- `Termux:API` for SMS sending via `termux-sms-send`.
- `termux-notification-list` polling for notification ingestion with no third-party automation app.
- a limited SSH account on the server so the phone can push event files and pull an SMS outbox file

The server-side bridge is still `python3 -m messaging_llm_bot.android_client`. On the Android device, use the self-contained `run_client.py` file from this repo. It supports:

- `generate-ssh-key`: create the SSH key used by the phone
- `collect-notifications`: poll `termux-notification-list` and append only newly seen notifications
- `sync`: push the local inbox to the server, pull the SMS outbox back down, and flush it with `termux-sms-send`
- `run`: do notification collection and sync in one loop on the phone

Recommended file layout on the server:

- inbox: `data/android-events.jsonl`
- receive cursor: `data/android-bridge-state.json`
- SMS outbox: `data/android-sms-outbox.jsonl`

Recommended file layout on the Android device:

- local event spool: `$HOME/buducca-sync/android-events.jsonl`
- local SMS outbox mirror: `$HOME/buducca-sync/android-sms-outbox.jsonl`
- local SMS outbox cursor: `$HOME/buducca-sync/android-sms-outbox-state.json`

Concrete setup sequence on the Android device:

1. Install `Termux` and `Termux:API`.
2. In Termux, install Python and the Termux API package:

```bash
pkg update
pkg install python termux-api openssh
```

3. Copy `run_client.py` onto the Android device. Then generate an SSH key on the Android device and copy the printed public key into the limited server account's `authorized_keys`:

```bash
python3 run_client.py generate-ssh-key \
  --private-key $HOME/.ssh/buducca_android_sync
```

The limited server account only needs read/write access to the Android sync directory. The bot process needs local access to that same directory on the server.

4. On the Android device, create the local sync directory and make the spool files world readable and writable if you want other local processes to append to them exactly as-is:

```bash
mkdir -p "$HOME/buducca-sync"
: > "$HOME/buducca-sync/android-events.jsonl"
: > "$HOME/buducca-sync/android-sms-outbox.jsonl"
chmod 666 "$HOME/buducca-sync/android-events.jsonl" "$HOME/buducca-sync/android-sms-outbox.jsonl"
```

5. On the server, create `config/android.json` and point it at the synced files:

```json
{
  "account": "android",
  "poll_interval_seconds": 1.0,
  "allowed_sender_ids": ["+15551234567"],
  "inbox_path": "data/android-events.jsonl",
  "state_file": "data/android-bridge-state.json",
  "sms_outbox_path": "data/android-sms-outbox.jsonl",
  "send_via_outbox": true,
  "read_only": false,
  "store_unanswered_messages": true
}
```

If you prefer a custom workflow, you can still override `receive_command` and `send_command` directly.

6. Start the self-contained Termux client:

```bash
python3 run_client.py run \
  --inbox "$HOME/buducca-sync/android-events.jsonl" \
  --notification-state-file "$HOME/buducca-sync/termux-notifications-state.json" \
  --outbox "$HOME/buducca-sync/android-sms-outbox.jsonl" \
  --outbox-state-file "$HOME/buducca-sync/android-sms-outbox-state.json" \
  --remote-host "$SERVER" \
  --remote-dir "$REMOTE_DIR" \
  --ssh-key "$HOME/.ssh/buducca_android_sync" \
  --include-package org.thoughtcrime.securesms \
  --include-package com.whatsapp \
  --interval-seconds 2
```

The client polls `termux-notification-list`, appends only newly seen active notifications, pushes the inbox file to the server, pulls the SMS outbox back down, and sends any newly queued SMS with `termux-sms-send`. Omit `--include-package` to ingest every visible notification. Lower intervals reduce the chance of missing short-lived notifications.

The collector appends lines shaped like:

```json
{"type":"notification","package_name":"org.thoughtcrime.securesms","app_name":"Signal","title":"Alice","body":"Are you free?","timestamp":"2026-03-18T09:01:00-04:00"}
```

If you also want SMS events in the Android bridge, append them to `$HOME/buducca-sync/android-events.jsonl` from your own device-side workflow with one JSON object per line:

```json
{"type":"sms","sender_id":"+15551234567","body":"Ping","timestamp":"2026-03-18T09:00:00-04:00"}
```

7. Grant SMS permission to `Termux:API` in Android system settings. Open Android Settings, find Apps, open `Termux:API`, open Permissions, and allow SMS. `termux-sms-send` will fail until this is granted.
8. Verify the receive side by running one collection pass on the phone, then reading through the bridge on the server:

```bash
python3 run_client.py collect-notifications once \
  --inbox "$HOME/buducca-sync/android-events.jsonl" \
  --state-file "$HOME/buducca-sync/termux-notifications-state.json"
python3 -m messaging_llm_bot.android_client receive --inbox data/android-events.jsonl --state-file data/android-bridge-state.json
```

The second command should print JSON with any newly collected notifications under `"messages"`.

9. Verify the send side on the server:

```bash
python3 -m messaging_llm_bot.android_client send \
  --recipient +15551234567 \
  --message "BUDUCCA Android bridge test" \
  --outbox data/android-sms-outbox.jsonl
```

Then run one sync pass on the phone or wait for the main loop to pull the outbox entry and transmit it:

```bash
python3 run_client.py sync once \
  --inbox "$HOME/buducca-sync/android-events.jsonl" \
  --outbox "$HOME/buducca-sync/android-sms-outbox.jsonl" \
  --outbox-state-file "$HOME/buducca-sync/android-sms-outbox-state.json" \
  --remote-host "$SERVER" \
  --remote-dir "$REMOTE_DIR" \
  --ssh-key "$HOME/.ssh/buducca_android_sync"
```

10. Start BUDUCCA on the server:

```bash
python3 run_bot.py --config config
```

Minimum requirements for appended events:

- One valid JSON object per line in `data/android-events.jsonl`.
- The bridge only reads new lines and does not fetch SMS or notifications from Android by itself.
- `python3 run_client.py collect-notifications ...` is the built-in notification ingester on the phone.
- `python3 -m messaging_llm_bot.android_client send --outbox ...` writes outbound SMS requests into a JSONL outbox file; `python3 run_client.py sync ...` is the device-side sender.
- SMS events must include enough data for the bridge to derive `conversation_id`, `sender_id`, and message text. The example `type`, `sender_id`, and `body` fields are sufficient.
- Notification events should use `type: "notification"` and usually include `package_name`, `title`, and `body`.

Operational notes:

- Set `android.allowed_sender_ids` to the SMS numbers that may trigger agent replies.
- Leave `store_unanswered_messages` enabled if you want all notification traffic persisted to `workspace/android.messages.recent`.
- Notifications from non-allowlisted senders are still collected into the recent file, but they are not allowed to trigger replies.
- SMS replies are queued on the server and sent by the Android device with `termux-sms-send`, so the device must grant the required SMS permission to `Termux:API`.
- The Termux client can only see notifications that still exist when `termux-notification-list` runs, so use a short poll interval for ephemeral notifications.

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
