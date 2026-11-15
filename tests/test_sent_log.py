import pathlib
import tempfile
import types
import unittest

from bot import sent_log


class SentLogTest(unittest.TestCase):
    def setUp(self):
        self.path = pathlib.Path(tempfile.mkdtemp()) / "sent.json"

    def test_record_and_recent(self):
        sent_log.record("twitch", "  hello\n chat ", now=1000, path=self.path)
        sent_log.record("discord", "long post", channel="#giveaways", now=1000, path=self.path)
        items = sent_log.recent(now=1001, path=self.path)
        self.assertEqual([e["where"] for e in items], ["twitch", "discord"])
        self.assertEqual(items[0]["text"], "hello chat")
        self.assertEqual(items[1]["text"], "")            # Discord: no text, only that it was sent
        self.assertLess(items[0]["id"], items[1]["id"])   # unique even in the same millisecond
        self.assertEqual(sent_log.recent(now=1000 + 4000, path=self.path), [])

    def test_discord_channels(self):
        ch = lambda n: types.SimpleNamespace(guild=object(), channel=types.SimpleNamespace(name=n))
        sent_log.record_discord(ch("giveaways"), now=5, path=self.path)
        sent_log.record_discord(ch("logs"), now=5, path=self.path)
        sent_log.record_discord(types.SimpleNamespace(guild=None, channel=None), now=5, path=self.path)
        self.assertEqual([e["channel"] for e in sent_log.read(self.path)], ["#giveaways", "DM"])

    def test_never_raises(self):
        sent_log.record("twitch", "x", path=pathlib.Path("/proc/nope/sent.json"))


if __name__ == "__main__":
    unittest.main()
