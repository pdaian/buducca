import signal
import unittest
from unittest.mock import patch

import run_bot


class RunBotSupervisorTests(unittest.TestCase):
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
