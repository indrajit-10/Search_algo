"""Checks the search pipeline end to end on a small made-up export.

The real export is private, so these tests build their own, with the same columns and
category codes. That keeps them runnable anywhere and makes each behaviour readable: every
test names a thing search has to do, and the fixture shows exactly why the answer is right.

Run from the repository root:
    python3 -m unittest discover -s search -v
"""
import csv
import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "intent"))
sys.path.insert(0, HERE)
import columns  # noqa: E402
import index as index_module  # noqa: E402
from eval import and_keyword_hits, read_queries  # noqa: E402
from rank import Search, apply_floor, diversify  # noqa: E402

HEADER = ["card_id", "card_title", "card_description", "card_tags", "q1_value",
          "status_id", "invalid_card", "views", "card_type", "created_date"]

# id, title, description, tags, category code, status, invalid, views, type, added
CARDS = [
    ("B1", "Sorry I'm Late!", "A belated birthday hug on its way to you.",
     "belated late birthday hug", "birth_belated", "1", "0", "900", "video", "2024-01-10"),
    ("B2", "Better Late Than Never", "Wishing you a very happy belated birthday.",
     "belated birthday", "birth_belated", "1", "0", "400", "animated", "2023-05-02"),
    ("B3", "Happy Birthday Sister", "For the best sister anyone could ask for.",
     "sister birthday", "birth_bronsis", "1", "0", "1200", "static", "2024-03-03"),
    ("B4", "Birthday Hugs Sis", "Sending my sister a big birthday squeeze.",
     "sister birthday hugs", "birth_bronsis", "1", "0", "700", "video", "2024-04-04"),
    ("B5", "Happy Birthday Brother", "To a brother who is one of a kind.",
     "brother birthday", "birth_bronsis", "1", "0", "500", "static", "2023-11-11"),
    ("B6", "Birthday Wishes Brother And Sister", "For my brother and sister both.",
     "brother sister birthday", "birth_bronsis", "1", "0", "300", "static", "2022-02-02"),
    ("B7", "Happy Birthday Dear Husband", "To my wonderful husband on his birthday.",
     "husband birthday love", "birth_hubbywife", "1", "0", "1500", "static", "2024-06-06"),
    ("B8", "Fifty And Fabulous", "A milestone birthday deserves a big cheer.",
     "50 milestone birthday", "birth_milestone", "1", "0", "600", "animated", "2024-02-02"),
    ("D1", "Happy Diwali To The Family", "Wishing your family light and joy this Diwali.",
     "diwali family lamps", "eoct_diwali_family", "1", "0", "800", "animated", "2024-09-09"),
    ("C1", "Merry Christmas To The Family", "Warm wishes to your whole family this season.",
     "christmas family tree", "edec_c_family", "1", "0", "1100", "animated", "2024-08-08"),
    ("S1", "Feliz Cumpleanos", "Que cumplas muchos anos mas.",
     "spanish birthday", "w_spanish_birthday", "1", "0", "300", "static", "2023-07-07"),
    ("G1", "Get Well Soon", "Hope you are back on your feet very soon.",
     "get well soon health", "gen_getwell", "1", "0", "950", "static", "2024-05-05"),
    ("G2", "Feel Better Fast", "Thinking of you until you feel better.",
     "get well better", "gen_getwell", "1", "0", "200", "static", "2023-03-03"),
    # A real trap: I Forgot Day is an August novelty holiday, the dictionaries label it
    # belated, and its title is the one place the word "forgot" appears. It is popular too.
    ("A1", "I Forgot Day Fun", "Celebrate I Forgot Day with a laugh.",
     "i forgot day", "eaug_iforgotday", "1", "0", "1400", "animated", "2024-07-01"),
    ("X1", "Retired Birthday Card", "This card is no longer live.",
     "birthday", "birth_belated", "0", "0", "9999", "static", "2020-01-01"),
    ("X2", "Flagged Birthday Card", "This card is flagged invalid.",
     "birthday sister", "birth_bronsis", "1", "1", "9999", "static", "2020-01-01"),
]

OCTOBER = datetime.date(2026, 10, 1)


