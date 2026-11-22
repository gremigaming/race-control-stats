import datetime
import json
import unittest

from bot import prestream as p

UTC = datetime.timezone.utc
START = datetime.datetime(2026, 11, 22, 14, 0, tzinfo=UTC)  # 15:00 Amsterdam
TWITCH = "https://twitch.tv/gremi_gaming"
EVENTS = [(1, START, "F1 26 LOBBIES | RANDOM GRID", TWITCH),
          (2, START, "Community race", "Discord voice")]
EMPTY = {"sent": {}, "used": {}, "tries": {}}


class PrestreamTests(unittest.TestCase):
    def test_due_one_hour_before_only_for_twitch(self):
        self.assertEqual(p.due(EVENTS, EMPTY, START - datetime.timedelta(minutes=70)), [])
        got = p.due(EVENTS, EMPTY, START - datetime.timedelta(minutes=60))
        self.assertEqual([e[2] for e in got], ["F1 26 LOBBIES | RANDOM GRID"])
        self.assertEqual(len(p.due(EVENTS, EMPTY, START - datetime.timedelta(minutes=5))), 1)
        self.assertEqual(p.due(EVENTS, EMPTY, START), [])  # started: too late

    def test_sent_once_and_again_after_a_reschedule(self):
        state = {"sent": {p.event_key(1, START): "x"}}
        now = START - datetime.timedelta(minutes=30)
        self.assertEqual(p.due(EVENTS, state, now), [])
        moved = START + datetime.timedelta(hours=1)
        self.assertEqual(len(p.due([(1, moved, "x", TWITCH)], state, moved - p.LEAD)), 1)

    def test_gives_up_after_max_tries(self):
        state = {"tries": {p.event_key(1, START): p.MAX_TRIES}}
        self.assertEqual(p.due(EVENTS, state, START - p.LEAD), [])

    def test_content_only_for_its_day(self):
        content = {"date": "2026-11-22", "topic": "t"}
        self.assertEqual(p.content_for(content, datetime.date(2026, 11, 22)), content)
        self.assertEqual(p.content_for(content, datetime.date(2026, 11, 25)), {})
        self.assertEqual(p.content_for(None, datetime.date(2026, 11, 22)), {})

    def test_challenge_rotation(self):
        chs = p.load_challenges()
        self.assertGreaterEqual(len(chs), 20)
        self.assertEqual(len({c["name"] for c in chs}), len(chs))
        self.assertEqual(p.pick_challenge(chs, {}), chs[0])
        used = {c["name"]: i + 1 for i, c in enumerate(chs)}
        used[chs[3]["name"]] = 0.5
        self.assertEqual(p.pick_challenge(chs, used), chs[3])
        self.assertEqual(p.pick_challenge(chs, {}, "pit lane start")["name"], "PIT LANE START")

    def test_messages_with_and_without_prep(self):
        ch = p.load_challenges()[0]
        full = {"news": "n", "source": "https://x", "topic": "t", "line_a": "A", "line_b": "B"}
        msgs = p.messages("Race", START, full, ch, 1)
        self.assertEqual(msgs[1:], ["A", "B"])
        self.assertIn("15:00 Race", msgs[0])
        self.assertIn("<https://x>", msgs[0])
        self.assertIn(ch["name"], msgs[0])
        bare = p.messages("Race", START, {}, ch, 1)
        self.assertEqual(len(bare), 2)
        self.assertIn(bare[1], p.FALLBACK_LINES)
        self.assertNotIn("News", bare[0])
        for m in msgs + bare:
            self.assertLess(len(m), 2000)

    def test_fallback_lines_fit_twitch(self):
        for line in p.FALLBACK_LINES:
            self.assertLess(len(line), 60)

    def test_committed_prep_file_is_valid(self):
        data = json.loads((p.HERE / "prestream.json").read_text(encoding="utf-8"))
        self.assertIn("date", data)
        for key in ("line_a", "line_b"):
            if data.get(key):
                self.assertLess(len(data[key]), 60)


if __name__ == "__main__":
    unittest.main()
