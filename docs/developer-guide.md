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
- Stable role, tool, and policy instructions stay in the system message. Minute-specific request time is appended to the final user message so compatible providers can reuse a longer prompt-cache prefix.
- Default prompt inclusion is intentionally narrow: only general learnings from `workspace/learnings` are auto-included.
- Other stored workspace memory such as birthdays, contacts, notes, tasks, routines, structured facts, and collector outputs should be described for discovery, but not expanded by default.

### How skill docs reach the agent prompt

Skill READMEs are not injected wholesale.

The exact flow for `skills/<name>/README.md` is:

1. `assistant_framework/skills.py` loads each skill module and stores `readme_path=file_path.parent / "README.md"` on the `Skill` object.
2. `SkillManager._resolve_args_schema()` reads `## Args schema` from that README only when the module does not define `ARGS_SCHEMA`.
3. `messaging_llm_bot/bot.py::_build_agent_context_sections()` builds the `[Tools]` section of the system prompt from loaded skill metadata.
4. For each skill, the prompt includes:
   - the registered `description`
   - the resolved `args_schema` text, if present
5. The rest of the skill README is not appended to the system prompt.

Concretely, for a skill like `skills/file/README.md`:

- `## Args schema` can affect the system prompt, but only as a fallback source for `skill.args_schema`.
- `## What it does` does not go into the model prompt.
- other sections such as examples, notes, or warnings do not go into the model prompt.

`## What it does` is currently used for human-facing `/skill <name>` help, via `messaging_llm_bot/bot.py::_build_skill_command_help()` and `_read_skill_doc_section()`.

If you want to verify what the model actually received, inspect the assembled prompt directly:

```bash
python3 -m assistant_framework.cli trace last-prompt --workspace workspace
```

If you want to inspect the runtime skill boundary without running the full bot:

```bash
python3 -m assistant_framework.cli skills list --skills skills
python3 -m assistant_framework.cli skills inspect file --skills skills
```

The `inspect` command shows the exact split between:

- prompt-visible metadata: `description` and `args_schema`
- human-facing help: `## What it does` from the skill README
- implementation location: source and README paths

## Skills and local models

This matters more with local or weaker OpenAI-compatible models than with frontier hosted models.

Why:

- The runtime does not expose Python callables, full README prose, or hidden implementation details to the model. It exposes a compressed tool catalog.
- That catalog is mostly the skill `description` plus `args_schema`, injected into the `[Tools]` section of the system prompt.
- A local model therefore selects tools from a sparse symbolic interface, not from rich latent knowledge of your repo.

Practical consequences:

- Skill descriptions need to be concrete. Vague descriptions increase false negatives, where the model fails to pick a skill that exists.
- Args schemas need to be narrow and legible. Local models are more likely to hallucinate fields when the schema is missing or loose.
- README depth still matters, but mainly for operators using `/skill <name>` and for developers reading the repo. It is not a substitute for prompt-facing metadata.

Scientifically, this setup behaves like a lossy interface layer:

- implementation space: Python module, arbitrary logic, filesystem effects
- documentation space: full README, examples, caveats
- inference space: short prompt-visible summary consumed by the model

For local models with shorter effective context or weaker tool-use priors, information lost at that boundary is rarely recovered. The reliable path is to keep the prompt surface explicit and minimal, then verify it with `trace last-prompt` or `skills inspect`.

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
- `llm.runners[].extra_body` is merged into the outgoing Chat Completions JSON for provider-specific fields such as reasoning or sampling controls.
- `extra_body` may override optional defaults such as `temperature`, but it cannot override the protected `model` or `messages` fields.
- Set top-level `llm.temperature` or `llm.max_tokens` to JSON `null` to omit those fields entirely for newer model profiles.

If you delete a plugin folder, it is not loaded.

## Model-client compatibility

The client deliberately stays on the small, widely implemented Chat Completions wire shape. This keeps Ollama, LM Studio, vLLM, SGLang, and hosted compatible APIs on one code path.

Modern request options do not justify a provider SDK dependency by themselves. Put those fields in the runner:

```json
{
  "model": "gpt-5.6-luna",
  "extra_body": {
    "reasoning_effort": "low",
    "max_completion_tokens": 4096
  }
}
```

