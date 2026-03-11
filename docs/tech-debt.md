# Tech Debt

This file tracks concrete, currently observed technical debt in the repository.
It is intentionally short and only covers issues that are visible in the codebase today.

## 1. Duplicate Telegram user-client implementations

- Files:
  - `assistant_framework/telegram_user_client.py`
  - `messaging_llm_bot/telegram_user_client.py`
- Why this is debt:
  - Both modules implement overlapping Telethon session management, event-loop handling, entity caching, sender resolution, and shutdown behavior.
  - The two implementations have already diverged in purpose and features, which increases the chance of fixing Telegram behavior in one place but not the other.
- Current impact:
  - Telegram bug fixes and API changes require touching multiple modules.
  - Tests must cover both versions to avoid silent regressions.
- Recommended cleanup:
  - Extract shared Telethon/session primitives into one reusable module and keep only frontend-specific behavior in thin wrappers.

## 2. Legacy plugin registration compatibility paths remain in the runtime

- Files:
  - `assistant_framework/collectors.py`
  - `assistant_framework/compressors.py`
  - `assistant_framework/skills.py`
- Why this is debt:
  - The runtime supports multiple plugin declaration styles at once, including `register_collector`, `create_collector`, `create_compressor`, direct `run(...)` exports, and legacy config-key fallbacks such as `*_collector` and `*_compressor`.
  - This keeps plugin loading flexible, but it also spreads compatibility branches through the core runtime.
- Current impact:
  - Loader code is harder to reason about and extend.
  - New plugin behavior must be validated against several registration paths instead of one canonical interface.
- Recommended cleanup:
  - Standardize on one registration contract per plugin type, migrate built-in plugins, then remove the legacy loader branches and config-key aliases in a follow-up release.
