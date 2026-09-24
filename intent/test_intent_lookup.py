"""Checks the intent lookup against real queries from the search report.

Run from the repository root:
    python3 -m unittest discover -s intent -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intent_lookup import OUT_DIR, IntentIndex, normalise  # noqa: E402


class IntentLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(os.path.join(OUT_DIR, "intent_table.csv")):
            raise unittest.SkipTest("no built tables: run intent/build_intent_table.py with a card export first")
        cls.index = IntentIndex()

    def top(self, query):
        results = self.index.lookup(query, top=5)
        return results[0][0] if results else None

    def top_occasions(self, query):
        return self.index.categories[self.top(query)]["occasions"].split()

    def test_belated_birthday(self):
        self.assertEqual(self.top("sorry i forgot your birthday"), "birth_belated")

    def test_sister_birthday(self):
        self.assertEqual(self.top("birthday wishes for my sister"), "birth_bronsis")

    def test_husband_anniversary_prefers_cards_for_him(self):
        self.assertEqual(self.top("anniversary card for my husband"), "anniv_ouranniversary_forhim")

    def test_zero_result_query_from_report_finds_husband_birthday(self):
        self.assertEqual(self.top("funny birthday cards to husband"), "birth_hubbywife")

    def test_diwali_for_family(self):
        self.assertEqual(self.top("happy diwali to my family"), "eoct_diwali_family")

    def test_spanish_birthday_with_accent_and_url_encoding(self):
        self.assertEqual(self.top("Feliz Cumpleaños"), "w_spanish_birthday")
        self.assertEqual(self.top("Feliz Cumplea%F1os"), "w_spanish_birthday")

    def test_teacher_beats_generic_thank_you(self):
        self.assertIn(self.top("thank you teacher"),
                      {"emay_teachersday", "esep_teachersdayindia", "w_spanish_teachersday"})

    def test_sympathy_phrases(self):
        self.assertIn(self.top("sorry for your loss"), {"insp_sympathy", "pet_lossofpet"})
        self.assertIn(self.top("condolences"), {"insp_sympathy", "pet_lossofpet"})

    def test_get_well(self):
        self.assertIn(self.top("get well soon"), {"gen_getwell", "insp_recovery", "pet_getwell"})

    def test_ordinal_birthday_is_a_milestone(self):
        self.assertEqual(self.top("50th birthday"), "birth_milestone")

    def test_mothers_day_spellings_agree(self):
        for query in ("mother's day", "mothers day", "Mother’s Day", "happy mothers day mom"):
            self.assertIn("mothers_day", self.top_occasions(query), query)

    def test_christmas_and_halloween(self):
        self.assertIn("christmas", self.top_occasions("merry christmas"))
        self.assertTrue(self.top("happy halloween").startswith("eoct_hallo"))

    def test_farewell(self):
        self.assertEqual(self.top("send off"), "bus_farewell")

    def test_browse_and_nonsense_queries_have_no_intent(self):
        self.assertEqual(self.index.lookup("free ecards"), [])
        self.assertEqual(self.index.lookup("asdfghjk"), [])

    def test_video_means_format_not_video_games_day(self):
        self.assertIsNone(self.top("video"))

    def test_normalise(self):
        self.assertEqual(normalise("Mother’s Day!"), ["mothers", "day"])
        self.assertEqual(normalise("Joyeux No%EBl"), ["joyeux", "noel"])

    def test_every_live_category_is_in_the_table(self):
        self.assertGreater(len(self.index.categories), 1000)
        labelled = [c for c in self.index.categories.values()
                    if c["occasions"] or c["recipients"] or c["tones"] or c["keywords"]]
        self.assertGreater(len(labelled) / len(self.index.categories), 0.95)


if __name__ == "__main__":
    unittest.main()
