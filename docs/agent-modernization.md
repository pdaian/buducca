# Agent modernization gameplan

Last reviewed: **2026-07-24**

BUDUCCA should adopt agent features only when they make the existing assistant more capable per token, easier to operate, or safer to trust. The goal is not to become another orchestration framework. The goal is to keep one small Python control plane current as model behavior improves.

## North star

Every proposed feature must pass all four checks:

1. **Measured value:** it improves task completion, latency, token use, or operator clarity on representative BUDUCCA conversations.
2. **Small operational footprint:** no required database, queue, dashboard, container fleet, or second orchestration framework.
3. **Provider independence:** the local OpenAI-compatible path remains first-class. Provider-native enhancements must be optional.
4. **Inspectable behavior:** prompts, tool calls, approvals, state, and failures remain visible through plain files and local traces.

If a feature adds a new service or an extra model call, it must remove more work than it creates.

## Repository audit

The current architecture is already pointed in the right direction:

- `messaging_llm_bot/bot.py` owns one explicit agent loop with bounded skill-chain steps, per-conversation ordering, compact tool metadata, and local traces.
- `assistant_framework/workspace.py` keeps durable state in normal files instead of requiring a database.
- `assistant_framework/action_runtime.py` provides allow, deny, and ask decisions plus a JSONL action audit.
- `assistant_framework/retrieval.py` uses bounded, targeted workspace context instead of loading an embedding stack.
- `messaging_llm_bot/llm_client.py` provides runner failover, continuation handling, reasoning-text filtering, and simple performance reporting.
- `skills/` and `collectors/` remain plain Python plugins with human-readable documentation.

The main modernization gaps were model configuration, prompt-cache friendliness, resource telemetry, and a written adoption policy. This pass closes the immediate gaps without changing the core architecture:

- current request-specific time now lives in the final user message instead of the reusable system-prompt prefix;
- runners accept validated `extra_body` fields for modern provider/model options;
- legacy `temperature` and `max_tokens` fields can be omitted with JSON `null`;
- cache-read, cache-write, and reasoning-token usage is surfaced when a backend reports it;
- the example backend and model guidance no longer point at an old GPT-4-era default.

## What changed in the agent ecosystem

The useful trend is **context engineering**, not bigger graphs. Current model and framework guidance converges on a few ideas:

| Development | BUDUCCA fit | Decision |
| --- | --- | --- |
| Lean prompts and task-relevant tools | High. Newer models need less repeated scaffolding, and smaller local models benefit from a precise surface. | Keep descriptions short, remove repetition only against evals, and add tool filtering only when the catalog becomes materially large. |
| Stable prompt prefixes and cache accounting | High. It reduces repeated input work without another service. | **Adopt now.** Keep stable instructions first, request-specific context last, and report cache usage when available. |
| Configurable reasoning effort | High. Routine chat and difficult tool workflows should not consume the same reasoning budget. | **Adopt now through runner options.** Start low and raise effort only when evals show a gain. |
| Context trimming and compaction | High for long conversations, but compaction can lose important constraints. | Add a provider-neutral, file-backed checkpoint only after token/length thresholds are measured. Preserve recent turns, user constraints, approval state, and source references. |
| Deferred tool discovery | Medium. It saves schema tokens when an agent has many tools. | Do not add a hosted dependency. Prototype a deterministic local shortlist only after prompt telemetry shows the tool catalog is a material cost. Always retain core discovery tools. |
| Native structured tool calling | Medium. Models now follow schemas more reliably than text-wrapped JSON. | Add later as an optional transport capability. Keep the current JSON skill protocol as the universal local fallback. |
| Resumable human approval | Medium. BUDUCCA already blocks and audits sensitive actions, but the user must edit policy and retry. | Add a small signed/persisted approval record only if real chat workflows need one-tap resume. Do not add a workflow engine. |
| Persisted provider reasoning | Low to medium. It may improve hosted multi-turn quality but couples state to one API. | Keep behind a future Responses transport; never make it the only history path. |
| MCP everywhere | Low by default. It broadens integrations but expands dependencies, tool surfaces, and trust boundaries. | Keep collectors and Python skills primary. Consider a narrow optional adapter only for a concrete integration that cannot stay simple otherwise. |
| Multi-agent and programmatic orchestration | Low for the default runtime. Parallel model work raises token use, scheduling complexity, and failure modes. | Do not ship as a default. Reconsider only for independently measurable tasks where lower wall-clock time justifies extra model work. |
| Mandatory vector memory | Low. BUDUCCA already has inspectable files and bounded retrieval. | Do not add. Improve file naming, manifests, and targeted search first. |

