# Scripts

Standalone helper scripts that are intended to be copied or run directly live here.

## Frontend updater

Use [`update_frontends.py`](./update_frontends.py) to update any installed messaging frontend packages the host can detect across supported package managers.

Preview the commands first:

```bash
python3 scripts/update_frontends.py --dry-run
```

Limit the run to specific frontends when needed:

```bash
python3 scripts/update_frontends.py signal telegram
```

## Android Termux client

Use [`run_client.py`](./run_client.py) on the Android device.

It now has sensible local defaults:

- inbox: `$HOME/buducca-sync/android-events.jsonl`
- notification state: `$HOME/buducca-sync/termux-notifications-state.json`
- SMS outbox: `$HOME/buducca-sync/android-sms-outbox.jsonl`
- SMS outbox state: `$HOME/buducca-sync/android-sms-outbox-state.json`
- SSH key: `$HOME/.ssh/buducca_android_sync`

Running it with no arguments prints the setup guide:

```bash
python3 scripts/run_client.py
```

For unattended runs, set the sync target once:

```bash
export BUDUCCA_REMOTE_HOST=user@example.com
export BUDUCCA_REMOTE_DIR=/srv/buducca/android
python3 scripts/run_client.py run --include-package org.thoughtcrime.securesms
```

The script creates the local sync directory/files it needs. Full Android bridge details are in [`docs/frontends.md`](../docs/frontends.md).