The reply footer reads common usage shapes and adds cache-hit, cache-write, and reasoning-token counts when a provider returns them. Missing metrics remain absent rather than being guessed.

At `runtime.log_level: "INFO"` (the default), model requests also emit detailed console logs:

- Each HTTP attempt has a request ID, runner and API model, endpoint, continuation and skill-chain position, message and prompt-character counts, sampling/thinking settings, timeout, and concurrent-request count.
- Streaming logs the first generated text and its latency, then reports progress every five seconds, including while waiting for the server or a stalled stream. Progress includes phase, elapsed and idle time, chunk and character counts, average characters/second, and reported token usage. Tokens/second uses reported completion tokens divided by elapsed request time; chunks and characters are never treated as token counts.
- `latest_reasoning` and `latest_output` show the last 400 characters of model-provided reasoning and answer text, with newlines and terminal controls escaped. Reasoning can come from `reasoning_content`, `reasoning`, or inline `<think>` blocks. Only reasoning exposed by the endpoint is available. These previews stay in console logs; reasoning is still filtered from delivered replies and is not added to request metrics.
- Completion and failure logs include final timing, available prompt/completion/total/cache/reasoning-token counts, and a finish reason or error type. Partial usage survives stream failures. Request metrics also retain first-token latency when measurable, chunk counts, and content/reasoning character counts.

