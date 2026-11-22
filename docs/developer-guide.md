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
- Default prompt inclusion is intentionally narrow: general learnings from `workspace/learnings` are auto-included. Structured facts explicitly marked `source: "learn"` also receive bounded previews in the request context.
- Other stored workspace memory and collector outputs are available for discovery and targeted retrieval, rather than expanded by default.

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

`## What it does` is used for human-facing `/skill <name>` help, via `messaging_llm_bot/bot.py::_build_skill_command_help()` and `assistant_framework.skills.read_skill_doc_section()`.

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

## Writing skill guidance

Hosted and local models receive the same catalog, not the implementation or full
README. Keep the model-visible contract concise and sufficient to select and call
the skill correctly:

- Lead `DESCRIPTION` with the capability and when it applies. Distinguish nearby
  skills when confusion would change the result, such as workspace memory versus
  Taskwarrior, or attachment discovery versus reading content.
- Keep conditional requirements, important defaults, result meaning, and
  non-obvious retry risks in the description. Put extended examples and setup
  details in the README; do not repeat the entire argument list in prose.
- Define `ARGS_SCHEMA` with explicit types and `?` for optional fields. The
  TypeScript-like text is prompt guidance, not executable JSON Schema validation;
  `run()` must still validate its inputs. Do not present example values as types.
- Preserve stable argument names, aliases, and action policy behavior when
  revising guidance. Describe observed limits rather than promising full reads,
  guaranteed delivery, deduplication, or rollback that the skill does not provide.
