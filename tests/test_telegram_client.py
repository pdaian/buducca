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
                        "message": {"chat": {"id": 9}, "voice": {"file_id": "voice-id"}},
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
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].text, "hello")
        self.assertEqual(updates[1].voice_file_id, "voice-id")
        self.assertEqual(updates[0].sender_id, "123")
        self.assertEqual(updates[0].sender_name, "Alice")
        self.assertEqual(updates[0].sender_contact, "Alice (@alice_tg)")

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
