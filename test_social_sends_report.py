"""
test_social_sends_report.py  --  the report is only as good as the parse.

    python test_social_sends_report.py

A category report cannot look broken. If the columns come out misaligned it
still prints a full set of tidy tables, and every number in them is wrong. So
the tests here are almost entirely about reading the file, and the one that
matters most is this: the same day's sends, in all three shapes it arrives in
- tab-separated, Excel, and copied straight out of the database browser -
must produce byte-identical reports.

No fixture file is needed. The rows are built here, written to a temporary
directory, and read back through the real entry points.
"""

import os
import shutil
import sys
import tempfile
import zipfile

import social_sends_report as report

COLUMNS = ["id", "card_id", "share_type", "share_result", "error_description",
           "ip", "country", "ua", "date", "date_added", "api_key", "status"]

# Six sends that between them cover every awkward case in a real day: two
# channels, the SMS path that capitalises its result, a failure carrying an
# error description, a repeat send from one IP, and a card id the catalogue
# has never heard of.
SENDS = [
    ["1", "359583", "More", "success", "", "185.223.152.119", "US",
     "iPhone-User-Agent", "2026-08-01 00:01:56", "2026-08-01 00:02:35",
     "123TestAKey1_v2", "1"],
    ["2", "359904", "Whatsapp", "success", "", "212.123.185.227", "NL",
     "Android-User-Agent", "2026-08-01 00:32:42", "2026-08-01 00:33:31",
     "AndroidGreetings_v2", "1"],
    ["3", "359904", "Whatsapp", "success", "", "212.123.185.227", "NL",
     "Android-User-Agent", "2026-08-01 00:34:02", "2026-08-01 00:34:44",
     "AndroidGreetings_v2", "1"],
    ["4", "113366", "SMS", "Success", "", "172.56.98.89", "US",
     "Android-User-Agent", "2026-08-01 04:52:21", "2026-08-01 04:52:33",
     "AndroidGreetings", "1"],
    ["5", "117465", "Whatsapp", "failure", "recipient rejected the message",
     "86.127.230.45", "ES", "iPhone-User-Agent", "2026-08-01 00:38:14",
     "2026-08-01 00:39:47", "123TestAKey1_v2", "1"],
    ["6", "999999999", "Text", "success", "", "72.72.201.15", "US",
     "iPhone-User-Agent", "2026-08-01 03:08:58", "2026-08-01 03:09:01",
     "123TestAKey1_v2", "1"],
]

CATEGORIES = {
    "359583": "birth_happybirthday",
    "359904": "birth_happybirthday",
    "113366": "eaug_friendshipday_happy",
    "117465": "anniv_anniversaryetc",
}


