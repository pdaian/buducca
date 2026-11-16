import io
import json
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from scripts import repair_signal


ACCOUNT = "+15551230000"


class SignalRepairScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.config = {"account": ACCOUNT}
        stack.enter_context(patch.object(repair_signal, "load_named_config_map", return_value=self.config))
        stack.enter_context(patch.object(repair_signal.shutil, "which", side_effect=lambda name: name if name.startswith("/") else f"/usr/bin/{name}"))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        stack.enter_context(redirect_stdout(self.stdout))
        stack.enter_context(redirect_stderr(self.stderr))
        self.run = Mock(side_effect=self.success)
        stack.enter_context(patch.object(repair_signal.subprocess, "run", side_effect=self.run))
        self.interactive_run = stack.enter_context(patch.object(repair_signal, "_run_interactive", side_effect=self.run))

    @staticmethod
    def success(command, **_kwargs):
        output = json.dumps([{"number": ACCOUNT}]) if "listAccounts" in command else "ok"
        return subprocess.CompletedProcess(command, 0, output, "")

    def test_default_checks_authentication_without_sending_or_consuming_messages(self) -> None:
        self.assertEqual(repair_signal.main([]), 0)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][-1], "--version")
        self.assertEqual(commands[1][-3:], ["-o", "json", "listAccounts"])
        self.assertEqual(commands[2][-3:], ["-a", ACCOUNT, "listDevices"])
        self.assertIn("Message delivery was not tested", self.stdout.getvalue())

    def test_relink_streams_qr_without_account_or_json_flags_and_verifies_afterward(self) -> None:
        self.assertEqual(repair_signal.main(["--relink", "--test-send"]), 0)
        calls = self.run.call_args_list
        link_call = next(call for call in calls if "link" in call.args[0])
        self.assertNotIn("-a", link_call.args[0])
        self.assertNotIn("-o", link_call.args[0])
        self.interactive_run.assert_called_once_with(link_call.args[0], timeout=link_call.kwargs["timeout"])
        self.assertGreaterEqual(link_call.kwargs["timeout"], 180)
        commands = [call.args[0] for call in calls]
        self.assertEqual([command[-1] for command in commands[2:-1]], ["listAccounts", "updateAccount", "sendSyncRequest", "listDevices"])
        self.assertIn("--note-to-self", commands[-1])
        self.assertIn("Note-to-self send accepted", self.stdout.getvalue())

    def test_dry_run_does_not_execute_even_with_all_actions(self) -> None:
        self.assertEqual(repair_signal.main(["--dry-run", "--upgrade", "--repair", "--relink", "--test-send"]), 0)
        self.run.assert_not_called()
        self.assertIn("Dry run only", self.stdout.getvalue())
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_matching_custom_executable_and_config_are_used_for_every_account_command(self) -> None:
        self.config.update({
            "receive_command": ["/opt/signal-cli", "--account=" + ACCOUNT, "--data-dir=/srv/signal", "-o", "json", "receive"],
            "send_command": ["/opt/signal-cli", "-c", "/srv/signal", "-a", ACCOUNT, "send", "-m", "{message}", "{recipient}"],
        })
        self.assertEqual(repair_signal.main(["--repair"]), 0)
        for call in self.run.call_args_list[1:]:
            self.assertEqual(call.args[0][:3], ["/opt/signal-cli", "--config", "/srv/signal"])

    def test_mismatched_account_or_store_and_wrappers_fail_before_commands(self) -> None:
        cases = [
            ["signal-cli", "-a", "+15559999999", "send"],
            ["signal-cli", "--config", "/other/store", "-a", ACCOUNT, "send"],
            ["python3", "wrapper.py", "send"],
            ["signal-cli", "--dbus", "-a", ACCOUNT, "send"],
            ["signal-cli", "--unknown", "send"],
            ["signal-cli", "--account"],
            ["signal-cli", "-a", ACCOUNT],
            ["signal-cli", "send"],
            ["~/bin/signal-cli", "-a", ACCOUNT, "send"],
            ["signal-cli", "--config", "~/signal", "-a", ACCOUNT, "send"],
            None,
        ]
        for command in cases:
            with self.subTest(command=command):
                self.config["send_command"] = command
                self.assertEqual(repair_signal.main(["--upgrade", "--relink"]), 1)
        self.run.assert_not_called()

    def test_relative_executable_keeps_explicit_current_directory(self) -> None:
        for verb in ("receive", "send"):
            self.config[f"{verb}_command"] = ["./signal-cli", "-a", ACCOUNT, verb]
        with patch.object(repair_signal.shutil, "which", return_value=None):
            _, prefix = repair_signal.load_signal_command("config")
        self.assertEqual(prefix[0], "./signal-cli")

    def test_default_data_directory_honors_xdg(self) -> None:
        with patch.dict(repair_signal.os.environ, {"XDG_DATA_HOME": "/srv/bot-data"}):
            self.assertEqual(repair_signal.main([]), 0)
        self.assertIn("/srv/bot-data/signal-cli", self.run.call_args_list[1].args[0])

    def test_missing_account_and_invalid_json_never_report_success(self) -> None:
        for output in ("[]", '[{"number":"+15559999999"}]', "not json", "{}"):
            with self.subTest(output=output):
                self.run.reset_mock()
                self.run.side_effect = [self.success(["--version"]), subprocess.CompletedProcess([], 0, output, "")]
                self.assertEqual(repair_signal.main(["--repair", "--test-send"]), 1)
                self.assertEqual(self.run.call_count, 2)
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_server_rejection_after_local_account_check_is_a_failure(self) -> None:
        self.run.side_effect = [self.success([]), self.success(["listAccounts"]), subprocess.CompletedProcess([], 1, "", "Authorization failed (401)")]
        self.assertEqual(repair_signal.main(["--test-send"]), 1)
        self.assertEqual(self.run.call_count, 3)
        self.assertIn("--relink", self.stderr.getvalue())
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_failed_repair_stops_without_test_send(self) -> None:
        self.run.side_effect = [self.success([]), self.success(["listAccounts"]), subprocess.CompletedProcess([], 1, "", "Rate limit exceeded")]
        self.assertEqual(repair_signal.main(["--repair", "--test-send"]), 1)
        self.assertEqual(self.run.call_count, 3)
        self.assertIn("retry interval", self.stderr.getvalue())

    def test_missing_executable_permission_error_and_timeout_are_nonzero(self) -> None:
        for error in (FileNotFoundError("missing"), PermissionError("denied"), subprocess.TimeoutExpired("signal-cli", 60)):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertEqual(repair_signal.main([]), 1)
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_link_failure_or_wrong_phone_cannot_pass_verification(self) -> None:
        for link_result in (subprocess.CompletedProcess([], 1, None, None), self.success([])):
            with self.subTest(link_result=link_result):
                self.run.side_effect = [self.success([]), link_result, subprocess.CompletedProcess([], 0, '[]', '')]
                self.assertEqual(repair_signal.main(["--relink"]), 1)
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_link_conflict_recommends_upgrading_before_retrying(self) -> None:
        self.run.side_effect = [
            self.success([]),
            subprocess.CompletedProcess([], 3, None, "Link request error: StatusCode: 409\n"),
        ]
        self.assertEqual(repair_signal.main(["--relink", "--test-send"]), 1)
        self.assertEqual(self.run.call_count, 2)
        self.assertIn("StatusCode: 409", self.stderr.getvalue())
        self.assertIn("--upgrade --relink", self.stderr.getvalue())
        self.assertIn(repair_signal.RELEASES_URL, self.stderr.getvalue())
        self.assertIn("new QR", self.stderr.getvalue())
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_capability_exception_has_upgrade_guidance_without_a_status_code(self) -> None:
        hint = repair_signal._recovery_hint("org.whispersystems.signalservice.internal.push.exceptions.MissingCapabilitiesException")
        self.assertIn("--upgrade --relink", hint)

    def test_unrelated_conflicts_and_numbers_do_not_get_link_upgrade_guidance(self) -> None:
        for detail in ("Send failed: StatusCode: 409", "Link request error: StatusCode: 4090",
                       "User +15554091234 is not registered", "Link request error: StatusCode: 429"):
            with self.subTest(detail=detail):
                self.assertNotIn("--upgrade --relink", repair_signal._recovery_hint(detail))

    def test_invalid_timeout_is_rejected_without_commands(self) -> None:
        for timeout in ("0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout), self.assertRaises(SystemExit) as error:
                repair_signal.main(["--timeout", timeout])
            self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_upgrade_targets_only_package_owning_selected_cli(self) -> None:
        manager = Mock(name="apt manager")
        manager.name = "apt-get"
        manager.refresh_command = ("apt-get", "update")
        manager.upgrade_command.return_value = ("apt-get", "install", "--only-upgrade", "-y", "signal-cli")
        manager.requires_privilege = False
        self.run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "/usr/bin/signal-cli\n", "")
        with patch.object(repair_signal, "MANAGERS", (manager,)), patch.object(repair_signal.shutil, "which", side_effect=lambda name: None if name == "pkg" else name):
            repair_signal.upgrade_signal_cli("/usr/bin/signal-cli", dry_run=False)
        self.assertEqual([call.args[0] for call in self.run.call_args_list], [
            ["dpkg-query", "-L", "signal-cli"],
            ("apt-get", "update"),
            ("apt-get", "install", "--only-upgrade", "-y", "signal-cli"),
        ])
        manager.upgrade_command.assert_called_once_with(["signal-cli"])

    def test_upgrade_does_not_touch_unrelated_cli_installation(self) -> None:
        manager = Mock()
        manager.name = "brew"
        self.run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "/opt/homebrew/Cellar/signal-cli/1.0\n", "")
        with patch.object(repair_signal, "MANAGERS", (manager,)):
            with self.assertRaisesRegex(repair_signal.RepairError, "manual/container"):
                repair_signal.upgrade_signal_cli("/opt/manual/bin/signal-cli", dry_run=False)
        manager.upgrade_command.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_upgrade_supports_brew_rpm_and_termux_package_ownership(self) -> None:
        for name, executable, probe_output, expected in (
            ("brew", "/opt/brew/Cellar/signal-cli/1/bin/signal-cli", "/opt/brew/Cellar/signal-cli/1\n", ("brew", "upgrade", "signal-cli")),
            ("dnf", "/usr/bin/signal-cli", "/usr/bin/signal-cli\n", ("dnf", "upgrade", "-y", "signal-cli")),
            ("yum", "/usr/bin/signal-cli", "/usr/bin/signal-cli\n", ("yum", "update", "-y", "signal-cli")),
            ("pkg", "/data/termux/bin/signal-cli", "/data/termux/bin/signal-cli\n", ("pkg", "upgrade", "-y", "signal-cli")),
        ):
            with self.subTest(manager=name):
                manager = next(manager for manager in repair_signal.MANAGERS if manager.name == name)
                self.run.reset_mock()
                self.run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, probe_output, "")
                with patch.object(repair_signal, "MANAGERS", (manager,)), patch.object(repair_signal.os, "geteuid", return_value=0):
                    repair_signal.upgrade_signal_cli(executable, dry_run=False)
                self.assertEqual(self.run.call_args.args[0], expected)

    def test_failed_package_refresh_prevents_upgrade_and_account_changes(self) -> None:
        manager = next(manager for manager in repair_signal.MANAGERS if manager.name == "brew")
        with patch.object(repair_signal, "MANAGERS", (manager,)):
            self.run.side_effect = [subprocess.CompletedProcess([], 0, "/usr\n", ""), subprocess.CompletedProcess([], 1, None, None)]
            self.assertEqual(repair_signal.main(["--upgrade", "--relink"]), 1)
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.run.call_args.args[0], ("brew", "update"))
        self.assertNotIn("check passed", self.stdout.getvalue())

    def test_upgrade_failure_aborts_before_account_changes(self) -> None:
        with patch.object(repair_signal, "upgrade_signal_cli", side_effect=repair_signal.RepairError("upgrade failed")):
            self.assertEqual(repair_signal.main(["--upgrade", "--relink"]), 1)
        self.run.assert_not_called()