def write_export(rows, header=HEADER):
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8", newline="")
    with handle as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return handle.name


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = write_export(CARDS)
        out = tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False)
        out.close()
        cls.index_path = out.name
        cls.index = index_module.build(cls.export, cls.index_path, quiet=True)
        cls.search = Search(index=cls.index, today=OCTOBER)

    @classmethod
    def tearDownClass(cls):
        for path in (cls.export, cls.index_path):
            if os.path.exists(path):
                os.unlink(path)

    def codes(self, query, **kw):
        return [r["code"] for r in self.search.query(query, **kw)["results"]]

    def ids(self, query, **kw):
        return [r["id"] for r in self.search.query(query, **kw)["results"]]

    # -- what goes into the index -----------------------------------------------------

    def test_only_live_cards_are_indexed(self):
        indexed = {doc["id"] for doc in self.index["docs"]}
        self.assertNotIn("X1", indexed, "status_id 0 is not live")
        self.assertNotIn("X2", indexed, "invalid_card 1 is not live")
        self.assertEqual(len(indexed), 14, "16 rows in, 2 not live")

    def test_category_codes_become_searchable_facets(self):
        categories = {c["code"]: c for c in self.index["categories"]}
        self.assertIn("belated", categories["birth_belated"]["occasions"].split())
        self.assertIn("birthday", categories["birth_belated"]["occasions"].split())
        self.assertIn("sister", categories["birth_bronsis"]["recipients"].split())
        self.assertIn("husband", categories["birth_hubbywife"]["recipients"].split())
        self.assertIn("spanish", categories["w_spanish_birthday"]["languages"].split())
        self.assertEqual(categories["eoct_diwali_family"]["event"], "Diwali")
        self.assertIn("october", categories["eoct_diwali_family"]["months"].split())

    def test_quality_prior_follows_the_popularity_column(self):
        quality = {doc["id"]: doc["quality"] for doc in self.index["docs"]}
        self.assertEqual(quality["B7"], 1.0, "the most viewed card is the best prior")
        self.assertGreater(quality["B3"], quality["B6"])

    # -- the queries keyword search cannot answer ------------------------------------

    def test_belated_birthday_shares_no_word_with_the_cards(self):
        query = "sorry i forgot your birthday"
        self.assertEqual(and_keyword_hits(self.search, query), 0,
                         "all-words keyword search finds nothing, which is the point")
        self.assertEqual(self.codes(query)[0], "birth_belated")

    def test_one_rare_word_cannot_carry_a_card(self):
        """The whole query decides, not its most unusual word."""
        found = self.search.query("sorry i forgot your birthday", explain=True)
        self.assertEqual(found["results"][0]["code"], "birth_belated")
        forgot_day = next(r for r in found["results"] if r["code"] == "eaug_iforgotday")
        self.assertIn("forgot", forgot_day["why"]["words"], "it is the only card saying it")
        self.assertLess(forgot_day["why"]["covered"], 1.0, "and it says nothing else")
        self.assertLess(forgot_day["score"], found["results"][0]["score"],
                        "belated birthday matches belated and birthday, I Forgot Day only belated")

    def test_husband_birthday(self):
        self.assertEqual(self.codes("funny birthday cards to hubby")[0], "birth_hubbywife")

    def test_spanish_birthday_with_accent_and_url_encoding(self):
        for query in ("Feliz Cumpleaños", "Feliz Cumplea%F1os", "cumpleanos"):
            self.assertEqual(self.codes(query)[0], "w_spanish_birthday", query)

    def test_ordinal_is_a_milestone(self):
        self.assertEqual(self.codes("50th birthday")[0], "birth_milestone")

    def test_get_well_soon(self):
        self.assertEqual(self.codes("hope you feel better")[0], "gen_getwell")

    # -- spelling ---------------------------------------------------------------------

    def test_a_typo_still_finds_the_card(self):
        found = self.search.query("birthdya wishes for sistre")
        self.assertEqual(dict(found["corrections"]), {"birthdya": "birthday", "sistre": "sister"})
        self.assertEqual(found["results"][0]["code"], "birth_bronsis")

    def test_a_word_the_dictionaries_know_is_never_corrected(self):
        self.assertEqual(self.search.query("hubby")["corrections"], [])

    def test_nonsense_is_left_alone(self):
        self.assertEqual(self.search.query("asdfghjk")["corrections"], [])

    # -- ranking ----------------------------------------------------------------------

    def test_naming_a_card_puts_it_first(self):
        self.assertEqual(self.ids("feel better fast")[0], "G2",
                         "a title named outright wins, even though G1 is more popular")

    def test_popularity_breaks_a_tie(self):
        sisters = [i for i in self.ids("birthday for my sister") if i in ("B3", "B4")]
        self.assertEqual(sisters, ["B3", "B4"], "same category, so the better card leads")

    def test_seasonality_applies_only_when_the_query_names_no_date(self):
        family = [c for c in self.codes("cards for the family")
                  if c in ("eoct_diwali_family", "edec_c_family")]
        self.assertEqual(family, ["eoct_diwali_family", "edec_c_family"],
                         "in October, October's occasion leads")
        self.assertTrue(self.search.query("cards for the family")["seasonal"])

        christmas = self.search.query("merry christmas to the family")
        self.assertFalse(christmas["seasonal"], "the query said when it means")
        self.assertEqual(christmas["results"][0]["code"], "edec_c_family")

    def test_the_season_decides_which_categories_are_boosted(self):
        """Ranking cards by season cannot help a category that was never recalled."""
        import rank
        was = rank.INTENT_TOP
        rank.INTENT_TOP = 1   # force a choice between the two family categories
        try:
            october = self.search.query("cards for the family")
            january = Search(index=self.index, today=datetime.date(2027, 1, 5))
            january = january.query("cards for the family")
        finally:
            rank.INTENT_TOP = was
        self.assertEqual([c for c, _ in october["intent"]], ["eoct_diwali_family"])
        self.assertEqual([c for c, _ in january["intent"]], ["edec_c_family"])

    def test_format_words_boost_but_never_filter(self):
        found = self.search.query("video birthday sister", explain=True)
        bonuses = {r["id"]: r["why"]["bonus"] for r in found["results"]}
        self.assertIn("format", bonuses["B4"], "B4 is the video")
        self.assertNotIn("format", bonuses["B3"])
        self.assertIn("B3", bonuses, "and the others are still here")

    def test_one_category_cannot_take_the_first_page(self):
        top = self.codes("birthday", top=5, per_category=1)
        self.assertEqual(len(top), len(set(top)), "one card per category with the cap at 1")
        self.assertIn("birth_bronsis", top)
        self.assertGreater(len(self.codes("birthday", top=5, per_category=0)), 0,
                           "a cap of 0 turns the rule off, it does not empty the page")

    def test_a_word_in_most_of_the_catalogue_is_not_a_search_term(self):
        """On a card site, "cards", "for" and "free" are in half the queries and say nothing."""
        self.assertTrue(Search.is_stopword(3000, 10000), "30% of the catalogue")
        self.assertFalse(Search.is_stopword(1000, 10000), "10% still discriminates")
        self.assertFalse(Search.is_stopword(9, 10),
                         "a frequency means nothing on a catalogue this small")

    def test_only_the_best_text_matches_are_reranked(self):
        """A common word matches much of the catalogue; the weak matches are not answers."""
        import rank
        was = rank.MAX_LEX_CANDIDATES
        rank.MAX_LEX_CANDIDATES = 2
        try:
            capped = self.search.query("you", per_category=0)
        finally:
            rank.MAX_LEX_CANDIDATES = was
        full = self.search.query("you", per_category=0)
        self.assertEqual(capped["intent"], [], "a word with no intent, so text alone recalls")
        self.assertLess(capped["candidates"], full["candidates"])
        self.assertEqual(capped["results"][0]["id"], full["results"][0]["id"],
                         "the cap drops the tail, never the best answer")

    def test_the_floor_cuts_the_tail_but_never_empties_the_page(self):
        strong = [{"score": s, "code": "c", "id": str(s)}
                  for s in (0.9, 0.8, 0.5, 0.4, 0.31, 0.3, 0.29, 0.2, 0.1, 0.05)]
        kept = apply_floor(strong, floor=0.3, keeps=4)
        self.assertEqual([r["score"] for r in kept], [0.9, 0.8, 0.5, 0.4, 0.31, 0.3, 0.29],
                         "the cutoff is 30% of the best score, 0.27, so 0.2 and under go")
        thin = [{"score": s, "code": "c", "id": str(s)} for s in (0.9, 0.1, 0.05)]
        self.assertEqual(len(apply_floor(thin, floor=0.3, keeps=4)), 3,
                         "a short page is kept whole rather than cut to one result")
        self.assertEqual(apply_floor([], floor=0.3, keeps=4), [])

    def test_the_cap_pushes_cards_down_and_never_drops_them(self):
        ranked = [{"code": "birth_bronsis", "id": i} for i in "abcd"]
        ranked += [{"code": "birth_belated", "id": "e"}]
        capped = diversify(ranked, 2)
        self.assertEqual([r["id"] for r in capped], ["a", "b", "e", "c", "d"])
        self.assertEqual(len(capped), len(ranked))

    def test_nothing_matched_falls_back_to_popular_cards(self):
        found = self.search.query("asdfghjk")
        self.assertEqual(found["fallback"], "popular")
        self.assertTrue(found["results"], "an empty page is never an answer")

    def test_a_match_never_falls_back(self):
        self.assertIsNone(self.search.query("birthday")["fallback"])

    def test_results_come_with_facets_to_narrow_by(self):
        found = self.search.query("birthday")
        facets = found["facets"]
        self.assertEqual(facets["group"][0][0], "Birthday", "the commonest group leads")
        birthday_cards = sum(1 for r in found["results"] if r["group"] == "Birthday")
        self.assertEqual(dict(facets["group"])["Birthday"], birthday_cards,
                         "a facet counts what is on the page")
        self.assertIn("sister", dict(facets["recipient"]))
        self.assertIn("spanish", dict(facets["language"]))

    def test_every_result_is_explained(self):
        found = self.search.query("happy birthday sister", explain=True)
        why = found["results"][0]["why"]
        self.assertGreater(why["lexical"], 0)
        self.assertGreater(why["intent"], 0)
        self.assertIn("sister", why["words"])
        self.assertTrue(found["why_intent"])


