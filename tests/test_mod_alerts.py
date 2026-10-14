import unittest

from bot import mod_alerts as M


def car(name, number, nat, platform, hidden=False, ai=False, index=0):
    return {"index": index, "driver-name": name, "participant-data": {
        "race-number": number, "nationality": nat, "platform": platform, "team-id": "RB",
        "show-online-names": not hidden, "ai-controlled": ai}}


FILE = "Race_Monza_2026_10_14_19_20_00.json"


class ModAlertTests(unittest.TestCase):
    def test_cars_skip_ai_and_default_combo(self):
        cars = M.cars_of({"classification-data": [
            car("A", 7, "Dutch", "Steam"), car("RB #2", 2, "Unspecified", "Xbox", hidden=True),
            car("Bot", 9, "Dutch", "Steam", ai=True)]})
        self.assertEqual([c["name"] for c in cars], ["A", "RB #2"])
        self.assertEqual(cars[0]["combo"], "7|Dutch|Steam")
        self.assertIsNone(cars[1]["combo"])

    def test_kick_counts(self):
        k = M.kick_counts([{"type": "kick", "driver": "Smithy (hidden as RB #21)", "day": "2026-10-11"},
                           {"type": "kick", "driver": "smithy", "day": "2026-10-12"},
                           {"type": "alias"}])
        self.assertEqual(k["smithy"][1:], (2, "2026-10-12"))

    def test_session_alerts(self):
        cars = M.cars_of({"classification-data": [
            car("Smithy", 21, "British", "Xbox"), car("Bad", 5, "Dutch", "Steam"),
            car("Ferrari #9", 9, "Polish", "PlayStation", hidden=True)]})
        kicks = {"smithy": ("Smithy", 2, "2026-10-11")}
        banned = {"bad": "Bad", "ghost": "Ghost"}
        combos = {"ghost": ["9|Polish|PlayStation"], "smithy": ["21|British|Xbox"]}
        out = dict(M.session_alerts(FILE, cars, kicks, banned, combos))
        self.assertIn("kicked|2026_10_14|smithy", out)
        self.assertIn("2 times", out["kicked|2026_10_14|smithy"])
        self.assertIn("banned|2026_10_14|bad", out)
        self.assertIn("hidden|2026_10_14|9|Polish|PlayStation|ghost", out)
        self.assertEqual(len(out), 3)  # Smithy drove named, so their own car is no hidden match

    def test_safety_and_pending(self):
        found = M.safety_alerts({"drivers": [
            {"name": "X", "flags": ["Monza: lost 22 in one race (kick line 20)", "close racing capped"]},
            {"name": "Y", "flags": [], "banned": True, "sr": -2}]})
        self.assertEqual(len(found), 2)
        self.assertEqual(M.pending(found + found, {found[0][0]}), [found[1]])

    def test_label(self):
        self.assertEqual(M.session_label("One_Shot_Qualifying_Monza_2026_10_14_19_20_00.json"), "Quali Monza, 19:20")
        self.assertEqual(M.session_label(FILE), "Race Monza, 19:20")
