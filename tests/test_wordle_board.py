"""Tests for wordle_board.py (no network)."""
import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import wordle_board as wb  # noqa: E402

SUMMARY = ("**Your group is on a 184 day streak!** 🔥🔥🔥 Here are yesterday's results:\n"
           "👑 3/6: <@1>\n4/6: @TTV\\_Some Nick <@2>\nX/6: <@3>")


def summary(id, text, ts="2026-10-15T06:16:05+00:00"):
    return {"id": id, "author": {"id": wb.WORDLE_BOT_ID}, "content": text, "timestamp": ts}


class Parse(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(wb.parse_summary(SUMMARY), [
            (3, True, ["1"], []), (4, False, ["2"], ["TTV_Some Nick"]), (7, False, ["3"], [])])

    def test_day_follows_the_streak_not_the_post_time(self):
        chan = {"last_day": "2026-08-19", "last_streak": 128}
        late = summary("9", SUMMARY.replace("184", "129"), "2026-08-20T23:47:14+00:00")
        self.assertEqual(wb.summary_day(late, chan), "2026-08-20")
        self.assertEqual(chan["last_streak"], 129)

    def test_first_summary_is_yesterday(self):
        self.assertEqual(wb.summary_day(summary("9", SUMMARY), {}), "2026-10-14")

    def test_an_streak(self):
        self.assertTrue(wb.SUMMARY.search("**Your group is on an 89 day streak!** Here are yesterday's results:"))


class Standings(unittest.TestCase):
    def test_points_crowns_and_left_members(self):
        state = {"days": {"2026-10-13": {"1": [3, 1], "2": [7, 0], "9": [1, 1]},
                          "2026-10-14": {"1": [5, 0], "2": [2, 1]}},
                 "names": {"1": "One", "2": "Two", "9": None}}
        rows = wb.standings(state, list(state["days"]))
        self.assertEqual([(r["name"], r["points"]) for r in rows], [("One", 7), ("Two", 6)])
        self.assertEqual(rows[1]["solved"], 0.5)
        self.assertEqual(rows[0]["streak"], 2)
        self.assertEqual(rows[0]["dist"], [0, 0, 1, 0, 1, 0, 0])


class Update(unittest.TestCase):
    def run_update(self, board_posts, messages):
        calls, sent = [], []

        def discord(method, path, data=None):
            calls.append((method, path))
            if path == "/users/@me":
                return {"id": "900"}
            if path.startswith("/channels/1/messages?limit=100"):
                return messages
            if path.startswith("/channels/50/messages"):
                return board_posts
            if "/members/" in path and method == "GET":
                return {"user": {"id": "x", "username": "u", "global_name": "Name"}, "roles": []}
            return {}

        def send(_stats, method, path, message, files):
            sent.append((method, path, message, files))
            return {"id": "new"}

        stats = types.SimpleNamespace(discord=discord, GUILD_ID="111")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(wb, "STATE_FILE", os.path.join(tmp, "wordle.json")), \
                mock.patch.object(wb, "WORDLE_CHANNEL_ID", "50"), \
                mock.patch.object(wb, "WORDLE_ROLE_ID", "77"), \
                mock.patch.object(wb, "SOURCE_CHANNELS", ["1"]), \
                mock.patch.object(wb, "picture", lambda state, month: b"png"), \
                mock.patch.object(wb, "find_by_name", lambda stats, name: "4"), \
                mock.patch("socials_board.send", send), \
                contextlib.redirect_stdout(io.StringIO()):
            state = wb.load_state()
            state["channels"]["1"] = {"after": "5"}
            wb.save_state(state)
            wb.update(stats)
            state = wb.load_state()
        return calls, sent, state

    def test_new_summary_gives_roles_and_posts_the_board(self):
        chat = {"id": "11", "author": {"id": "123"}, "content": "hi"}
        calls, sent, state = self.run_update([], [summary("10", SUMMARY), chat])
        self.assertEqual(state["days"]["2026-10-14"]["4"], [4, 0])
        self.assertEqual(state["channels"]["1"]["after"], "10")  # chat lines don't move it
        roles = sorted(p for m, p in calls if m == "PUT")
        self.assertEqual(roles, [f"/guilds/111/members/{i}/roles/77" for i in "1234"])
        self.assertEqual(sent[0][0], "POST")
        self.assertTrue(sent[0][3][0][0].startswith("wordle-2026-10-"))

    def test_existing_month_post_is_edited(self):
        old = {"id": "8", "author": {"id": "900"}, "attachments": [{"filename": "wordle-2026-10-old.png"}]}
        _, sent, _ = self.run_update([old], [summary("10", SUMMARY)])
        self.assertEqual(sent[0][:2], ("PATCH", "/channels/50/messages/8"))


if __name__ == "__main__":
    unittest.main()