class ColumnsTest(unittest.TestCase):
    def test_a_header_resolves_to_the_names_search_uses(self):
        cols = columns.resolve(HEADER)
        self.assertEqual(cols.title, "card_title")
        self.assertEqual(cols.code, "q1_value")
        self.assertEqual(cols.popularity, "views")
        self.assertEqual(cols.missing, [])

    def test_spelling_and_case_do_not_matter(self):
        cols = columns.resolve(["ID", "Title", "Description", "Keywords", "Category_Code",
                                "Status", "Is_Invalid"])
        self.assertEqual(cols.tags, "Keywords")
        self.assertEqual(cols.code, "Category_Code")
        self.assertEqual(cols.missing, ["popularity", "format", "created"])

    def test_a_missing_required_column_is_an_error_that_names_it(self):
        with self.assertRaises(ValueError) as caught:
            columns.resolve(["card_title", "card_description", "card_tags", "q1_value"])
        self.assertIn("status", str(caught.exception))
        self.assertIn("invalid", str(caught.exception))

    def test_live_cards(self):
        cols = columns.resolve(HEADER)
        self.assertTrue(cols.is_live({"status_id": "1", "invalid_card": "0"}))
        self.assertTrue(cols.is_live({"status_id": "1", "invalid_card": ""}))
        self.assertFalse(cols.is_live({"status_id": "0", "invalid_card": "0"}))
        self.assertFalse(cols.is_live({"status_id": "1", "invalid_card": "1"}))


