import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_framework.workspace import Workspace


def load_openhue_module():
    skill_path = Path("skills/openhue/__init__.py")
    spec = importlib.util.spec_from_file_location("openhue_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load openhue skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenHueSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_openhue_module()
        self.workspace = Workspace(".")

    def test_list_returns_human_readable_lights(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen"}]})
        with patch.object(self.module, "_run_command", return_value=(0, payload, "")):
            result = self.module.run(self.workspace, {"action": "list"})
        self.assertIn("Kitchen (id: 1)", result)

    def test_on_resolves_light_name_and_id(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen"}, {"id": "2", "name": "Desk"}]})
        calls = []

        def fake(command, timeout=20.0):
            calls.append(command)
            if "list" in command:
                return (0, payload, "")
            return (0, "ok", "")

        with patch.object(self.module, "_run_command", side_effect=fake):
            result = self.module.run(self.workspace, {"action": "on", "lights": ["Kitchen", "2"]})

        self.assertIn("Applied `on` to", result)
        self.assertIn("Kitchen (1)", result)
        self.assertIn("Desk (2)", result)
        self.assertTrue(any(cmd[-2:] == ["--id", "1"] for cmd in calls))
        self.assertTrue(any(cmd[-2:] == ["--id", "2"] for cmd in calls))

    def test_missing_lights_arg(self) -> None:
        result = self.module.run(self.workspace, {"action": "off"})
        self.assertEqual(result, "Missing required arg `lights` (non-empty list of names and/or ids).")

    def test_reports_unknown_light_names(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen"}]})

        def fake(command, timeout=20.0):
            if "list" in command:
                return (0, payload, "")
            return (0, "ok", "")

        with patch.object(self.module, "_run_command", side_effect=fake):
            result = self.module.run(self.workspace, {"action": "off", "lights": ["Bedroom", "Kitchen"]})

        self.assertIn("Not found: Bedroom", result)
        self.assertIn("Kitchen (1)", result)

    def test_set_command_uses_argv_without_shell(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen Counter"}]})
        calls = []

        def fake(command, timeout=20.0):
            calls.append(command)
            if "list" in command:
                return (0, payload, "")
            return (0, "ok", "")

        with patch.object(self.module, "_run_command", side_effect=fake):
            result = self.module.run(
                self.workspace,
                {
                    "action": "toggle",
                    "lights": ["Kitchen Counter"],
                    "set_command_template": ["openhue", "lights", "{action}", "--name", "{name}"],
                    "brightness": 42,
                    "transition_ms": 250,
                },
            )

        self.assertIn("Applied `toggle` to", result)
        self.assertIn(
            [
                "openhue",
                "lights",
                "toggle",
                "--name",
                "Kitchen Counter",
                "--brightness",
                "42",
                "--transition-ms",
                "250",
            ],
            calls,
        )

    def test_rejects_invalid_brightness(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen"}]})

        with patch.object(self.module, "_run_command", return_value=(0, payload, "")):
            result = self.module.run(
                self.workspace,
                {"action": "on", "lights": ["Kitchen"], "brightness": 999},
            )

        self.assertEqual(result, "`brightness` must be in range 1-254.")

    def test_reports_missing_list_command(self) -> None:
        with patch.object(self.module.subprocess, "run", side_effect=FileNotFoundError("missing openhue")):
            result = self.module.run(self.workspace, {"action": "list"})

        self.assertIn("OpenHue list command failed: missing openhue", result)

    def test_rejects_non_positive_timeout(self) -> None:
        result = self.module.run(self.workspace, {"action": "list", "timeout_seconds": 0})

        self.assertEqual(result, "`timeout_seconds` must be greater than 0.")


if __name__ == "__main__":
    unittest.main()
