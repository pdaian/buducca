<p align="center">
  <img src="assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA — Private, local-first personal assistant 🤖

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

- Configure `telegram` in `config.json` to run the bot on Telegram.
- Configure `signal` to run the bot on Signal (`signal-cli`).
- Configure both to accept messages on either backend and reply on the same backend that received the message.
- Set `runtime.max_reply_chunk_chars` to chunk long responses before sending.

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
- `summarize_workspace` → `skills/summarize_workspace/README.md`
- `taskwarrior` → `skills/taskwarrior/README.md`
- `web_search` → `skills/web_search/README.md`
- `openhue` → `skills/openhue/README.md`

### Available collectors

- `telegram_recent` → `collectors/telegram_recent/README.md`
- `signal_messages` → `collectors/signal_messages/README.md`
- `gmail` → `collectors/gmail/README.md`
- `slack` → `collectors/slack/README.md`
- `twitter_recent` → `collectors/twitter_recent/README.md`
- `whatsapp_messages` → `collectors/whatsapp_messages/README.md`
- `google_calendar` → `collectors/google_calendar/README.md`

### Dynamic loading and optional removal

Skills and collectors are loaded dynamically from the filesystem at runtime. If you delete a skill or collector folder, it is no longer loaded (no extra toggles required).

### Collector file structure in system prompt

Each collector declares `FILE_STRUCTURE` in its module. The bot includes this collector file structure list in the system prompt so the agent can discover relevant collector files without extra user instructions.

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

### Collector implementation pattern: explicit setup/signup command

Collectors should avoid interactive setup during normal collection loops.

- Any first-time auth/signup flow should be exposed as a separate command.
- Collector runtime should be non-blocking and non-fatal when setup is incomplete.
- If setup has not happened yet, collectors should return no data and wait for setup to be completed.

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

## Optional: Telegram user-account collection (QR login)

If you need messages a normal bot token cannot access:

1. `pip install telethon`
2. Add an entry under `collectors.telegram_recent.accounts` with `user_client.enabled = true`
3. Add `api_id` and `api_hash` in that account
4. Run one-time signup command: `python3 -m collectors.telegram_recent.signup --config agent_config.json`
5. Re-run collectors

> Backward compatibility: `collectors.telegram_recent_collector` is still accepted.


## Additional collector signup commands

Some collectors need one-time auth outside the main collector loop:

```bash
# Signal second-device QR flow
python3 -m collectors.signal_messages.signup --config agent_config.json

# WhatsApp Web QR flow
python3 -m collectors.whatsapp_messages.signup --config agent_config.json
```

## Data locations

- `workspace/telegram.recent` — recent Telegram message snapshots
- `workspace/collector_status.json` — collector health/status
- `workspace/collectors/telegram_recent.offset` — collector checkpoint state
- `workspace/signal.messages.recent` — Signal messages
- `workspace/gmail.recent` — Gmail message snapshots via Google Agentic CLI
- `workspace/slack.recent` — Slack message snapshots
- `workspace/twitter.following.recent` — X/Twitter posts from following timeline
- `workspace/twitter.dms.recent` — X/Twitter direct messages
- `workspace/whatsapp.messages.recent` — WhatsApp message snapshots
- `workspace/google_calendar/<account>.<month>.events.jsonl` — Google Calendar events per account/month

That's it: one local workspace, one small backend, one assistant you control. 🔐
