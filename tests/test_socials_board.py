"""Tests for socials_board.py and board_image.py with simulated sources (no network)."""
import contextlib
import io
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import board_image  # noqa: E402
import socials_board  # noqa: E402

STREAM = {"title": "Spa 6h, stint 2", "game_name": "Le Mans Ultimate",
          "viewer_count": 1234, "started_at": "2026-10-06T18:00:00Z",
          "thumbnail_url": "https://static-cdn.jtvnw.net/x-{width}x{height}.jpg"}


def png():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (320, 180), (90, 40, 160)).save(out, "PNG")
    return out.getvalue()


class FakeHttp:
    def __init__(self, vod=True, yt_video=True, processing=False, tiktok=True,
                 tiktok_error=None):
        self.vod, self.yt_video, self.processing = vod, yt_video, processing
        self.tiktok, self.tiktok_error = tiktok, tiktok_error

    def __call__(self, method, url, headers=None, data=None, form=False):
        if "/helix/users" in url:
            return {"data": [{"id": "42", "offline_image_url": "https://img/banner.png"}]}
        if "/helix/videos" in url:
            if not self.vod:
                return {"data": []}
            thumb = ("https://vod-secure.twitch.tv/_404/404_processing_%{width}x%{height}.png"
                     if self.processing else "https://img/vod-%{width}x%{height}.jpg")
            return {"data": [{"id": "9", "title": "Monza league race",
                              "url": "https://twitch.tv/videos/9", "thumbnail_url": thumb}]}
        if "/youtube/v3/channels" in url:
            return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if "/youtube/v3/playlistItems" in url:
            if not self.yt_video:
                return {"items": []}
            return {"items": [{"snippet": {
                "title": "My first Le Mans", "resourceId": {"videoId": "abc"},
                "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/abc/mq.jpg"}}}}]}
        if "/video/list/" in url:
            assert method == "POST" and headers["Authorization"] == "Bearer tt-access"
            if self.tiktok_error:
                return {"error": {"code": self.tiktok_error}}
            videos = [{"id": "77", "title": "", "video_description": "Last lap at Spa",
                       "share_url": "https://www.tiktok.com/@x/video/77",
                       "cover_image_url": "https://p16.tiktokcdn.com/cover.jpg?x-expires=1"}]
            return {"data": {"videos": videos if self.tiktok else []},
                    "error": {"code": "ok"}}
        raise AssertionError(f"unexpected HTTP call {method} {url}")


def fake_stats(stream=None, twitch=1639, youtube=2690, tiktok=3904, board=None,
               http=None, configured=True, tiktok_token="tt-access"):
    calls = []

    def discord(method, path, data=None):
        calls.append((method, path, data))
        if path == "/users/@me":
            return {"id": "bot"}
        if path.endswith("/messages?limit=50"):
            old = {"id": "1", "author": {"id": "someone"}, "content": "links", "embeds": []}
            return [board, old] if board else [old]
        raise AssertionError(f"unexpected Discord call {method} {path}")

    return types.SimpleNamespace(
        twitch_followers=lambda: twitch, youtube_subscribers=lambda: youtube,
        tiktok_followers=lambda: tiktok, twitch_stream=lambda: stream,
        twitch_headers=lambda: {"Client-Id": "x"} if configured else None,
        http=http or FakeHttp(), discord=discord, calls=calls,
        _tiktok_access={"token": tiktok_token} if tiktok_token else {},
        TIKTOK_API="https://open.tiktokapis.com/v2",
        TWITCH_LOGIN="gremi_gaming", YOUTUBE_API_KEY="yt-key" if configured else "",
        YOUTUBE_CHANNEL_ID="UCgremi" if configured else "")


class BoardTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", "555"),
                        mock.patch.dict(os.environ, {"YOUTUBE_CHANNEL_ID": "UCgremi",
                                                     "YOUTUBE_URL": ""}),
                        mock.patch.object(socials_board, "fetch_bytes",
                                          lambda url: png() if url else None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def quiet(self, fn, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args)
        return result, out.getvalue()

    def gather(self, stats):
        return self.quiet(socials_board.gather, stats)[0]

    def test_columns_side_by_side_in_brand_colours(self):
        cols = self.gather(fake_stats())
        self.assertEqual([c["name"] for c in cols], ["Twitch", "YouTube", "TikTok"])
        self.assertEqual([c["color"] for c in cols],
                         [socials_board.TWITCH_PURPLE, socials_board.YOUTUBE_RED,
                          socials_board.TIKTOK_CYAN])
        self.assertEqual([c["count"] for c in cols], [1639, 2690, 3904])
        self.assertEqual(cols[0]["badge"], "OFFLINE")

    def test_previews(self):
        twitch, youtube, tiktok = (c["preview"] for c in self.gather(fake_stats()))
        self.assertEqual((twitch["label"], twitch["title"], twitch["thumb"]),
                         ("Last stream", "Monza league race", "https://img/vod-320x180.jpg"))
        self.assertEqual((youtube["label"], youtube["url"]),
                         ("Latest video", "https://www.youtube.com/watch?v=abc"))
        self.assertEqual((tiktok["label"], tiktok["title"]), ("Latest TikTok", "Last lap at Spa"))

    def test_processing_broadcast_uses_banner(self):
        twitch = self.gather(fake_stats(http=FakeHttp(processing=True)))[0]["preview"]
        self.assertEqual(twitch["thumb"], "https://img/banner.png")

    def test_live(self):
        cols = self.gather(fake_stats(stream=STREAM))
        self.assertEqual(cols[0]["badge"], "LIVE \u00b7 1,234")
        self.assertTrue(cols[0]["live"])
        self.assertEqual(cols[0]["preview"]["label"], "Live now")
        self.assertEqual(cols[0]["preview"]["thumb"], "https://static-cdn.jtvnw.net/x-320x180.jpg")
        self.assertEqual(socials_board.components_for(cols)[0]["components"][0]["label"],
                         "Watch live")
        name, value = (lambda f: (f["name"], f["value"]))(socials_board.fields_for(cols)[0])
        self.assertIn("Live now", name)
        self.assertIn("[Spa 6h, stint 2](https://twitch.tv/GreMi_Gaming)", value)
        self.assertIn("Le Mans Ultimate \u00b7 1,234 watching", value)

    def test_tiktok_without_permission_has_no_preview(self):
        stats = fake_stats(http=FakeHttp(tiktok_error="scope_not_authorized"))
        cols, out = self.quiet(socials_board.gather, stats)
        self.assertIsNone(cols[2]["preview"])
        self.assertIn("scope_not_authorized", out)
        self.assertIsNone(self.gather(fake_stats(tiktok_token=None))[2]["preview"])

    def test_unconfigured_sources(self):
        cols = self.gather(fake_stats(configured=False, twitch=None, youtube=None,
                                      tiktok_token=None))
        self.assertEqual([c["preview"] for c in cols], [None, None, None])
        (msg, files), _ = self.quiet(socials_board.build, fake_stats(configured=False))
        self.assertTrue(all(data.startswith(b"\x89PNG") for _, data in files))

    def test_buttons(self):
        rows = socials_board.components_for(self.gather(fake_stats()))
        self.assertEqual([b["label"] for b in rows[0]["components"]],
                         ["Twitch", "YouTube", "TikTok"])

    def test_text_columns(self):
        fields = socials_board.fields_for(self.gather(fake_stats()))
        self.assertEqual([f["name"] for f in fields],
                         ["Last stream", "Latest video", "Latest TikTok"])
        self.assertTrue(all(f["inline"] for f in fields))
        self.assertIn("[Monza league race](https://twitch.tv/videos/9)", fields[0]["value"])
        self.assertIn("Offline", fields[0]["value"])
        self.assertEqual(fields[1]["value"],
                         "[My first Le Mans](https://www.youtube.com/watch?v=abc)")
        self.assertIn("Last lap at Spa", fields[2]["value"])

    def test_text_columns_without_videos(self):
        fields = socials_board.fields_for(self.gather(fake_stats(
            http=FakeHttp(vod=False, yt_video=False), tiktok_token=None)))
        self.assertEqual([f["name"] for f in fields], ["Twitch", "YouTube", "TikTok"])
        self.assertIn("Follow on TikTok", fields[2]["value"])

    def test_titles_are_cleaned_for_links(self):
        self.assertEqual(socials_board.link("\U0001F680 [NEW] race !join", "https://x"),
                         "[(NEW) race](https://x)")

    def test_fingerprint_ignores_changing_picture_addresses(self):
        a = self.gather(fake_stats())
        b = self.gather(fake_stats())
        b[2]["preview"]["thumb"] += "&x-expires=2"
        self.assertEqual(socials_board.fingerprint(a), socials_board.fingerprint(b))
        self.assertNotEqual(socials_board.fingerprint(a),
                            socials_board.fingerprint(self.gather(fake_stats(tiktok=3905))))

    def test_build(self):
        (msg, files), _ = self.quiet(socials_board.build, fake_stats())
        names = [n for n, _ in files]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(d.startswith(b"\x89PNG") for _, d in files))
        self.assertEqual(msg["content"], socials_board.HEADER)
        top, bottom = msg["embeds"]
        self.assertEqual(top["image"]["url"], f"attachment://{names[0]}")
        self.assertEqual(bottom["image"]["url"], f"attachment://{names[1]}")
        self.assertEqual(len(bottom["fields"]), 3)
        self.assertEqual(msg["attachments"], [{"id": 0, "filename": names[0]},
                                              {"id": 1, "filename": names[1]}])
        self.assertEqual(msg["allowed_mentions"], {"parse": []})

    def run_update(self, stats):
        sent = []
        with mock.patch.object(socials_board, "send",
                               lambda *a: sent.append(a) or {}):
            _, out = self.quiet(socials_board.update, stats)
        return sent, out

    def test_not_posted_yet_is_skipped(self):
        sent, out = self.run_update(fake_stats())
        self.assertIn("not posted yet", out)
        self.assertEqual(sent, [])

    def posted(self, stats):
        (msg, files), _ = self.quiet(socials_board.build, stats)
        return {"id": "99", "author": {"id": "bot"}, "content": msg["content"],
                "embeds": msg["embeds"], "components": msg["components"],
                "attachments": [{"filename": n} for n, _ in files]}

    def test_unchanged_board_is_not_edited(self):
        board = self.posted(fake_stats())
        sent, out = self.run_update(fake_stats(board=board))
        self.assertIn("unchanged: socials board", out)
        self.assertEqual(sent, [])

    def test_changed_numbers_redraw_the_picture(self):
        board = self.posted(fake_stats(twitch=1600))
        sent, out = self.run_update(fake_stats(board=board))
        self.assertIn("updated:   socials board", out)
        method, path = sent[0][1], sent[0][2]
        self.assertEqual((method, path), ("PATCH", "/channels/555/messages/99"))

    def test_old_picture_board_is_replaced(self):
        old = {"id": "99", "author": {"id": "bot"}, "content": socials_board.HEADER,
               "embeds": [], "attachments": [{"filename": "socials-board-abc.png"}]}
        sent, _ = self.run_update(fake_stats(board=old))
        self.assertEqual(len(sent[0][3]["embeds"]), 2)

    def test_switched_off_without_channel(self):
        stats = fake_stats()
        with mock.patch.object(socials_board, "SOCIALS_CHANNEL_ID", ""):
            self.run_update(stats)
        self.assertEqual(stats.calls, [])


class ImageTest(unittest.TestCase):
    def test_clean_titles(self):
        self.assertEqual(board_image.clean(
            "\U0001F680 F1 26 VIEWER LOBBIES \U0001F680 ∣ ⚔️ GREMI'S GRID "
            "⚔️∣ !join !discord"), "F1 26 VIEWER LOBBIES | GREMI'S GRID")

    def test_wrap_adds_ellipsis(self):
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        lines = board_image.wrap(d, "one two three four five six seven eight nine ten",
                                 board_image.font("SemiBold", 19), 120, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))

    def test_render_sizes(self):
        from PIL import Image
        cols = [{"name": n, "color": 0x9146FF, "count": 5, "word": "followers",
                 "preview": {"label": "Latest", "title": "x", "image": b"broken"}}
                for n in ("Twitch", "YouTube", "TikTok")]
        cols[2]["preview"] = None
        top = Image.open(io.BytesIO(board_image.render_header(cols)))
        bottom = Image.open(io.BytesIO(board_image.render_previews(cols)))
        self.assertEqual(top.size, (board_image.W, board_image.HEAD_H))
        self.assertEqual(bottom.size, (board_image.W, board_image.PREVIEW_H))

if __name__ == "__main__":
    unittest.main()
