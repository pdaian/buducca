# Google Calendar collector

Fetches events for each configured account and writes one file per account/month:
- `workspace/google_calendar/<account>.<YYYY-MM>.events.jsonl`

The default command uses the built-in script:
- `python3 collectors/google_calendar/google_calendar_api.py --account ...`

Dependency setup:
1. Install the single runtime dependency: `pip install gcsa`
2. Create OAuth credentials for Google Calendar API.
3. Put the OAuth client file at `~/.config/buducca/google_calendar_credentials.json`, or pass `--credentials-path`.
4. The token cache defaults to `~/.config/buducca/google_calendar_token.pickle`.

## Multi-account setup
1. Add `collectors.google_calendar.accounts` (string list or objects).
2. Optionally set per-account `command_template`.
3. Run collectors.

## File structure
- `collectors/google_calendar/__init__.py`
- `collectors/google_calendar/google_calendar_api.py`
- `collectors/google_calendar/README.md`
