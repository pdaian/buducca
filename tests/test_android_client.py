import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import run_client
from messaging_llm_bot.android_client import (
    AndroidClient,
    AndroidFrontendUnavailableError,
    MAX_ANDROID_OUTBOX_LINES,
    generate_ssh_key,
    main,
)
from messaging_llm_bot.termux_notification_collector import _normalize_include_packages, collect_once


class AndroidClientTests(unittest.TestCase):
    def test_get_updates_parses_sms_and_notification_payloads(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "type": "sms",
                        "sender_id": "+15550001",
                        "body": "hello",
                    },
                    {
                        "type": "notification",
                        "package_name": "org.example.app",
                        "title": "Build",
                        "body": "Finished",
                    },
                ]
            }
        )
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                updates = client.get_updates()

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].backend, "android")
        self.assertEqual(updates[0].conversation_id, "+15550001")
        self.assertEqual(updates[1].sender_id, "notif:org.example.app")
        self.assertIn("[Notification]", updates[1].text or "")

    def test_get_updates_uses_contact_for_sms_notification_sender(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "type": "notification",
                        "package_name": "com.google.android.apps.messaging",
                        "title": "(646) 374-2069",
                        "body": "tesst",
                    }
                ]
            }
        )
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].sender_id, "(646) 374-2069")
        self.assertEqual(updates[0].conversation_id, "(646) 374-2069")
        self.assertEqual(updates[0].sender_contact, "(646) 374-2069")

    def test_get_updates_generates_stable_event_id_when_source_event_id_is_missing(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "type": "notification",
                        "package_name": "org.example.app",
                        "title": "Build",
                        "body": "Finished",
                        "timestamp": "2026-03-18T09:01:00-04:00",
                    }
                ]
            }
        )
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                first_client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                second_client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                first_updates = first_client.get_updates()
                second_updates = second_client.get_updates()

        self.assertEqual(len(first_updates), 1)
        self.assertEqual(first_updates[0].event_id, second_updates[0].event_id)

    def test_get_updates_namespaces_event_and_conversation_by_client_uid(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "client_uid": "phone-a",
                        "type": "sms",
                        "sender_id": "+15550001",
                        "conversation_id": "+15550001",
                        "event_id": "42",
                        "body": "hello",
                    }
                ]
            }
        )
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "android-client:phone-a:+15550001")
        self.assertEqual(updates[0].event_id, "android-client:phone-a:42")

    def test_send_message_replaces_placeholders(self) -> None:
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "--to", "{recipient}", "--message", "{message}"],
                )
                client.send_message("+15550001", "hi")

        run.assert_called_once_with(
            ["python3", "send.py", "--to", "+15550001", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_main_send_can_queue_sms_to_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            outbox = Path(td) / "android-sms-outbox.jsonl"

            exit_code = main(
                [
                    "send",
                    "--recipient",
                    "+15550001",
                    "--message",
                    "queued",
                    "--outbox",
                    str(outbox),
                ]
            )

            self.assertEqual(exit_code, 0)
            payloads = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["recipient"], "+15550001")
            self.assertEqual(payloads[0]["message"], "queued")

    def test_main_send_normalizes_us_sms_recipient_when_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            outbox = Path(td) / "android-sms-outbox.jsonl"

            exit_code = main(
                [
                    "send",
                    "--recipient",
                    "(646) 374-2069",
                    "--message",
                    "queued",
                    "--outbox",
                    str(outbox),
                ]
            )

            self.assertEqual(exit_code, 0)
            payloads = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(payloads[0]["recipient"], "+16463742069")

    def test_main_send_limits_outbox_to_latest_50_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            outbox = Path(td) / "android-sms-outbox.jsonl"
            existing = [
                json.dumps({"recipient": f"+1555000{index:02d}", "message": f"old-{index}"}, ensure_ascii=False)
                for index in range(MAX_ANDROID_OUTBOX_LINES)
            ]
            outbox.write_text("\n".join(existing) + "\n", encoding="utf-8")

            exit_code = main(
                [
                    "send",
                    "--recipient",
                    "+15559999999",
                    "--message",
                    "newest",
                    "--outbox",
                    str(outbox),
                ]
            )

            self.assertEqual(exit_code, 0)
            payloads = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(payloads), MAX_ANDROID_OUTBOX_LINES)
            self.assertEqual(payloads[0]["message"], "old-1")
            self.assertEqual(payloads[-1]["message"], "newest")

    def test_main_flush_outbox_sends_only_new_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            outbox = Path(td) / "android-sms-outbox.jsonl"
            state = Path(td) / "android-sms-state.json"
            outbox.write_text(
                json.dumps({"recipient": "+15550001", "message": "first"}) + "\n"
                + json.dumps({"recipient": "+15550002", "message": "second"}) + "\n",
                encoding="utf-8",
            )

            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/termux-sms-send"):
                with patch("messaging_llm_bot.android_client.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout="", stderr="")
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "flush-outbox",
                                "--outbox",
                                str(outbox),
                                "--state-file",
                                str(state),
                            ]
                        )

                    self.assertEqual(exit_code, 0)
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(json.loads(stdout.getvalue()), {"delivered": 2})

                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "flush-outbox",
                                "--outbox",
                                str(outbox),
                                "--state-file",
                                str(state),
                            ]
                        )

                    self.assertEqual(exit_code, 0)
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(json.loads(stdout.getvalue()), {"delivered": 0})

    def test_main_send_normalizes_us_sms_recipient_for_direct_send(self) -> None:
        with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/termux-sms-send"):
            with patch("messaging_llm_bot.android_client.subprocess.run") as run:
                run.return_value = Mock(returncode=0, stdout="", stderr="")

                exit_code = main(
                    [
                        "send",
                        "--recipient",
                        "(646) 374-2069",
                        "--message",
                        "queued",
                    ]
                )

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(
            ["termux-sms-send", "-n", "+16463742069", "queued"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_generate_ssh_key_returns_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "android-sync-ed25519"
            public_key = key_path.with_name(key_path.name + ".pub")
            public_key.write_text("ssh-ed25519 AAAA example@test\n", encoding="utf-8")

            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/ssh-keygen"):
                with patch("messaging_llm_bot.android_client.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout="", stderr="")
                    value = generate_ssh_key(private_key_path=key_path, comment="android@test", force=True)

            self.assertEqual(value, "ssh-ed25519 AAAA example@test")
            run.assert_called_once()

    def test_missing_executable_raises_frontend_unavailable(self) -> None:
        client = AndroidClient(receive_command=["missingcmd"], send_command=["python3", "send.py"])
        with patch("messaging_llm_bot.android_client.which", return_value=None):
            with self.assertRaises(AndroidFrontendUnavailableError):
                client.get_updates()

    def test_main_receive_reads_only_new_jsonl_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "android-state.json"
            inbox.write_text(
                json.dumps({"type": "sms", "sender_id": "+15550001", "body": "hello"}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertIn("+15550001", stdout.getvalue())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), '{"messages": []}')

    def test_main_receive_consolidates_client_scoped_inboxes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "android-state.json"
            (Path(td) / "android-events.phone-a.jsonl").write_text(
                json.dumps({"type": "sms", "sender_id": "+15550001", "body": "hello"}) + "\n",
                encoding="utf-8",
            )
            (Path(td) / "android-events.phone-b.jsonl").write_text(
                json.dumps({"type": "sms", "sender_id": "+15550002", "body": "hi"}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["messages"]), 2)
            self.assertEqual({item["client_uid"] for item in payload["messages"]}, {"phone-a", "phone-b"})

    def test_termux_notification_collector_appends_only_new_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "applicationLabel": "Example Chat",
                        "id": 41,
                        "title": "Alice",
                        "content": "hello",
                        "when": "2026-03-18T09:01:00-04:00",
                    }
                ]
            )

            with patch("messaging_llm_bot.termux_notification_collector.which", return_value="/usr/bin/termux-notification-list"):
                with patch("messaging_llm_bot.termux_notification_collector.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout=payload, stderr="")
                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )

                    self.assertEqual(appended, 1)
                    written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(
                        written,
                        [
                            {
                                "type": "notification",
                                "package_name": "org.example.chat",
                                "app_name": "Example Chat",
                                "title": "Alice",
                                "body": "hello",
                                "timestamp": "2026-03-18T09:01:00-04:00",
                            }
                        ],
                    )

                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )
                    self.assertEqual(appended, 0)
                    run.assert_called_with(
                        ["termux-notification-list"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

    def test_termux_notification_collector_filters_packages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {"packageName": "org.keep", "title": "Wanted", "content": "collect"},
                    {"packageName": "org.drop", "title": "Noise", "content": "ignore"},
                ]
            )

            with patch("messaging_llm_bot.termux_notification_collector.which", return_value="/usr/bin/termux-notification-list"):
                with patch("messaging_llm_bot.termux_notification_collector.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout=payload, stderr="")
                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                        include_packages={"org.keep"},
                    )

            self.assertEqual(appended, 1)
            written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]["package_name"], "org.keep")

    def test_termux_notification_collector_uses_all_packages_when_filter_is_unspecified(self) -> None:
        self.assertIsNone(_normalize_include_packages(None))
        self.assertIsNone(_normalize_include_packages([]))
        self.assertIsNone(_normalize_include_packages({"", "   "}))