The built-in HTTP client requests streaming and `stream_options.include_usage: true` by default. This uses the compatible SSE protocol and usage/reasoning fields described in [LM Studio's API changelog](https://lmstudio.ai/docs/developer/api-changelog). A server that responds with ordinary JSON remains supported. For endpoints that reject streaming, set the runner's `extra_body.stream` to `false`; waiting heartbeats remain active, while previews and usage arrive with the complete response. To disable just the usage request, set `extra_body.stream_options.include_usage` to `false`. Missing usage and first-token timing are marked unavailable, and a stream that ends without completion is treated as a failed attempt. Full request/response payload logging still requires debug mode.

LM Studio/llama.cpp can return `Context size has been exceeded.` as a streaming error or HTTP 500 when it cannot find free KV-cache space. The client classifies this as `ContextLengthExceededError` for streaming and ordinary JSON responses, preserving the existing bounded skill-result recovery and context-limit reply without exposing the error body. Other HTTP 500 errors remain generic. In [llama.cpp's server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-context.cpp), this failure can follow repeated `failed to find free space in the KV cache` warnings and successively smaller batches. Those retries can explain a slowdown before failure; they do not establish CPU offload or GPU-memory exhaustion. A failure with `first_token=unavailable`, no content/reasoning, and `chunks=1` may have received only the error event. `idle=0` then reflects that event's arrival, not prompt-processing progress.

For this failure, compare the server's loaded context and concurrent predictions with `runtime.max_concurrent_requests`, `llm.history_messages`, and the requested output limit. Start with one concurrent request when diagnosing cache pressure, including other clients that share the server. Leave context room for both the prompt and generation; `max_tokens` does not increase the loaded context. If increasing context, check the memory requirement first with [LM Studio's `lms load --estimate-only`](https://lmstudio.ai/docs/cli/local-models/load). The bot's `model_tag` is only a display label; use `api_model` in the request log to identify the requested model.

The current client does not implement the Responses API or native function-tool objects. Those are optional future transports in [agent-modernization.md](./agent-modernization.md); the existing JSON skill protocol remains the local/provider-neutral contract.

An empty answer is a failed model request, even when HTTP succeeds. In particular, a reasoning model can spend its entire generation budget before producing visible output: [OpenAI's completion limit includes reasoning tokens](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). The default and example output limits are 4,096 tokens; models or tasks that need more reasoning may require a higher limit and enough loaded context space. A runner's `max_completion_tokens` takes precedence over the legacy `max_tokens` field.

If an answer is empty with `finish_reason: "length"` or `"max_tokens"` and a positive output limit was sent, the client retries that prompt once per runner with twice the limit (at least 4,096 tokens). This retry preserves reasoning settings, consumes the existing request budget, and does not rerun completed skills. It does not modify saved config. Persistent empty answers and other empty completions use runner failover, then report a specific generation-limit, reasoning-only, filtered-response, or empty-response error. Failed attempts retain usage and finish reasons in traces and do not update runner success status or chat history. Check the INFO-level request logs for the limit, `finish_reason`, `reasoning_chars`, and reported reasoning tokens. The old "I couldn't produce a usable reply" fallback alone is not evidence of VRAM exhaustion; server logs are needed to establish a memory failure.

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
6. When documenting generated files or prompt-visible workspace data, make it explicit whether content is auto-included or only listed for discovery. The default is listed-only unless the data is written to `workspace/learnings`.

## Style goals for contributions

- Prefer small pure-Python modules over framework-heavy abstractions.
- Keep names explicit and predictable.
- Avoid duplicated logic between skills/collectors/core runtime.
- Write code that reads like a clear script first, clever trick second.

## Execution limits and result recovery

The runtime has three optional controls in addition to `max_skill_chain_steps`:

- `max_llm_requests_per_turn` counts actual endpoint attempts before dispatch, including continuations, failed requests, and runner failover. The counter spans every model call in a chat turn, scheduled reminder, or hourly routine. A rejected attempt does not appear as a completed request in usage metrics.
- `max_repeated_skill_results` stops consecutive calls only when their skill name, canonical JSON arguments, and complete result all match. A different call or changed result resets the count. This is deliberately narrower than cycle detection, and operators can disable it for intentional polling.
- `max_skill_result_chars` bounds each result before the next model call. The preview retains the beginning and end, including a notice that explains how to retrieve omitted text. The limit includes that notice; it does not bound the entire prompt or provider token usage.

The result limit defaults to 4,000 characters, including for existing configurations that omit it. The request and repetition limits default to `None`. All three can be disabled with JSON `null`. The example configuration enables limits of 24 requests, 3 repeated results, and 4,000 characters. The minimum supported values are 1, 2, and 512. Existing chain, request timeout, and concurrency settings still apply independently. Completed actions are not rolled back when any limit stops a turn.

If an endpoint explicitly rejects a skill follow-up for exceeding its context window, the bot can retry twice with smaller previews of completed skill results. The first recovery cap is at most 2,000 characters, then halves down to a minimum of 512; the reduced cap applies to subsequent results in that chain. System instructions, the original request, history, and recorded skill calls are preserved. Completed skills are not executed again, full results remain readable, and every retry consumes the existing request budget. Disabling the result limit also disables this recovery. Other HTTP 400 errors are not retried this way. Recognized context errors use a fixed diagnostic without exposing response bodies; an unrecoverable chat request reports the context limit instead of an internal error. Increase the model's loaded context length or reduce history when instructions and conversation alone exceed the window.

With result limits enabled, `BotRunner` registers the reserved runtime skill `read_skill_result`. For example:

```json
{"skill_call": {"name": "read_skill_result", "args": {"step": 1, "offset": 1000, "limit": 2000}, "done": false}}
```

`step` is the one-based index of a completed skill call in this turn. `offset` is a zero-based character offset; `limit` is the requested number of characters. Returned ranges use an exclusive end offset and include the next offset. Pages are capped to leave room for metadata. Reads count toward the same skill-chain and model-request limits.

The reader accesses only this thread's current trace steps. It accepts no file path and cannot load prior traces, other conversations, or results from a previous turn. Results expire from memory when the request context exits. Full results remain available to the operator in the existing saved trace. A skill receives its own copy of arguments, preserving the original call for traces and repetition detection.

When the result cap is disabled, the existing web-search behavior is retained: full HTML is shown once, then summarized in the prompt buffer. With the cap enabled, the bounded original result is retained so recovery offsets always refer to the original result.

Chat traces include per-step `result_chars`, `context_chars`, and `result_truncated`. `metrics.stop_reason` identifies a request budget, repeated result, or chain-limit stop. Request-budget failures preserve completed steps and provider usage already received. Scheduled routines keep their existing log files and do not gain chat trace files.

The chain limit counts executed skills, including returned errors and policy denials. A normal final answer after the last allowed skill is delivered. If the model asks for another skill, the runner stops before dispatching it. Tool JSON returned in a mandatory follow-up is processed through the same bounded loop instead of being sent as a final answer.

Skills must return text. A malformed result becomes an explicit skill error that the model can handle through the same loop; empty text remains a valid result. Existing reasoning-tag filtering and whitespace normalization still apply before a result enters the trace, so recovery offsets refer to that stored result.

## Concurrency and request state

`BotRunner._request_context()` acquires a re-entrant lock for the frontend/conversation pair, then a process-wide `BoundedSemaphore` controlled by `max_concurrent_requests`. Messages in one conversation remain ordered while unrelated conversations may progress concurrently. `/status` bypasses the request slots so operators can inspect a busy bot.

Evidence, trace steps, endpoint metrics, stop reasons, and reply footers are thread-local. The model client's request-budget scopes are also thread-local and are cleaned up even when a request fails. Nested budget scopes consume their parent budget. Runner selection and in-flight request tracking use a separate short-lived lock.

These controls apply within one process. They do not coordinate multiple bot processes, implement a provider rate limit, or make arbitrary plugin writes transactional. Plugins that modify shared workspace files must account for concurrent conversations when operators raise `max_concurrent_requests` above 1.

## Timed reminder messages

The [`add_reminder` skill](../skills/add_reminder/README.md) adds, lists, updates, and removes reminders with stable IDs and once/daily/weekly frequencies. Chat calls inherit the current conversation and configured prompt timezone. Daily and weekly schedules use calendar time in the selected timezone, including daylight-saving transitions.

`ReminderService` starts and stops with `BotRunner`. Its timer checks at startup and at minute boundaries, and dispatches each due message on a separate worker. Delivery bypasses `_request_context`, conversation locks, request slots, and model calls. A blocked chat, hourly routine, or unrelated outbound request therefore does not hold up reminder dispatch. Delivery still depends on the selected frontend being available and writable. Telegram user-mode calls share a dedicated event loop so polling and sending can overlap; see Python's [cross-thread coroutine scheduling](https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe).

`ReminderStore` reads `assistant/reminders.jsonl` as an append-only journal of full records and deletion markers. A process-wide lock protects read/modify/append operations; delivery holds no store lock during network I/O. Completion compares the dispatched record with the current record before removing it or advancing its schedule, preserving concurrent edits and removals. As with the rest of the bot, run one process per workspace. Legacy saved-prompt reminders use the same journal but retain their model-driven execution path.

Overdue reminders deliver once on restart, then recurring reminders advance past missed occurrences. Failed sends retry after at least one minute. Delivery is at least once: a crash between the external send and recording completion, or an ambiguous network failure, can duplicate a message. An edit or removal cannot recall a send already in progress. See the skill README for DST behavior, schedule examples, and policy names.

## Configuration and action boundaries

`python3 -B run_bot.py --config config --check-config` validates configuration and exits before creating a runner or supervisor. It reads the configuration and contact maps, reports section and field errors, and never checks external credentials or service reachability. `--help` also exits before starting a child process.

Configuration rejects unknown fields, invalid section types, booleans masquerading as numeric limits, nonfinite request values, malformed model URLs, and invalid streaming options. Use `api_key` for authentication; model base URLs cannot contain credentials, queries, or fragments. The documented underscore-prefixed comment fields remain supported.

An absent action policy preserves the existing default behavior. An existing empty, malformed, or unreadable policy denies actions until corrected. Invalid decision values also deny; valid policies retain their existing `allow`, `deny`, and `ask` semantics and action audit records.

HTTP errors retain the status and a sanitized endpoint URL. They omit URL user information, query strings, Telegram token path segments, response bodies, and chained transport errors that might disclose those values. Wrapped socket timeouts become `RequestTimeoutError` consistently for JSON and binary requests. JSON responses must be objects, and model responses must contain a choice with a message object.

## Validation without generated files

The in-memory regression suite covers reminders, execution limits, result recovery, policy handling, configuration validation, model transport, and trace accounting. It mocks network calls and persistence and can run without bytecode, pytest caches, or temporary capture files:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -B -m pytest \
  --capture=sys --assert=plain -p no:cacheprovider \
  tests/test_reminders.py tests/test_execution.py tests/test_action_runtime.py \
  tests/test_runtime_controls.py tests/test_config_validation.py \
  tests/test_llm_client.py tests/test_llm_streaming.py tests/test_http.py \
  tests/test_trace_metrics.py tests/test_run_bot.py
```

The broader existing suite includes temporary-directory and file-writing fixtures. Run it when filesystem writes are permitted; the command above is the subset suitable for a no-generated-files workflow.