- Check the actual loaded manifest with `skills inspect`. Keep its schema and
  the README schema consistent, and verify examples against implementation and
  current upstream documentation when external commands are involved.

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
    "max_completion_tokens": 32768
  }
}
```

The reply footer reads common usage shapes and adds cache-hit, cache-write, and reasoning-token counts when a provider returns them. Missing metrics remain absent rather than being guessed.

At `runtime.log_level: "INFO"` (the default), model requests also emit detailed console logs:

- Each HTTP attempt has a request ID, runner and API model, endpoint, continuation and skill-chain position, message and prompt-character counts, sampling/thinking settings, timeout, and concurrent-request count.
- Streaming logs the first generated text and its latency, then reports progress every five seconds, including while waiting for the server or a stalled stream. Progress includes phase, elapsed and idle time, chunk and character counts, average characters/second, and reported token usage. Tokens/second uses reported completion tokens divided by elapsed request time; chunks and characters are never treated as token counts.
- `latest_reasoning` and `latest_output` show the last 400 characters of model-provided reasoning and answer text, with newlines and terminal controls escaped. Reasoning can come from `reasoning_content`, `reasoning`, or inline `<think>` blocks. Only reasoning exposed by the endpoint is available. These previews stay in console logs; reasoning is still filtered from delivered replies and is not added to request metrics.
- Completion and failure logs include final timing, available prompt/completion/total/cache/reasoning-token counts, and a finish reason or error type. Partial usage survives stream failures. Request metrics also retain first-token latency when measurable, message and prompt-character counts, chunk counts, and content/reasoning character counts. Context failures retain the server's available numeric counts under `context_error`: `prompt_tokens`, `context_window_tokens`, and `requested_tokens`. `context_error_kind` distinguishes `length` from the recognized server `capacity` failure; `cache_prompt_retry` identifies attempts made with cache reuse disabled by recovery. These diagnostic counts are separate from reported `usage`; absent counts remain unknown, and private error bodies are not retained.

The built-in HTTP client requests streaming and `stream_options.include_usage: true` by default. This uses the compatible SSE protocol and usage/reasoning fields described in [LM Studio's API changelog](https://lmstudio.ai/docs/developer/api-changelog). A server that responds with ordinary JSON remains supported. For endpoints that reject streaming, set the runner's `extra_body.stream` to `false`; waiting heartbeats remain active, while previews and usage arrive with the complete response. To disable just the usage request, set `extra_body.stream_options.include_usage` to `false`. Missing usage and first-token timing are marked unavailable, and a stream that ends without completion is treated as a failed attempt. Full request/response payload logging still requires debug mode.

If a stream reports an error, disconnects, or ends without completion, the client retries the current prompt once per runner using `stream: false`. This can recover a streaming failure during extended reasoning or reveal a specific error that the stream hid; [LM Studio users have reported context errors appearing only with streaming disabled](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1806). The retry preserves reasoning and output limits, discards incomplete output, and consumes the turn's request budget. Completed skills are not repeated, and reported usage from the failed attempt is retained. Continuations and output-limit recovery stay non-streaming for that runner attempt; later calls use the configured streaming setting again. A failed fallback follows normal runner failover. Explicit context errors use the existing context recovery instead, while malformed events and request timeouts do not trigger this fallback. The configured timeout remains in effect, including while waiting for a non-streaming response.

LM Studio/llama.cpp can return `Context size has been exceeded.` as a streaming error or HTTP 500 when it cannot find free KV-cache space. In [llama.cpp's server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-context.cpp), this comes from the decode/allocation path, separately from the prompt-length check. A cache-state failure can therefore occur on a second skill-chain request even with a small prompt. The client retains `ContextLengthExceededError` for compatibility but marks this signature as a capacity failure unless an explicit length-error code or reported prompt/context counts establish prompt overflow. Other HTTP 500 errors remain generic. The user-facing diagnostic retains available token counts without exposing the error body.

For this capacity signature, the client retries the current model request once per runner with `cache_prompt: false`, the [llama.cpp option that disables reuse of the previous request's KV cache](https://github.com/ggml-org/llama.cpp/tree/master/tools/server#post-completion-given-a-prompt-it-returns-the-predicted-completion). The retry preserves the prompt, skill results, reasoning settings, and output limit, and consumes the existing request budget. Completed skills are never replayed. An explicit `extra_body.cache_prompt: false` skips this retry. The override lasts through continuations and streaming fallback for that runner attempt; later model calls use the configured setting again. Persistent capacity failures use runner failover and then stop without trimming history or skill results. Check server logs and runtime/cache settings; reloading the model may clear stale state. On compatible servers, `extra_body.cache_prompt: false` can also disable reuse from the first request, at the cost of reprocessing the prompt. This option is not added for ordinary `context_length_exceeded` or `exceed_context_size_error` responses.

The server may log repeated `failed to find free space in the KV cache` warnings and successively smaller batches before a capacity failure. Those retries can explain a slowdown; they do not establish CPU offload or GPU-memory exhaustion. A failure with `first_token=unavailable`, no content/reasoning, and `chunks=1` may have received only the error event. `idle=0` then reflects that event's arrival, not prompt-processing progress.

For this failure, compare the server's loaded context and concurrent predictions with `runtime.max_concurrent_requests`, `llm.history_messages`, and the requested output limit. Start with one concurrent request when diagnosing cache pressure, including other clients that share the server. Leave context room for both the prompt and generation; `max_tokens` does not increase the loaded context. If increasing context, check the memory requirement first with [LM Studio's `lms load --estimate-only`](https://lmstudio.ai/docs/cli/local-models/load). An estimate below installed RAM does not establish that the GPU can allocate that memory; check the driver limits described [below](#lm-studio-load-failures-on-linux-strix-halo). The bot's `model_tag` is only a display label; use `api_model` in the request log to identify the requested model.

The visible conversation is only part of the prompt: BUDUCCA also sends system instructions, skill metadata, structured memory, retrieved evidence, and skill results. Results are uncapped by default. `prompt_chars` measures text characters, not tokens, and the reply footer sums usage across attempts and continuations rather than measuring remaining context. Compare individual request metrics with the server's loaded per-request context, which can differ from the advertised model maximum.

When a context error reports both the prompt token count and context capacity, and the prompt fits but the requested output allowance does not, the client retries the same prompt once per runner with `context_window_tokens - prompt_tokens` as the output limit. For example, 6,000 prompt tokens plus a 4,000-token output allowance exceeds an 8,192-token window; the retry requests 2,192 output tokens. This also recovers an automatic empty-answer retry that increased the output allowance too far. The adjustment applies only to that runner attempt, preserves reasoning settings and full skill results, consumes the turn's request budget, and does not replay completed skills. Subsequent empty-answer retries cannot raise the limit above that reported capacity. For explicit length errors, missing counts, a prompt that already fills the window, or a repeated failure follow existing history/result recovery and runner failover; the client never estimates tokens from character counts or invents an output limit when none was configured.

The current client does not implement the Responses API or native function-tool objects. Those are optional future transports in [agent-modernization.md](./agent-modernization.md); the existing JSON skill protocol remains the local/provider-neutral contract.

An empty answer is a failed model request, even when HTTP succeeds. In particular, a reasoning model can spend its entire generation budget before producing visible output: [OpenAI's completion limit includes reasoning tokens](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). The generic default remains 4,096 tokens for local compatibility; the hosted example reserves 32,768 tokens for reasoning and the answer, following [OpenAI's guidance to leave at least 25,000 tokens when evaluating reasoning models](https://developers.openai.com/api/docs/guides/reasoning#allocating-space-for-reasoning). Models or tasks may require a different limit, supported by the backend, and enough loaded context space for both prompt and generation. A runner's `max_completion_tokens` takes precedence over the legacy `max_tokens` field.

If an answer is empty with `finish_reason: "length"` or `"max_tokens"` and a positive output limit was sent, the client retries that prompt at most twice per runner. The first retry doubles the limit with a 4,096-token minimum; the second doubles it again with a 32,768-token minimum. For example, a 4,096-token request can retry with 8,192 and then 32,768 tokens. The retry allowance is shared with continuations of the same reply. These retries preserve reasoning settings, consume the existing request budget, and do not rerun completed skills. They do not modify saved config or invent a limit when none was supplied. Explicit context errors still use the bounded prompt-compaction recovery. Persistent empty answers and other empty completions use runner failover, then report a specific generation-limit, reasoning-only, filtered-response, or empty-response error. Failed attempts retain usage, finish reasons, and the sent `max_tokens` or `max_completion_tokens` in traces and do not update runner success status or chat history. Check those metrics and the INFO-level request logs for the limit, `finish_reason`, `reasoning_chars`, and reported reasoning tokens. The old "I couldn't produce a usable reply" fallback alone is not evidence of VRAM exhaustion; server logs are needed to establish a memory failure.

### LM Studio load failures on Linux Strix Halo

An `lms load` error ending in `exited before becoming healthy`, `exitCode=null`, and `signal=SIGABRT` means the inference process aborted during startup. Read the preceding runtime messages in LM Studio's server logs (`~/.lmstudio/server-logs/`); the wrapper error alone does not identify the cause. This occurs before BUDUCCA can send a model request, so changing the bot's HTTP timeout or output-token limit will not repair it.

On a 128 GB Ryzen AI Max+ 395 running Linux 7.2.15 and LM Studio's Vulkan runtime 2.73.0, the November 22, 2026 failure showed:

```text
radv/amdgpu: Not enough memory for command submission.
ggml_vulkan: device lost on Vulkan0
terminate called after throwing an instance of 'vk::DeviceLostError'
what(): vk::Queue::submit: ErrorDeviceLost
```

The live driver exposed only 62.47 GiB of GTT memory, with about 62.4 GiB in use, while Linux still reported about 57 GiB available. The TTM limit was 16,376,809 pages of 4,096 bytes, exactly matching the GTT total. This establishes a GPU shared-memory ceiling despite remaining system RAM. [AMD's Strix Halo guidance](https://rocm.docs.amd.com/en/docs-7.2.0/how-to/system-optimization/strixhalo.html#memory-settings) explains that dynamic GTT allocation defaults to approximately half of system RAM. Dynamic allocation still has a limit; BIOS Auto does not make all installed RAM available to every GPU allocation.

Inspect the live limits without loading another model:

```bash
getconf PAGESIZE
cat /sys/module/ttm/parameters/pages_limit
cat /proc/cmdline
free -h
for device in /sys/class/drm/card[0-9]*/device; do
    [ -r "$device/mem_info_gtt_total" ] || continue
    printf '%s\n' "$device"
    for name in mem_info_gtt_total mem_info_gtt_used mem_info_vram_total; do
        printf '%s: ' "$name"
        cat "$device/$name"
    done
