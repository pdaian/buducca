# Attach file skill

## What it does
Sends a file from the workspace as an attachment through a configured messaging backend.

Supported backends:
- `telegram` (bot or user mode)
- `signal`
- `whatsapp`
- `google_fi` / `fi`

Notes:
- `whatsapp` and `google_fi` file sends require the configured `send_command` to include a `{attachment}` placeholder.
- `signal` file sends use local `signal-cli`.

## Usage
```bash
python3 -m assistant_framework.cli skill attach_file --args '{"backend":"telegram","recipient":123456789,"path":"reports/latest.pdf","caption":"Latest report"}'
python3 -m assistant_framework.cli skill attach_file --args '{"backend":["signal","fi"],"recipients":{"signal":"+15551234567","fi":"+15557654321"},"path":"assistant/export.csv"}'
```

## Args schema
```ts
{
  backend: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi" | string[];
  path: string;
  caption?: string;
  recipient?: string | number;
  recipients?: Partial<Record<"telegram" | "signal" | "whatsapp" | "google_fi" | "fi", string | number>>;
  config_path?: string;
}
```
