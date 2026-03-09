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

Configure `whatsapp` in `config.json` with your external receive/send JSON commands.

## Common behavior flags

Per frontend (`telegram`, `signal`, `whatsapp`):

- `read_only: true` → receive-only mode, no outgoing replies.
- `store_unanswered_messages: true` → persist non-agent/unanswered messages into workspace files.

Global runtime:

- `runtime.max_reply_chunk_chars` → split long responses into smaller message chunks.

## Allowlist overrides

- Signal sender allowlist: `signal.allowed_sender_ids`
- Signal group allowlist override: `signal.allowed_group_ids_when_sender_not_allowed`
- Telegram sender allowlist: `telegram.allowed_sender_ids`
- Telegram group allowlist override: `telegram.allowed_group_ids_when_sender_not_allowed`

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

Configure `google_fi` in `config.json` with external receive/send commands for https://messages.google.com/web/.

- `receive_command` must output JSON as either a list of message objects or an object with `messages` and optional `calls` arrays.
- `send_command` supports `{recipient}` and `{message}` placeholders.
- Call events from `calls` are persisted to `google_fi.calls.recent` in the workspace.

Per-frontend flags also apply: `read_only` and `store_unanswered_messages`.
