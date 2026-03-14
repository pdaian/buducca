# Main Group skill

## What it does
Saves the preferred group target for hourly scheduled output in `assistant/main_group.json`.

It can save an explicit backend and conversation id, or resolve a saved group/contact alias from the workspace contact maps.

## Args schema
```ts
{
  group?: string;
  name?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi";
  conversation_id?: string | number;
  config_path?: string;
}
```
