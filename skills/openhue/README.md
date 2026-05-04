# OpenHue skill

Control OpenHue lights from the assistant by light ID **or** light name.

## What it does
- List known lights from OpenHue.
- Turn lights on/off/toggle using `lights` values that can be IDs or names.
- Resolve names to IDs automatically using the list command output.

## Usage
```bash
# List lights
python3 -m assistant_framework.cli skill openhue --args '{"action":"list"}'

# Turn on lights by name + id
python3 -m assistant_framework.cli skill openhue --args '{"action":"on","lights":["Kitchen","3"]}'

# Turn off with optional brightness/transition
python3 -m assistant_framework.cli skill openhue --args '{"action":"off","lights":["Desk Lamp"],"brightness":40,"transition_ms":1000}'
```

## Optional command overrides
The skill defaults to:
- `list_command`: `openhue lights list --format json`
- `set_command_template`: `openhue lights {action} --id {id}`

Command overrides are executed directly as argument lists, not through a shell. String commands are parsed with `shlex`; pass a JSON array when an argument must be kept exactly as written.

You can override with args per call:
```json
{
  "action": "on",
  "lights": ["Kitchen"],
  "list_command": "my-openhue-wrapper list --json",
  "set_command_template": ["my-openhue-wrapper", "set", "--action", "{action}", "--id", "{id}"]
}
```

Or via environment variables:
- `OPENHUE_LIST_COMMAND`
- `OPENHUE_SET_COMMAND_TEMPLATE`

## Args schema
```ts
{
  action: "list" | "on" | "off" | "toggle";
  lights?: string[];
  brightness?: number; // 1-254
  transition_ms?: number; // >= 0
  list_command?: string | string[];
  set_command_template?: string | string[];
}
```
