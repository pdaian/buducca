# Attach file skill

## What it does

Sends an existing workspace file through Telegram (bot or user mode), Signal,
or WhatsApp. Use it for an authorized attachment send after identifying the
file and destination; [read_attachment](../read_attachment/README.md) reads
inbound files without sending them.

## Guidance

- `path` is relative to the configured workspace. The file must already exist.
- One backend accepts `recipient`. Multiple backends require a `recipients`
  map with one destination per backend; mapped values take precedence. Telegram
  requires an integer chat ID. Resolve aliases before calling this skill.
- `backend` accepts a supported name or an array of names, not `"all"`.
  `caption` adds text alongside the file; legacy `message` is also accepted.
- Check each backend's result. Multi-backend sends can partially succeed, and
  an exception or timeout may occur after delivery. Check the destination before
  retrying an uncertain send; do not resend successful targets.

## Setup

Uses the bot's configured messaging clients. Read-only backends refuse sends.
Signal uses local `signal-cli`; WhatsApp's `send_command` must include an
`{attachment}` placeholder. Backend support and configured bridge behavior
determine which file types and sizes can be sent.

Optional `config_path` resolves inside the workspace. Otherwise the skill looks
for `config/` or `config.json` beside the conventional `workspace/` directory,
or inside the workspace when it has another name. It creates its own clients
from that configuration rather than inheriting a destination from the current chat.

## Usage

```bash
python3 -m assistant_framework.cli skill attach_file --args '{"backend":"telegram","recipient":123456789,"path":"reports/latest.pdf","caption":"Latest report"}'
python3 -m assistant_framework.cli skill attach_file --args '{"backend":["signal","whatsapp"],"recipients":{"signal":"+15551234567","whatsapp":"+15557654321"},"path":"assistant/export.csv"}'
```

## Args schema

```ts
{
  backend: "telegram" | "signal" | "whatsapp" | string[];
  path: string;
  caption?: string;
  recipient?: string | number;
  recipients?: Partial<Record<"telegram" | "signal" | "whatsapp", string | number>>;
  config_path?: string;
}
```
