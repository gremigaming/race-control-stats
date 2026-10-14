import types
import unittest

from bot import socials_live as s


class SocialsLiveTest(unittest.TestCase):
    def test_digits_from_stat_channel(self):
        self.assertEqual(s.digits("🎵┃𝗧𝗜𝗞𝗧𝗢𝗞: 𝟯,𝟵𝟰𝟭"), 3941)
        self.assertIsNone(s.digits("🔴┃𝗦𝗧𝗔𝗧𝗨𝗦: 𝗢𝗙𝗙𝗟𝗜𝗡𝗘"))

    def test_channel_count(self):
        guild = types.SimpleNamespace(voice_channels=[
            types.SimpleNamespace(name="🟣┃𝗧𝗪𝗜𝗧𝗖𝗛: 𝟭,𝟲𝟰𝟱"),
            types.SimpleNamespace(name="🎵┃𝗧𝗜𝗞𝗧𝗢𝗞: 𝟯,𝟵𝟰𝟭")])
        self.assertEqual(s.channel_count(guild, "tiktok"), 3941)
        self.assertIsNone(s.channel_count(guild, "youtube"))

    def test_public_drops_stale_and_offline_viewers(self):
        state = {"followers": {"twitch": 1645, "youtube": None},
                 "viewers": {"twitch": {"count": 12, "at": 1000}, "youtube": {"count": None, "at": 1000},
                             "old": {"count": 5, "at": 0}}}
        out = s.public(state, 1100)
        self.assertEqual(out["followers"], {"twitch": 1645})
        self.assertEqual(out["viewers"], {"twitch": 12})


if __name__ == "__main__":
    unittest.main()
