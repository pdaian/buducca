## Config Layout

Copy this directory to `config/` and only keep the files you need.

- `telegram.json`, `signal.json`, `whatsapp.json`, `android.json`: frontend-specific settings
- `llm.json`: model/provider settings, including optional per-runner `extra_body` request fields
- `runtime.json`: shared runtime behavior and paths
- `collectors/*.json`: one file per collector

Both the bot and the framework CLI can load either a single JSON file or a config directory tree.

`llm.temperature` and `llm.max_tokens` may be `null` when a backend uses newer request fields. Runner `extra_body` values are merged into the outgoing JSON request, except that `model` and `messages` cannot be overridden. See [`docs/agent-modernization.md`](../docs/agent-modernization.md) for current model profiles.

Validate the configuration before starting any frontend:

```bash
python3 -B run_bot.py --config config --check-config
```

This checks configuration structure, types, limits, and model URLs without contacting providers, starting frontend sessions, or writing runtime files. It does not verify credentials or external service availability. Unknown settings, nonfinite numbers, and streaming requests are rejected. Model base URLs must use HTTP(S), with credentials supplied through `api_key` instead of the URL.

The example runtime enables these limits:

| Setting | Example | Behavior |
| --- | --- | --- |
| `max_skill_chain_steps` | `12` | Maximum completed skill calls, with a final model response still permitted. |
| `max_llm_requests_per_turn` | `24` | Maximum actual endpoint attempts, including failures, continuations, and failover, across the entire turn. |
| `max_repeated_skill_results` | `3` | Stop after this many consecutive identical skill names, arguments, and results. Changing results or intervening calls reset the count. |
| `max_skill_result_chars` | `4000` | Maximum characters from each skill result sent to the model, including the truncation notice. Full results remain in the trace. |

Omitting `max_skill_result_chars` uses a 4,000-character limit, including in existing configurations. Set it to `null` to disable result limits and result-compaction recovery. On explicit context errors, the bot can still retry with older complete conversation turns omitted from the active prompt, preserving the current request and saved history. History recovery permits at most three retries per failed model call and consumes the same request budget; skill follow-ups compact results first when enabled. The request and repetition limits remain disabled when omitted or set to `null`. Minimum values are `1`, `2`, and `512`, respectively. Set the repetition limit to `null` for workflows that intentionally poll unchanged results. These are execution and character limits, not token or billing guarantees; existing actions are not rolled back when a limit stops a turn.

When result limits are enabled, the runtime reserves the skill name `read_skill_result` for reading omitted ranges from completed results in the current turn. It cannot read other conversations, previous turns, or arbitrary trace files. See [runtime controls](../docs/developer-guide.md#execution-limits-and-result-recovery) for the call shape and trace fields.

Action policy files retain the existing allow-by-default behavior when absent. An existing empty, invalid, or unreadable policy now denies actions until corrected. Valid policy decisions remain `allow`, `deny`, and `ask`.
