# AI stack overview

BUDUCCA is a messaging-first assistant stack. Its center is not a web app, queue, vector database, or hosted agent service; it is a small Python runtime that connects everyday messaging channels to an OpenAI-compatible model endpoint, a local workspace, action skills, and background collectors.

## What is in the stack

- **Messaging frontends:** Telegram, Signal, WhatsApp, and Android adapters receive messages and send replies through the same assistant core.
- **Model layer:** `messaging_llm_bot/llm_client.py` talks to OpenAI-compatible chat-completions endpoints. That can be a hosted provider, a local server, or multiple configured runners.
- **Assistant runtime:** `messaging_llm_bot/bot.py` builds prompts, manages per-conversation history, dispatches skills, writes traces, and coordinates replies.
- **Workspace state:** `assistant_framework/workspace.py` keeps assistant data as normal files under the configured workspace. This makes state easy to inspect, back up, diff, and repair.
- **Skills:** `skills/` contains explicit action modules such as file operations, attachment reading, workspace search, memory, and outbound message sending when enabled.
- **Collectors:** `collectors/` pulls external context such as Gmail, calendar, Slack, Reddit, news, and Twitter into workspace files.
- **Configuration:** `config.example/` uses plain JSON files for model runners, frontends, runtime behavior, collectors, and permissions.

## How the pieces fit

1. A frontend receives a message from an allowed chat, sender, group, or device.
2. The bot builds context from system config, recent conversation state, prompt-visible skill metadata, selected workspace evidence, and enabled collector manifests.
3. The model returns either a normal reply or a structured skill request.
4. The runtime validates and runs the skill through the local workspace boundary.
5. The final reply is sent back through the same frontend, with traces and audit data written to local files.

This design favors explicit boundaries: messaging is handled by frontends, reasoning by the model endpoint, actions by skills, long-lived data by workspace files, and external context by collectors.

## Compared with other AI stacks

| Stack type | Typical shape | BUDUCCA difference |
| --- | --- | --- |
| Hosted assistant platforms | Provider-hosted model, tools, memory, orchestration, and dashboard | More self-managed and inspectable. You own the files, config, and runtime process, but you also operate the stack yourself. |
| Web chat apps | Browser UI wrapped around one model API | BUDUCCA starts from Telegram, Signal, WhatsApp, and Android instead of requiring users to open a dedicated app. |
| LangChain-style agent frameworks | General-purpose chains, agents, retrievers, tool abstractions, and integrations | Smaller and more direct. BUDUCCA has fewer abstraction layers and a narrower plugin shape, which makes behavior easier to inspect but less broad out of the box. |
| RAG-first knowledge bots | Document ingestion, embedding search, vector store, and grounded answer generation | BUDUCCA can retrieve workspace evidence, but it is not built around a vector database. It prioritizes action, messaging, and file-backed memory over semantic search infrastructure. |
| Automation platforms | Trigger/action workflows with limited natural-language reasoning | BUDUCCA uses a model as the interaction layer and delegates concrete work to Python skills. It is more conversational, but less visual and less no-code. |
| Single-channel chatbots | One bot connected to one service | BUDUCCA keeps one assistant core behind several frontends, so skills, memory, and collectors can be shared across channels. |

## Where it is strongest

- Personal or small-team assistants that should live in existing chat channels.
- Local or open-model deployments that need OpenAI-compatible serving without a large SaaS dependency.
- Workflows where state must remain readable as files.
- Operators who want to add small Python skills or collectors instead of adopting a full agent framework.
- Bots that need both live messaging and periodic background context collection.

## Tradeoffs

- You operate the runtime, frontend credentials, model endpoint, and optional collectors.
- Plain-file state is simple and transparent, but it is not a substitute for a transactional database in high-scale multi-user deployments.
- The skill interface is intentionally compact; tool descriptions and argument schemas need to stay precise, especially for weaker local models.
- There is no required vector database, hosted dashboard, or visual workflow builder. Add those only if your deployment needs them.

## Short version

BUDUCCA is best understood as a local-first assistant control plane for chat channels. Compared with larger agent frameworks and hosted assistants, it trades broad managed infrastructure for a smaller, auditable Python stack: OpenAI-compatible model endpoint in, messaging replies and local workspace actions out.
