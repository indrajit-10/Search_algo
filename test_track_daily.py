"""
test_track_daily.py  --  the tracker has to be safe to run every day.

    python test_track_daily.py

A daily tracker fails in ways a one-off report cannot: it double-counts when a
day is entered twice, it loses yesterday when today is added, and it silently
writes a workbook nobody can open. So the tests here are about accumulation -
add a day, add another, add the first one again - and about the file that comes
out actually being a readable .xlsx.

No fixture files. Everything is built here, written to a temporary directory,
and read back through the real entry points.
"""

import os
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ElementTree

import social_sends_report as report
import track_daily
import xlsx_writer

COLUMNS = ["id", "card_id", "share_type", "share_result", "error_description",
           "ip", "country", "ua", "date", "date_added", "api_key", "status"]

CATEGORIES = {
    "359583": "birth_happybirthday",
    "359904": "birth_happybirthday",
    "113366": "eaug_friendshipday_happy",
    "117465": "anniv_anniversaryetc",
}


def app_log(path, date, rows):
    """A send log for one day: (card, channel, ip) per send."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\t".join(COLUMNS) + "\n")
        for i, (card, channel, ip) in enumerate(rows, start=1):
            handle.write("\t".join([
                str(i), card, channel, "success", "", ip, "US",
                "iPhone-User-Agent", f"{date} 0{i % 9}:01:00",
                f"{date} 0{i % 9}:01:30", "key", "1"]) + "\n")


def web_pivot(path, rows):
    """A card x channel pivot: (card, whatsapp, sms) per card."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("Cardnumber\tWhatsapp (Mobile Web)\tSMS (App)\tTotal\n")
        for card, wa, sms in rows:
            handle.write(f"{card}\t{wa}\t{sms}\t{wa + sms}\n")


class Suite:
    def __init__(self):
        self.passed = self.failed = 0
        self.notes = []

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
            print(f"  ok    {label}")
        else:
            self.failed += 1
            self.notes.append(f"{label} -- {detail}")
            print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))

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


