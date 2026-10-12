import unittest

from bot import twitch_chat as tc

CONFIG = {"only_live": True, "gap": 10, "timers": [
    {"text": "discord", "first": 15, "every": 45},
    {"text": "site", "first": 30, "every": 60},
]}


class SealTests(unittest.TestCase):
    def test_round_trip_needs_the_same_bot_token(self):
        login = {"client_id": "a", "client_secret": "b", "refresh_token": "c"}
        text = tc.seal(login, "discord-token")
        self.assertNotIn("refresh", text)
        self.assertEqual(tc.unseal(text, "discord-token"), login)
        with self.assertRaises(Exception):
            tc.unseal(text, "other-token")


class TimerTests(unittest.TestCase):
    def test_quiet_while_offline(self):
        self.assertIsNone(tc.next_timer(CONFIG, None, {}, 0, 10_000))

    def test_waits_for_first(self):
        self.assertIsNone(tc.next_timer(CONFIG, 10, {}, 0, 10_000))
        self.assertEqual(tc.next_timer(CONFIG, 16, {}, 0, 10_000), 0)

    def test_gap_between_messages(self):
        now = 100_000
        self.assertIsNone(tc.next_timer(CONFIG, 31, {0: now - 60}, now - 60, now))
        self.assertEqual(tc.next_timer(CONFIG, 41, {0: now - 600}, now - 600, now), 1)

    def test_repeats_after_every(self):
        now = 100_000
        sent = {0: now - 46 * 60, 1: now - 20 * 60}
        self.assertEqual(tc.next_timer(CONFIG, 90, sent, now - 20 * 60, now), 0)
        sent[0] = now - 30 * 60
        self.assertIsNone(tc.next_timer(CONFIG, 90, sent, now - 20 * 60, now))

    def test_real_timers_file_loads(self):
        config = tc.load_timers()
        self.assertTrue(config["timers"])
        for t in config["timers"]:
            self.assertLessEqual(len(t["text"]), 500)
            self.assertGreater(t["every"], 0)


if __name__ == "__main__":
    unittest.main()
