from __future__ import annotations

import argparse
import getpass
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_framework.config_files import load_named_config_map
from scripts.update_frontends import MANAGERS, UpdateError, _with_privilege


RELEASES_URL = "https://github.com/AsamK/signal-cli/releases/latest"
SETUP_URL = "https://github.com/AsamK/signal-cli/wiki/Registration-with-signal-cli"
_OPTION_NAMES = {
    "-a": "--account", "-c": "--config", "-d": "--config", "--data-dir": "--config",
    "-o": "--output", "-v": "--verbose",
}
_VALUE_OPTIONS = {
    "--account", "--config", "--output", "--service-environment", "--trust-new-identities",
    "--tls-proxy", "--log-file",
}
_FLAG_OPTIONS = {"--verbose", "--scrub-log", "--disable-send-log"}


class RepairError(RuntimeError):
    """An unresolved Signal setup or repair failure."""


def _command_settings(command: list[str], verb: str, account: str) -> tuple[str, dict[str, str]]:
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise RepairError(f"signal.{verb}_command must be a non-empty list of strings")
    executable = command[0]
    if executable.startswith("~"):
        raise RepairError(f"Use an absolute executable path in signal.{verb}_command; command arguments are not shell-expanded")
    if Path(executable).name not in {"signal-cli", "signal-cli.bat"}:
        raise RepairError(
            f"signal.{verb}_command uses a wrapper ({command[0]}). Run repair inside that wrapper's "
            "host/container with a config pointing directly to its signal-cli and account data."
        )
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    options = {"--config": str((data_home / "signal-cli").resolve()), "--service-environment": "live"}
    has_account = False
    index = 1
    while index < len(command) and command[index] != verb:
        token, equals, value = command[index].partition("=")
        option = _OPTION_NAMES.get(token, token)
        if option in _VALUE_OPTIONS:
            if not equals:
                index += 1
                if index >= len(command):
                    raise RepairError(f"Missing value for {token} in signal.{verb}_command")
                value = command[index]
            if not value or "{" in value:
                raise RepairError(f"Invalid value for {token} in signal.{verb}_command")
            if option == "--account":
                has_account = True
                if value != account:
                    raise RepairError(f"signal.{verb}_command account {value!r} differs from signal.account {account!r}")
            elif option == "--config":
                if value.startswith("~"):
                    raise RepairError(f"Use an absolute --config path in signal.{verb}_command; command arguments are not shell-expanded")
                options[option] = str(Path(value).resolve())
            elif option not in {"--output", "--log-file"}:
                options[option] = value
        elif option in _FLAG_OPTIONS and not equals:
            # Logging flags do not select a different account or server.
            if option == "--disable-send-log":
                options[option] = ""
        else:
            raise RepairError(f"Unsupported global option {token!r} in signal.{verb}_command; check the CLI setup first")
        index += 1
    if index == len(command):
        raise RepairError(f"signal.{verb}_command does not contain the {verb!r} command")
    if not has_account:
        raise RepairError(f"Add -a {account} to signal.{verb}_command so the bot and repair select the same account")
    return shutil.which(executable) or executable, options


def load_signal_command(config_path: str) -> tuple[str, list[str]]:
    config = load_named_config_map(config_path, section_name="signal")
    account = config.get("account")
    if not isinstance(account, str) or not account.strip() or account != account.strip():
        raise RepairError("Set signal.account in the bot config before running repair")
    settings = []
    for verb in ("receive", "send"):
        command = config.get(f"{verb}_command", [])
        if command == []:
            command = ["signal-cli", "-a", account, verb]
        settings.append(_command_settings(command, verb, account))
    (executable, options), (send_executable, send_options) = settings
    if Path(executable).resolve() != Path(send_executable).resolve() or options != send_options:
        raise RepairError(
            "signal.receive_command and signal.send_command use different executables, data directories, "
            "or global options. Make them use the same Signal account store and server before repairing."
        )
    prefix = [executable]
    for option, value in sorted(options.items()):
        prefix.append(option)
        if value:
            prefix.append(value)
    return account, prefix


