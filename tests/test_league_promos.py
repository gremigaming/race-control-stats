import unittest

from bot import league_promos as lp


class LeaguePromoTests(unittest.TestCase):
    def test_league_ads_are_caught(self):
        for text in [
            "REZØNANCE RACING – SEASON 2 IS COMING\nLooking for clean, competitive racing?\n"
            "F2 CHAMPIONSHIP – Saturdays\nF1 TIER 2 – Sundays",
            "# Welcome everyone to Full Send Formula League - FSFL!!\nWe offer clean racing"
            "\n- Drivers\n- Team principals\n- FIA",
            "Anyone wanna join they just closed a tier because of there were only 7 drivers "
            "pls join https://discord.gg/njZqWrfuj",
            "New league, sign ups open! https://discord.gg/abc123",
        ]:
            self.assertTrue(lp.is_league_promo(text), text)

    def test_streams_and_chat_are_left_alone(self):
        for text in [
            "https://www.twitch.tv/hiddegam1ng?sr=a",
            "League race https://www.twitch.tv/hiddegam1ng?sr=a",
            "Kijk samen met mij naar hiddegam1ng op Twitch! https://www.twitch.tv/hiddegam1ng",
            "Im NOT joining that league again",
            "https://vm.tiktok.com/ZGdCyLy8F/",
            "https://youtu.be/LOWNb1xHLck",
            "https://discord.gg/jX5YyFEMx",
            "Last race of season",
        ]:
            self.assertFalse(lp.is_league_promo(text), text)

    def test_reply_mentions_role_only_when_missing(self):
        self.assertIn("<#1532005211235684362>", lp.reply_text(5, True))
        self.assertNotIn("League Racer", lp.reply_text(5, True))
        self.assertIn("League Racer", lp.reply_text(5, False))

    def test_due(self):
        state = [{"channel": 1, "id": 2, "at": 100}, {"channel": 1, "id": 3, "at": 200}]
        self.assertEqual([p["id"] for p in lp.due(state, 150)], [2])


if __name__ == "__main__":
    unittest.main()
