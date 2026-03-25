import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_client.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scripts.run_client", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/run_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunClientScriptTests(unittest.TestCase):
    def test_main_without_args_prints_setup_guide(self) -> None:
        module = _load_module()
        out = StringIO()
        with redirect_stdout(out):
            exit_code = module.main([])
        rendered = out.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("BUDUCCA Android client setup", rendered)
        self.assertIn("python3 scripts/run_client.py run", rendered)
        self.assertIn("BUDUCCA_REMOTE_HOST", rendered)

    def test_run_parser_uses_environment_defaults(self) -> None:
        module = _load_module()
        with mock.patch.dict(
            module.os.environ,
            {
                module.DEFAULT_REMOTE_HOST_ENV: "bot@example.com",
                module.DEFAULT_REMOTE_DIR_ENV: "/srv/buducca/android",
            },
            clear=False,
        ):
            parser = module.build_parser()
            args = parser.parse_args(["run"])

        self.assertEqual(args.remote_host, "bot@example.com")
        self.assertEqual(args.remote_dir, "/srv/buducca/android")

    def test_run_parser_uses_persisted_sync_defaults(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "client-config.json"
            config_path.write_text(
                json.dumps({"remote_host": "persisted@example.com", "remote_dir": "/srv/custom/android"}) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(module, "DEFAULT_SYNC_CONFIG_FILE", str(config_path)):
                with mock.patch.dict(
                    module.os.environ,
                    {
                        module.DEFAULT_REMOTE_HOST_ENV: "",
                        module.DEFAULT_REMOTE_DIR_ENV: "",
                    },
                    clear=False,
                ):
                    parser = module.build_parser()
                    args = parser.parse_args(["run"])

        self.assertEqual(args.remote_host, "persisted@example.com")
        self.assertEqual(args.remote_dir, "/srv/custom/android")

    def test_sync_target_prompts_and_persists_when_missing(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "client-config.json"
            args = module.argparse.Namespace(remote_host="", remote_dir="")
            with mock.patch.object(module, "DEFAULT_SYNC_CONFIG_FILE", str(config_path)):
                with mock.patch.dict(
                    module.os.environ,
                    {
                        module.DEFAULT_REMOTE_HOST_ENV: "",
                        module.DEFAULT_REMOTE_DIR_ENV: "",
                    },
                    clear=False,
                ):
                    with mock.patch.object(module.sys.stdin, "isatty", return_value=True):
                        with mock.patch("builtins.input", side_effect=["prompted@example.com", ""]):
                            remote_host, remote_dir = module._sync_target(args)

            persisted = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(remote_host, "prompted@example.com")
        self.assertEqual(remote_dir, module.DEFAULT_REMOTE_DIR)
        self.assertEqual(persisted["remote_host"], "prompted@example.com")
        self.assertEqual(persisted["remote_dir"], module.DEFAULT_REMOTE_DIR)

    def test_sync_once_auto_generates_missing_ssh_key(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            outbox = Path(td) / "android-sms-outbox.jsonl"
            state = Path(td) / "android-sms-outbox-state.json"
            ssh_key = Path(td) / "buducca_android_sync"
            with mock.patch.object(module, "_sync_target", return_value=("bot@example.com", "/srv/buducca/android")):
                with mock.patch.object(module, "generate_ssh_key", return_value="ssh-ed25519 AAAA test") as generate:
                    with mock.patch.object(module, "sync_once", return_value=0) as sync_once:
                        exit_code = module.main(
                            [
                                "sync",
                                "--inbox",
                                str(inbox),
                                "--outbox",
                                str(outbox),
                                "--outbox-state-file",
                                str(state),
                                "--ssh-key",
                                str(ssh_key),
                                "once",
                            ]
                        )

        self.assertEqual(exit_code, 0)
        generate.assert_called_once_with(
            private_key_path=ssh_key,
            comment=module.DEFAULT_SSH_KEY_COMMENT,
            force=True,
        )
        sync_once.assert_called_once()

    def test_run_client_loop_continues_after_collection_and_sync_failures(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            notification_state = Path(td) / "notification-state.json"
            outbox = Path(td) / "android-sms-outbox.jsonl"
            outbox_state = Path(td) / "android-sms-outbox-state.json"
            stderr = StringIO()
            with mock.patch.object(
                module,
                "collect_notifications_once",
                side_effect=[module.ClientError("network down"), 0],
            ) as collect:
                with mock.patch.object(
                    module,
                    "sync_once",
                    side_effect=[module.ClientError("scp aborted"), 0],
                ) as sync:
                    with mock.patch.object(module.time, "sleep", side_effect=[None, KeyboardInterrupt]):
                        with redirect_stderr(stderr):
                            with self.assertRaises(KeyboardInterrupt):
                                module.run_client_loop(
                                    inbox_path=inbox,
                                    notification_state_path=notification_state,
                                    outbox_path=outbox,
                                    outbox_state_path=outbox_state,
                                    remote_host="bot@example.com",
                                    remote_dir="/srv/buducca/android",
                                    ssh_key="/tmp/key",
                                    sms_command="termux-sms-send",
                                    notification_command="termux-notification-list",
                                    notification_dismiss_command=module.DEFAULT_NOTIFICATION_DISMISS_COMMAND,
                                    include_packages=None,
                                    scp_command="scp",
                                    interval_seconds=0.1,
                                )

        rendered = stderr.getvalue()
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(sync.call_count, 2)
        self.assertIn("notification collection failed: network down", rendered)
        self.assertIn("sync failed: scp aborted", rendered)

    def test_sync_run_mode_logs_failure_and_keeps_looping(self) -> None:
        module = _load_module()
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(module, "_ensure_ssh_key"):
            with mock.patch.object(module, "_sync_target", return_value=("bot@example.com", "/srv/buducca/android")):
                with mock.patch.object(
                    module,
                    "sync_once",
                    side_effect=[module.ClientError("network down"), 3],
                ) as sync_once:
                    with mock.patch.object(module.time, "sleep", side_effect=[None, KeyboardInterrupt]):
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            exit_code = module.main(["sync", "run"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(sync_once.call_count, 2)
        self.assertIn("sync failed: network down", stderr.getvalue())
        self.assertIn('"delivered": 3', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
