# Developer guide

## Architecture in one screen

- `assistant_framework/` provides core primitives:
  - `Workspace` for file-backed state
  - `SkillManager` for loading runnable skills
  - `CollectorManager` + `CollectorRunner` for data ingestion loops
- `messaging_llm_bot/` provides frontend clients and bot orchestration.
- `skills/` and `collectors/` are dynamic plugin directories.

## Plugin layout

Skills:

- code: `skills/<skill_name>/__init__.py`
- docs: `skills/<skill_name>/README.md`

Collectors:

- code: `collectors/<collector_name>/__init__.py`
- docs: `collectors/<collector_name>/README.md`
- metadata: declare `DESCRIPTION`, `FILE_STRUCTURE`, and `GENERATED_FILES` so the bot can describe loaded collector outputs in its system prompt

## Config layout

- Bot/runtime config can be a single JSON file or a directory tree.
- Directory config is loaded dynamically by JSON path:
  - `config/telegram.json` -> `telegram`
  - `config/llm.json` -> `llm`
  - `config/runtime.json` -> `runtime`
  - `config/collectors/gmail.json` -> `collectors.gmail`
- `index.json` may be used to assign config to a directory key directly.

If you delete a plugin folder, it is not loaded.

## Add a new skill

1. Create `skills/<name>/__init__.py`.
2. Expose either:
   - `register()` returning metadata and callable, or
   - module constants + `run(workspace, args)`.
3. Add `skills/<name>/README.md` with behavior, dependencies, config, and examples.
4. Run tests.

For agent-facing skills, keep the README explicit:
- Add a `## What it does` section. The bot surfaces this section in `/skill <name>` help.
- Keep `ARGS_SCHEMA` accurate, or provide a matching `## Args schema` block in the README.
- Document common scope forms directly when a skill accepts one item or many, such as `path` vs `paths`.

## Add a new collector

1. Create `collectors/<name>/__init__.py`.
2. Expose either:
   - `register_collector(config)` returning `name`, `description`, `interval_seconds`, `generated_files`, `file_structure`, and `run`, or
   - `create_collector(config)` returning the same fields for backward compatibility, or
   - module constants + `run(workspace)`.
3. Keep interactive setup out of the runtime loop; use a separate signup/setup command when needed.
4. Add `collectors/<name>/README.md`.
5. If the collector generates workspace files, describe them in `GENERATED_FILES`. Only enabled collectors that load successfully are exposed to the agent prompt.

## Style goals for contributions

- Prefer small pure-Python modules over framework-heavy abstractions.
- Keep names explicit and predictable.
- Avoid duplicated logic between skills/collectors/core runtime.
- Write code that reads like a clear script first, clever trick second.
