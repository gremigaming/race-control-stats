import asyncio
import collections
import unittest

from bot import twitch_chat as tc
from bot import youtube_chat as yc


class FakeYouTube:
    def __init__(self):
        self.sent = collections.deque(maxlen=20)
        self.said = []

    async def say(self, text):
        self.sent.append(text)
        self.said.append(text)


def item(text, owner=False, mod=False):
    return {"snippet": {"displayMessage": text},
            "authorDetails": {"isChatOwner": owner, "isChatModerator": mod}}


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.twitch = []
        tc.ROOM, tc.SAY = tc.Room(), self.say_twitch
        self.config = {"timers": [{"text": "Simgasm GREMI10", "commands": ["!simgasm"]},
                                  {"text": "Join the Discord", "commands": ["!discord"]}]}
        self.load, tc.load_timers = tc.load_timers, lambda: self.config

    def tearDown(self):
        tc.ROOM, tc.SAY, tc.load_timers = None, None, self.load

    async def say_twitch(self, text):
        self.twitch.append(text)

    def test_simgasm_command_posts_on_both_and_counts_as_sent(self):
        yt = FakeYouTube()
        asyncio.run(yc.handle(yt, [item("!simgasm")], {}))
        self.assertEqual(yt.said, ["Simgasm GREMI10"])
        self.assertEqual(self.twitch, ["Simgasm GREMI10"])
        self.assertIn(0, tc.ROOM.commands)

    def test_other_commands_stay_on_youtube_and_own_messages_are_ignored(self):
        yt = FakeYouTube()
        yt.sent.append("Simgasm GREMI10")
        asyncio.run(yc.handle(yt, [item("!discord"), item("Simgasm GREMI10", owner=True),
                                   item("hi")], {}))
        self.assertEqual(yt.said, ["Join the Discord"])
        self.assertEqual(self.twitch, [])
        self.assertEqual(len(tc.ROOM.chat), 1)  # the viewer's chat line, not commands or ours


if __name__ == "__main__":
    unittest.main()
