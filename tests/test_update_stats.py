"""Tests for update_stats.py using simulated API responses (no network).

Run with: python3 -m unittest discover -s tests -t .
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import update_stats  # noqa: E402

B = update_stats.bold_caps
CATEGORY = {"id": "900", "type": 4, "name": "\U0001F4CA " + B("stats")}
EMOJIS = {
    "members": "\U0001F465",
    "status": "\U0001F534",
    "twitch": "\U0001F7E3",
    "youtube": "▶️",
    "tiktok": "\U0001F3B5",
}
CONFIG = {
    "TWITCH_CLIENT_ID": "cid",
    "TWITCH_CLIENT_SECRET": "csecret",
    "TWITCH_LOGIN": "gremi_gaming",
    "YOUTUBE_API_KEY": "yt-secret-key",
    "YOUTUBE_CHANNEL_ID": "UCgremi",
}


def stat_channels():
    chans = [CATEGORY]
    for i, emoji in enumerate(EMOJIS.values()):
        chans.append({"id": str(i + 1), "type": 2, "parent_id": "900",
                      "name": f"{emoji}┃old"})
    return chans


class FakeDiscord:
    def __init__(self, members=250, outage=False, channels=None):
        self.members = members
        self.outage = outage
        self.channels = channels or stat_channels()
        self.renames = {}

    def __call__(self, method, path, data=None):
        if self.outage:
            raise RuntimeError(f"{method} {path} failed after retries")
        if path.endswith("/channels"):
            return self.channels
        if "with_counts" in path:
            return {"approximate_member_count": self.members}
        if method == "PATCH":
            self.renames[path.rsplit("/", 1)[1]] = data["name"]
            return {}
        raise AssertionError(f"unexpected Discord call {method} {path}")


class FakeHttp:
    """Answers the Twitch and YouTube URLs the updater calls."""

    def __init__(self, live=False, followers=1234, subs=5678, hidden=False,
                 youtube_found=True, twitch_user_found=True,
                 followers_error=None, youtube_error=None,
                 tiktok_followers=999, tiktok_refresh="rt-same", tiktok_token_error=None):
        self.live = live
        self.followers = followers
        self.subs = subs
        self.hidden = hidden
        self.youtube_found = youtube_found
        self.twitch_user_found = twitch_user_found
        self.followers_error = followers_error
        self.youtube_error = youtube_error
        self.tiktok_followers = tiktok_followers
        self.tiktok_refresh = tiktok_refresh
        self.tiktok_token_error = tiktok_token_error

    def __call__(self, method, url, headers=None, data=None, form=False):
        if url.startswith("https://id.twitch.tv/oauth2/token"):
            return {"access_token": "app-token"}
        if "/helix/streams" in url:
            return {"data": [{"type": "live"}] if self.live else []}
        if "/helix/users" in url:
            return {"data": [{"id": "42"}] if self.twitch_user_found else []}
        if "/helix/channels/followers" in url:
            if self.followers_error:
                raise RuntimeError(self.followers_error)
            return {"total": self.followers, "data": []}
        if "googleapis.com/youtube" in url:
            if self.youtube_error:
                raise RuntimeError(self.youtube_error)
            if not self.youtube_found:
                return {"items": []}
            stats = {"hiddenSubscriberCount": self.hidden}
            if not self.hidden:
                stats["subscriberCount"] = str(self.subs)
            return {"items": [{"statistics": stats}]}
        if url == "https://open.tiktokapis.com/v2/oauth/token/":
            if self.tiktok_token_error:
                return {"error": self.tiktok_token_error}
            return {"access_token": "tt-access", "refresh_token": self.tiktok_refresh}
        if url.startswith("https://open.tiktokapis.com/v2/user/info/"):
            assert headers["Authorization"] == "Bearer tt-access"
            return {"data": {"user": {"follower_count": self.tiktok_followers}},
                    "error": {"code": "ok"}}
        raise AssertionError(f"unexpected HTTP call {method} {url}")


TIKTOK_CONFIG = {"TIKTOK_CLIENT_KEY": "tt-key", "TIKTOK_CLIENT_SECRET": "tt-secret",
                 "TIKTOK_REFRESH_TOKEN": "rt-same"}
ALL_CONFIG_NAMES = list(CONFIG) + list(TIKTOK_CONFIG)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        update_stats._twitch_cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tiktok_file = os.path.join(tmp.name, "tiktok.json")
        self.write_tiktok({"followers": None})

    def write_tiktok(self, content):
        with open(self.tiktok_file, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def run_main(self, discord=None, http=None, config=CONFIG, refresh_out=""):
        """Runs main() and returns (renames by channel id, exit code, output)."""
        discord = discord or FakeDiscord()
        http = http or FakeHttp()
        out = io.StringIO()
        code = 0
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(update_stats, "discord", discord))
            stack.enter_context(mock.patch.object(update_stats, "http", http))
            stack.enter_context(mock.patch.object(update_stats, "TIKTOK_FILE",
                                                  self.tiktok_file))
            stack.enter_context(mock.patch.object(update_stats,
                                                  "TIKTOK_REFRESH_TOKEN_OUT", refresh_out))
            for name in ALL_CONFIG_NAMES:
                stack.enter_context(mock.patch.object(update_stats, name,
                                                      config.get(name, "")))
            stack.enter_context(contextlib.redirect_stdout(out))
            try:
                update_stats.main()
            except SystemExit as e:
                code = e.code
        return discord.renames, code, out.getvalue()

    def assertNoSecrets(self, output):
        for secret in ("test-discord-token", "yt-secret-key", "csecret", "app-token",
                       "tt-secret", "rt-same", "rt-new", "tt-access"):
            self.assertNotIn(secret, output)

    # ----- tests -----
    def test_normal_values(self):
        self.write_tiktok({"followers": 4321})
        renames, code, out = self.run_main(FakeDiscord(members=1234),
                                           FakeHttp(live=True, followers=567, subs=8900))
        self.assertEqual(code, 0)
        self.assertEqual(renames, {
            "1": "\U0001F465┃" + B("members: 1,234"),
            "2": "\U0001F534┃" + B("status: live now"),
            "3": "\U0001F7E3┃" + B("twitch: 567"),
            "4": "▶️┃" + B("youtube: 8,900"),
            "5": "\U0001F3B5┃" + B("tiktok: 4,321"),
        })
        self.assertNoSecrets(out)

    def test_offline_status(self):
        renames, code, _ = self.run_main(http=FakeHttp(live=False))
        self.assertEqual(code, 0)
        self.assertEqual(renames["2"], "\U0001F534┃" + B("status: offline"))

    def test_unchanged_name_is_not_renamed(self):
        channels = stat_channels()
        channels[1]["name"] = "\U0001F465\u2503" + B("members: 250")
        renames, code, out = self.run_main(FakeDiscord(members=250, channels=channels))
        self.assertEqual(code, 0)
        self.assertNotIn("1", renames)
        self.assertIn("unchanged: members: 250", out)

    def test_hidden_youtube_count_leaves_channel_alone(self):
        renames, code, _ = self.run_main(http=FakeHttp(hidden=True))
        self.assertEqual(code, 0)
        self.assertNotIn("4", renames)

    def test_wrong_youtube_channel_id(self):
        renames, code, out = self.run_main(http=FakeHttp(youtube_found=False))
        self.assertEqual(code, 1)
        self.assertIn("FAILED update_youtube", out)
        self.assertIn("check YOUTUBE_CHANNEL_ID", out)
        # the other sources still ran
        self.assertIn("1", renames)
        self.assertIn("3", renames)

    def test_youtube_outage_does_not_block_others(self):
        renames, code, out = self.run_main(http=FakeHttp(
            youtube_error="GET https://www.googleapis.com/youtube/v3/channels failed after retries"))
        self.assertEqual(code, 1)
        self.assertIn("FAILED update_youtube", out)
        self.assertEqual(set(renames), {"1", "2", "3"})
        self.assertNoSecrets(out)

    def test_discord_outage_fails_the_run(self):
        with self.assertRaises(RuntimeError):
            self.run_main(FakeDiscord(outage=True))

    def test_unconfigured_sources_are_skipped(self):
        renames, code, _ = self.run_main(config={})
        self.assertEqual(code, 0)
        self.assertEqual(set(renames), {"1"})  # only members, needs no extra config

    def test_twitch_401_fails_only_twitch_followers(self):
        renames, code, out = self.run_main(http=FakeHttp(
            followers_error="GET https://api.twitch.tv/helix/channels/followers failed: 401 b'Unauthorized'"))
        self.assertEqual(code, 1)
        self.assertIn("FAILED update_twitch", out)
        self.assertIn("401", out)
        self.assertEqual(set(renames), {"1", "2", "4"})
        self.assertNoSecrets(out)

    def test_unknown_twitch_login(self):
        renames, code, out = self.run_main(http=FakeHttp(twitch_user_found=False))
        self.assertEqual(code, 1)
        self.assertIn("check TWITCH_LOGIN", out)
        self.assertNotIn("3", renames)
        self.assertIn("2", renames)  # live status uses the login directly

    def test_tiktok_bad_value(self):
        self.write_tiktok({"followers": "12k"})
        renames, code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("FAILED update_tiktok", out)
        self.assertNotIn("5", renames)

    def test_tiktok_broken_json(self):
        self.write_tiktok("{followers: 12")
        _, code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("tiktok.json is not valid JSON", out)

    def test_tiktok_missing_file_is_skipped(self):
        os.remove(self.tiktok_file)
        renames, code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("5", renames)

    # ----- TikTok API -----
    def test_tiktok_api_followers(self):
        self.write_tiktok({"followers": 1})  # the API wins over the file
        out_file = self.tiktok_file + ".refresh"
        renames, code, out = self.run_main(http=FakeHttp(tiktok_followers=12345),
                                           config={**CONFIG, **TIKTOK_CONFIG},
                                           refresh_out=out_file)
        self.assertEqual(code, 0)
        self.assertEqual(renames["5"], "\U0001F3B5\u2503" + B("tiktok: 12,345"))
        self.assertFalse(os.path.exists(out_file))  # same refresh token, nothing to save
        self.assertNoSecrets(out)

    def test_tiktok_new_refresh_token_is_written_for_saving(self):
        out_file = self.tiktok_file + ".refresh"
        _, code, out = self.run_main(http=FakeHttp(tiktok_refresh="rt-new"),
                                     config={**CONFIG, **TIKTOK_CONFIG},
                                     refresh_out=out_file)
        self.assertEqual(code, 0)
        with open(out_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "rt-new")
        self.assertNoSecrets(out)

    def test_tiktok_expired_login(self):
        renames, code, out = self.run_main(http=FakeHttp(tiktok_token_error="invalid_grant"),
                                           config={**CONFIG, **TIKTOK_CONFIG})
        self.assertEqual(code, 1)
        self.assertIn("FAILED update_tiktok", out)
        self.assertIn("run the TikTok login workflow again", out)
        self.assertNotIn("5", renames)
        self.assertIn("1", renames)
        self.assertNoSecrets(out)


class TikTokLoginTest(unittest.TestCase):
    def test_extract_code_from_full_address(self):
        import tiktok_login
        url = "https://github.com/gremigaming/race-control-stats/?code=abc%2A123%21&scopes=x&state=s"
        self.assertEqual(tiktok_login.extract_code(url), "abc*123!")
        self.assertEqual(tiktok_login.extract_code("  abc%2A123  "), "abc*123")


class HttpTest(unittest.TestCase):
    """http() itself: retries and never leaking the query string."""

    def fake_error(self, code, body=b"{}"):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))

    def test_error_message_hides_query_string(self):
        with mock.patch("urllib.request.urlopen", side_effect=self.fake_error(400)):
            with self.assertRaises(RuntimeError) as ctx:
                update_stats.http("GET", "https://example.com/api?key=yt-secret-key")
        self.assertNotIn("yt-secret-key", str(ctx.exception))
        self.assertIn("400", str(ctx.exception))

    def test_retries_after_429(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"ok": true}'
        side = [self.fake_error(429, b'{"retry_after": 0.1}'), ok]
        with mock.patch("urllib.request.urlopen", side_effect=side), \
                mock.patch("time.sleep") as sleep:
            self.assertEqual(update_stats.http("GET", "https://example.com"), {"ok": True})
        sleep.assert_called_once()

    def test_gives_up_after_repeated_outage(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("down")), \
                mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                update_stats.http("GET", "https://example.com/x?key=yt-secret-key")
        self.assertIn("failed after retries", str(ctx.exception))
        self.assertNotIn("yt-secret-key", str(ctx.exception))

    def test_sends_discord_user_agent(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b""
        with mock.patch("urllib.request.urlopen", return_value=ok) as urlopen:
            update_stats.http("GET", "https://example.com")
        req = urlopen.call_args[0][0]
        self.assertTrue(req.get_header("User-agent").startswith("DiscordBot ("))


if __name__ == "__main__":
    unittest.main()
