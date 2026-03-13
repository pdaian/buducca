# Developer guide

## Architecture in one screen

- `assistant_framework/` provides core primitives:
  - `Workspace` for file-backed state
  - `SkillManager` for loading runnable skills
  - `CollectorManager` + `CollectorRunner` for data ingestion loops
- `messaging_llm_bot/` provides frontend clients and bot orchestration.
- `skills/` and `collectors/` are dynamic plugin directories.

## Prompting

- Prompt assembly lives in `messaging_llm_bot/bot.py` via `_build_system_prompt()` and `_build_agent_context_sections()`.
- Default base prompt and prompt-related config live in `messaging_llm_bot/config.py`.
- Default prompt inclusion is intentionally narrow: only learn-generated fact records from `assistant/facts/*.json` with `source == "learn"` are auto-included.
- Other stored workspace memory such as birthdays, contacts, notes, tasks, routines, and collector outputs should be described for discovery, but not expanded by default.

## Plugin layout

Skills:

- code: `skills/<skill_name>/__init__.py`
- docs: `skills/<skill_name>/README.md`

Collectors:

- code: `collectors/<collector_name>/__init__.py`
- docs: `collectors/<collector_name>/README.md`
- metadata: declare `DESCRIPTION`, `FILE_STRUCTURE`, and `GENERATED_FILES` so the bot can describe loaded collector outputs in its system prompt
- keep those descriptions clear that generated files are workspace files for discovery and targeted reads, not prompt content that is expanded by default

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
6. When documenting generated files or prompt-visible workspace data, make it explicit whether content is auto-included or only listed for discovery. The default is listed-only unless the data is a learn-generated fact record.

## Style goals for contributions

- Prefer small pure-Python modules over framework-heavy abstractions.
- Keep names explicit and predictable.
- Avoid duplicated logic between skills/collectors/core runtime.
- Write code that reads like a clear script first, clever trick second.

## Locking around LLM calls

This codebase already serializes LLM requests in the bot:

- `messaging_llm_bot/bot.py` creates `self._processing_lock = threading.RLock()`
- `messaging_llm_bot/bot.py` wraps the model call in `_generate_reply_with_lock()`

The current pattern is:

```python
def _generate_reply_with_lock(self, prompt: list[dict[str, str]]) -> str:
    with self._processing_lock:
        return self.llm.generate_reply(prompt)
```

### Why lock an LLM call at all?

Locking is not about "making HTTP thread-safe" by itself. It is about protecting state around the call.

Typical reasons:

- the model client is not safe to use concurrently
- request/response ordering must stay deterministic
- shared conversation state can be corrupted if two replies are generated at once
- downstream tools assume only one active reasoning/action loop at a time

In this repository, the lock is a coarse-grained gate for reply generation. That is a reasonable default because the bot keeps shared history, traces, frontend state, and multi-step skill flows in the same process.

### What the lock does

If two threads reach the LLM at the same time:

1. thread A acquires the lock
2. thread B blocks
3. thread A finishes the request and releases the lock
4. thread B starts its request

That gives you serialization: only one protected call runs at a time.

### What a lock does not do

A lock does not:

- limit requests across multiple processes or multiple machines
- enforce provider rate limits
- prevent duplicate work if the same message is queued twice
- make unrelated shared state safe unless that state is also accessed under the same lock

For cross-process coordination, use an external primitive such as a database row lock, Redis lease, or queue worker model.

### The main design choice: what exactly are you protecting?

There are two common scopes.

#### 1. Lock only the LLM call

Use this when the shared risk is the model client itself or the provider interaction.

```python
class Bot:
    def __init__(self) -> None:
        self._llm_lock = threading.Lock()

    def generate_reply(self, prompt: list[dict[str, str]]) -> str:
        with self._llm_lock:
            return self.llm.generate_reply(prompt)
```

This is the smallest critical section. It reduces contention, but it does not protect prompt construction or history updates done outside the lock.

#### 2. Lock the full conversation transaction

Use this when prompt building, the model call, and history mutation must act like one unit.

```python
class ConversationWorker:
    def __init__(self) -> None:
        self._conversation_lock = threading.Lock()
        self._history: list[dict[str, str]] = []

    def handle_user_message(self, text: str) -> str:
        with self._conversation_lock:
            prompt = [*self._history, {"role": "user", "content": text}]
            reply = self.llm.generate_reply(prompt)
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": reply})
            return reply
```

This is safer for per-conversation ordering, but it holds the lock for longer. That reduces throughput.

### Why this repo uses `RLock`

`threading.RLock()` is a re-entrant lock: the same thread can acquire it multiple times.

That matters when protected code can call another helper that also needs the same lock.

