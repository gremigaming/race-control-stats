"""Tests for socials_board.py and board_image.py with simulated sources (no network)."""
import contextlib
import json
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
            boards = board if isinstance(board, list) else [board] if board else []
            return boards + [old]
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
                          socials_board.TIKTOK_DARK])
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
        B = socials_board.sans_bold
        cols = self.gather(fake_stats(stream=STREAM))
        self.assertEqual(cols[0]["badge"], "LIVE \u00b7 1,234")
        self.assertTrue(cols[0]["live"])
        self.assertEqual(cols[0]["preview"]["thumb"], "https://static-cdn.jtvnw.net/x-320x180.jpg")
        self.assertEqual(socials_board.button_for(cols[0])[0]["components"][0]["label"],
                         "Watch live")
        text = socials_board.card_text(cols[0])
        self.assertIn(f"[{B('Spa 6h, stint 2')}](https://twitch.tv/GreMi_Gaming)", text)
        self.assertIn(B("Live now \u00b7 Le Mans Ultimate \u00b7 1,234 watching"), text)
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
        msgs, _ = self.quiet(socials_board.build, fake_stats(configured=False))
        self.assertEqual(len(msgs), 3)

    def test_each_card_has_its_own_button(self):
        cols = self.gather(fake_stats())
        self.assertEqual([socials_board.link_button(c)["label"] for c in cols],
                         ["Follow on Twitch", "Subscribe on YouTube", "Follow on TikTok"])

    def test_platform_cards(self):
        B = socials_board.sans_bold
        cols = self.gather(fake_stats())
        twitch, youtube, tiktok = (socials_board.embed_for(c, f"{c['key']}.png") for c in cols)
        self.assertEqual([e["color"] for e in (twitch, youtube, tiktok)],
                         [socials_board.TWITCH_PURPLE, socials_board.YOUTUBE_RED,
                          socials_board.TIKTOK_DARK])
        self.assertEqual(twitch["author"]["name"], "Twitch")
        self.assertEqual(twitch["description"],
                         "## 1,639 followers\n"
                         f"[{B('Monza league race')}](https://twitch.tv/videos/9)\n"
                         f"-# {B('Last stream')} \u00b7 {B('offline right now')}")
        self.assertIn(B("My first Le Mans"), youtube["description"])
        self.assertIn(B("Last lap at Spa"), tiktok["description"])
        self.assertEqual(youtube["image"]["url"], "attachment://youtube.png")
        # the platform logo sits top right
        self.assertEqual(youtube["thumbnail"]["url"],
                         "https://cdn.discordapp.com/emojis/1508733155883094066.png?size=128")
        # same number of lines on every card, so they are the same height
        self.assertEqual({e["description"].count("\n") for e in (twitch, youtube, tiktok)}, {2})
    def test_cards_without_videos(self):
        B = socials_board.sans_bold
        cols = self.gather(fake_stats(http=FakeHttp(vod=False, yt_video=False), tiktok_token=None))
        tiktok = socials_board.embed_for(cols[2], None)
        self.assertIn(f"[{B('Follow on TikTok')}]", tiktok["description"])
        self.assertIn(B("Clips and highlights"), tiktok["description"])
        self.assertEqual(tiktok["description"].count("\n"), 2)
        self.assertNotIn("image", tiktok)
    def test_titles_are_cleaned_and_cut_to_one_line(self):
        B = socials_board.sans_bold
        self.assertEqual(socials_board.link("\U0001F680 [NEW] race !join", "https://x"),
                         f"[{B('(NEW) race')}](https://x)")
        long = socials_board.link("word " * 30, "https://x")
        self.assertLessEqual(len(long.split("](")[0]) - 1, socials_board.TITLE_LIMIT)
        self.assertEqual(B("Az 09,"), "\U0001D5D4\U0001D607 \U0001D7EC\U0001D7F5,")
    def test_fingerprint_ignores_changing_picture_addresses(self):
        a = self.gather(fake_stats())
        b = self.gather(fake_stats())
        b[2]["preview"]["thumb"] += "&x-expires=2"
        self.assertEqual(socials_board.fingerprint(a), socials_board.fingerprint(b))
        self.assertNotEqual(socials_board.fingerprint(a),
                            socials_board.fingerprint(self.gather(fake_stats(tiktok=3905))))

    def test_build_three_messages(self):
        B = socials_board.sans_bold
        msgs, _ = self.quiet(socials_board.build, fake_stats())
        self.assertEqual(list(msgs), ["twitch", "youtube", "tiktok"])
        for key, (msg, files) in msgs.items():
            self.assertEqual(msg["flags"], socials_board.COMPONENTS_V2)
            self.assertEqual((msg["content"], msg["embeds"]), ("", []))
            (card,) = msg["components"]
            self.assertEqual(card["type"], socials_board.CONTAINER)
            section, gallery, row = card["components"]
            # logo top right, full-width strip, button at the bottom
            self.assertIn("/emojis/", section["accessory"]["media"]["url"])
            ((name, data),) = files
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertEqual(gallery["items"][0]["media"]["url"], f"attachment://{name}")
            self.assertEqual(msg["attachments"], [{"id": 0, "filename": name}])
            self.assertEqual(row["components"][0]["type"], 2)
            self.assertEqual(msg["allowed_mentions"], {"parse": []})
        card = msgs["twitch"][0]["components"][0]
        self.assertEqual(card["accent_color"], socials_board.TWITCH_PURPLE)
        self.assertEqual(card["components"][0]["components"][0]["content"],
                         "**Twitch**\n## 1,639 followers\n"
                         f"[{B('Monza league race')}](https://twitch.tv/videos/9)\n"
                         f"-# {B('Last stream')} \u00b7 {B('offline right now')}")
        self.assertEqual(msgs["tiktok"][0]["components"][0]["accent_color"], 0x161823)

    def test_single_card_backup(self):
        B = socials_board.sans_bold
        (msg, files), _ = self.quiet(socials_board.single_card, fake_stats())
        names = [n for n, _ in files]
        self.assertEqual(len(names), 1)  # only the colour strip, no previews
        (embed,) = msg["embeds"]
        self.assertEqual(embed["image"]["url"], f"attachment://{names[0]}")
        fields = embed["fields"]
        self.assertEqual([f["name"] for f in fields],
                         ["<:twitch:1085833562474938438> Twitch",
                          "<:Youtube_logo:1508733155883094066> YouTube",
                          "<:TikTok:1097447464010788864> TikTok"])
        self.assertEqual(fields[0]["value"],
                         "**1,639** followers\n\nLast stream\n"
                         f"[{B('Monza league\u2026')}](https://twitch.tv/videos/9)")
        self.assertEqual(len(msg["components"][0]["components"]), 3)

    def test_strip_has_a_block_per_platform(self):
        from PIL import Image
        img = Image.open(io.BytesIO(board_image.strip([0x9146FF, 0xFF0033, 0x25F4EE])))
        self.assertEqual(img.size, board_image.STRIP)
        self.assertEqual(img.mode, "RGB")  # nothing see-through
        mid = board_image.STRIP[1] // 2
        r, g, b = img.getpixel((100, mid))
        self.assertTrue(b > r > g)  # Twitch purple block
        r, g, b = img.getpixel((500, mid))
        self.assertTrue(r > b and r > g)  # YouTube red block
        r, g, b = img.getpixel((900, mid))
        self.assertTrue(g > r and b > r)  # TikTok cyan block

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

    def posted(self, stats, keys=("twitch", "youtube", "tiktok")):
        msgs, _ = self.quiet(socials_board.build, stats)
        out = []
        for k in reversed(keys):  # Discord returns the messages newest first
            msg = json.loads(json.dumps(msgs[k][0]))
            # Discord swaps attachment:// for its own picture address
            gallery = msg["components"][0]["components"][1]
            name = gallery["items"][0]["media"]["url"].split("://")[1]
            gallery["items"][0]["media"]["url"] = (
                f"https://cdn.discordapp.com/attachments/1/2/{name}?ex=abc")
            out.append(dict(msg, id=f"m-{k}", author={"id": "bot"}))
        return out

    def test_unchanged_board_is_not_edited(self):
        boards = self.posted(fake_stats())
        sent, out = self.run_update(fake_stats(board=boards))
        self.assertEqual(out.count("unchanged: socials board"), 3)
        self.assertEqual(sent, [])

    def test_only_the_changed_platform_is_edited(self):
        boards = self.posted(fake_stats(twitch=1600))
        sent, out = self.run_update(fake_stats(board=boards))
        self.assertEqual([(a[1], a[2]) for a in sent],
                         [("PATCH", "/channels/555/messages/m-twitch")])
        self.assertIn("updated:   socials board (twitch)", out)

    def test_missing_platform_message_is_skipped_not_posted(self):
        boards = self.posted(fake_stats(), keys=("twitch",))
        sent, out = self.run_update(fake_stats(board=boards))
        self.assertEqual(sent, [])
        self.assertIn("no youtube message posted yet", out)

    def test_single_card_board_becomes_the_twitch_message(self):
        (old, _), _ = self.quiet(socials_board.single_card, fake_stats())
        old = dict(old, id="99", author={"id": "bot"})
        sent, _ = self.run_update(fake_stats(board=old))
        self.assertEqual([a[2] for a in sent], ["/channels/555/messages/99"])
        self.assertIn("**Twitch**", sent[0][3]["components"][0]["components"][0]
                      ["components"][0]["content"])

    def test_old_picture_board_is_replaced(self):
        old = {"id": "99", "author": {"id": "bot"}, "content": socials_board.HEADER,
               "embeds": [], "attachments": [{"filename": "socials-board-abc.png"}]}
        sent, _ = self.run_update(fake_stats(board=old))
        self.assertEqual([a["filename"] for a in sent[0][3]["attachments"]],
                         ["socials-board-twitch-strip-%d.png" % board_image.LAYOUT])

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

    def test_thumbnail_is_a_solid_wide_strip(self):
        from PIL import Image
        img = Image.open(io.BytesIO(board_image.thumbnail(png())))
        self.assertEqual(img.size, board_image.THUMB)
        self.assertEqual(img.mode, "RGB")  # nothing see-through
        self.assertIsNone(board_image.thumbnail(b"broken"))

if __name__ == "__main__":
    unittest.main()
