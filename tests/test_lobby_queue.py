import unittest

from bot import lobby_queue as lq


class InterestTest(unittest.TestCase):
    def setUp(self):
        self.config = lq.load_config()

    def test_wants_to_join(self):
        for text in ["can i join?", "how do i join the lobby", "mag ik meedoen?", "can you invite me next race",
                     "what's your ea id", "can I race with you", "is the lobby full?", "kan ik mee racen",
                     "add me pls", "send me an invite"]:
            self.assertTrue(lq.interested(self.config, text), text)

    def test_not_interest(self):
        for text in ["just joined the stream", "join the discord", "can i join the discord?", "nice lobby",
                     "i joined yesterday", "what a race", "lobby music is great?", "hello everyone"]:
            self.assertFalse(lq.interested(self.config, text), text)

    def test_reply_by_game(self):
        self.assertIn("EA", lq.reply_text(self.config, "F1 25", "sam"))
        self.assertIn("LMU", lq.reply_text(self.config, "Le Mans Ultimate", "sam"))
        self.assertTrue(lq.reply_text(self.config, None, "sam").startswith("@sam"))


class WaitlistTest(unittest.TestCase):
    def setUp(self):
        self.config = dict(lq.load_config(), size=3)
        self.state = {"entries": []}

    def say(self, user, text, now, platform="twitch"):
        return lq.handle(self.config, self.state, platform, user, user, text, "F1 25", now)

    def test_added_once_and_answered_once(self):
        self.assertIsNotNone(self.say("sam", "can i join?", 100))
        self.assertIsNone(self.say("sam", "can i join please?", 200))
        self.assertEqual(len(self.state["entries"]), 1)

    def test_back_after_dropping_out_of_last_n(self):
        self.say("sam", "can i join?", 100)
        for i, who in enumerate(["a", "b", "c"]):
            self.say(who, "can i join?", 110 + i)
        self.assertIsNotNone(self.say("sam", "can i join?", 200))
        self.assertEqual([e["name"] for e in lq.recent(self.config, self.state["entries"], 200)], ["b", "c", "sam"])

    def test_command_always_answered_after_cooldown(self):
        self.assertIsNotNone(self.say("sam", "!join", 100))
        self.assertIsNone(self.say("sam", "!join", 120))
        self.assertIsNotNone(self.say("sam", "!ea", 200))
        self.assertEqual(len(self.state["entries"]), 1)

    def test_other_chat_ignored(self):
        self.assertIsNone(self.say("sam", "great overtake", 100))
        self.assertEqual(self.state["entries"], [])

    def test_platforms_separate_and_old_entries_hidden(self):
        self.say("sam", "can i join?", 100)
        self.assertIsNotNone(self.say("sam", "can i join?", 101, "youtube"))
        later = 100 + self.config["keep_hours"] * 3600 + 10
        self.assertEqual(lq.public(self.state, self.config, later)["entries"], [])


if __name__ == "__main__":
    unittest.main()
