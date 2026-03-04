import unittest

from telegram_llm_bot.telegram_client import TelegramClient


class FakeHttp:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, url, payload):
        self.calls.append(("post", url, payload))
        if url.endswith("/getUpdates"):
            return {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 9}, "text": "hello"}},
                    {
                        "update_id": 2,
                        "message": {"chat": {"id": 9}, "voice": {"file_id": "voice-id"}},
                    },
                ],
            }
        if url.endswith("/getFile"):
            return {"ok": True, "result": {"file_path": "voice/test.ogg"}}
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

    def test_get_file_and_download(self) -> None:
        http = FakeHttp()
        client = TelegramClient(bot_token="token", http_client=http)
        path = client.get_file_path("voice-id")
        data = client.download_file(path)
        self.assertEqual(path, "voice/test.ogg")
        self.assertEqual(data, b"abc")


if __name__ == "__main__":
    unittest.main()
