## Config Layout

Copy this directory to `config/` and only keep the files you need.

- `telegram.json`, `signal.json`, `whatsapp.json`, `android.json`: frontend-specific settings
- `llm.json`: model/provider settings, including optional per-runner `extra_body` request fields
- `runtime.json`: shared runtime behavior and paths
- `collectors/*.json`: one file per collector

Both the bot and the framework CLI can load either a single JSON file or a config directory tree.

`llm.temperature` and `llm.max_tokens` may be `null` when a backend uses newer request fields. Runner `extra_body` values are merged into the outgoing JSON request, except that `model` and `messages` cannot be overridden. See [`docs/agent-modernization.md`](../docs/agent-modernization.md) for current model profiles.