done
```

The `mem_info_*` values are bytes; `pages_limit` is a page count. CPU and GPU share physical RAM, so these figures are not additional RAM to add to `MemTotal`. Compare GPU allocations with the GTT budget as well as total memory demand with available system memory. Leave room for CPU allocations, the OS, and runtime buffers.

For a 128 GB system with 4 KiB pages, a conservative starting limit is 96 GiB: `96 * 1024^3 / 4096 = 25165824` pages. On Debian with GRUB, append `ttm.pages_limit=25165824` to the existing `GRUB_CMDLINE_LINUX_DEFAULT` value in `/etc/default/grub`, preserving other boot arguments. For example, an existing `"quiet"` value becomes:

```text
GRUB_CMDLINE_LINUX_DEFAULT="quiet ttm.pages_limit=25165824"
```

Applying this host configuration requires administrator access: run `sudo update-grub`, then reboot when workloads can be stopped. After reboot, rerun the inspection commands and confirm both `pages_limit=25165824` and a GTT total of `103079215104` bytes (96 GiB) on the AMD device. [Linux 7.2 derives the GTT size from the TTM limit at driver initialization](https://github.com/torvalds/linux/blob/v7.2/drivers/gpu/drm/amd/amdgpu/amdgpu_ttm.c); changing only the live TTM value may leave the existing GTT budget unchanged. AMD recommends retaining a small BIOS VRAM reservation, such as 512 MiB, and increasing this shared limit. This raises a ceiling rather than reserving 96 GiB immediately, and does not guarantee that every context size will fit. To revert, remove the added argument, regenerate GRUB configuration, and reboot.

Also check the effective context in the runtime log. In this incident, the failed run used `n_ctx_seq=400128`. A subsequent 300,000-token request allocated `n_ctx_seq=300032`, loaded successfully, then logged that it was capping the slot context to the training limit: `n_slots=4`, `n_ctx_slot=262144`, `kv_unified='true'`. The [Qwen3.8-Flash-Next model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next#best-practices) documents a native 262,144-token context and recommends supported RoPE scaling, such as YaRN, for longer contexts. Increasing `--context-length` alone can allocate more memory without increasing the usable per-request window. Verify runtime support and the resulting `n_ctx_slot` before relying on an extended context; the four slots share cache capacity rather than each receiving an independent full cache.

Unload the existing instance before comparing load configurations. Start at the native context, letting LM Studio choose GPU offload:

```bash
lms load qwen3.8-flash-next@iq3_xxs --context-length 262144
```

`--gpu max` requests all layers on the GPU. In the failing run, `common_fit_params` reported that the explicit layer count prevented automatic fitting. Omitting that flag allows automatic offload selection, but does not raise the kernel memory ceiling or enable context extension. The `mlock` warning also appeared in the successful load, so it does not by itself explain this abort.

## Add a new skill

1. Create `skills/<name>/__init__.py`.
2. Expose `register()` returning `name`, `description`, and the `run(workspace, args)` callable. Module constants alone do not register a skill. Return text from `run()`.
3. Define `ARGS_SCHEMA` (or return `args_schema` from `register()`). Add `build_action` for actions that need the runtime's policy/audit path, and `requires_llm_response` when results need interpretation before delivery.
4. Add `skills/<name>/README.md` with behavior, dependencies, configuration, and relevant examples.
5. Validate loading, argument handling, and meaningful behavior without contacting live services or changing user data.

For agent-facing skills, keep the README explicit:

- Add a `## What it does` section. The bot surfaces this section in `/skill <name>` help.
- Keep `ARGS_SCHEMA` accurate, or provide a matching `## Args schema` block in the README.
- Document common scope forms directly when a skill accepts one item or many, such as `path` vs `paths`.

