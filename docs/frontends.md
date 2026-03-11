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

## Signal

Configure `signal` in `config/signal.json` and point BUDUCCA to your `signal-cli` setup.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.signal_signup --config config
```

## WhatsApp

Configure `whatsapp` in `config/whatsapp.json` with receive/send JSON commands. By default, call `python3 -m messaging_llm_bot.whatsapp_client receive` and `python3 -m messaging_llm_bot.whatsapp_client send` so no extra PATH executables are required.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.whatsapp_signup --config config
```

WhatsApp linking is handled by the external bridge behind those commands, not by BUDUCCA itself. Start that bridge in QR/pairing mode, then scan the QR from your phone in WhatsApp under `Settings -> Linked Devices -> Link a Device`.

## Common behavior flags

Per frontend (`telegram`, `signal`, `whatsapp`):

- `read_only: true` → receive-only mode, no outgoing replies.
- `store_unanswered_messages: true` → persist non-agent/unanswered messages into workspace files.

Global runtime:

- `runtime.max_reply_chunk_chars` → split long responses into smaller message chunks.

## Frontend commands

- `/status` returns bot uptime and collector status without calling the LLM.
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
The receive command also stores its dedupe state under `data/google_fi_receive_state.json` by default.
If login takes longer, increase the wait window with `--signup-wait-seconds` (default: `300`).

Per-frontend flags also apply: `read_only` and `store_unanswered_messages`.
