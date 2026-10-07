"""Tests for leaderboard_post.py (no network)."""
import contextlib
import io
import json
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import leaderboard_post as lp  # noqa: E402

ME = "900"


def post(id, filename, author=ME):
    return {"id": id, "author": {"id": author}, "attachments": [{"filename": filename}]}


class Update(unittest.TestCase):
    def run_update(self, card, messages):
        sent = []
        stats = types.SimpleNamespace(UA="test")

        def discord(method, path, data=None):
            if path == "/users/@me":
                return {"id": ME}
            return messages

        stats.discord = discord

        def fetch(_stats, path):
            return json.dumps(card).encode() if path.startswith("discord/leaderboard.json") else b"png"

        def send(_stats, method, path, message, files):
            sent.append((method, path, message, files))
            return {"id": "new"}

        with mock.patch.object(lp, "LEADERBOARD_CHANNEL_ID", "42"), \
                mock.patch.object(lp, "fetch", fetch), \
                mock.patch("socials_board.send", send), \
                contextlib.redirect_stdout(io.StringIO()):
            lp.update(stats)
        return sent

    def test_first_post_of_the_month(self):
        sent = self.run_update({"month": "2026-10", "hash": "abc", "races": 3},
                               [post("1", "leaderboard-2026-09-old.png")])
        self.assertEqual(sent[0][0], "POST")
        self.assertIn("October 2026", sent[0][2]["content"])
        self.assertEqual(sent[0][3][0][0], "leaderboard-2026-10-abc.png")

    def test_new_standings_edit_this_months_post(self):
        sent = self.run_update({"month": "2026-10", "hash": "new", "races": 4},
                               [post("7", "leaderboard-2026-10-old.png")])
        self.assertEqual(sent[0][:2], ("PATCH", "/channels/42/messages/7"))

    def test_unchanged_standings_send_nothing(self):
        sent = self.run_update({"month": "2026-10", "hash": "same", "races": 4},
                               [post("7", "leaderboard-2026-10-same.png")])
        self.assertEqual(sent, [])

    def test_new_month_without_races_keeps_last_post(self):
        sent = self.run_update({"month": "2026-11", "hash": "x", "races": 0},
                               [post("7", "leaderboard-2026-10-same.png")])
        self.assertEqual(sent, [])

    def test_other_peoples_posts_are_ignored(self):
        sent = self.run_update({"month": "2026-10", "hash": "abc", "races": 2},
                               [post("8", "leaderboard-2026-10-abc.png", author="1")])
        self.assertEqual(sent[0][0], "POST")

    def test_off_without_a_channel(self):
        stats = types.SimpleNamespace()
        with mock.patch.object(lp, "LEADERBOARD_CHANNEL_ID", ""):
            self.assertIsNone(lp.update(stats))


if __name__ == "__main__":
    unittest.main()