Example:

```python
class Bot:
    def __init__(self) -> None:
        self._processing_lock = threading.RLock()

    def generate_once(self, prompt: list[dict[str, str]]) -> str:
        with self._processing_lock:
            return self._generate_internal(prompt)

    def _generate_internal(self, prompt: list[dict[str, str]]) -> str:
        with self._processing_lock:
            return self.llm.generate_reply(prompt)
```

With a plain `threading.Lock()`, that pattern deadlocks when the same thread re-enters the lock. With `RLock()`, it succeeds.

If re-entrancy is impossible by design, prefer `threading.Lock()` because it is simpler and makes accidental nested locking more visible.

### A common bug: locking too little

This version looks safe, but it is not safe for shared history:

```python
def handle_user_message(self, text: str) -> str:
    prompt = [*self._history, {"role": "user", "content": text}]
    with self._llm_lock:
        reply = self.llm.generate_reply(prompt)
    self._history.append({"role": "user", "content": text})
    self._history.append({"role": "assistant", "content": reply})
    return reply
```

Failure mode:

1. thread A builds a prompt from history version 10
2. thread B builds a prompt from history version 10
3. thread A gets reply A and appends it
4. thread B gets reply B and appends it

Now both replies were generated from stale history, and ordering is wrong even though the LLM call itself was locked.

If history consistency matters, build the prompt and commit the result under the same lock, or keep a separate lock per conversation.

### A common bug: locking too much

Avoid holding the lock across slow work that does not need protection.

Bad:

```python
def handle_user_message(self, text: str) -> str:
    with self._processing_lock:
        attachments = self.download_attachments()
        prompt = self.build_prompt(text, attachments)
        reply = self.llm.generate_reply(prompt)
        self.write_trace_to_disk(prompt, reply)
        return reply
```

That blocks every other caller while waiting on downloads and disk I/O.

Better:

```python
def handle_user_message(self, text: str) -> str:
    attachments = self.download_attachments()
    prompt = self.build_prompt(text, attachments)
    with self._processing_lock:
        reply = self.llm.generate_reply(prompt)
    self.write_trace_to_disk(prompt, reply)
    return reply
```

Use the smallest critical section that still protects the invariant you care about.

### Per-process lock vs per-conversation lock

A single global lock is simple and conservative:

- easiest to reason about
- avoids interleaving across all conversations
- lowers throughput because unrelated conversations block each other

A per-conversation lock is usually the better production shape when conversations are independent:

```python
from collections import defaultdict
import threading


class Bot:
    def __init__(self) -> None:
        self._conversation_locks = defaultdict(threading.Lock)

    def handle_message(self, conversation_id: str, text: str) -> str:
        lock = self._conversation_locks[conversation_id]
        with lock:
            prompt = self._build_prompt(conversation_id, text)
            reply = self.llm.generate_reply(prompt)
            self._commit_reply(conversation_id, text, reply)
            return reply
```

That preserves message ordering within one conversation while allowing different conversations to progress in parallel.

If you use this pattern, make sure the protected data is also scoped per conversation. A per-conversation lock does not protect global mutable state.

### Async code uses `asyncio.Lock`, not `threading.Lock`

In `asyncio`, use an async lock:

```python
import asyncio


class AsyncBot:
    def __init__(self) -> None:
        self._llm_lock = asyncio.Lock()

    async def generate_reply(self, prompt: list[dict[str, str]]) -> str:
        async with self._llm_lock:
            return await self.llm.generate_reply(prompt)
```

Do not use `threading.Lock()` to coordinate coroutines inside one event loop. It blocks the thread instead of yielding control cooperatively.

### Rate limiting is different from locking

These are related but distinct:

- locking controls concurrency around shared state
- rate limiting controls how often you call the provider

If your provider allows only 5 concurrent requests, use a semaphore:

```python
import threading


class Bot:
    def __init__(self) -> None:
        self._llm_slots = threading.Semaphore(5)

    def generate_reply(self, prompt: list[dict[str, str]]) -> str:
        with self._llm_slots:
            return self.llm.generate_reply(prompt)
```

If you need both state safety and bounded concurrency, use both, but protect each concern separately.

### Practical guidance for this repository

- Keep `_processing_lock` if you want one in-flight LLM reasoning path at a time in this process.
- Move to per-conversation locks if unrelated chats should not block each other.
- If prompt/history consistency matters more than raw throughput, lock the full conversation transaction, not only `llm.generate_reply()`.
- If only the HTTP client is unsafe, lock only the call site and leave history/state under their own locks.
- If the bot ever runs in multiple processes, do not assume the current in-memory lock is sufficient.
