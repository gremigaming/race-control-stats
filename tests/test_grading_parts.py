"""Grading: split an incident into parts with a call, severity and note per driver."""
import html
import json
import os
import tempfile
import unittest

os.environ.setdefault("STAFF_GRADES_FILE", os.path.join(tempfile.mkdtemp(), "grades.json"))
from staff import grading as G  # noqa: E402

INC = {"id": "t-1", "track": "Las Vegas", "date": "2026-10-30", "lap": 1, "race_time": "0:32",
       "replay": "https://x/race?inc=32.8&car=2,9,8", "drivers": [
           {"car": 2, "name": "#8 Stepino81"}, {"car": 9, "name": "#13 Jakubm18_F1"}, {"car": 8, "name": "#24 rubengggarcia"}]}


def form(**kw):
    f = {"id": "t-1", "p0_t0": "32", "p0_t1": "34", "p0_real": "yes", "p0_call_0": "fault", "p0_sev_0": "6",
         "p0_call_1": "clear", "p0_call_2": "out", "p0_tag": "Early turn-in\nnot a tag",
         "p3_t0": "52", "p3_t1": "56", "p3_real": "yes", "p3_call_0": "shared", "p3_sev_0": "7",
         "p3_call_1": "shared", "p3_sev_1": "7", "p3_call_2": "shared", "p3_sev_2": "5", "p3_note_2": "late on the brakes"}
    f.update(kw)
    return f


class Parts(unittest.TestCase):
    def setUp(self):
        G.GRADES_FILE.write_text("{}")

    def test_save_parts(self):
        g = G.save(INC, 1, "GreMi", form())
        self.assertEqual(len(g["parts"]), 2)
        a, b = g["parts"]
        self.assertEqual(a["tags"], ["Early turn-in"])
        self.assertEqual([d["call"] for d in a["drivers"]], ["fault", "clear", "out"])
        self.assertEqual(b["drivers"][2], {"name": "#24 rubengggarcia", "call": "shared", "severity": 5, "note": "late on the brakes"})
        self.assertEqual(g["severity"], 7)
        self.assertEqual(g["at_fault"], ["#13 Jakubm18_F1", "#24 rubengggarcia", "#8 Stepino81"])
        self.assertIn("1) #8 Stepino81 at fault | 2) ", G.verdict_of(g))

    def test_needs_severity_for_blame(self):
        with self.assertRaises(ValueError):
            G.save(INC, 1, "GreMi", form(p0_sev_0=""))

    def test_needs_two_drivers(self):
        with self.assertRaises(ValueError):
            G.save(INC, 1, "GreMi", form(p0_call_1="out"))

    def test_decision_parts_have_cars(self):
        g = G.save(INC, 1, "GreMi", form())
        G.batches = lambda: [("t", [INC])]
        d = G.decisions({"t-1": {"1": g}}, 1)["t-1"]
        self.assertEqual(d["parts"][0]["drivers"][0]["car"], 2)
        self.assertEqual(d["parts"][1]["drivers"][2]["note"], "late on the brakes")

    def test_old_grade_still_works(self):
        g = G.save(INC, 1, "GreMi", {"id": "t-1", "real": "yes", "fault": "#8 Stepino81", "severity": "5"})
        self.assertNotIn("parts", g)
        self.assertEqual(G.verdict_of(g), "#8 Stepino81 at fault")
        self.assertEqual(G.default_parts(INC, g)[0]["drivers"][0]["severity"], 5)

    def test_page_renders_split_button(self):
        G.batches = lambda: [("t", [INC])]
        h = {"e": lambda s: html.escape(str(s)), "page": lambda t, b, u, tab: b, "csrf_token": lambda u: "x",
             "is_owner": lambda uid: True}
        G.linked_driver = lambda uid: None
        out = G.grading_page({"id": "1", "name": "GreMi"}, {}, h)
        self.assertIn("Split: add another incident", out)
        self.assertIn("name=p0_call_2", out)
        self.assertIn("value='32.8'", out)

    def test_fix_used_until_regraded(self):
        fx = json.loads((G.BATCH_DIR / "fixes.json").read_text())["fixes"]["b4-12"]
        old = {"name": "GreMi", "real": True, "at_fault": [], "severity": 9, "at": fx["replaces_at"]}
        self.assertEqual(len(G.owner_grade("b4-12", old)["parts"]), 3)
        newer = dict(old, at=fx["replaces_at"] + 1)
        self.assertNotIn("parts", G.owner_grade("b4-12", newer))


if __name__ == "__main__":
    unittest.main()