class WithoutOptionalColumnsTest(unittest.TestCase):
    """The export may not carry views, a type or a date. Search has to work anyway."""

    @classmethod
    def setUpClass(cls):
        header = ["card_title", "card_description", "card_tags", "q1_value",
                  "status_id", "invalid_card"]
        rows = [card[1:7] for card in CARDS]
        cls.export = write_export(rows, header)
        out = tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False)
        out.close()
        cls.index_path = out.name
        index = index_module.build(cls.export, cls.index_path, quiet=True)
        cls.search = Search(index=index, today=OCTOBER)

    @classmethod
    def tearDownClass(cls):
        for path in (cls.export, cls.index_path):
            if os.path.exists(path):
                os.unlink(path)

    def test_the_index_says_what_it_is_missing(self):
        self.assertEqual(sorted(self.search.meta["missing_columns"]),
                         ["created", "format", "id", "popularity"])

    def test_search_still_answers_and_ids_fall_back_to_the_row(self):
        found = self.search.query("sorry i forgot your birthday")
        self.assertEqual(found["results"][0]["code"], "birth_belated")
        self.assertTrue(found["results"][0]["id"].isdigit())

    def test_quality_is_neutral_so_text_and_intent_decide(self):
        self.assertFalse(self.search.has_format)
        self.assertEqual({doc["quality"] for doc in self.search.docs}, {0.5})


class QueryFileTest(unittest.TestCase):
    def test_counts_are_read_in_either_column(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        with handle as f:
            f.write("# a comment\n\nhappy birthday\n1843\tbirthday wishes\n"
                    "diwali cards,57\n")
        try:
            self.assertEqual(read_queries(handle.name),
                             [("happy birthday", 1), ("birthday wishes", 1843),
                              ("diwali cards", 57)])
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()
