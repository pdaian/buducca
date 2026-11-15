import json
import unittest
from unittest.mock import Mock

from assistant_framework.action_runtime import ActionEnvelope, decide_action, load_action_policy


class ActionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Mock()
        self.workspace.resolve.return_value.exists.return_value = True
        self.action = ActionEnvelope("file.write", {}, "requested", ["notes/x"], requires_approval=True)

    def test_missing_policy_retains_legacy_allow_behavior(self) -> None:
        self.workspace.resolve.return_value.exists.return_value = False
        policy = load_action_policy(self.workspace)
        self.assertEqual(decide_action(policy, self.action), "allow")
        self.workspace.read_text.assert_not_called()

    def test_malformed_or_empty_policy_denies_actions(self) -> None:
        for raw in ("", "{broken", "null", "[]", '{"default": "alow"}', '{"actions": []}',
                    '{"actions": {"file.write": "yes"}}', '{"default": null}', '{"defualt": "deny"}'):
            with self.subTest(raw=raw), self.assertLogs(level="ERROR"):
                self.workspace.read_text.return_value = raw
                policy = load_action_policy(self.workspace)
                self.assertEqual(decide_action(policy, self.action), "deny")

    def test_unreadable_policy_denies_actions(self) -> None:
        self.workspace.read_text.side_effect = PermissionError("unreadable")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(decide_action(load_action_policy(self.workspace), self.action), "deny")

    def test_policy_lookup_errors_deny_actions(self) -> None:
        self.workspace.resolve.return_value.exists.side_effect = PermissionError("unreadable directory")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(decide_action(load_action_policy(self.workspace), self.action), "deny")

    def test_explicit_policy_preserves_approval_and_per_action_decisions(self) -> None:
        self.workspace.read_text.return_value = json.dumps({"default": " DENY ", "actions": {"file.write": "ALLOW"}, "_comment": "example"})
        policy = load_action_policy(self.workspace)
        self.assertEqual(decide_action(policy, self.action), "ask")
        self.action.requires_approval = False
        self.assertEqual(decide_action(policy, self.action), "allow")
        self.action.name = "file.delete"
        self.assertEqual(decide_action(policy, self.action), "deny")

    def test_invalid_direct_decision_is_denied(self) -> None:
        self.assertEqual(decide_action({"default": "alow"}, self.action), "deny")
