# Frontends

BUDUCCA frontends are bidirectional adapters: they receive messages and send replies on the same channel. Google Fi additionally emits call events that are logged.

## Telegram

Set `telegram.mode` in `config.json`:

- `"bot"` for bot-token mode.
- `"user"` for full-account mode via Telethon session.

## Signal

Configure `signal` in `config.json` and point BUDUCCA to your `signal-cli` setup.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.signal_signup --config config.json
```

## WhatsApp

Configure `whatsapp` in `config.json` with receive/send JSON commands. By default, call `python3 -m messaging_llm_bot.whatsapp_client receive` and `python3 -m messaging_llm_bot.whatsapp_client send` so no extra PATH executables are required.

One-time signup/help command:

```bash
python3 -m messaging_llm_bot.whatsapp_signup --config config.json
```

WhatsApp linking is handled by the external bridge behind those commands, not by BUDUCCA itself. Start that bridge in QR/pairing mode, then scan the QR from your phone in WhatsApp under `Settings -> Linked Devices -> Link a Device`.

## Common behavior flags

Per frontend (`telegram`, `signal`, `whatsapp`):

- `read_only: true` → receive-only mode, no outgoing replies.
- `store_unanswered_messages: true` → persist non-agent/unanswered messages into workspace files.

Global runtime:

- `runtime.max_reply_chunk_chars` → split long responses into smaller message chunks.

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
