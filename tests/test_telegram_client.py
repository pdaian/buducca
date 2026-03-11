import unittest

from messaging_llm_bot.telegram_client import TelegramClient


class FakeHttp:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, url, payload):
        self.calls.append(("post", url, payload))
        if url.endswith("/getUpdates"):
            return {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 9}, "from": {"id": 123, "first_name": "Alice", "username": "alice_tg"}, "text": "hello"}},
                    {
                        "update_id": 2,
                        "message": {
                            "chat": {"id": -1001, "title": "Release Channel"},
                            "sender_chat": {"id": -1001, "title": "Announcements", "username": "announcements"},
                            "text": "posted as channel",
                        },
                    },
                    {
                        "update_id": 3,
                        "message": {"chat": {"id": 9}, "voice": {"file_id": "voice-id"}},
                    },
                    {
                        "update_id": 4,
                        "message": {"chat": {"id": 9}, "audio": {"file_id": "audio-id", "mime_type": "audio/ogg"}},
                    },
                    {
                        "update_id": 5,
                        "message": {
                            "chat": {"id": 9},
                            "from": {"id": 123, "first_name": "Alice"},
                            "caption": "see attachment",
                            "document": {"file_id": "doc-id", "file_name": "report.pdf", "mime_type": "application/pdf"},
                            "date": 1710000000,
                        },
                    },
                ],
            }
        if url.endswith("/getFile"):
            return {"ok": True, "result": {"file_path": "voice/test.ogg"}}
        if url.endswith("/sendChatAction"):
            return {"ok": True}
        raise AssertionError(f"unexpected url: {url}")

    def get_bytes(self, url):
        self.calls.append(("get", url, None))
        return b"abc"


class TelegramClientTests(unittest.TestCase):
    def test_get_updates_supports_voice_and_text(self) -> None:
        client = TelegramClient(bot_token="token", http_client=FakeHttp())
        updates = client.get_updates()
        self.assertEqual(len(updates), 5)
        self.assertEqual(updates[0].text, "hello")
        self.assertEqual(updates[1].text, "posted as channel")
        self.assertEqual(updates[2].voice_file_id, "voice-id")
        self.assertEqual(updates[3].voice_file_id, "audio-id")
        self.assertEqual(updates[3].attachments, [])
        self.assertEqual(updates[4].text, "see attachment")
        self.assertEqual(len(updates[4].attachments), 1)
        self.assertEqual(updates[4].attachments[0].file_id, "doc-id")
        self.assertEqual(updates[4].attachments[0].filename, "report.pdf")
        self.assertIsNotNone(updates[4].sent_at)
        self.assertEqual(updates[0].sender_id, "123")
        self.assertEqual(updates[0].sender_name, "Alice")
        self.assertEqual(updates[0].sender_contact, "Alice (@alice_tg)")
        self.assertEqual(updates[1].sender_id, "-1001")
        self.assertEqual(updates[1].sender_name, "Announcements")
        self.assertEqual(updates[1].sender_contact, "Announcements (@announcements)")
        self.assertEqual(updates[1].conversation_name, "Release Channel")

    def test_send_file_uses_multipart_upload(self) -> None:
        class MultipartHttp(FakeHttp):
            def post_multipart(self, url, *, fields, files):
                self.calls.append(("multipart", url, fields, sorted(files)))
                return {"ok": True}

        http = MultipartHttp()
        client = TelegramClient(bot_token="token", http_client=http)

        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".txt") as handle:
            handle.write(b"hello")
            handle.flush()
            client.send_file(99, handle.name, caption="note")

        self.assertEqual(http.calls[-1][0], "multipart")
        self.assertEqual(http.calls[-1][1], "https://api.telegram.org/bottoken/sendDocument")

    def test_get_file_and_download(self) -> None:
        http = FakeHttp()
        client = TelegramClient(bot_token="token", http_client=http)
        path = client.get_file_path("voice-id")
        data = client.download_file(path)
        self.assertEqual(path, "voice/test.ogg")
        self.assertEqual(data, b"abc")

    def test_send_typing_action(self) -> None:
        http = FakeHttp()
        client = TelegramClient(bot_token="token", http_client=http)

        client.send_typing_action(99)

        self.assertEqual(http.calls[-1], ("post", "https://api.telegram.org/bottoken/sendChatAction", {"chat_id": 99, "action": "typing"}))


if __name__ == "__main__":
    unittest.main()
