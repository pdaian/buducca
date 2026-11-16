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

## Signal repair

Use [`repair_signal.py`](./repair_signal.py) from the repository root to diagnose and repair the **bot's signal-cli account**. Stop the bot and other signal-cli processes first, and run as the bot's OS user with the same working directory and environment. Pass the bot config directory, full config JSON, or `signal.json` with `--config`.

```bash
# Check the configured executable, account store, and server authentication.
python3 -B scripts/repair_signal.py --config config

# Preview an upgrade and account refresh without executing commands.
python3 -B scripts/repair_signal.py --config config --upgrade --repair --dry-run

# Upgrade the CLI, refresh account attributes, and send a test to Note to Self.
python3 -B scripts/repair_signal.py --config config --upgrade --repair --test-send
```

For `User <number> is not registered`, first check that `signal.account`, the `-a` arguments, OS user, and account directory match the working signal-cli installation. The script detects mismatches between receive and send commands, including custom executable and `--config`/`--data-dir` paths. Custom commands must specify `-a`/`--account` explicitly. Wrappers, containers, and D-Bus commands need diagnosis in their own runtime; the script stops rather than selecting a different local account.

If the linked device has expired or been removed, reset its authorization through QR pairing. Remove its stale BUDUCCA entry in **Signal on your phone → Settings → Linked devices**, if present, then run:

```bash
python3 -B scripts/repair_signal.py --config config --relink --test-send
```

Scan the terminal QR with the phone for the configured account. Relinking refreshes account attributes, requests contact/group synchronization, checks server authentication, and optionally sends the test. If an older CLI prints only a link URI, follow the [signal-cli linking instructions](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli.1.adoc#link). Primary accounts that need SMS/CAPTCHA/PIN registration must follow the [upstream registration procedure](https://github.com/AsamK/signal-cli/wiki/Registration-with-signal-cli); `--repair` alone cannot recreate missing credentials.

If pairing reaches `Received link information ...` and then fails with `Link request error: StatusCode: 409` or `MissingCapabilitiesException`, the CLI may lack capabilities now required by Signal ([upstream issue](https://github.com/AsamK/signal-cli/issues/1556)). Upgrade the configured CLI before pairing again:

```bash
python3 -B scripts/repair_signal.py --config config --upgrade --relink --test-send
```

Scan the new QR from this run. Keep the existing Signal account data. BUDUCCA uses the host's configured `signal-cli`; there is no bundled CLI version to bump in this repository. [Upstream recommends keeping signal-cli current](https://github.com/AsamK/signal-cli#readme), since releases older than three months may stop working with Signal's servers.

`--upgrade` supports an existing signal-cli package owned by apt, Homebrew, dnf/yum, or Termux pkg. It checks ownership of the executable used by the bot, so a separate desktop app or a different CLI installation cannot be mistaken for an upgrade. Missing or manually installed/container binaries produce a nonzero result and point to the [official releases](https://github.com/AsamK/signal-cli/releases/latest) and their runtime requirements. Update that installation first, then rerun without `--upgrade`.

Package repositories can lag behind upstream. If `--upgrade` completes but pairing still returns 409, compare the printed version with the official releases and update that same executable through its installation method, including the required Java/native runtime, before retrying `--relink`.

The helper never wipes the Signal data directory or deletes BUDUCCA logs. It does not call `receive`, delete lock files, unregister accounts, or change your identity-trust policy. Failures, including rate limits, bad credentials, timeouts, and incomplete pairing, return a nonzero exit code with recovery guidance. A successful account check does not prove message delivery; use `--test-send` to check sending. After repair, **restart the bot** to clear its disabled Signal state and resume message reception and synchronization.

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
