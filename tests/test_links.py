"""Tests for bot/links.py (no Discord needed)."""
import tempfile
import unittest
from pathlib import Path

from bot import links

DRIVERS = ["TTV/GreMi_Gaming", "DDRK", "QDR_SHW1KS", "Jakubm18_F1", "EthanMoonski", "George66",
           "Overfast-_", "niet_Lars123", "conor racing1", "Mickael_340", "Ferrari '26 #28"]


class MatchTests(unittest.TestCase):
    def test_exact_and_near_names(self):
        self.assertEqual(links.match(["GreMi_Gaming"], DRIVERS), "TTV/GreMi_Gaming")
        self.assertEqual(links.match(["ddrk"], DRIVERS), "DDRK")
        self.assertEqual(links.match(["[QDR] Shw1ks"], DRIVERS), "QDR_SHW1KS")
        self.assertEqual(links.match(["Ethan Moonski"], DRIVERS), "EthanMoonski")
        self.assertEqual(links.match(["Jakubm18"], DRIVERS), "Jakubm18_F1")
        self.assertEqual(links.match(["Some Guy", "Michael_340"], DRIVERS), "Mickael_340")
        self.assertIsNone(links.match(["George66"], DRIVERS))  # "george" is too common to link alone

    def test_no_match(self):
        self.assertIsNone(links.match(["Max"], DRIVERS))
        self.assertIsNone(links.match(["Lewis Hamilton"], DRIVERS))
        self.assertIsNone(links.match(["Ferrari"], DRIVERS))
        self.assertIsNone(links.match(["[LCR] Jack"], ["[T10] Jack"]))  # other clan
        self.assertIsNone(links.match(["zeno_k25"], ["ZenoK_22"]))  # other number
        self.assertIsNone(links.match(["Leclerc"], ["LECLERC"]))  # too common
        self.assertIsNone(links.match(["fanofverstappen"], ["VERSTAPPEN"]))
        self.assertIsNone(links.match(["Harry"], ["Ttvharryjo6"]))

    def test_two_alike_drivers_need_a_pick(self):
        self.assertIsNone(links.match(["Racer One"], ["RacerOne1", "RacerOne2"]))

    def test_ranks(self):
        self.assertEqual(links.rank_of(100), "S+")
        self.assertEqual(links.rank_of(99.5), "S")
        self.assertEqual(links.rank_of(50), "B")
        self.assertEqual(links.rank_of(49.5), "C")
        self.assertEqual(links.rank_of(3), "F")
        self.assertEqual(links.rank_of(-4), "F")


class LinksTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.l = links.Links(Path(self.dir.name) / "links.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_auto_links_once_and_skips_shared_matches(self):
        new = self.l.auto([(1, ["GreMi_Gaming"]), (2, ["DDRK"]), (3, ["ddrk "]), (4, ["Nobody"])], DRIVERS)
        self.assertEqual(new, [(1, "TTV/GreMi_Gaming")])  # two members match DDRK: nobody gets it
        self.assertEqual(self.l.get(1)["how"], "auto")
        self.assertEqual(self.l.auto([(1, ["GreMi_Gaming"])], DRIVERS), [])

    def test_self_link_replaces_auto_but_not_another_self_link(self):
        self.l.auto([(1, ["DDRK"])], DRIVERS)
        ok, _ = self.l.link(2, "DDRK", "self")
        self.assertTrue(ok)
        self.assertIsNone(self.l.get(1))
        ok, msg = self.l.link(3, "ddrk", "self")
        self.assertFalse(ok)
        self.assertIn("already linked", msg)
        ok, _ = self.l.link(3, "DDRK", "staff", by=9)
        self.assertTrue(ok)
        self.assertEqual(self.l.owner("ddrk"), 3)

    def test_unlink_and_roles(self):
        self.l.link(1, "DDRK", "self")
        self.l.link(2, "George66", "self")
        self.l.link(3, "Old Name", "self")
        drivers = {"DDRK": {"sr": 58.5}, "George66": {"sr": -1, "banned": True}}
        self.assertEqual(links.wanted_roles(self.l.read(), drivers), {1: "B", 2: None, 3: None})
        self.assertTrue(self.l.unlink(1))
        self.assertFalse(self.l.unlink(1))

    def test_personal_url(self):
        self.assertTrue(links.personal_url("TTV/GreMi_Gaming").endswith("driver=ttv/gremi_gaming"))


if __name__ == "__main__":
    unittest.main()
