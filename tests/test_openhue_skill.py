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
        with patch.object(self.module, "_run_shell", return_value=(0, payload, "")):
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

        with patch.object(self.module, "_run_shell", side_effect=fake):
            result = self.module.run(self.workspace, {"action": "on", "lights": ["Kitchen", "2"]})

        self.assertIn("Applied `on` to", result)
        self.assertIn("Kitchen (1)", result)
        self.assertIn("Desk (2)", result)
        self.assertTrue(any("--id 1" in cmd for cmd in calls))
        self.assertTrue(any("--id 2" in cmd for cmd in calls))

    def test_missing_lights_arg(self) -> None:
        result = self.module.run(self.workspace, {"action": "off"})
        self.assertEqual(result, "Missing required arg `lights` (non-empty list of names and/or ids).")

    def test_reports_unknown_light_names(self) -> None:
        payload = json.dumps({"lights": [{"id": "1", "name": "Kitchen"}]})

        def fake(command, timeout=20.0):
            if "list" in command:
                return (0, payload, "")
            return (0, "ok", "")

        with patch.object(self.module, "_run_shell", side_effect=fake):
            result = self.module.run(self.workspace, {"action": "off", "lights": ["Bedroom", "Kitchen"]})

        self.assertIn("Not found: Bedroom", result)
        self.assertIn("Kitchen (1)", result)


if __name__ == "__main__":
    unittest.main()
