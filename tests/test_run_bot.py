import signal
import unittest
from unittest.mock import patch

import run_bot


class RunBotSupervisorTests(unittest.TestCase):
    def test_check_config_validates_without_starting_a_process(self) -> None:
        with patch("run_bot.subprocess.Popen") as spawn, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(run_bot.main(["--check-config", "--config", "config.example"]), 0)
        spawn.assert_not_called()
        self.assertIn("Configuration valid", output.getvalue())
        self.assertNotIn("replace-with", output.getvalue())

    def test_check_config_reports_errors_without_a_traceback(self) -> None:
        with patch("messaging_llm_bot.config.load_config", side_effect=ValueError("runtime.max_skill_result_chars must be >= 512")), patch(
            "sys.stderr", new_callable=io.StringIO,
        ) as output, patch("run_bot.subprocess.Popen") as spawn:
            self.assertEqual(run_bot.main(["--check-config"]), 2)
        spawn.assert_not_called()
        self.assertIn("Configuration error: runtime.max_skill_result_chars", output.getvalue())

    def test_help_does_not_start_the_supervisor(self) -> None:
        with patch("sys.stdout", new_callable=io.StringIO), patch("run_bot.subprocess.Popen") as spawn:
            with self.assertRaises(SystemExit) as caught:
                run_bot.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        spawn.assert_not_called()

    def test_killed_child_restarts_after_delay(self) -> None:
        returncodes = iter([-signal.SIGKILL, 0])
        sleeps = []
        popen_calls = []

        class FakeProcess:
            def __init__(self, returncode: int) -> None:
                self.returncode = returncode

            def wait(self) -> int:
                return self.returncode

        def fake_popen(command, env):
            popen_calls.append((command, env))
            return FakeProcess(next(returncodes))

        with patch("run_bot.subprocess.Popen", side_effect=fake_popen):
            status = run_bot._run_supervised(["--config", "config"], sleep=sleeps.append)

        self.assertEqual(status, 0)
        self.assertEqual(sleeps, [60.0])
        self.assertEqual(len(popen_calls), 2)
        self.assertEqual(popen_calls[0][1][run_bot._CHILD_ENV_VAR], "1")

    def test_non_killed_child_exit_is_returned_without_restart(self) -> None:
        class FakeProcess:
            def wait(self) -> int:
                return 2

        with patch("run_bot.subprocess.Popen", return_value=FakeProcess()):
            status = run_bot._run_supervised([], sleep=lambda _seconds: self.fail("unexpected restart"))

        self.assertEqual(status, 2)

    def test_shell_style_sigkill_exit_is_treated_as_killed(self) -> None:
        self.assertTrue(run_bot._was_killed(128 + signal.SIGKILL))


if __name__ == "__main__":
    unittest.main()
import io