This direction matches current official guidance: OpenAI recommends leaner prompts and relevant tool exposure, documents prompt caching and compaction, and now offers deferred tool search; Pydantic AI has explicit deferred approval tools; LangChain emphasizes context selection and bounded memory; and Letta continues to explore persistent memory. BUDUCCA should borrow the small ideas, not their surrounding platforms.

## Delivery plan

### Phase 0 — shipped in this pass

- Modernize the example hosted runner from `gpt-4o-mini` to `gpt-5.6-luna`.
- Add per-runner `extra_body` request fields with `model` and `messages` protected from overrides.
- Allow `temperature` and `max_tokens` to be `null`, so a profile can use newer fields such as `max_completion_tokens`.
- Move minute-specific time data out of the system prompt, preserving a much longer reusable prefix.
- Report cache-hit, cache-write, and reasoning tokens in the existing reply footer when the provider includes them.
- Document current hosted and local model roles, with an explicit review date.

Acceptance: existing behavior and tests remain green; no new runtime dependency or service is introduced.

### Phase 1 — measure before changing behavior

1. Add usage fields to saved traces: prompt, output, cached, cache-write, reasoning tokens, duration, continuation count, and skill-chain length.
2. Build a tiny checked-in eval set from sanitized task shapes, not private conversation text:
   - direct answer with no skill;
   - one exact file read;
   - search then file read;
   - collector-backed answer with citations;
   - denied write;
   - multi-step write with a final user-facing summary;
   - malformed/empty tool output recovery.
3. Compare only changes that can be explained:
   - current full tool catalog versus a shortlist;
   - current prompt versus one reduced section at a time;
   - low versus medium reasoning effort;
   - 8 versus 12 retained history messages.
4. Record task success first, then input tokens, total tokens, latency, and tool-call count. A cheaper failure is still a failure.

Exit gate: ship an optimization only when it preserves all safety/approval cases and improves either median input tokens by at least 15% or median latency by at least 10% without reducing task success.

### Phase 2 — bounded context lifecycle

Implement one small `ConversationCheckpoint` module using JSON:

- trigger by an approximate character/token threshold, never every turn;
- retain the last few complete user/assistant/tool groups;
- summarize older content into facts, open tasks, user constraints, decisions, and cited workspace paths;
- keep action approvals and denials outside the lossy summary;
- write checkpoints under the existing data root so they are inspectable and recoverable;
- compact during idle time when practical, not on the latency-critical reply path;
- permit `/clear` to remove both recent history and its checkpoint.

For a future OpenAI Responses adapter, server-side compaction may be an optional implementation. The file-backed checkpoint remains the provider-neutral source of truth.

### Phase 3 — optional modern transports

Add a transport protocol only when it can stay smaller than a provider SDK integration:

```text
generate(messages, tools, options) -> text | tool calls + usage
```

Keep two implementations:

- `chat_completions`: the current default for Ollama, LM Studio, vLLM, SGLang, DeepSeek, and other compatible endpoints;
- `responses`: optional hosted path for native tool calling, provider compaction, persisted reasoning, and deferred tool search.

The bot and skills must not import provider SDK types. Normalize responses at the client boundary and preserve the current text/JSON path as fallback.

### Phase 4 — only after demonstrated demand

- resumable approval tokens in chat;
- deterministic tool namespaces when the skill count is large enough to matter;
- direct image input after all enabled frontends can represent attachments consistently;
- multi-agent execution only for opt-in, independent research partitions with a hard call/token budget.

## Modern backend profiles

Model names change quickly. Treat this table as a dated starting point and confirm the provider’s current model page before production deployment.