def write_tsv(path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\t".join(COLUMNS) + "\n")
        for row in SENDS:
            handle.write("\t".join(row) + "\n")


def write_paste(path, header=True, row_number=True, blank_errors=True):
    """The database-browser copy: one cell per line, header row on top."""
    lines = ["\t".join(COLUMNS)] if header else []
    for row in SENDS:
        if row_number:
            lines.append(row[0] + "\t")     # the row counter, tab and all
        for index, value in enumerate(row):
            if value == "" and not blank_errors:
                continue
            lines.append(value)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_xlsx(path, rows, columns):
    """
    A minimal .xlsx, written by hand for the same reason read_xlsx() reads one
    by hand: no third-party library in this repo, in tests either.
    """
    def cell(column, index, value):
        letter = chr(ord("A") + column)
        return (f'<c r="{letter}{index}" t="inlineStr"><is><t>'
                f'{value}</t></is></c>')

    body = []
    for index, row in enumerate([columns] + rows, start=1):
        cells = "".join(cell(c, index, v) for c, v in enumerate(row))
        body.append(f'<row r="{index}">{cells}</row>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.'
             'openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + "".join(body) + "</sheetData></worksheet>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0"?><Types xmlns="http://schemas.'
                         'openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class Suite:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.notes.append(f"{label} -- {detail}")
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        return ok

    def rule(self, title):
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)

    def report(self):
        print("\n" + "=" * 78)
        print(f"{self.passed} passed, {self.failed} failed")
        if self.notes:
            print("\nFailures:")
            for note in self.notes:
                print(f"  - {note}")
        print("=" * 78)
        return self.failed == 0


def main():
    work = tempfile.mkdtemp(prefix="sends-report-")
    s = Suite()
    try:
        tsv = os.path.join(work, "sends.tsv")
        paste = os.path.join(work, "paste.txt")
        excel = os.path.join(work, "sends.xlsx")
        write_tsv(tsv)
        write_paste(paste)
        write_xlsx(excel, SENDS, COLUMNS)

        # ------------------------------------------------ every shape reads
        s.rule("1. THE SAME DAY, IN EVERY SHAPE IT ARRIVES IN")
        texts = {}
        for name, path in (("tsv", tsv), ("paste", paste), ("xlsx", excel)):
            rows = report.normalise(report.read_any(path))
            s.check(len(rows) == len(SENDS), f"{name}: reads {len(SENDS)} sends",
                    f"got {len(rows)}")
            texts[name] = report.render(report.build(rows, CATEGORIES))
        s.check(texts["tsv"] == texts["paste"],
                "paste and tsv give identical reports")
        s.check(texts["tsv"] == texts["xlsx"],
                "xlsx and tsv give identical reports")

        # A paste without the header, or without the row-number column, is
        # the same data and has to come out the same way.
        for label, kwargs in (("no header row", {"header": False}),
                              ("no row counter", {"row_number": False}),
                              ("empty cells omitted", {"blank_errors": False})):
            variant = os.path.join(work, "variant.txt")
            write_paste(variant, **kwargs)
            rows = report.normalise(report.read_any(variant))
            s.check(report.render(report.build(rows, CATEGORIES)) == texts["tsv"],
                    f"paste with {label} still reads the same")

        # ---------------------------------------------- the fields are right
        s.rule("2. THE COLUMNS LAND WHERE THEY BELONG")
        rows = report.normalise(report.read_any(paste))
        first, failed = rows[0], rows[4]
        s.check(first["card_id"] == "359583", "card_id parsed", first["card_id"])
        s.check(first["share_type"] == "More", "share_type parsed",
                first["share_type"])
        s.check(first["country"] == "US", "country parsed", first["country"])
        s.check(first["date"] == "2026-08-01 00:01:56", "date parsed",
                first["date"])
        s.check(first["api_key"] == "123TestAKey1_v2", "api_key parsed",
                first["api_key"])
        s.check(failed["error_description"] == "recipient rejected the message",
                "a multi-word error stays in one field",
                failed["error_description"])

        # ------------------------------------------------------ the counting
        s.rule("3. THE NUMBERS")
        built = report.build(rows, CATEGORIES)
        s.check(built["occasions"]["birth"].sends == 3,
                "birthday sends counted", built["occasions"]["birth"].sends)
        s.check(len(built["occasions"]["birth"].cards) == 2,
                "two different birthday cards",
                len(built["occasions"]["birth"].cards))
        s.check(len(built["occasions"]["birth"].senders) == 2,
                "a repeat send is one sender, not two",
                len(built["occasions"]["birth"].senders))
        s.check(built["unmatched"] == {"999999999": 1},
                "an unknown card is reported, not dropped into an other bucket",
                dict(built["unmatched"]))
        s.check(len(built["failures"]) == 1,
                "the failed send is found", len(built["failures"]))
        s.check(built["channels"]["SMS"] == 1,
                "SMS counted despite its capitalised result",
                built["channels"]["SMS"])
        s.check(sum(t.sends for t in built["occasions"].values())
                + sum(built["unmatched"].values()) == len(SENDS),
                "categorised plus uncategorised equals every send read")

        # ------------------------------------------- refuses to guess badly
        s.rule("4. A FILE IT CANNOT READ STOPS, RATHER THAN GUESSING")
        broken = os.path.join(work, "broken.txt")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("\n".join(["More", "success", "1.2.3.4", "US", "ua",
                                    "2026-08-01 00:01:56", "2026-08-01 00:02:35",
                                    "key", "1"]) + "\n")
        try:
            report.read_any(broken)
            s.check(False, "a record with no id raises", "it returned rows")
        except SystemExit as error:
            s.check("Export it as CSV" in str(error),
                    "a record with no id raises, and says what to do instead",
                    str(error)[:60])

        empty = os.path.join(work, "empty.txt")
        open(empty, "w").close()
        s.check(report.read_any(empty) == [], "an empty file reads as no rows")

        s.rule("5. THE CARD LIST")
        cards = os.path.join(work, "ACTIVE_CARDS.xlsx")
        write_xlsx(cards, [[k, v] for k, v in CATEGORIES.items()],
                   ["card_number", "q1_value"])
        s.check(report.load_categories(cards) == CATEGORIES,
                "the card list round-trips through the xlsx reader")

        wrong = os.path.join(work, "wrong.csv")
        with open(wrong, "w", encoding="utf-8") as handle:
            handle.write("a,b,c\n1,2,3\n4,5,6\n")
        try:
            report.load_categories(wrong)
            s.check(False, "a card list with no q1_value raises", "it returned")
        except SystemExit as error:
            s.check("does not look like a card list" in str(error),
                    "a card list with no q1_value raises", str(error)[:60])

        # ------------------------------------ the split the whole report rests on
        s.rule("6. CATEGORY AND SUB-CATEGORY")
        s.check(report.split_q1("birth_happybirthday") == ("birth", "happybirthday"),
                "a two-part q1 splits into category and sub-category",
                report.split_q1("birth_happybirthday"))
        # The one that would quietly invent a category if the split were greedy:
        # eaug_friendshipday_happy is August / friendshipday_happy, and never
        # August / friendshipday with a stray "happy" beside it.
        s.check(report.split_q1("eaug_friendshipday_happy")
                == ("eaug", "friendshipday_happy"),
                "a sub-category keeps its own underscores",
                report.split_q1("eaug_friendshipday_happy"))
        s.check(report.split_q1("anniv_ouranniversary_forher")[0] == "anniv",
                "a three-part q1 still has one category",
                report.split_q1("anniv_ouranniversary_forher")[0])
        s.check(report.label_of("eaug_friendshipday_happy", split_events=True)
                == "August occasions",
                "a month code is spelled out",
                report.label_of("eaug_friendshipday_happy", split_events=True))
        s.check(report.label_of("zzz_newthing", split_events=True) == "zzz",
                "an unknown category prints as itself, not as a guess",
                report.label_of("zzz_newthing", split_events=True))

        detail = report.render_detail(built)
        s.check("BIRTHDAY" in detail and "EVENTS CARDS" in detail
                and "ANNIVERSARY" in detail,
                "every category that was sent gets a block")
        s.check("happybirthday" in detail and "friendshipday_happy" in detail,
                "sub-categories are listed under their category")
        s.check("birth_happybirthday" not in detail,
                "a sub-category row drops the category it is already under")
        for prefix, tally in built["occasions"].items():
            s.check(sum(tally.subcategories.values()) == tally.sends,
                    f"{prefix}: sub-categories add up to the category",
                    f"{sum(tally.subcategories.values())} vs {tally.sends}")
        s.check(sum(len(t.subcategories) for t in built["occasions"].values())
                == len(built["subcategories"]),
                "no sub-category is dropped between the two tables")
        s.check(max(len(line) for line in detail.splitlines()) <= 78,
                "the detail block fits an 80-column terminal",
                max(len(line) for line in detail.splitlines()))
        s.check(report.plural(1, "card") == "1 card"
                and report.plural(2, "card") == "2 cards",
                "one card is not 1 cards")

        folded = []
        report.wrap(folded.append, "  Channels   ",
                    [f"Channel{i} {i} ({i}.0%)" for i in range(9)], 78)
        s.check(len(folded) > 1 and max(len(line) for line in folded) <= 78,
                "a long channel list folds instead of running off the line",
                max(len(line) for line in folded))
        s.check(all("(" not in line or ")" in line for line in folded),
                "folding never splits one channel across two lines")

        # ------------------------------------- the sixteen, and everything else
        s.rule("7. EVENTS CARDS")
        s.check(report.bucket_of("birth_happybirthday") == "birth",
                "a core category is reported under its own name",
                report.bucket_of("birth_happybirthday"))
        s.check(report.bucket_of("eaug_friendshipday_happy") == report.EVENTS,
                "a month code becomes Events cards",
                report.bucket_of("eaug_friendshipday_happy"))
        # wed is deliberately absent from CORE_PREFIXES - a wedding is an event.
        s.check(report.bucket_of("wed_congrats") == report.EVENTS,
                "wed is not one of the sixteen, so it is Events cards",
                report.bucket_of("wed_congrats"))
        s.check(report.bucket_of("zzz_brandnewoccasion") == report.EVENTS,
                "a prefix nobody has seen before lands in Events, not in its own row",
                report.bucket_of("zzz_brandnewoccasion"))
        s.check(len(report.CORE_PREFIXES) == 16,
                "sixteen categories keep their own name", len(report.CORE_PREFIXES))
        s.check(all(p in report.PREFIX_LABEL for p in report.CORE_PREFIXES),
                "every core category has a plain-English name")
        s.check(report.EVENTS not in report.CORE_PREFIXES
                and all("_" not in p for p in report.CORE_PREFIXES),
                "the bucket key cannot collide with a real prefix")

        s.check(report.sub_label("birth", "birth_happybirthday") == "happybirthday",
                "under a real category the prefix is dropped",
                report.sub_label("birth", "birth_happybirthday"))
        s.check(report.sub_label(report.EVENTS, "eaug_friendshipday_happy")
                == "eaug_friendshipday_happy",
                "under Events the prefix stays, or August and December merge",
                report.sub_label(report.EVENTS, "eaug_friendshipday_happy"))

        s.check(built["occasions"][report.EVENTS].sends == 1,
                "the eaug send is counted as Events cards",
                built["occasions"][report.EVENTS].sends)
        s.check("eaug" not in built["occasions"],
                "and is not also counted under its own prefix")
        s.check(dict(built["occasions"][report.EVENTS].sources) == {"eaug": 1},
                "Events records which prefixes fed it",
                dict(built["occasions"][report.EVENTS].sources))
        s.check("Made up of" in report.render_detail(built),
                "the Events block says what is in it")

        split = report.build(rows, CATEGORIES, split_events=True)
        s.check(split["occasions"]["eaug"].sends == 1
                and report.EVENTS not in split["occasions"],
                "--split-events reports the prefix under its own name instead")
        s.check(sum(t.sends for t in built["occasions"].values())
                == sum(t.sends for t in split["occasions"].values()),
                "bucketing moves sends between rows, it never adds or loses one")

        # ------------------------------------------------------ the totals line
        s.rule("8. THE TOTALS ADD UP")
        matched = len(SENDS) - sum(built["unmatched"].values())
        tail = report.render_detail(built).splitlines()[-5:]
        s.check(any("TOTAL - every category" in line and f"{matched:,}" in line
                    for line in tail),
                "the detail section ends on a grand total", " / ".join(tail).strip())
        csv_dir = os.path.join(work, "csv")
        written = report.write_csv(built, csv_dir, CATEGORIES)
        s.check(len(written) == 5, "five tables are written", len(written))
        import csv as csv_module
        with open(os.path.join(csv_dir, "by_category.csv"), encoding="utf-8") as handle:
            table = list(csv_module.reader(handle))
        s.check(table[-1][0] == "TOTAL" and int(table[-1][2]) == matched,
                "by_category.csv ends on a TOTAL row that matches the report",
                table[-1][:3])
        s.check(sum(int(r[2]) for r in table[1:-1]) == matched,
                "and the category rows above it sum to the same number",
                sum(int(r[2]) for r in table[1:-1]))
        with open(os.path.join(csv_dir, "category_by_channel.csv"),
                  encoding="utf-8") as handle:
            channels = list(csv_module.reader(handle))
        s.check(channels[-1][0] == "TOTAL"
                and int(channels[-1][-1]) == len(SENDS),
                "category_by_channel.csv totals every send, categorised or not",
                channels[-1])

        # ------------------------------------ the other shape a send file takes
        s.rule("9. A CARD x CHANNEL PIVOT")
        pivot = os.path.join(work, "pivot.tsv")
        with open(pivot, "w", encoding="utf-8") as handle:
            handle.write("\t".join(["Cardnumber", "Whatsapp (Mobile Web)",
                                    "SMS (App)", "Total"]) + "\n")
            for card, wa, sms in (("359583", 3, 0), ("359904", 0, 2),
                                  ("113366", 1, 1), ("999999999", 1, 0)):
                handle.write(f"{card}\t{wa}\t{sms}\t{wa + sms}\n")
        raw = report.read_any(pivot)
        s.check(report.looks_like_pivot(raw), "a pivot is recognised as one")
        s.check(not report.looks_like_pivot(report.read_any(tsv)),
                "and a send log is not mistaken for one")
        expanded = report.expand_pivot(raw)
        s.check(len(expanded) == 8, "a cell of 3 becomes three sends",
                len(expanded))
        s.check(sum(1 for r in expanded if r["card_id"] == "359583") == 3,
                "each send keeps its card")
        s.check(sum(1 for r in expanded if r["share_type"] == "SMS (App)") == 3,
                "and its channel, spaces and brackets intact")

        built_pivot = report.build(expanded, CATEGORIES)
        s.check(built_pivot["occasions"]["birth"].sends == 5,
                "the pivot categorises the same way a log does",
                built_pivot["occasions"]["birth"].sends)
        s.check(built_pivot["unmatched"] == {"999999999": 1},
                "an unknown card is still called out", dict(built_pivot["unmatched"]))
        s.check(not built_pivot["has_senders"] and not built_pivot["has_countries"],
                "a pivot knows it has no senders and no countries")
        text = report.render(built_pivot)
        s.check("distinct IPs" not in text,
                "so the report does not claim it has any")
        s.check("carries counts, not sends" in text,
                "and says why the line is missing instead of printing 1")

        # The Total column is the file's own arithmetic; disagreeing is an error.
        broken_pivot = os.path.join(work, "broken_pivot.tsv")
        with open(broken_pivot, "w", encoding="utf-8") as handle:
            handle.write("Cardnumber\tWhatsapp (Mobile Web)\tSMS (App)\tTotal\n")
            handle.write("359583\t3\t1\t9\n")
        try:
            report.expand_pivot(report.read_any(broken_pivot))
            s.check(False, "a row that disagrees with its own Total raises",
                    "it returned rows")
        except SystemExit as error:
            s.check("adds up to 4" in str(error) and "says 9" in str(error),
                    "a row that disagrees with its own Total raises, and says how",
                    str(error).splitlines()[0])

        # ------------------------------------------ two surfaces, one total
        s.rule("10. CUMULATIVE ACROSS SURFACES")
        app = [dict(r, _surface="App") for r in rows]
        web = [dict(r, _surface="Web") for r in expanded]
        both = report.build(app + web, CATEGORIES)
        s.check(len(both["sends"]) == len(SENDS) + 8,
                "every send from both files is read", len(both["sends"]))
        s.check(dict(both["surfaces"]) == {"App": len(SENDS), "Web": 8},
                "and counted back to the file it came from",
                dict(both["surfaces"]))
        s.check(sum(t.sends for t in both["occasions"].values())
                + sum(both["unmatched"].values()) == len(both["sends"]),
                "the cumulative total loses nothing")
        for prefix, tally in both["occasions"].items():
            s.check(sum(tally.surfaces.values()) == tally.sends,
                    f"{prefix}: its surfaces add up to its total",
                    f"{sum(tally.surfaces.values())} vs {tally.sends}")
        s.check(both["occasions"]["birth"].sends
                == built["occasions"]["birth"].sends
                + built_pivot["occasions"]["birth"].sends,
                "a category is the sum of the same category in each file",
                both["occasions"]["birth"].sends)

        # The pivot has no IPs. Counting its blank as a sender would add a
        # phantom one to every category it touches.
        s.check(len(both["senders"]) == len(built["senders"]),
                "a file with no addresses adds no senders",
                f"{len(both['senders'])} vs {len(built['senders'])}")
        s.check(both["sender_surfaces"] == {"App"},
                "and the report knows which surface the senders came from",
                both["sender_surfaces"])
        cum = report.render(both)
        s.check("CUMULATIVE" in cum and "BY SURFACE" in cum
                and "CATEGORY BY SURFACE" in cum,
                "the cumulative report says so and breaks the total back down")
        s.check("addresses and countries come from" in cum
                and "carries neither" in " ".join(cum.split()),
                "and warns that the sender count covers only part of it",
                [l for l in cum.splitlines() if "addresses and countries" in l])

        folded = []
        report.note(folded.append, " " * 16,
                    "a sentence long enough that it has to be folded onto more "
                    "than one line before it runs off the right-hand edge", 78)
        s.check(len(folded) > 1 and max(len(line) for line in folded) <= 78,
                "a note folds instead of running off the page",
                max(len(line) for line in folded))
        s.check(all(line.startswith(" " * 16) for line in folded),
                "and every folded line stays under the label")
        s.check("Surfaces" in report.render_detail(both),
                "each category block shows its own surface split")

        cum_dir = os.path.join(work, "csv2")
        names = [os.path.basename(p) for p in report.write_csv(both, cum_dir, CATEGORIES)]
        s.check("by_surface.csv" in names, "a cumulative run writes by_surface.csv", names)
        with open(os.path.join(cum_dir, "by_surface.csv"), encoding="utf-8") as handle:
            surf = list(csv_module.reader(handle))
        s.check(surf[-1][0] == "TOTAL read" and int(surf[-1][-2]) == len(both["sends"]),
                "which ends on the total that was read", surf[-1])
        s.check(int(surf[-3][-2]) + int(surf[-2][-2]) == int(surf[-1][-2]),
                "categorised plus uncategorised equals it",
                [surf[-3][-2], surf[-2][-2], surf[-1][-2]])
        s.check("by_surface.csv" not in
                [os.path.basename(p) for p in report.write_csv(built, csv_dir, CATEGORIES)],
                "and a single-file run does not")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return 0 if s.report() else 1


if __name__ == "__main__":
    sys.exit(main())