CLI examples use `--workspace workspace` by default. Pass `--workspace .` when
an example reads repository files. Direct CLI execution calls `run()` without
the bot's action-policy checks, result caps, or model follow-up; document any
relevant difference instead of assuming chat behavior applies.

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
- `max_skill_result_chars`, when set to an integer, bounds each result before the next model call. The preview retains the beginning and end, including a notice that explains how to retrieve omitted text. The limit includes that notice; it does not bound the entire prompt or provider token usage.

All three controls default to `None` and can be disabled with JSON `null`. Skill results return in full directly to the model's context by default, without requiring `read_skill_result`. The example configuration enables limits of 24 requests and 3 repeated results, with no result character cap. Existing configurations with an explicit numeric result cap retain it; remove the setting or set it to `null` to use full results. The minimum supported numeric values are 1, 2, and 512. Existing chain, request timeout, and concurrency settings still apply independently. Completed actions are not rolled back when any limit stops a turn.

Skill chains repeat the main task only in the latest follow-up, keeping older results without accumulating copies of the instructions. With result limits enabled, if an endpoint explicitly rejects a skill follow-up for exceeding its context window, the bot first retries up to three times with smaller previews of completed skill results. The first recovery cap is at most 2,000 characters, then halves down to a minimum of 512; the reduced cap applies to subsequent results in that chain. This recovery also removes the repeated task reminder and stops when the prompt cannot be shortened. Disabling the result limit disables result compaction.

