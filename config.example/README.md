## Config Layout

Copy this directory to `config/` and only keep the files you need.

- `telegram.json`, `signal.json`, `whatsapp.json`, `google_fi.json`: frontend-specific settings
- `llm.json`: model/provider settings
- `runtime.json`: shared runtime behavior and paths
- `collectors/*.json`: one file per collector
- `compressors/*.json`: one file per compressor

Both the bot and the framework CLI can load either a single JSON file or a config directory tree.
