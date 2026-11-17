# Main Group skill

## What it does

Replaces the preferred destination for hourly scheduled output in
`assistant/main_group.json`. It saves a target only: it does not send a message,
create a group, or enable an hourly schedule.

## Guidance

- Provide `backend` and `conversation_id` for an explicit destination, or
  `group` (legacy alias `name`) for a saved contact/group alias. With an explicit
  destination, `group`/`name` is only a display label.
- Alias lookup is case-insensitive and checks workspace contact maps before
  configured contacts. Supply `backend` to disambiguate a name shared across
  platforms. Resolve ambiguous targets rather than inventing a conversation ID.
- Telegram needs an integer chat ID. Successful storage confirms the selected
  target, not its reachability or the bot's permission to send there.
- Use [file](../file/README.md) to inspect the saved record,
  [message_send](../message_send/README.md) for an immediate send, and
  [add_reminder](../add_reminder/README.md) for a timed reminder.

Uses the Python standard library and saved workspace/config contacts. Optional
`config_path` is workspace-relative and used for fallback alias lookup; default
discovery follows the messaging skills' `config/` or `config.json` convention.

## Args schema

```ts
{
  group?: string;
  name?: string;
  backend?: "telegram" | "signal" | "whatsapp";
  conversation_id?: string | number;
  config_path?: string;
}
```

## Usage

```bash
python3 -m assistant_framework.cli skill main_group --args '{"group":"Family","backend":"telegram"}'
python3 -m assistant_framework.cli skill main_group --args '{"backend":"telegram","conversation_id":"-1001234567890","group":"Family"}'
```
