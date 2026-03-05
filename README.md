<p align="center">
  <img src="assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA — Private, local-first personal assistant 🤖

Run your own Telegram assistant with a tiny, understandable Python stack.

## Why BUDUCCA

- 🔒 **Privacy-first:** your data stays on your machine.
- 🖥️ **Local-first execution:** connectors, skills, and optional voice transcription run locally.
- 📡 **No telemetry:** no tracking pipeline, no analytics SDK, no hidden reporting.
- 🧩 **Simple backend:** small modular scripts + JSON config, easy to inspect and change.
- 🐣 **Useful with tiny local models:** built for practical automation, not giant cloud-only setups.

## Quick start

```bash
cp config.example.json config.json
cp agent_config.example.json agent_config.json
python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json
python3 run_bot.py --config config.json
```

## Plugin layout (skills + connectors)

To keep the main README focused, each skill and connector now has its own README in its own subfolder:

- Skills live in `skills/<skill_name>/`
  - code: `skills/<skill_name>/__init__.py`
  - docs: `skills/<skill_name>/README.md`
- Connectors live in `collectors/<connector_name>/`
  - code: `collectors/<connector_name>/__init__.py`
  - docs: `collectors/<connector_name>/README.md`

### Available skills

- `file` → `skills/file/README.md`
- `summarize_workspace` → `skills/summarize_workspace/README.md`
- `taskwarrior` → `skills/taskwarrior/README.md`
- `web_search` → `skills/web_search/README.md`

### Available connectors

- `telegram_recent` → `collectors/telegram_recent/README.md`

### Dynamic loading and optional removal

Skills and connectors are loaded dynamically from the filesystem at runtime. If you delete a skill or connector folder, it is no longer loaded (no extra toggles required).

## Core commands

```bash
# Run a skill manually
python3 run_skill.py summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

For skill-specific and connector-specific command examples, see each module README listed above.

## Adding new skills and connectors

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
2. Set `collectors.telegram_recent.user_client.enabled = true` in `agent_config.json`
3. Add your `api_id` and `api_hash`
4. Run one-time signup command: `python3 run_telegram_collector_signup.py --config agent_config.json`
5. Re-run collectors

> Backward compatibility: `collectors.telegram_recent_collector` is still accepted.

## Data locations

- `workspace/telegram.recent` — recent Telegram message snapshots
- `workspace/collector_status.json` — connector health/status
- `workspace/collectors/telegram_recent.offset` — connector checkpoint state

That's it: one local workspace, one small backend, one assistant you control. 🔐
