# Agent prompt review

This note looks at how the runtime currently prompts the model and lists the highest-value TODOs for making the agent smarter without turning the prompt into an unmaintainable blob.

## Current prompt shape

The bot builds the system prompt in `messaging_llm_bot/bot.py` by concatenating:

- `llm.system_prompt` from config
- current date/time in the configured timezone
- available skills and optional arg schemas
- persistent learnings from `workspace/learnings`
- loaded collector outputs
- skill-calling rules and file-skill guidance

That structure is simple and readable, but it currently optimizes more for exposing tools than for teaching good reasoning behavior.

## TODOs

- Strengthen the base role prompt. The default `You are a helpful assistant.` in `messaging_llm_bot/config.py` is too weak to anchor behavior. Replace it with a default that explicitly prioritizes accuracy, evidence, asking clarifying questions when needed, and finishing tasks end-to-end.

- Separate policy from capabilities. `_build_agent_context_sections()` currently mixes factual context, tool inventory, and behavioral rules into one flat prompt. Split these into clearly labeled sections such as `Role`, `Truthfulness`, `Planning`, `Tools`, and `Workspace sources` so the model can follow them more reliably.

- Add explicit uncertainty rules. The prompt should tell the model what to do when it is missing information: say what is unknown, ask a targeted follow-up, or inspect workspace evidence before answering. Right now there is no direct instruction for ambiguity handling.

- Add a lightweight planning policy. Multi-step work is only implied through `done: false`. Add instructions to first decide whether the task needs `answer now`, `ask one question`, or `run one or more skills`, and to prefer short internal plans over impulsive tool use.

- Tighten tool-selection heuristics. The prompt says skills can be called if the user asks, but it does not teach when *not* to call them, when to prefer workspace inspection first, or when repeated tool loops are justified. Add decision rules for cheap read-first behavior and for stopping conditions.

- Add conflict-resolution rules for memory vs workspace vs user input. The prompt says to prefer workspace evidence over guesses, but it does not define precedence among live user instructions, durable learnings, collector outputs, and older memory. Encode an explicit ordering.

- Teach source discipline more precisely. The current prompt only says to cite source paths when explicitly referenced. Add rules for when claims require workspace evidence, when to quote versus summarize, and how to separate direct evidence from inference.

- Constrain learning writes. The prompt encourages saving durable preferences with the `learn` skill, but it does not say what *not* to save. Add rules against storing transient task state, one-off facts, secrets, or ambiguous preferences as durable learnings.

- Add failure-recovery instructions after tool calls. The model needs explicit guidance for malformed tool results, empty search hits, permission failures, and partial success. Today the prompt mostly describes the happy path.

- Add a small set of in-prompt examples. One or two compact examples of `answer directly`, `ask a clarifying question`, and `skill_call` would likely improve consistency more than more prose rules.

- Trim prompt noise from low-value listings. Full arg schemas and collector file lists are useful, but they can crowd out behavioral guidance. Consider shortening or summarizing these sections when they get large, or exposing full detail through a dedicated help path instead of every prompt.

- Add recency and time-sensitivity guidance. The prompt includes current time, but it does not instruct the model to treat schedules, deadlines, reminders, and time-relative requests differently from static facts.

- Add reply-shaping rules. The model should be told to adapt verbosity, lead with the answer, avoid repeating workspace filenames unless useful, and keep action confirmations distinct from analytical explanations.

- Add scheduled-run specific policy. Hourly automation reuses most of the same context, but scheduled runs need stronger guardrails around side effects, duplicate actions, and confirmation thresholds than normal chat turns.

- Back the prompt contract with focused tests. There are good prompt-presence tests today, but not enough behavioral contract tests. Add tests for uncertainty handling, clarifying-question behavior, learning write thresholds, and skill-selection precedence.

## Suggested implementation order

1. Improve the default base prompt and split prompt sections by responsibility.
2. Add uncertainty, planning, and source-discipline rules.
3. Add memory-write and tool-failure guardrails.
4. Reduce prompt bloat and add a few compact examples.
5. Expand tests to lock in the new behavior.
