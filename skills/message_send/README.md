# Message send skill

## What it does

Sends a message through configured Telegram (bot or user mode), Signal, or
WhatsApp clients. Use it when the user intends an outbound send; a normal
assistant reply already returns to the current conversation.

## Guidance

- Supply a non-empty `message` and a verified destination. The skill does not
  resolve contact aliases or infer the current conversation's recipient.
- One backend accepts `recipient`; multiple backends need a `recipients` map
  with one destination per backend. A mapped recipient takes precedence over
  `recipient`. Telegram requires an integer chat ID.
- `backend` accepts one supported name, an array, or `"all"`. `all` selects every
  configured backend, not every contact. Use it only when that fanout is intended.
- Read-only backends refuse sends. Multi-backend output includes a success count
  and per-backend results; report partial success accurately.
- Check delivery before retrying an uncertain failure. Sends are not
  transactional or deduplicated; repeating a successful target sends again.
- Use [attach_file](../attach_file/README.md) for files and
  [add_reminder](../add_reminder/README.md) for a prepared message sent later.

## Setup

Requires configured messaging clients and network access or the relevant local
bridge. Optional `config_path` resolves inside the workspace. Otherwise config
is discovered beside a conventional `workspace/` directory, or inside the
workspace when it has another name, preferring `config/` over `config.json`.
Legacy `text` and Telegram `chat_id` aliases remain accepted; prefer the
canonical fields below. The bot may append its reply footer automatically.

## Usage

```bash
python3 -m assistant_framework.cli skill message_send --args '{"backend":"telegram","recipient":123456789,"message":"hello"}'
python3 -m assistant_framework.cli skill message_send --args '{"backend":["signal","whatsapp"],"recipients":{"signal":"+15551234567","whatsapp":"+15557654321"},"message":"check in"}'
```

## Args schema

```ts
{
  backend: "telegram" | "signal" | "whatsapp" | "all" | string[];
  message: string;
  recipient?: string | number;
  recipients?: Partial<Record<"telegram" | "signal" | "whatsapp", string | number>>;
  config_path?: string;
}
```
