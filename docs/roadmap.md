# Roadmap

This roadmap is intentionally skeptical. BUDUCCA should not try to become a general-purpose agent framework with opaque orchestration, hidden state, or dependency sprawl.

Research note: this document was prepared from repository inspection and stable public characteristics of well-known local agent and assistant projects. Upstream projects move quickly; re-check current implementations before copying any integration detail.

## Current position

BUDUCCA already has a strong base:

- Small Python codebase.
- Local-first workspace with readable files.
- Simple plugin model for skills, collectors, and compressors.
- Real messaging frontends instead of a demo-only chat UI.

BUDUCCA is still missing several things that stronger personal assistant systems usually need:

- A deterministic action layer with approvals and audit logs.
- Better first-class memory structures than "just write files".
- Evidence and provenance in replies.
- Scheduling, reminders, and routine execution.
- Normalized ingestion for documents, not only message streams.
- Better operator tooling for inspecting what happened and why.

## Benchmark set

The projects below are worth studying, but mostly as sources of specific ideas rather than full templates.

| Project | What it gets right | Why BUDUCCA should not copy it wholesale | What BUDUCCA still needs from it |
| --- | --- | --- | --- |
| OpenHands | Strong agent loop for software tasks; explicit action execution | Too broad and complex for a personal assistant core | Better action/result tracing and recoverable run state |
| Open Interpreter | Direct local tool execution with a simple mental model | Can drift into unsafe "do anything" behavior without enough structure | A minimal approval gate around local actions |
| AutoGPT | Long-horizon autonomy and task decomposition | Historically heavy, brittle, and abstraction-happy | Only the useful part: explicit task queues and resumable goals |
| CrewAI | Role-based decomposition | Multi-agent orchestration often adds ceremony without reliability | Possibly a tiny handoff pattern, but only for clear bounded cases |
| LangGraph | Durable state machines and explicit graph execution | Useful ideas, but importing the framework would fight the repo's simplicity | Small internal state-machine patterns for reminders and approvals |
| smolagents | Minimal tool-calling ergonomics | Still aimed more at generic agents than personal operations | Cleaner skill definitions and argument contracts |
| aider | Focused coding loop with good change discipline | Narrow coding scope, not a personal assistant | Provenance, patch review, and concise action summaries |
| Continue | IDE-native workflows and context gathering | Tied to editor-centric development rather than assistant operations | Better retrieval packaging for workspace context |
| Open WebUI | Broad local model integrations and user accessibility | UI/platform scope is much larger than needed here | Model/provider abstraction ideas, not the full surface area |
| PrivateGPT | Local document Q&A with ingestion pipelines | More RAG product than assistant runtime | Document ingestion, chunking, and source citation patterns |
| Home Assistant Assist | Practical routines, devices, intents, and household workflows | Home automation assumptions do not fit the whole product | Intent schemas, reminders, and routine execution |
| Rhasspy / OpenVoiceOS / Mycroft | Voice-first local assistant patterns | Voice stacks tend to bring substantial infra and hardware complexity | Optional offline voice loop, kept modular and off by default |
| Leon | Personal-assistant framing with pluggable skills | Larger product shell and more opinionated UX than needed | User-facing assistant domains and intent organization |
| Danswer / AnythingLLM | Retrieval, connectors, and source management | Often evolve toward server products with many moving parts | Conservative document/source indexing for local corpora |

## Strategic conclusions

What BUDUCCA already does unusually well:

- Real personal channels instead of synthetic benchmarks.
- Plain-file state that can be inspected without a database browser.
- A plugin model simple enough to read quickly.

What leading projects collectively show:

- Reliability comes from constrained execution, not from adding more agents.
- Durable memory needs structure, not just more tokens.
- Retrieval is only useful when sources are visible to the user and operator.
- Scheduling and routine execution matter more for a personal assistant than fancy planning loops.

What BUDUCCA should avoid:

- Agent graphs as a default programming model.
- Hidden vector databases as a mandatory dependency.
- Browser-heavy desktop shells when chat frontends already exist.
- Complex multi-agent hierarchies.
- Dependency-heavy observability stacks.

## Feature roadmap

### 1. Add a deterministic action and approval layer

Priority: `P0`

Goal: every state-changing skill action should be visible, reviewable, and optionally require approval before execution.

Plan:

1. Introduce a small action envelope shared by skills: `name`, `args`, `reason`, `writes`, `requires_approval`.
2. Add a runtime policy file in the workspace for allow/deny/ask decisions per action name.
3. Write pending approvals and execution results to JSONL under `workspace/audit/`.
4. Update the bot loop so the model can propose an action, receive a deterministic decision, and only then execute.
5. Keep the first version file-based and single-process. No queue service.

Success criteria:

- A user can inspect exactly what action was proposed and what changed.
- Read-only deployments can enforce "never mutate".
- Existing skills remain usable with a thin compatibility wrapper.

Non-goals:

- General sandboxing.
- Arbitrary shell execution as a first-class default.

### 2. Create a structured memory layer for people, tasks, routines, and facts

Priority: `P0`

Goal: replace ad hoc memory writes with a small set of stable, human-readable data shapes.

Plan:

1. Add `assistant/people/`, `assistant/tasks/`, `assistant/routines/`, and `assistant/facts/` conventions under the workspace.
2. Define one JSON schema per area and keep each object small enough to diff comfortably.
3. Add a new memory skill that performs validated reads/writes for those schemas.
4. Keep append-only history in JSONL next to mutable JSON state for auditability.
5. Migrate the current `learn` behavior to write through this layer instead of free-form lines only.

Success criteria:

- Operator can answer "why does the assistant think this?" from files alone.
- Tasks and routines become queryable without prompt hacks.
- Schema violations fail loudly.