| Workload | Suggested model | Why it fits | BUDUCCA note |
| --- | --- | --- | --- |
| Local, general assistant | `Qwen/Qwen3.5-9B` | Current small open model with efficient architecture, strong tool use, long context, and OpenAI-compatible vLLM/SGLang serving. | Best first local profile. Reduce the serving context if memory is constrained; BUDUCCA itself does not need a huge window. |
| Local, reasoning/tool-heavy | `gpt-oss-20b` | Open-weight agentic model with configurable reasoning that can run in about 16 GB with its published MXFP4 setup. | Use when the machine can carry it and Qwen 9B misses multi-step tasks. Preserve the Harmony chat format through the serving engine. |
| Hosted, efficient/high volume | `gpt-5.6-luna` | Current GPT-5.6 cost-sensitive tier with configurable reasoning. | Example default. Use low effort first and raise it only for measured quality gaps. |
| Hosted, balanced | `gpt-5.6-terra` | Current balance of intelligence and cost. | Prefer for skill reliability when Luna is not enough. |
| Hosted, hardest tasks | `gpt-5.6-sol` or alias `gpt-5.6` | Current flagship capability. | Route intentionally; do not spend flagship tokens on every chat. |
| Hosted OpenAI-compatible alternative | `deepseek-v4-flash` | Current DeepSeek fast model on the existing Chat Completions interface. | Use the explicit V4 slug. The old `deepseek-chat` and `deepseek-reasoner` aliases reached their documented discontinuation date on 2026-07-24. |
| Hosted OpenAI-compatible, higher quality | `deepseek-v4-pro` | Current DeepSeek higher-capability V4 profile with tool calls. | Benchmark its tool JSON against Terra/Sol on BUDUCCA tasks. |
| Watch list, not a default | Gemma 4 E4B/12B or Mistral Small 4 | Modern multimodal/reasoning models with native tool capabilities. | Gemma becomes more relevant after direct multimodal transport. Mistral Small 4's published minimum infrastructure is outside the normal personal BUDUCCA footprint. |

### Hosted efficient example

`extra_body` is merged into the JSON request body. It may override optional sampling/output fields, but it cannot replace `model` or `messages`.

```json
{
  "runners": [
    {
      "base_url": "https://api.openai.com/v1",
      "api_key": "replace-with-api-key",
      "model": "gpt-5.6-luna",
      "endpoint_path": "/chat/completions",
      "model_tag": "hosted-efficient",
      "extra_body": {
        "reasoning_effort": "low",
        "max_completion_tokens": 800
      }
    }
  ],
  "temperature": null,
  "max_tokens": null
}
```

### Local Qwen example

```json
{
  "runners": [
    {
      "base_url": "http://127.0.0.1:8000/v1",
      "api_key": "local-token",
      "model": "Qwen/Qwen3.5-9B",
      "endpoint_path": "/chat/completions",
      "model_tag": "local-efficient",
      "extra_body": {
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.5
      }
    }
  ],
  "temperature": 0.7,
  "max_tokens": 800
}
```

The `/nothink` prefix remains useful with compatible local servers: BUDUCCA sends `chat_template_kwargs.enable_thinking=false` for that request. Provider support varies, so verify the outgoing payload with debug logging before relying on it.

## Sources reviewed

Primary project documentation and release notes used for this review:

- OpenAI: [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model), [model catalog](https://developers.openai.com/api/docs/models), [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [compaction](https://developers.openai.com/api/docs/guides/compaction), [tool search and tool workflows](https://developers.openai.com/api/docs/guides/tools), and [Agents SDK release notes](https://openai.github.io/openai-agents-python/release/).
- Pydantic AI: [deferred tools and approvals](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) and [capabilities/usage limits](https://pydantic.dev/docs/ai/core-concepts/capabilities/).
- LangChain/LangGraph: [context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering) and [short-term memory management](https://docs.langchain.com/oss/python/langgraph/add-memory).
- Letta: [memory-first local agent repository](https://github.com/letta-ai/letta-code).
- Current model cards and provider notes: [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B), [gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b), [Gemma 4](https://ai.google.dev/gemma/docs/core/model_card_4), [Mistral Small 4](https://mistral.ai/news/mistral-small-4/), and [DeepSeek API changelog](https://api-docs.deepseek.com/updates/).

## Definition of “modern enough”

BUDUCCA is modern when it:

- runs current local and hosted models without special-case forks;
- spends tokens on the user’s task, not repeated framework prose;
- exposes enough usage data to tune cost and latency;
- keeps long conversations bounded and recoverable;
- validates every action through one obvious policy boundary;
- remains understandable by reading a handful of Python files.

It does not need a graph editor, swarm, vector database, hosted trace UI, or universal protocol layer to meet that bar.