def _run(command: list[str] | tuple[str, ...], *, dry_run: bool, timeout: float,
         interactive: bool = False) -> subprocess.CompletedProcess[str] | None:
    print(shlex.join(command), flush=True)
    if dry_run:
        return None
    try:
        result = subprocess.run(command, capture_output=not interactive, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RepairError(
            f"{command[0]} timed out after {timeout:g}s. Stop competing bot/signal-cli processes and check connectivity. "
            "A timed-out send may already have succeeded; inspect Signal before repeating it."
        ) from exc
    except OSError as exc:
        raise RepairError(f"Unable to run {command[0]}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or f"exit code {result.returncode}; see output above"
        raise RepairError(f"Command failed: {detail}")
    return result


def upgrade_signal_cli(executable: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"Would upgrade the package owning {executable} (signal-cli only), then check its version.")
        return
    located = shutil.which(executable)
    if not located:
        raise RepairError(f"signal-cli is missing. Install it for the bot's OS user: {RELEASES_URL}")
    resolved = Path(located).resolve()
    for manager in MANAGERS:
        if manager.name not in {"apt-get", "pkg", "brew", "dnf", "yum"} or not shutil.which(manager.name):
            continue
        if manager.name == "apt-get" and shutil.which("pkg"):
            continue  # Termux packages must be managed with pkg, without sudo.
        if manager.name in {"apt-get", "pkg"}:
            probe = ["dpkg-query", "-L", "signal-cli"]
        elif manager.name == "brew":
            probe = ["brew", "--prefix", "signal-cli"]
        else:
            probe = ["rpm", "-ql", "signal-cli"]
        if not shutil.which(probe[0]):
            continue
        result = subprocess.run(probe, capture_output=True, text=True, check=False, timeout=30)
        if result.returncode != 0:
            continue
        paths = [Path(line.strip()).resolve() for line in result.stdout.splitlines() if line.startswith("/")]
        owns_executable = any(path in resolved.parents for path in paths) if manager.name == "brew" else resolved in paths
        if not owns_executable:
            continue
        commands = [manager.refresh_command, manager.upgrade_command(["signal-cli"])]
        for command in commands:
            if command is not None:
                _run(_with_privilege(command, requires_privilege=manager.requires_privilege),
                     dry_run=False, timeout=900, interactive=True)
        return
    raise RepairError(
        f"No supported package manager owns {located} (resolved: {resolved}). "
        f"Update this manual/container installation using {RELEASES_URL}, including its required Java/native runtime, "
        "then rerun without --upgrade. No unrelated desktop packages were upgraded."
    )


def _recovery_hint(error: str) -> str:
    detail = error.lower()
    if any(word in detail for word in ("not registered", "not present", "authorization", "unauthorized", "401")):
        return (
            "Check the OS user, XDG_DATA_HOME, signal.account and command --config paths. "
            "For a linked device, remove the stale BUDUCCA device in Signal on your phone, then run this script "
            f"with --relink and scan its QR in Settings > Linked devices. For a primary account, register/verify via {SETUP_URL}."
        )
    if "already exists" in detail:
        return "Remove only the stale BUDUCCA linked device on your phone, then retry --relink. Keep primary-account data intact."
    if any(word in detail for word in ("rate limit", "ratelimit", "429", "captcha")):
        return "Wait for Signal's retry interval; if requested, solve the CAPTCHA and use signal-cli submitRateLimitChallenge."
    if any(word in detail for word in ("untrusted", "identity key", "safety number")):
        return "Verify the contact's safety number in Signal before updating trust. Repair never automatically trusts changed keys."
    if any(word in detail for word in ("lock", "in use")):
        return "Stop other bot/signal-cli processes using this data directory, then retry. Do not delete active lock files."
    if any(word in detail for word in ("java", "libsignal", "unsupportedclassversion", "glibc")):
        return f"Upgrade signal-cli and install the runtime required by that release: {RELEASES_URL}"
    return "Check the error above, network/proxy access, data-directory permissions and signal-cli version. Repair is incomplete."


def repair_signal(args: argparse.Namespace) -> None:
    account, prefix = load_signal_command(args.config)
    print(f"Signal account: {account}; OS user: {getpass.getuser()}")
    print("Stop BUDUCCA and other signal-cli processes first. Run as the same OS user/environment as the bot.")
    if args.upgrade:
        upgrade_signal_cli(prefix[0], dry_run=args.dry_run)
    result = _run([prefix[0], "--version"], dry_run=args.dry_run, timeout=args.timeout)
    if result is not None:
        print(result.stdout.strip())
    if args.relink:
        print(f"Link the phone for {account}: Signal > Settings > Linked devices > Link a device.")
        # link rejects -a and JSON output; let the CLI display its QR directly.
        _run([*prefix, "link", "--name", "BUDUCCA"], dry_run=args.dry_run,
             timeout=max(180, args.timeout), interactive=True)
    result = _run([*prefix, "-o", "json", "listAccounts"], dry_run=args.dry_run, timeout=args.timeout)
    if result is not None:
        try:
            accounts = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RepairError("listAccounts did not return valid JSON; upgrade signal-cli and check its output settings") from exc
        if not isinstance(accounts, list):
            raise RepairError("Unexpected listAccounts output; upgrade signal-cli")
        identifiers = {str(item.get(key)) for item in accounts if isinstance(item, dict) for key in ("number", "aci") if item.get(key)}
        if account not in identifiers:
            raise RepairError(
                f"Configured account {account} is not present in this Signal data directory. "
                f"Available accounts: {', '.join(sorted(identifiers)) or 'none'}."
            )
    account_command = [*prefix, "-a", account]
    if args.repair or args.relink:
        _run([*account_command, "updateAccount"], dry_run=args.dry_run, timeout=args.timeout)
    if args.relink:
        _run([*account_command, "sendSyncRequest"], dry_run=args.dry_run, timeout=args.timeout)
    # This contacts Signal without consuming pending messages from the bot.
    _run([*account_command, "listDevices"], dry_run=args.dry_run, timeout=args.timeout)
    if args.test_send:
        _run([*account_command, "send", "--note-to-self", "-m", "BUDUCCA Signal repair check."],
             dry_run=args.dry_run, timeout=args.timeout)
    if args.dry_run:
        print("Dry run only: no commands executed and no repair or health check performed.")
    else:
        print("Signal account check passed." + (" Note-to-self send accepted." if args.test_send else " Message delivery was not tested."))
        print("Restart BUDUCCA to clear its disabled Signal state and resume receiving/synchronization.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check and repair the bot's Signal setup without deleting account data")
    parser.add_argument("--config", default="config", help="bot config directory, full JSON config, or signal.json")
    parser.add_argument("--upgrade", action="store_true", help="upgrade the package owning the configured signal-cli")
    parser.add_argument("--repair", action="store_true", help="refresh account attributes on Signal before checking authentication")
    parser.add_argument("--relink", action="store_true", help="reset an expired device link via a phone QR scan, then refresh/sync")
    parser.add_argument("--test-send", action="store_true", help="send a single test message to Note to Self")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without running commands or changing state")
    parser.add_argument("--timeout", type=float, default=60, help="seconds per Signal command (default: 60; linking allows at least 180)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    try:
        repair_signal(args)
    except (RepairError, UpdateError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Signal repair incomplete: {exc}\n{_recovery_hint(str(exc))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Signal repair interrupted; account health was not verified.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