Non-goals:

- A full database.
- Implicit embeddings for every memory entry.

### 3. Add reminders, deferred tasks, and recurring routines

Priority: `P0`

Goal: make BUDUCCA useful as an actual assistant, not only a reactive chatbot.

Plan:

1. Add a scheduler loop that scans structured task/routine files on an interval.
2. Support three primitives only: one-shot reminder, due task, recurring routine.
3. Emit due items back through existing messaging frontends with concise status text.
4. Persist run history and next-run calculations in workspace files.
5. Keep timezone handling explicit and reuse the existing config timezone model.

Success criteria:

- Reminders survive restarts.
- Recurring routines do not double-fire.
- The schedule can be inspected and corrected by editing files.

Non-goals:

- A cron DSL.
- Calendar replacement.

### 4. Normalize ingestion for documents and attachments

Priority: `P1`

Goal: make collectors useful beyond message snapshots by creating a consistent local corpus.

Plan:

1. Define a common collected-item format with `source`, `timestamp`, `title`, `text`, `attachments`, `metadata`.
2. Update collectors to emit normalized records rather than each plugin inventing its own shape.
3. Add an attachment ingestion path for PDFs, plain text, and simple office exports when locally available.
4. Store raw artifacts and normalized text separately so re-processing is possible.
5. Keep parsers optional and degrade gracefully when extra dependencies are absent.

Success criteria:

- New retrieval features can read one collector format.
- Operators can re-run parsing without recollecting.
- Optional dependencies remain optional.

Non-goals:

- Full enterprise connector breadth.
- OCR-first pipelines.

### 5. Add evidence-backed retrieval and reply citations

Priority: `P1`

Goal: replies that rely on workspace data should point to the source files that justify them.

Plan:

1. Start with lexical retrieval over normalized text and structured memory; do not require a vector store.
2. Return source paths and short snippets to the prompt builder.
3. Include source references in model-visible context and optionally in user-visible replies.
4. Add a simple ranking pipeline that prefers recent, high-signal, user-owned data.
5. Introduce embeddings only behind an optional plugin interface if lexical retrieval proves insufficient.

Success criteria:

- A reply about collected data can cite where it came from.
- Retrieval behavior can be debugged from logs.
- The default install remains dependency-light.

Non-goals:

- Mandatory vector database.
- Complex RAG orchestration frameworks.

### 6. Add operator observability and replay tooling

Priority: `P1`

Goal: debugging should require reading plain files and one CLI, not guessing through logs.

Plan:

1. Add CLI commands to inspect last message, last prompt, last action proposal, and last skill result.
2. Persist a compact per-turn trace with prompt inputs, chosen actions, and response metadata.
3. Add a replay command that can rerun a past turn against stubbed skill outputs for debugging.
4. Keep trace payloads redacted where secrets may appear.
5. Avoid external tracing systems unless a concrete need emerges.

Success criteria:

- A bug report can point to one trace file.
- Regressions can be reproduced from saved inputs.
- Operators do not need to enable debug logging globally to understand failures.

Non-goals:

- OpenTelemetry by default.
- Full session video/replay UI.

### 7. Add a tiny plugin contract test kit

Priority: `P1`

Goal: keep extension quality high without adding a framework.

Plan:

1. Provide shared tests/helpers for skill registration, collector contracts, and workspace safety.
2. Add schema and README checks for plugin docs where argument schemas are declared.
3. Expose one command that runs contract tests against all installed plugins.
4. Keep helpers in plain `unittest` to match the current test style.

Success criteria:

- New plugins fail fast when they break the public contract.
- Docs and code drift less often.
- No plugin SDK package is required.

Non-goals:

- A generated plugin scaffold system.
- Metaclass-heavy registries.

### 8. Add a narrow interoperability layer for external tool ecosystems

Priority: `P2`

Goal: integrate with the broader agent ecosystem without surrendering the codebase to it.

Plan:

1. Add one adapter surface for exposing selected skills through a stable tool API.
2. Evaluate a minimal MCP-compatible adapter only if it can remain optional and isolated.
3. Keep BUDUCCA-native skills as the source of truth; adapters translate, not redefine behavior.
4. Do not pull external orchestration frameworks into the core runtime.

Success criteria:

- A small number of external clients can call BUDUCCA tools.
- Core runtime remains readable without understanding the adapter.

Non-goals:

- Rebuilding BUDUCCA around another framework.
- Full marketplace compatibility.

### 9. Add optional offline voice workflow, but keep text first

Priority: `P3`

Goal: support practical local voice notes and short commands without turning the project into a voice platform.

Plan:

1. Reuse the existing transcription command hook as the core integration point.
2. Add a voice note processing skill/pipeline that stores transcript, source file, and confidence metadata.
3. Support push-to-process voice notes from messaging frontends before considering wake-word loops.
4. Keep TTS and wake word explicitly out of the default stack.

Success criteria:

- Voice notes become searchable and citeable in the workspace.
- Text-first deployments pay almost no complexity cost.

Non-goals:

- Smart speaker runtime.
- Always-on microphones by default.

## Implementation order

Recommended sequence:

1. Deterministic action layer.
2. Structured memory.
3. Reminders and routines.
4. Ingestion normalization.
5. Retrieval with citations.
6. Observability and replay.
7. Plugin contract tests.
8. Interoperability adapter.
9. Optional voice improvements.

## Release gating

Do not call BUDUCCA "production-ready personal assistant" until all of the following are true:

- State-changing actions have approvals and audit trails.
- Reminders and tasks survive restart and are covered by tests.
- Replies grounded in workspace data can surface sources.
- Operators can inspect and replay failures without modifying code.
