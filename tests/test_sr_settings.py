import html
import unittest

from staff import sr_settings as S


def form(**kw):
    f = {k: (", ".join(f"{x:g}" for x in v) if isinstance(v, list) else ("on" if v else "off") if isinstance(v, bool) else f"{v:g}")
         for k, v in S.DEFAULTS.items()}
    f.update(reason="test", **kw)
    return f


class SrSettingsTest(unittest.TestCase):
    def test_save_undo_reset(self):
        data = {}
        ch = S.post(form(gravity_joined="0.05", window_weights="1, 0.5"), data, "GreMi")
        self.assertEqual(ch, {"gravity_joined": [0.04, 0.05], "window_weights": [[1.0, 0.8, 0.6, 0.4, 0.2], [1.0, 0.5]]})
        self.assertEqual(data["values"]["gravity_joined"], 0.05)
        at = data["history"][0]["at"]
        S.post({"do": "undo", "at": at, "reason": "Undo"}, data, "GreMi")
        self.assertEqual(S.current(data), S.DEFAULTS)
        S.post(form(tier_60="1.3"), data, "GreMi")
        S.post({"do": "reset", "reason": "back"}, data, "GreMi")
        self.assertEqual(S.current(data), S.DEFAULTS)

    def test_bad_values_refused(self):
        with self.assertRaises(ValueError) as cm:
            S.post(form(tier_60="abc", kick_race="-5", dirty_big="1.5"), {}, "GreMi")
        self.assertIn("SR 60-69", str(cm.exception))
        self.assertIn("Kick line", str(cm.exception))
        self.assertIn("whole number", str(cm.exception))

    def test_unchecked_box_is_off(self):
        f = form(); f.pop("no_bonus_dirty")
        data = {}
        S.post(f, data, "GreMi")
        self.assertIs(data["values"]["no_bonus_dirty"], False)

    def test_page_renders(self):
        body = S.page_body(lambda s: html.escape(str(s)), {}, {}, True, "tok")
        self.assertIn("Save and push live", body)
        self.assertIn("The site runs on these values", body)


if __name__ == "__main__":
    unittest.main()
