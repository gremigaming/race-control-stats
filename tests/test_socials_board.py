"""Tests for socials_board.py with simulated sources (no network)."""
import contextlib
import io
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import socials_board  # noqa: E402

STREAM = {"title": " Spa 6h, stint 2 ", "game_name": "Le Mans Ultimate",
          "viewer_count": 1234, "started_at": "2026-10-06T18:00:00Z",
          "thumbnail_url": "https://static-cdn.jtvnw.net/x-{width}x{height}.jpg"}


def fake_stats(stream=None, twitch=1639, youtube=2690, tiktok=3904, board=None,
               tiktok_error=None):
    calls = []

    def tiktok_followers():
        if tiktok_error:
            raise RuntimeError(tiktok_error)
        return tiktok

    def discord(method, path, data=None):
        calls.append((method, path, data))
        if path == "/users/@me":
            return {"id": "bot"}
        if path == "/guilds/111":
            return {"id": "111", "icon": "abc"}
        if path.endswith("/messages?limit=50"):
            old = {"id": "1", "author": {"id": "someone"}, "content": "links", "embeds": []}
            return [board, old] if board else [old]
        if method == "PATCH":
            return {}
        raise AssertionError(f"unexpected Discord call {method} {path}")

    return types.SimpleNamespace(
        twitch_followers=lambda: twitch, youtube_subscribers=lambda: youtube,
        tiktok_followers=tiktok_followers, twitch_stream=lambda: stream,
        discord=discord, calls=calls, GUILD_ID="111")


def posted(message):
    """The message as Discord would return it after posting."""
    return {"id": "99", "author": {"id": "bot"}, **message}


class BoardTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", "555")
        patcher.start()
        self.addCleanup(patcher.stop)

    def update(self, stats):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            socials_board.update(stats)
        return out.getvalue()

    def test_offline_board(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_CHANNEL_ID": "UCgremi"}):
            msg = socials_board.build(fake_stats())
        embed = msg["embeds"][0]
        self.assertIn("Offline", embed["description"])
        self.assertEqual(embed["color"], socials_board.RED)
        values = [f["value"] for f in embed["fields"]]
        self.assertEqual(values, ["**1,639**\nfollowers", "**2,690**\nsubscribers",
                                  "**3,904**\nfollowers"])
        self.assertTrue(embed["footer"]["text"].endswith("8,233 total"))
        self.assertEqual(embed["author"]["icon_url"],
                         "https://cdn.discordapp.com/icons/111/abc.png?size=128")
        self.assertNotIn("image", embed)
        labels = [b["label"] for b in msg["components"][0]["components"]]
        self.assertEqual(labels, ["Twitch", "YouTube", "TikTok"])
        self.assertEqual(msg["allowed_mentions"], {"parse": []})

    def test_live_board(self):
        embed = socials_board.build(fake_stats(stream=STREAM))["embeds"][0]
        self.assertIn("Live now", embed["description"])
        self.assertIn("> Spa 6h, stint 2", embed["description"])
        self.assertIn("Le Mans Ultimate", embed["description"])
        self.assertIn("1,234 watching", embed["description"])
        self.assertEqual(embed["color"], socials_board.PURPLE)
        self.assertEqual(embed["image"]["url"],
                         "https://static-cdn.jtvnw.net/x-1280x720.jpg?s=20261006180000")

    def test_failing_source_shows_placeholder(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            embed = socials_board.build(fake_stats(tiktok_error="down"))["embeds"][0]
        tiktok = next(f for f in embed["fields"] if "TikTok" in f["name"])
        self.assertEqual(tiktok["value"], "**\u2014**\nfollowers")
        self.assertTrue(embed["footer"]["text"].endswith("4,329 total"))

    def test_no_youtube_button_without_channel(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_CHANNEL_ID": "", "YOUTUBE_URL": ""}):
            msg = socials_board.build(fake_stats())
        labels = [b["label"] for b in msg["components"][0]["components"]]
        self.assertEqual(labels, ["Twitch", "TikTok"])

    def test_not_posted_yet_is_skipped(self):
        stats = fake_stats()
        out = self.update(stats)
        self.assertIn("not posted yet", out)
        self.assertFalse([c for c in stats.calls if c[0] != "GET"])

    def test_unchanged_board_is_not_edited(self):
        board = posted(socials_board.build(fake_stats()))
        board["embeds"][0]["timestamp"] = "2020-01-01T00:00:00+00:00"
        stats = fake_stats(board=board)
        self.assertIn("unchanged: socials board", self.update(stats))
        self.assertFalse([c for c in stats.calls if c[0] == "PATCH"])

    def test_changed_numbers_edit_the_board(self):
        board = posted(socials_board.build(fake_stats(twitch=1600)))
        stats = fake_stats(board=board)
        self.assertIn("updated:   socials board", self.update(stats))
        patches = [c for c in stats.calls if c[0] == "PATCH"]
        self.assertEqual(patches[0][1], "/channels/555/messages/99")

    def test_going_live_edits_the_board(self):
        board = posted(socials_board.build(fake_stats()))
        stats = fake_stats(stream=STREAM, board=board)
        self.assertIn("updated:", self.update(stats))

    def test_new_total_edits_the_board(self):
        board = posted(socials_board.build(fake_stats()))
        stats = fake_stats(youtube=2700, board=board)
        self.assertIn("updated:", self.update(stats))

    def test_switched_off_without_channel(self):
        stats = fake_stats()
        with mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", ""):
            self.update(stats)
        self.assertEqual(stats.calls, [])


if __name__ == "__main__":
    unittest.main()