class SignalRepairInteractiveTests(unittest.TestCase):
    def test_stderr_is_streamed_and_retained_without_redirecting_qr_or_input(self) -> None:
        process = MagicMock()
        process.__enter__.return_value = process
        process.stderr = io.TextIOWrapper(io.BytesIO(b"Link request error: StatusCode: 409\n"), encoding="utf-8")
        process.returncode = 3
        output = io.StringIO()
        with patch.object(repair_signal.subprocess, "Popen", return_value=process) as popen, redirect_stderr(output):
            result = repair_signal._run_interactive(["signal-cli", "link"], timeout=180)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr, output.getvalue())
        self.assertIn("StatusCode: 409", result.stderr)
        self.assertNotIn("stdin", popen.call_args.kwargs)
        self.assertNotIn("stdout", popen.call_args.kwargs)
        process.wait.assert_called_once_with(timeout=180)

    def test_stderr_decodes_characters_split_across_reads_and_replaces_invalid_bytes(self) -> None:
        process = MagicMock()
        process.__enter__.return_value = process
        process.stderr.encoding = "utf-8"
        process.stderr.buffer.read1.side_effect = [b"\xe2\x9c", b"\x93\xff\xe2", b""]
        process.returncode = 0
        output = io.StringIO()
        with patch.object(repair_signal.subprocess, "Popen", return_value=process), redirect_stderr(output):
            result = repair_signal._run_interactive(["signal-cli", "link"], timeout=180)
        self.assertEqual(result.stderr, "\u2713\ufffd\ufffd")
        self.assertEqual(result.stderr, output.getvalue())

    def test_large_stderr_does_not_deadlock_and_only_retains_a_bounded_tail(self) -> None:
        output = io.StringIO()
        command = [sys.executable, "-B", "-c",
                   "import sys; sys.stderr.write('x' * 200000 + '\\nLink request error: StatusCode: 409\\n'); sys.exit(3)"]
        with redirect_stderr(output):
            result = repair_signal._run_interactive(command, timeout=10)
        self.assertEqual(result.returncode, 3)
        self.assertGreater(len(output.getvalue()), 200000)
        self.assertLessEqual(len(result.stderr), 32 * 4096)
        self.assertTrue(result.stderr.endswith("Link request error: StatusCode: 409\n"))

    def test_timeout_and_interruption_kill_and_reap_the_child(self) -> None:
        for error in (subprocess.TimeoutExpired("signal-cli", 1), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                process = MagicMock()
                process.__enter__.return_value = process
                process.stderr = io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")
                process.wait.side_effect = [error, -9]
                with patch.object(repair_signal.subprocess, "Popen", return_value=process):
                    with self.assertRaises(type(error)):
                        repair_signal._run_interactive(["signal-cli", "link"], timeout=1)
                process.kill.assert_called_once_with()
                self.assertEqual(process.wait.call_count, 2)


class SignalRepairConfigFormatTests(unittest.TestCase):
    def test_reads_full_config_signal_json_and_config_directory(self) -> None:
        for source, raw, is_dir in (("bot.json", {"signal": {"account": ACCOUNT}}, False),
                                    ("signal.json", {"account": ACCOUNT}, False),
                                    ("config", {"account": ACCOUNT}, True)):
            with self.subTest(source=source), patch.object(Path, "is_dir", return_value=is_dir), \
                    patch.object(Path, "rglob", return_value=[Path("config/signal.json")]), \
                    patch("assistant_framework.config_files.read_json_file", return_value=raw):
                account, _ = repair_signal.load_signal_command(source)
            self.assertEqual(account, ACCOUNT)


if __name__ == "__main__":
    unittest.main()
