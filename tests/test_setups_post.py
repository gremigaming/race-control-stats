"""Tests for setups_post.py (no network)."""
import contextlib
import io
import json
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import setups_post as sp  # noqa: E402

ME = "900"
MANIFEST = {"tracks": [
    {"post": "Belgium", "cards": [{"kind": "race", "picture": "setup-belgium-race.png", "hash": "aa"},
                                  {"kind": "quali", "picture": "setup-belgium-quali.png", "hash": "bb"}]},
    {"post": "Madrid", "cards": [{"kind": "dry", "picture": "setup-madrid-dry.png", "hash": "cc",
                                  "placeholder": True}]},
]}


class Update(unittest.TestCase):
    def run_update(self, messages, archived=False):
        calls, sent = [], []
        stats = types.SimpleNamespace(UA="test", GUILD_ID="111")

        def discord(method, path, data=None):
            calls.append((method, path, data))
            if path == "/users/@me":
                return {"id": ME}
            if path.endswith("/threads/active"):
                return {"threads": []}
            if "/threads/archived/" in path:
                return {"threads": [{"id": "55", "name": "Belgium ",
                                     "thread_metadata": {"archived": archived}}], "has_more": False}
            return messages

        stats.discord = discord

        def fetch(_stats, path):
            return json.dumps(MANIFEST).encode() if path.startswith("discord/setups.json") else b"png"

        def send(_stats, method, path, message, files):
            sent.append((method, path, message, files))
            return {"id": "new"}

        with mock.patch.object(sp, "SETUPS_FORUM_ID", "42"), \
                mock.patch("leaderboard_post.fetch", fetch), \
                mock.patch("socials_board.send", send), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            sp.update(stats)
        return calls, sent, out.getvalue()

    def test_first_post_and_missing_post_skipped(self):
        _, sent, out = self.run_update([])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][:2], ("POST", "/channels/55/messages"))
        self.assertEqual([f[0] for f in sent[0][3]],
                         ["setup-belgium-race-aa.png", "setup-belgium-quali-bb.png"])
        self.assertIn("Dry race, dry quali", sent[0][2]["content"])
        self.assertIn("no forum post for Madrid", out)

    def test_unchanged_cards_send_nothing(self):
        msg = {"id": "7", "author": {"id": ME}, "attachments": [
            {"filename": "setup-belgium-quali-bb.png"}, {"filename": "setup-belgium-race-aa.png"}]}
        _, sent, _ = self.run_update([msg])
        self.assertEqual(sent, [])

    def test_changed_card_edits_and_rearchives(self):
        msg = {"id": "7", "author": {"id": ME}, "attachments": [{"filename": "setup-belgium-race-old.png"}]}
        calls, sent, _ = self.run_update([msg], archived=True)
        self.assertEqual(sent[0][:2], ("PATCH", "/channels/55/messages/7"))
        patches = [c for c in calls if c[0] == "PATCH"]
        self.assertEqual([p[2] for p in patches], [{"archived": False}, {"archived": True}])

    def test_placeholder_text(self):
        msg = sp.message_for(MANIFEST["tracks"][1])
        self.assertIn("No setup for this track yet", msg["content"])

    def test_off_without_forum_id(self):
        stats = types.SimpleNamespace()
        with mock.patch.object(sp, "SETUPS_FORUM_ID", ""):
            self.assertIsNone(sp.update(stats))


if __name__ == "__main__":
    unittest.main()
