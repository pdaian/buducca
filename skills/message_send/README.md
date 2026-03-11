# Message send skill

## What it does
Sends outbound messages through the messaging backends already configured for the bot runtime.

Supported backends:
- `telegram` (bot or user mode)
- `signal`
- `whatsapp`
- `google_fi` / `fi`

## Usage
```bash
python3 -m assistant_framework.cli skill message_send --args '{"backend":"telegram","recipient":123456789,"message":"hello"}'
python3 -m assistant_framework.cli skill message_send --args '{"backend":["signal","fi"],"recipients":{"signal":"+15551234567","fi":"+15557654321"},"message":"check in"}'
```

## Args schema
```ts
{
  backend: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi" | "all" | string[];
  message: string;
  recipient?: string | number;
  recipients?: Partial<Record<"telegram" | "signal" | "whatsapp" | "google_fi" | "fi", string | number>>;
  config_path?: string;
}
```
