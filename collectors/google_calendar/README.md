# Google Calendar collector

Fetches events for each configured account and writes one file per account/month:
- `workspace/google_calendar/<account>.<YYYY-MM>.events.jsonl`

## Multi-account setup
1. Add `collectors.google_calendar.accounts` (string list or objects).
2. Optionally set per-account `command_template`.
3. Run collectors.

## File structure
- `collectors/google_calendar/__init__.py`
- `collectors/google_calendar/README.md`
