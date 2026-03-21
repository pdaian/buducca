# Attach file skill

## What it does
Sends a file from the workspace as an attachment through a configured messaging backend.

Supported backends:
- `telegram` (bot or user mode)
- `signal`
- `whatsapp`

Notes:
- `whatsapp` file sends require the configured `send_command` to include a `{attachment}` placeholder.
- `signal` file sends use local `signal-cli`.

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
