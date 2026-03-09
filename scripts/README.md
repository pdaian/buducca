# Messaging bridge scripts

These scripts are the concrete command targets referenced by `config.example.json` for Google Fi.

- `google_fi_receive.py`: reads pending events from `workspace/google_fi_inbox.json` and prints JSON to stdout for the bot.
- `google_fi_send.py`: appends outbound messages to `workspace/google_fi_outbox.jsonl` for an external sender bridge.

## Why this exists

BUDUCCA does not directly automate `https://messages.google.com/web/` by default. A separate process/tool should:

1. Pull data from Google Fi / Google Messages Web.
2. Write inbound payloads into `workspace/google_fi_inbox.json`.
3. Read queued outbound messages from `workspace/google_fi_outbox.jsonl` and deliver them.

## Inbox format

```json
{
  "messages": [
    {
      "conversation_id": "thread-123",
      "sender_id": "+15550001111",
      "text": "hello"
    }
  ],
  "calls": [
    {
      "conversation_id": "thread-123",
      "sender_id": "+15550001111",
      "status": "missed"
    }
  ]
}
```
