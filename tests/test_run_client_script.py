import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


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
        with unittest.mock.patch.dict(
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
            with unittest.mock.patch.object(module, "DEFAULT_SYNC_CONFIG_FILE", str(config_path)):
                with unittest.mock.patch.dict(
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
            with unittest.mock.patch.object(module, "DEFAULT_SYNC_CONFIG_FILE", str(config_path)):
                with unittest.mock.patch.dict(
                    module.os.environ,
                    {
                        module.DEFAULT_REMOTE_HOST_ENV: "",
                        module.DEFAULT_REMOTE_DIR_ENV: "",
                    },
                    clear=False,
                ):
                    with unittest.mock.patch.object(module.sys.stdin, "isatty", return_value=True):
                        with unittest.mock.patch("builtins.input", side_effect=["prompted@example.com", ""]):
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
            with unittest.mock.patch.object(module, "_sync_target", return_value=("bot@example.com", "/srv/buducca/android")):
                with unittest.mock.patch.object(module, "generate_ssh_key", return_value="ssh-ed25519 AAAA test") as generate:
                    with unittest.mock.patch.object(module, "sync_once", return_value=0) as sync_once:
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


if __name__ == "__main__":
    unittest.main()