class RunClientTests(unittest.TestCase):
    def test_run_client_receive_reads_only_new_jsonl_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "android-state.json"
            inbox.write_text(json.dumps({"type": "sms", "sender_id": "+15550001", "body": "hello"}) + "\n", encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = run_client.main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertIn("+15550001", stdout.getvalue())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = run_client.main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), '{"messages": []}')

    def test_run_client_collect_notifications_once_appends_only_new_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "applicationLabel": "Example Chat",
                        "id": 41,
                        "title": "Alice",
                        "content": "hello",
                        "when": "2026-03-18T09:01:00-04:00",
                    }
                ]
            )

            with patch("run_client.which", return_value="/usr/bin/termux-notification-list"):
                with patch("run_client.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout=payload, stderr="")
                    appended = run_client.collect_notifications_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )
                    self.assertEqual(appended, 1)
                    appended = run_client.collect_notifications_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )
                    self.assertEqual(appended, 0)

            written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]["package_name"], "org.example.chat")

    def test_run_client_collect_notifications_dismisses_logged_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "id": 41,
                        "title": "Alice",
                        "content": "hello",
                    }
                ]
            )

            with patch("run_client.which", return_value="/usr/bin/termux"):
                with patch("run_client.subprocess.run") as run:
                    run.side_effect = [
                        Mock(returncode=0, stdout=payload, stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                    ]
                    appended = run_client.collect_notifications_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )

            self.assertEqual(appended, 1)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0], ["termux-notification-remove", "41"])

    def test_run_client_collect_notifications_dismisses_negative_ids_with_end_of_options(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "id": -1,
                        "title": "Alice",
                        "content": "hello",
                    }
                ]
            )

            with patch("run_client.which", return_value="/usr/bin/termux"):
                with patch("run_client.subprocess.run") as run:
                    run.side_effect = [
                        Mock(returncode=0, stdout=payload, stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                    ]
                    appended = run_client.collect_notifications_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )

            self.assertEqual(appended, 1)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0], ["termux-notification-remove", "--", "-1"])

    def test_run_client_collect_notifications_limits_inbox_to_latest_100_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            existing = [
                json.dumps({"type": "notification", "title": f"old-{index}"}, ensure_ascii=False)
                for index in range(100)
            ]
            inbox.write_text("\n".join(existing) + "\n", encoding="utf-8")
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "id": 42,
                        "title": "newest",
                        "content": "hello",
                    }
                ]
            )

            with patch("run_client.which", return_value="/usr/bin/termux"):
                with patch("run_client.subprocess.run") as run:
                    run.side_effect = [
                        Mock(returncode=0, stdout=payload, stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                    ]
                    appended = run_client.collect_notifications_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )

            self.assertEqual(appended, 1)
            written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(written), run_client.MAX_ANDROID_EVENT_LINES)
            self.assertEqual(written[0]["title"], "old-1")
            self.assertEqual(written[-1]["title"], "newest")

    def test_run_client_sync_once_pushes_pulls_and_flushes_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            outbox = Path(td) / "android-sms-outbox.jsonl"
            outbox_state = Path(td) / "android-sms-outbox-state.json"
            inbox.write_text("", encoding="utf-8")
            outbox.write_text(json.dumps({"recipient": "+15550001", "message": "queued"}) + "\n", encoding="utf-8")

            with patch("run_client.which", return_value="/usr/bin/fake"):
                with patch("run_client.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout="", stderr="")
                    delivered = run_client.sync_once(
                        local_inbox=inbox,
                        local_outbox=outbox,
                        client_uid="phone-a",
                        remote_host="server",
                        remote_dir="/srv/android",
                        ssh_key="/tmp/key",
                        sms_command="termux-sms-send",
                        outbox_state_path=outbox_state,
                        scp_command="scp",
                    )

            self.assertEqual(delivered, 1)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[0].args[0][-1], "server:/srv/android/android-events.phone-a.jsonl")
            self.assertEqual(run.call_args_list[1].args[0][4], "server:/srv/android/android-sms-outbox.phone-a.jsonl")


if __name__ == "__main__":
    unittest.main()
