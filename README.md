<p align="center">
  <img src="assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA — Private, local-first personal assistant 🤖

> [!IMPORTANT]
> 🚧 **BUDUCCA is pre-release software** (early preview). Things may be rough around the edges, and your feedback will directly shape what we build next.
>
> 🤝 **Interested in using BUDUCCA?** We would love to talk with potential users, tinkerers, and anyone curious about the project.
>
> [![Join the BUDUCCA Telegram](https://img.shields.io/badge/Join%20our%20Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/buducca)
>
> <a href="https://t.me/buducca"><img src="https://telegram.org/img/t_logo.png" alt="Telegram logo" width="72"/></a>
>
> 👉 **How to join:**
> 1. Click the button (or logo) above.
> 2. Open the group in Telegram.
> 3. Say hi and tell us what you want from a private, local-first assistant.

Run your own Telegram and/or Signal assistant with a tiny, understandable Python stack.

## Why BUDUCCA

- 🔒 **Privacy-first:** your data stays on your machine.
- 🖥️ **Local-first execution:** collectors, skills, and optional voice transcription run locally.
- 📡 **No telemetry:** no tracking pipeline, no analytics SDK, no hidden reporting.
- 🧩 **Simple backend:** small modular scripts + JSON config, easy to inspect and change.
- 🐣 **Useful with tiny local models:** built for practical automation, not giant cloud-only setups.

## Quick start

```bash
cp config.example.json config.json
cp agent_config.example.json agent_config.json
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config agent_config.json
python3 run_bot.py --config config.json
```


## Frontends

- Configure `telegram` in `config.json` to run Telegram in bot mode (`telegram.mode: "bot"`) or full-account user mode (`telegram.mode: "user"` with Telethon session).
- Configure `signal` to run the bot on Signal (`signal-cli`).
- Signal frontend registration must be done directly in `signal-cli` (phone number or linked-device QR), then BUDUCCA can use that configured account.
- Configure both to accept messages on either backend and reply on the same backend that received the message.
- Set `runtime.max_reply_chunk_chars` to chunk long responses before sending.
- Signal allowlist override: set `signal.allowed_group_ids_when_sender_not_allowed` to raw Signal group IDs (the `groupInfo.groupId` value from `signal-cli` JSON output, not the `group:<title>|...` conversation label). Example: `"AQi7f+/4S3mQv6s5hN2xwQ=="`.

- Telegram sender/group allowlist override (similar to Signal): set `telegram.allowed_sender_ids` to allow specific senders, and set `telegram.allowed_group_ids_when_sender_not_allowed` to allow specific chat/group IDs even when sender is not allowlisted.

- `telegram.read_only` / `signal.read_only` can be set to `true` to run a frontend in collector-only mode (receives messages, sends no replies).
- `telegram.store_unanswered_messages` / `signal.store_unanswered_messages` control whether unanswered/non-agent messages are persisted to `workspace/telegram.recent` or `workspace/signal.messages.recent` (default: `false`).
- Replied interactions are logged to `workspace/logs/agenta_queries.history`.

## Plugin layout (skills + collectors)

To keep the main README focused, each skill and collector now has its own README in its own subfolder:

- Skills live in `skills/<skill_name>/`
  - code: `skills/<skill_name>/__init__.py`
  - docs: `skills/<skill_name>/README.md`
- Collectors live in `collectors/<collector_name>/`
  - code: `collectors/<collector_name>/__init__.py`
  - docs: `collectors/<collector_name>/README.md`

### Available skills

- `file` → `skills/file/README.md`
- `learn` → `skills/learn/README.md`
- `summarize_workspace` → `skills/summarize_workspace/README.md`
- `taskwarrior` → `skills/taskwarrior/README.md`
- `web_search` → `skills/web_search/README.md`
- `openhue` → `skills/openhue/README.md`

### Available collectors

- `gmail` → `collectors/gmail/README.md`
- `slack` → `collectors/slack/README.md`
- `twitter_recent` → `collectors/twitter_recent/README.md`
- `whatsapp_messages` → `collectors/whatsapp_messages/README.md`
- `google_calendar` → `collectors/google_calendar/README.md`

### Dynamic loading and optional removal

Skills and collectors are loaded dynamically from the filesystem at runtime. If you delete a skill or collector folder, it is no longer loaded (no extra toggles required).

## Core commands

```bash
# Run a skill manually
python3 -m assistant_framework.cli skill summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

For skill-specific and collector-specific command examples, see each module README listed above.

## Adding new skills and collectors

1. Create a new folder under `skills/` or `collectors/`.
2. Add implementation in `__init__.py` using the existing module patterns.
3. Add a `README.md` in the same folder describing:
   - what it does,
   - required dependencies,
   - configuration,
   - usage examples.
4. Test locally.
5. Open a pull request back to this repository.

Recommended workflow:
- Import this repository into Codex (or a similar coding assistant/tool).
- Describe the new functionality you want.
- Let the tool generate/edit code + docs in the module folder.
- Review, run tests, and submit a PR for inclusion in the main repo.

## Design patterns

BUDUCCA has two first-class plugin surfaces:

- **Collectors**: ingest external data into workspace files for the assistant to consume.
- **Frontends**: messaging plugins (Telegram, Signal, and future channels) that both receive and send messages.

Frontends can be configured to:

- respond normally (default),
- filter to agent-handled messages only,
- store unanswered/non-agent messages into collector-style workspace files,
- or do both filtering and storage depending on `read_only` and `store_unanswered_messages` settings.

### Collector implementation pattern: explicit setup/signup command

Collectors should avoid interactive setup during normal collection loops.

- Any first-time auth/signup flow should be exposed as a separate command.
- Collector runtime should be non-blocking and non-fatal when setup is incomplete.
- If setup has not happened yet, collectors should return no data and wait for setup to be completed.

### Frontend implementation pattern

- Frontends are bidirectional message adapters: they **receive updates** and **send replies** on the same channel.
- Frontends should support policy controls (allowlists, read-only mode, unanswered-message storage) without forcing one behavior.
- Frontends should emit stable workspace log formats when storage is enabled so collector tooling can reuse the same files.

## Voice notes with OpenAI Whisper CLI

When `runtime.enable_voice_notes` is `true`, `runtime.voice_transcribe_command` can call the Python `whisper` command directly. The bot replaces:

- `{input}` with the downloaded voice-note path
- `{input_dir}` with the temporary directory containing that file

Example command array for `config.json`:

```json
"voice_transcribe_command": [
  "whisper",
  "--model", "base.en",
  "--output_dir", "{input_dir}",
  "--output_format", "txt",
  "{input}"
]
```

## Additional collector/signup commands

Some integrations need one-time auth outside the main runtime loops:

```bash
# Signal frontend (config.json > signal) QR flow
python3 -m messaging_llm_bot.signal_signup --config config.json  # prints setup docs and exits

# WhatsApp Web QR flow
python3 -m collectors.whatsapp_messages.signup --config agent_config.json
```

## Data locations

- `workspace/telegram.recent` — recent Telegram message snapshots
- `workspace/collector_status.json` — collector health/status (for background collectors)
- `workspace/signal.messages.recent` — Signal messages
- `workspace/gmail.recent` — Gmail message snapshots via Google Agentic CLI
- `workspace/slack.recent` — Slack message snapshots
- `workspace/twitter.following.recent` — X/Twitter posts from following timeline
- `workspace/twitter.dms.recent` — X/Twitter direct messages
- `workspace/whatsapp.messages.recent` — WhatsApp message snapshots
- `workspace/google_calendar/<account>.<month>.events.jsonl` — Google Calendar events per account/month

That's it: one local workspace, one small backend, one assistant you control. 🔐
