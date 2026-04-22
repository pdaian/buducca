from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable


FRONTEND_PACKAGES: dict[str, dict[str, tuple[str, ...]]] = {
    "signal": {
        "apt-get": ("signal-desktop", "signal-cli"),
        "brew": ("signal", "signal-cli"),
        "dnf": ("signal-desktop",),
        "flatpak": ("org.signal.Signal",),
        "pkg": ("signal-cli",),
        "snap": ("signal-desktop",),
        "yum": ("signal-desktop",),
    },
    "telegram": {
        "apt-get": ("telegram-desktop",),
        "brew": ("telegram", "telegram-desktop"),
        "dnf": ("telegram-desktop",),
        "flatpak": ("org.telegram.desktop",),
        "pkg": ("telegram",),
        "snap": ("telegram-desktop",),
        "yum": ("telegram-desktop",),
    },
    "whatsapp": {
        "apt-get": ("whatsapp-for-linux", "zapzap"),
        "brew": ("whatsapp", "whatsapp-for-linux", "zapzap"),
        "dnf": ("zapzap",),
        "flatpak": ("com.rtosta.zapzap", "io.github.mimbrero.WhatsAppDesktop"),
        "snap": ("whatsapp-for-linux",),
        "yum": ("zapzap",),
    },
}

ALL_FRONTENDS = tuple(sorted(FRONTEND_PACKAGES))


class UpdateError(RuntimeError):
    """Raised when frontend updates cannot be completed."""


@dataclass(frozen=True)
class PackageManager:
    name: str
    refresh_command: tuple[str, ...] | None
    install_candidates: Callable[[tuple[str, ...]], list[str]]
    upgrade_command: Callable[[list[str]], tuple[str, ...]]
    requires_privilege: bool = False


def _installed_via_dpkg(candidates: tuple[str, ...]) -> list[str]:
    installed: list[str] = []
    for package in candidates:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and "install ok installed" in proc.stdout:
            installed.append(package)
    return installed


def _installed_via_brew(candidates: tuple[str, ...]) -> list[str]:
    proc = subprocess.run(
        ["brew", "list", "--formula", "--cask"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    installed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return [package for package in candidates if package in installed]


def _installed_via_rpm(candidates: tuple[str, ...]) -> list[str]:
    installed: list[str] = []
    for package in candidates:
        proc = subprocess.run(
            ["rpm", "-q", package],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            installed.append(package)
    return installed


def _installed_via_flatpak(candidates: tuple[str, ...]) -> list[str]:
    proc = subprocess.run(
        ["flatpak", "list", "--app", "--columns=application"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    installed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return [package for package in candidates if package in installed]


def _installed_via_snap(candidates: tuple[str, ...]) -> list[str]:
    proc = subprocess.run(
        ["snap", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    installed = {line.split()[0] for line in proc.stdout.splitlines()[1:] if line.split()}
    return [package for package in candidates if package in installed]


MANAGERS: tuple[PackageManager, ...] = (
    PackageManager(
        name="apt-get",
        refresh_command=("apt-get", "update"),
        install_candidates=_installed_via_dpkg,
        upgrade_command=lambda packages: ("apt-get", "install", "--only-upgrade", "-y", *packages),
        requires_privilege=True,
    ),
    PackageManager(
        name="brew",
        refresh_command=("brew", "update"),
        install_candidates=_installed_via_brew,
        upgrade_command=lambda packages: ("brew", "upgrade", *packages),
    ),
    PackageManager(
        name="dnf",
        refresh_command=None,
        install_candidates=_installed_via_rpm,
        upgrade_command=lambda packages: ("dnf", "upgrade", "-y", *packages),
        requires_privilege=True,
    ),
    PackageManager(
        name="flatpak",
        refresh_command=None,
        install_candidates=_installed_via_flatpak,
        upgrade_command=lambda packages: ("flatpak", "update", "-y", *packages),
    ),
    PackageManager(
        name="pkg",
        refresh_command=("pkg", "update", "-y"),
        install_candidates=_installed_via_dpkg,
        upgrade_command=lambda packages: ("pkg", "upgrade", "-y", *packages),
    ),
    PackageManager(
        name="snap",
        refresh_command=None,
        install_candidates=_installed_via_snap,
        upgrade_command=lambda packages: ("snap", "refresh", *packages),
        requires_privilege=True,
    ),
    PackageManager(
        name="yum",
        refresh_command=None,
        install_candidates=_installed_via_rpm,
        upgrade_command=lambda packages: ("yum", "update", "-y", *packages),
        requires_privilege=True,
    ),
)


def _shell_join(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _with_privilege(command: tuple[str, ...], *, requires_privilege: bool) -> tuple[str, ...]:
    if not requires_privilege or os.name == "nt" or os.geteuid() == 0:
        return command
    if shutil.which("sudo"):
        return ("sudo", *command)
    raise UpdateError(f"{command[0]} requires elevated privileges and sudo is not available")


def _candidate_packages(frontends: tuple[str, ...], manager_name: str) -> tuple[str, ...]:
    packages: list[str] = []
    for frontend in frontends:
        packages.extend(FRONTEND_PACKAGES[frontend].get(manager_name, ()))
    return tuple(dict.fromkeys(packages))


def _available_managers() -> list[PackageManager]:
    return [manager for manager in MANAGERS if shutil.which(manager.name)]


def _run(command: tuple[str, ...], *, dry_run: bool) -> None:
    print(_shell_join(command))
    if dry_run:
        return
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise UpdateError(f"command failed with exit code {completed.returncode}: {_shell_join(command)}")


def update_frontends(*, frontends: tuple[str, ...], dry_run: bool = False) -> int:
    managers = _available_managers()
    if not managers:
        print("No supported package manager found.")
        return 0

    any_updates = False
    for manager in managers:
        candidates = _candidate_packages(frontends, manager.name)
        if not candidates:
            continue
        installed = manager.install_candidates(candidates)
        if not installed:
            continue
        any_updates = True
        print(f"[{manager.name}] updating: {', '.join(installed)}")
        if manager.refresh_command is not None:
            _run(_with_privilege(manager.refresh_command, requires_privilege=manager.requires_privilege), dry_run=dry_run)
        _run(_with_privilege(manager.upgrade_command(installed), requires_privilege=manager.requires_privilege), dry_run=dry_run)

    if not any_updates:
        names = ", ".join(frontends)
        print(f"No installed frontend packages found for: {names}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update installed messaging frontends")
    parser.add_argument(
        "frontends",
        nargs="*",
        choices=ALL_FRONTENDS,
        default=list(ALL_FRONTENDS),
        help="frontends to update",
    )
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return update_frontends(frontends=tuple(args.frontends), dry_run=args.dry_run)
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