def sheet_values(path, name):
    """Read a sheet back out of the written workbook, without openpyxl."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        names = [s.get("name") for s in book.iter(f"{ns}sheet")]
        index = names.index(name) + 1
        sheet = ElementTree.fromstring(
            archive.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in sheet.iter(f"{ns}row"):
        values = []
        for cell in row.findall(f"{ns}c"):
            text = cell.find(f"{ns}is/{ns}t")
            if text is not None:
                values.append(text.text)
                continue
            number = cell.find(f"{ns}v")
            values.append(float(number.text) if number is not None else None)
        rows.append(values)
    return rows


def main():
    work = tempfile.mkdtemp(prefix="track-daily-")
    original = track_daily.REPORTS, track_daily.WORKBOOK
    track_daily.REPORTS = os.path.join(work, "reports")
    track_daily.WORKBOOK = os.path.join(track_daily.REPORTS, "tracking.xlsx")
    s = Suite()
    try:
        day1_app = os.path.join(work, "app1.tsv")
        day1_web = os.path.join(work, "web1.tsv")
        app_log(day1_app, "2026-08-01", [
            ("359583", "Text", "1.1.1.1"), ("359583", "Text", "1.1.1.1"),
            ("359904", "More", "2.2.2.2"), ("117465", "SMS", "3.3.3.3")])
        web_pivot(day1_web, [("359583", 3, 0), ("113366", 1, 1),
                             ("999999999", 2, 0)])

        # ------------------------------------------------------ one day in
        s.rule("1. A DAY GOES IN")
        catalogue = os.path.join(work, "ACTIVE_CARDS.csv")
        with open(catalogue, "w", encoding="utf-8") as handle:
            handle.write("card_number,q1_value\n")
            for card, q1 in CATEGORIES.items():
                handle.write(f"{card},{q1}\n")

        code = track_daily.main([day1_app, day1_web, "--cards", catalogue])
        s.check(code == 0, "the first day is recorded", code)
        totals = track_daily.read_ledger("totals")
        s.check(len(totals) == 2, "one row per surface", len(totals))
        s.check(sum(int(r["sends"]) for r in totals) == 4 + 7,
                "every send from both files is counted",
                sum(int(r["sends"]) for r in totals))
        s.check({r["surface"] for r in totals} == {track_daily.APP, track_daily.WEB},
                "the two files are named app and web by default",
                {r["surface"] for r in totals})
        s.check(sum(int(r["uncategorised"]) for r in totals) == 2,
                "the unknown card is counted as uncategorised",
                sum(int(r["uncategorised"]) for r in totals))
        s.check(os.path.exists(track_daily.WORKBOOK), "and a workbook is written")

        # ---------------------------------------------------- a second day
        s.rule("2. A SECOND DAY IS ADDED, NOT SUBSTITUTED")
        day2_app = os.path.join(work, "app2.tsv")
        day2_web = os.path.join(work, "web2.tsv")
        app_log(day2_app, "2026-08-02", [("117465", "Text", "4.4.4.4")])
        web_pivot(day2_web, [("359904", 5, 0)])
        track_daily.main([day2_app, day2_web, "--cards", catalogue])

        totals = track_daily.read_ledger("totals")
        s.check(sorted({r["date"] for r in totals}) == ["2026-08-01", "2026-08-02"],
                "both days are in the ledger",
                sorted({r["date"] for r in totals}))
        s.check(sum(int(r["sends"]) for r in totals) == 11 + 6,
                "and yesterday's sends are still there",
                sum(int(r["sends"]) for r in totals))

        by_date = sheet_values(track_daily.WORKBOOK, "By date")
        s.check(by_date[0][0] == "Date" and by_date[0][1] == "Sends",
                "the workbook leads with a date column", by_date[0][:2])
        s.check([r[0] for r in by_date[1:-1]] == ["2026-08-01", "2026-08-02"],
                "one row per day, in date order", [r[0] for r in by_date[1:-1]])
        s.check(by_date[-1][0] == "TOTAL" and by_date[-1][1] == 17,
                "and a total across every day", by_date[-1][:2])
        s.check(by_date[-1][-1] is None and by_date[-1][-2] is None,
                "distinct cards and senders are not summed across days",
                by_date[-1][-2:])

        # -------------------------------------------- the same day again
        s.rule("3. THE SAME DAY AGAIN REPLACES IT")
        track_daily.main([day1_app, day1_web, "--cards", catalogue])
        totals = track_daily.read_ledger("totals")
        s.check(len(totals) == 4, "no rows are duplicated", len(totals))
        s.check(sum(int(r["sends"]) for r in totals) == 17,
                "and the total does not double", sum(int(r["sends"]) for r in totals))

        s.check(len({(r["date"], r["surface"], r["category"])
                     for r in track_daily.read_ledger("categories")})
                == len(track_daily.read_ledger("categories")),
                "the category ledger has no repeated key either")

        try:
            track_daily.main([day1_app, day1_web, "--cards", catalogue, "--once"])
            s.check(False, "--once refuses to overwrite a recorded day",
                    "it overwrote")
        except SystemExit as error:
            s.check("already recorded" in str(error),
                    "--once refuses to overwrite a recorded day, and says so",
                    str(error).splitlines()[0])

        # ------------------------------------------------- the numbers
        s.rule("4. THE NUMBERS SURVIVE THE ROUND TRIP")
        detail = sheet_values(track_daily.WORKBOOK, "Category detail")
        header = detail[0]
        birthday = [r for r in detail
                    if r[0] == "2026-08-01" and r[1] == "Birthday"][0]
        s.check(birthday[header.index("App")] == 3,
                "app sends land in the app column", birthday[header.index("App")])
        s.check(birthday[header.index(track_daily.WEB)] == 3,
                "web sends land in the web column",
                birthday[header.index(track_daily.WEB)])
        s.check(birthday[header.index("Total")] == 6,
                "and the total is the two added up", birthday[header.index("Total")])
        s.check(birthday[header.index("Cards")] == 2,
                "a card shared on both surfaces is one card, not two",
                birthday[header.index("Cards")])

        events = [r for r in detail
                  if r[0] == "2026-08-01" and r[1] == "Events cards"]
        s.check(events and events[0][header.index("Total")] == 2,
                "eaug is tracked as Events cards",
                events[0][header.index("Total")] if events else None)

        day_total = [r for r in detail if r[0] == "TOTAL 2026-08-01"][0]
        s.check(day_total[header.index("Total")] == 9,
                "each day's rows carry their own total",
                day_total[header.index("Total")])

        # ------------------------------------------------------ rebuilding
        s.rule("5. THE WORKBOOK IS DERIVED, NOT THE RECORD")
        before = open(track_daily.WORKBOOK, "rb").read()
        os.remove(track_daily.WORKBOOK)
        track_daily.main(["--rebuild"])
        s.check(open(track_daily.WORKBOOK, "rb").read() == before,
                "deleting the workbook loses nothing - --rebuild restores it byte "
                "for byte")

        shutil.rmtree(track_daily.REPORTS)
        try:
            track_daily.main(["--rebuild"])
            s.check(False, "rebuilding with no ledger stops", "it returned")
        except SystemExit as error:
            s.check("Nothing recorded yet" in str(error),
                    "rebuilding with no ledger stops and says what to run",
                    str(error).splitlines()[0])

        # ---------------------------------------------------- the xlsx itself
        s.rule("6. THE FILE IS A REAL WORKBOOK")
        path = os.path.join(work, "plain.xlsx")
        xlsx_writer.write_xlsx(path, [
            xlsx_writer.Sheet("One", ["A", "B"], [["x", 1], ["TOTAL", 1]]),
            xlsx_writer.Sheet("Two", ["C"], [["&<>\"'"]])])
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            s.check("[Content_Types].xml" in names and "xl/workbook.xml" in names
                    and "xl/worksheets/sheet2.xml" in names,
                    "every part an xlsx needs is present", sorted(names))
            s.check(archive.testzip() is None, "and the zip is not corrupt")
        s.check(sheet_values(path, "Two")[1][0] == "&<>\"'",
                "a value full of XML metacharacters round-trips",
                sheet_values(path, "Two")[1][0])
        s.check(xlsx_writer.column_name(0) == "A"
                and xlsx_writer.column_name(25) == "Z"
                and xlsx_writer.column_name(26) == "AA",
                "columns past Z are named properly",
                xlsx_writer.column_name(26))

        first = open(path, "rb").read()
        xlsx_writer.write_xlsx(path, [
            xlsx_writer.Sheet("One", ["A", "B"], [["x", 1], ["TOTAL", 1]]),
            xlsx_writer.Sheet("Two", ["C"], [["&<>\"'"]])])
        s.check(open(path, "rb").read() == first,
                "the same data writes the same bytes, so git sees no diff")
    finally:
        track_daily.REPORTS, track_daily.WORKBOOK = original
        shutil.rmtree(work, ignore_errors=True)

    return 0 if s.report() else 1


if __name__ == "__main__":
    sys.exit(main())