Initial requests can also recover from an explicit context error by omitting older complete conversation turns from the active prompt. Each retry removes the oldest half of the remaining turns, rounded up; the third retry omits all remaining history. The bot makes at most three history retries per failed model call and stops sooner when no complete turns remain. Skill follow-ups use the same fallback after result compaction is exhausted or disabled. This applies to chat, saved-prompt reminders, and hourly routines, without deleting saved conversation history or changing configuration. Later turns start with the normal configured history again.

Both recovery paths preserve system instructions, the entire current request, and recorded skill calls. Completed skills are not executed again, full results remain readable, and every retry consumes the existing request budget. Logs report prompt sizes and omitted turn counts. The recognized KV-cache capacity failure uses the separate cache retry described above; it does not trigger prompt compaction. Other HTTP errors are not retried this way. Recognized context errors use a fixed diagnostic without exposing response bodies; an unrecoverable chat request reports the context limit or server capacity failure instead of an internal error. If the required instructions, current request, and generation still exceed the loaded context after recovery, increase the model's loaded context length or narrow the request. Reducing concurrent requests may also help when the server cannot allocate enough context space.

An unrecoverable context error in an hourly routine records a failure trace and suppresses further attempts for that hourly slot in the current process. This avoids repeated failures and replaying completed actions on every scheduler poll. The next hourly slot runs normally; timeouts and other transient failures retain their existing retry behavior. The failed slot is not recorded as successfully completed. Restarting the process permits another attempt, so review the failure trace before restarting to retry a partially completed routine.

With result limits enabled, `BotRunner` registers the reserved runtime skill `read_skill_result`. For example:

```json
{"skill_call": {"name": "read_skill_result", "args": {"step": 1, "offset": 1000, "limit": 2000}, "done": false}}
```

`step` is the one-based index of a completed skill call in this turn. `offset` is a zero-based character offset; `limit` is the requested number of characters. Returned ranges use an exclusive end offset and include the next offset. Pages are capped to leave room for metadata. Reads count toward the same skill-chain and model-request limits.

The reader accesses only this thread's current trace steps. It accepts no file path and cannot load prior traces, other conversations, or results from a previous turn. Results expire from memory when the request context exits. Full results remain available to the operator in the existing saved trace. A skill receives its own copy of arguments, preserving the original call for traces and repetition detection.

When the result cap is disabled, the existing web-search behavior is retained: full HTML is shown once, then summarized in the prompt buffer. With the cap enabled, the bounded original result is retained so recovery offsets always refer to the original result.

Chat traces include per-step `result_chars`, `context_chars`, and `result_truncated`. `metrics.stop_reason` identifies a request budget, repeated result, context-window, or chain-limit stop. Request-budget failures preserve completed steps and provider usage already received. Scheduled routines keep their existing log files; hourly context failures additionally save completed steps and request metrics through the same trace writer as chat requests.

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
