import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_frontends.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scripts.update_frontends", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/update_frontends.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class UpdateFrontendsScriptTests(unittest.TestCase):
    def test_dry_run_updates_only_installed_packages(self) -> None:
        module = _load_module()
        stdout = io.StringIO()

        apt_manager = module.PackageManager(
            name="apt-get",
            refresh_command=("apt-get", "update"),
            install_candidates=lambda candidates: ["signal-desktop"],
            upgrade_command=lambda packages: ("apt-get", "install", "--only-upgrade", "-y", *packages),
            requires_privilege=False,
        )
        brew_manager = module.PackageManager(
            name="brew",
            refresh_command=("brew", "update"),
            install_candidates=lambda candidates: [],
            upgrade_command=lambda packages: ("brew", "upgrade", *packages),
            requires_privilege=False,
        )

        with mock.patch.object(module, "_available_managers", return_value=[apt_manager, brew_manager]):
            with redirect_stdout(stdout):
                exit_code = module.update_frontends(frontends=("signal", "telegram"), dry_run=True)

        rendered = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("[apt-get] updating: signal-desktop", rendered)
        self.assertIn("apt-get update", rendered)
        self.assertIn("apt-get install --only-upgrade -y signal-desktop", rendered)
        self.assertNotIn("[brew]", rendered)

    def test_reports_when_no_supported_manager_exists(self) -> None:
        module = _load_module()
        stdout = io.StringIO()
        with mock.patch.object(module, "_available_managers", return_value=[]):
            with redirect_stdout(stdout):
                exit_code = module.update_frontends(frontends=("signal",), dry_run=True)

        self.assertEqual(exit_code, 0)
        self.assertIn("No supported package manager found.", stdout.getvalue())

    def test_main_returns_error_when_privilege_is_required_without_sudo(self) -> None:
        module = _load_module()
        stderr = io.StringIO()
        apt_manager = module.PackageManager(
            name="apt-get",
            refresh_command=("apt-get", "update"),
            install_candidates=lambda candidates: ["signal-desktop"],
            upgrade_command=lambda packages: ("apt-get", "install", "--only-upgrade", "-y", *packages),
            requires_privilege=True,
        )
        with mock.patch.object(module.os, "geteuid", return_value=1000):
            with mock.patch.object(module, "_available_managers", return_value=[apt_manager]):
                with mock.patch.object(module.shutil, "which", return_value=None):
                    with redirect_stderr(stderr):
                        exit_code = module.main(["signal", "--dry-run"])

        self.assertEqual(exit_code, 1)
        self.assertIn("requires elevated privileges", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
