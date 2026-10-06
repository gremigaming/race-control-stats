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


class FakeHttp:
    def __init__(self, vod=True, banner="https://img/banner.png", yt_video=True,
                 processing=False):
        self.vod, self.banner, self.yt_video, self.processing = vod, banner, yt_video, processing

    def __call__(self, method, url, headers=None, data=None, form=False):
        if "/helix/users" in url:
            return {"data": [{"id": "42", "profile_image_url": "https://img/avatar.png",
                              "offline_image_url": self.banner}]}
        if "/helix/videos" in url:
            if not self.vod:
                return {"data": []}
            thumb = ("https://vod-secure.twitch.tv/_404/404_processing_%{width}x%{height}.png"
                     if self.processing else "https://img/vod-%{width}x%{height}.jpg")
            return {"data": [{"title": "Monza league race", "url": "https://twitch.tv/videos/9",
                              "thumbnail_url": thumb}]}
        if "/youtube/v3/channels" in url:
            return {"items": [{"snippet": {"thumbnails": {"high": {"url": "https://img/yt.png"}}},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if "/youtube/v3/playlistItems" in url:
            if not self.yt_video:
                return {"items": []}
            return {"items": [{"snippet": {
                "title": "My first Le Mans", "resourceId": {"videoId": "abc"},
                "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abc/hq.jpg"},
                               "maxres": {"url": "https://i.ytimg.com/vi/abc/max.jpg"}}}}]}
        raise AssertionError(f"unexpected HTTP call {method} {url}")


def fake_stats(stream=None, twitch=1639, youtube=2690, tiktok=3904, board=None,
               tiktok_error=None, http=None, configured=True):
    calls = []

    def tiktok_followers():
        if tiktok_error:
            raise RuntimeError(tiktok_error)
        return tiktok

    def discord(method, path, data=None):
        calls.append((method, path, data))
        if path == "/users/@me":
            return {"id": "bot"}
        if path.endswith("/messages?limit=50"):
            old = {"id": "1", "author": {"id": "someone"}, "content": "links", "embeds": []}
            return [board, old] if board else [old]
        if method == "PATCH":
            return {}
        raise AssertionError(f"unexpected Discord call {method} {path}")

    return types.SimpleNamespace(
        twitch_followers=lambda: twitch, youtube_subscribers=lambda: youtube,
        tiktok_followers=tiktok_followers, twitch_stream=lambda: stream,
        twitch_headers=lambda: {"Client-Id": "x"} if configured else None,
        http=http or FakeHttp(), discord=discord, calls=calls,
        TWITCH_LOGIN="gremi_gaming", YOUTUBE_API_KEY="yt-key" if configured else "",
        YOUTUBE_CHANNEL_ID="UCgremi" if configured else "")


def posted(message):
    """The message as Discord would return it after posting."""
    return {"id": "99", "author": {"id": "bot"}, **message}


class BoardTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", "555"),
                        mock.patch.dict(os.environ, {"YOUTUBE_CHANNEL_ID": "UCgremi",
                                                     "YOUTUBE_URL": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self, stats):
        with contextlib.redirect_stdout(io.StringIO()):
            return socials_board.build(stats)

    def update(self, stats):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            socials_board.update(stats)
        return out.getvalue()

    def test_one_coloured_card_per_platform(self):
        msg = self.build(fake_stats())
        self.assertEqual(msg["content"], socials_board.HEADER)
        twitch, youtube, tiktok = msg["embeds"]
        self.assertEqual([c["color"] for c in msg["embeds"]],
                         [socials_board.TWITCH_PURPLE, socials_board.YOUTUBE_RED,
                          socials_board.TIKTOK_CYAN])
        self.assertIn("**1,639** followers", twitch["description"])
        self.assertIn("**2,690** subscribers", youtube["description"])
        self.assertIn("**3,904** followers", tiktok["description"])
        labels = [b["label"] for b in msg["components"][0]["components"]]
        self.assertEqual(labels, ["Twitch", "YouTube", "TikTok"])
        self.assertEqual(msg["allowed_mentions"], {"parse": []})

    def test_offline_twitch_shows_last_stream(self):
        twitch = self.build(fake_stats())["embeds"][0]
        self.assertIn("Offline", twitch["description"])
        self.assertIn("[Monza league race](https://twitch.tv/videos/9)", twitch["description"])
        self.assertEqual(twitch["image"]["url"], "https://img/vod-1280x720.jpg")
        self.assertEqual(twitch["thumbnail"]["url"], "https://img/avatar.png")

    def test_offline_twitch_falls_back_to_banner(self):
        for http in (FakeHttp(vod=False), FakeHttp(processing=True)):
            twitch = self.build(fake_stats(http=http))["embeds"][0]
            self.assertEqual(twitch["image"]["url"], "https://img/banner.png")

    def test_live_twitch(self):
        msg = self.build(fake_stats(stream=STREAM))
        twitch = msg["embeds"][0]
        self.assertIn("Live now", twitch["description"])
        self.assertIn("> Spa 6h, stint 2", twitch["description"])
        self.assertIn("1,234 watching", twitch["description"])
        self.assertNotIn("Last stream", twitch["description"])
        self.assertEqual(twitch["image"]["url"],
                         "https://static-cdn.jtvnw.net/x-1280x720.jpg?s=20261006180000")
        self.assertEqual(msg["components"][0]["components"][0]["label"], "Watch live")

    def test_youtube_latest_video(self):
        youtube = self.build(fake_stats())["embeds"][1]
        self.assertIn("[My first Le Mans](https://www.youtube.com/watch?v=abc)",
                      youtube["description"])
        self.assertEqual(youtube["image"]["url"], "https://i.ytimg.com/vi/abc/max.jpg")
        self.assertEqual(youtube["thumbnail"]["url"], "https://img/yt.png")

    def test_youtube_without_uploads(self):
        youtube = self.build(fake_stats(http=FakeHttp(yt_video=False)))["embeds"][1]
        self.assertNotIn("image", youtube)
        self.assertNotIn("Latest video", youtube["description"])

    def test_unconfigured_sources_still_build(self):
        msg = self.build(fake_stats(configured=False, twitch=None, youtube=None))
        self.assertEqual(len(msg["embeds"]), 3)
        self.assertIn("**—** followers", msg["embeds"][0]["description"])

    def test_failing_source_shows_placeholder(self):
        tiktok = self.build(fake_stats(tiktok_error="down"))["embeds"][2]
        self.assertIn("**—** followers", tiktok["description"])

    def test_detail_failure_keeps_the_card(self):
        def broken(*a, **k):
            raise RuntimeError("down")
        msg = self.build(fake_stats(http=broken))
        self.assertIn("**1,639** followers", msg["embeds"][0]["description"])
        self.assertNotIn("thumbnail", msg["embeds"][0])

    def test_long_titles_are_shortened(self):
        self.assertEqual(len(socials_board.short("x" * 200)), 90)
        self.assertEqual(socials_board.short(" a \n b "), "a b")

    def test_no_youtube_button_without_channel(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_CHANNEL_ID": "", "YOUTUBE_URL": ""}):
            msg = self.build(fake_stats())
        labels = [b["label"] for b in msg["components"][0]["components"]]
        self.assertEqual(labels, ["Twitch", "TikTok"])

    def test_not_posted_yet_is_skipped(self):
        stats = fake_stats()
        out = self.update(stats)
        self.assertIn("not posted yet", out)
        self.assertFalse([c for c in stats.calls if c[0] != "GET"])

    def test_unchanged_board_is_not_edited(self):
        board = posted(self.build(fake_stats()))
        stats = fake_stats(board=board)
        self.assertIn("unchanged: socials board", self.update(stats))
        self.assertFalse([c for c in stats.calls if c[0] == "PATCH"])

    def test_changed_numbers_edit_the_board(self):
        board = posted(self.build(fake_stats(twitch=1600)))
        stats = fake_stats(board=board)
        self.assertIn("updated:   socials board", self.update(stats))
        patches = [c for c in stats.calls if c[0] == "PATCH"]
        self.assertEqual(patches[0][1], "/channels/555/messages/99")

    def test_going_live_edits_the_board(self):
        board = posted(self.build(fake_stats()))
        stats = fake_stats(stream=STREAM, board=board)
        self.assertIn("updated:", self.update(stats))

    def test_old_single_card_board_is_found_and_replaced(self):
        old = {"id": "99", "author": {"id": "bot"}, "content": "",
               "embeds": [{"title": "Official channels", "color": 0xC8102E}]}
        stats = fake_stats(board=old)
        self.assertIn("updated:", self.update(stats))
        patch = [c for c in stats.calls if c[0] == "PATCH"][0]
        self.assertEqual(len(patch[2]["embeds"]), 3)

    def test_switched_off_without_channel(self):
        stats = fake_stats()
        with mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", ""):
            self.update(stats)
        self.assertEqual(stats.calls, [])


if __name__ == "__main__":
    unittest.main()
