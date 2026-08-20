"""
test_hourly_report.py  --  the hourly grid has to say what actually happened.

    python test_hourly_report.py

An hourly breakdown fails quietly. Put a send in the wrong hour and the number
still looks reasonable; drop the empty hours and an outage reads as a normal
day; let a burst into the average and the burst is measured as normal. So the
tests here are about the things that would not look wrong:

  - an hour with no sends survives into the grid as a zero, not as a gap
  - a burst is counted apart AND kept out of the shape it would otherwise set
  - a quiet day is not flagged for being quiet, only for being the wrong shape
  - re-running a date replaces it rather than doubling it
  - rows without a usable timestamp are dropped, not dumped into hour 0

No fixture files. Everything is built here, written to a temporary directory,
and read back through the real entry points.
"""

import os
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ElementTree

import hourly_report

COLUMNS = ["id", "card_id", "share_type", "share_result", "error_description",
           "ip", "country", "ua", "date", "date_added", "api_key", "status"]


def app_log(path, date, sends):
    """A day's log from (hour, card, ip) triples. minute counts up so that the
    rows inside an hour are distinct."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\t".join(COLUMNS) + "\n")
        for i, send in enumerate(sends, start=1):
            hour, card, ip = send
            handle.write("\t".join([
                str(i), card, "Text", "success", "", ip, "US",
                "iPhone-User-Agent", f"{date} {hour:02d}:{i % 60:02d}:00",
                f"{date} {hour:02d}:{i % 60:02d}:30", "key", "1"]) + "\n")


def flat_day(date, path, per_hour=2, card="100001"):
    """A day with the same number of sends in every hour - the shape a fitted
    expectation should reproduce exactly."""
    sends = [(h, card, f"10.0.0.{h}") for h in range(24)
             for _ in range(per_hour)]
    app_log(path, date, sends)
    return sends


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
    work = tempfile.mkdtemp(prefix="hourly-report-")
    original = hourly_report.REPORTS, hourly_report.LEDGER, \
        hourly_report.WORKBOOK
    hourly_report.REPORTS = os.path.join(work, "reports")
    hourly_report.LEDGER = os.path.join(hourly_report.REPORTS, "hourly.tsv")
    hourly_report.WORKBOOK = os.path.join(hourly_report.REPORTS, "hourly.xlsx")
    s = Suite()

    try:
        # ------------------------------------------------------------------
        s.rule("Putting sends in hours")

        one = os.path.join(work, "one.tsv")
        app_log(one, "2026-08-03", [
            (0, "100001", "1.1.1.1"), (0, "100002", "1.1.1.2"),
            (9, "100001", "1.1.1.3"), (23, "100003", "1.1.1.1")])
        rows = hourly_report.summarise([one])
        by_hour = {r["hour"]: r for r in rows}
        s.check(len(rows) == 3, "only the hours that have sends get a row",
                len(rows))
        s.check(by_hour[0]["sends"] == 2 and by_hour[9]["sends"] == 1
                and by_hour[23]["sends"] == 1,
                "each send lands in the hour its timestamp says")
        s.check(by_hour[0]["cards"] == 2 and by_hour[0]["senders"] == 2,
                "distinct cards and IPs are counted per hour")
        s.check(all(r["date"] == "2026-08-03" for r in rows),
                "the date comes off the timestamp")

        broken = os.path.join(work, "broken.tsv")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("\t".join(COLUMNS) + "\n")
            for i, stamp in enumerate(
                    ["", "2026-08-04", "not a date",
                     "2026-08-04 07:15:00"], start=1):
                handle.write("\t".join([
                    str(i), "100001", "Text", "success", "", "1.1.1.1", "US",
                    "iPhone-User-Agent", stamp, stamp, "key", "1"]) + "\n")
        rows = hourly_report.summarise([broken])
        s.check(len(rows) == 1 and rows[0]["sends"] == 1,
                "a row without a usable timestamp is dropped, not put in "
                "hour 0", rows)

        # ------------------------------------------------------------------
        s.rule("Empty hours are observations, not gaps")

        quiet = os.path.join(work, "quiet.tsv")
        app_log(quiet, "2026-08-03",
                [(h, "100001", "1.1.1.1") for h in range(24) if h != 15])
        rows = hourly_report.summarise([quiet])
        grid = hourly_report.grid_of(rows)
        s.check(set(grid["2026-08-03"]) == set(range(24)),
                "every hour of the day is in the grid",
                sorted(grid["2026-08-03"]))
        s.check(grid["2026-08-03"][15] == 0,
                "an hour with no sends is a zero, so an outage is visible",
                grid["2026-08-03"][15])

        # ------------------------------------------------------------------
        s.rule("Bursts")

        burst = os.path.join(work, "burst.tsv")
        app_log(burst, "2026-08-03",
                [(4, "100001", "9.9.9.9")] * hourly_report.BURST_MIN
                + [(4, "100002", "9.9.9.9")] * (hourly_report.BURST_MIN - 1)
                + [(5, "100001", "8.8.8.8")])
        rows = hourly_report.summarise([burst])
        four = [r for r in rows if r["hour"] == 4][0]
        five = [r for r in rows if r["hour"] == 5][0]
        s.check(four["bursts"] == 1
                and four["burst_sends"] == hourly_report.BURST_MIN,
                "one IP repeating one card enough times is one burst",
                (four["bursts"], four["burst_sends"]))
        s.check(four["sends"] == 2 * hourly_report.BURST_MIN - 1,
                "the burst sends still count towards the hour's total",
                four["sends"])
        s.check(five["burst_sends"] == 0,
                "a single send is not a burst", five["burst_sends"])

        split = os.path.join(work, "split.tsv")
        app_log(split, "2026-08-03",
                [(4, "100001", "9.9.9.9")] * (hourly_report.BURST_MIN - 1)
                + [(5, "100001", "9.9.9.9")] * (hourly_report.BURST_MIN - 1))
        rows = hourly_report.summarise([split])
        s.check(all(r["burst_sends"] == 0 for r in rows),
                "repeats spread thinly across two hours are not a burst",
                [(r["hour"], r["burst_sends"]) for r in rows])

        # ------------------------------------------------------------------
        s.rule("Fitting")

        flat = os.path.join(work, "flat.tsv")
        flat_day("2026-08-03", flat)                       # a Monday
        rows = hourly_report.summarise([flat])
        resid = hourly_report.residuals(rows)
        s.check(all(abs(z) < 1e-9 for z, *_ in resid),
                "a perfectly flat day fits its own shape exactly",
                max(abs(z) for z, *_ in resid))

        small = os.path.join(work, "small.tsv")
        app_log(small, "2026-08-04",
                [(h, "100001", f"7.7.7.{h}") for h in range(24)])
        rows = hourly_report.merge(hourly_report.summarise([flat]),
                                   hourly_report.summarise([small]))
        resid = {(d, h): z for z, d, h, _, _
                 in hourly_report.residuals(rows)}
        s.check(all(abs(resid[("2026-08-04", h)]) < 1e-9 for h in range(24)),
                "a day with half the volume but the same shape is not "
                "flagged for being quiet",
                max(abs(resid[("2026-08-04", h)]) for h in range(24)))

        # A burst must not drag the fitted shape towards itself.
        loud = os.path.join(work, "loud.tsv")
        app_log(loud, "2026-08-05",
                [(h, "100001", f"6.6.6.{h}") for h in range(24)
                 for _ in range(2)]
                + [(4, "100009", "5.5.5.5")] * 40)
        rows = hourly_report.merge(hourly_report.summarise([flat]),
                                   hourly_report.summarise([loud]))
        shape = hourly_report.shapes(
            hourly_report.grid_of(rows),
            exclude_bursts={(r["date"], int(r["hour"])): r["burst_sends"]
                            for r in rows})
        s.check(abs(shape["weekday"][4] - 1 / 24) < 1e-9,
                "a burst does not pull the fitted shape towards its own hour",
                shape["weekday"][4])
        resid = {(d, h): z for z, d, h, _, _
                 in hourly_report.residuals(rows)}
        s.check(abs(resid[("2026-08-05", 4)]) < 1e-9,
                "and the hour it happened in is judged on its other sends",
                resid[("2026-08-05", 4)])

        # Weekdays and weekends are fitted apart.
        sat = os.path.join(work, "sat.tsv")
        app_log(sat, "2026-08-01",                          # a Saturday
                [(h, "100001", f"4.4.4.{h}") for h in range(12)
                 for _ in range(4)])
        rows = hourly_report.merge(hourly_report.summarise([flat]),
                                   hourly_report.summarise([sat]))
        resid = {(d, h): z for z, d, h, _, _
                 in hourly_report.residuals(rows)}
        s.check(all(abs(resid[("2026-08-01", h)]) < 1e-9 for h in range(24)),
                "a weekend shape is not judged against the weekday one",
                max(abs(resid[("2026-08-01", h)]) for h in range(24)))
        s.check(all(abs(resid[("2026-08-03", h)]) < 1e-9 for h in range(24)),
                "and the weekday is not judged against the weekend either")

        s.check(abs(hourly_report.dispersion([1, -1, 1, -1]) - 1.0) < 1e-9
                and hourly_report.dispersion([]) == 0.0,
                "dispersion is the root-mean-square, and empty is zero",
                hourly_report.dispersion([1, -1, 1, -1]))

        # ------------------------------------------------------------------
        s.rule("Accumulating, day after day")

        hourly_report.main([flat])
        first = hourly_report.read_ledger()
        s.check(len({r["date"] for r in first}) == 1,
                "one day in, one day recorded")

        hourly_report.main([small])
        two = hourly_report.read_ledger()
        s.check(sorted({r["date"] for r in two})
                == ["2026-08-03", "2026-08-04"],
                "a second day is added without losing the first",
                sorted({r["date"] for r in two}))

        hourly_report.main([flat])
        again = hourly_report.read_ledger()
        s.check(len(again) == len(two),
                "re-running a date replaces it rather than doubling it",
                (len(again), len(two)))
        s.check(sum(int(r["sends"]) for r in again)
                == sum(int(r["sends"]) for r in two),
                "and the totals do not move")

        corrected = os.path.join(work, "corrected.tsv")
        app_log(corrected, "2026-08-03", [(1, "100001", "1.1.1.1")])
        hourly_report.main([corrected])
        fixed = hourly_report.read_ledger()
        day = [r for r in fixed if r["date"] == "2026-08-03"]
        s.check(len(day) == 1 and int(day[0]["sends"]) == 1,
                "a corrected export replaces the day entirely, leaving no "
                "hours behind from the old one", day)

        # ------------------------------------------------------------------
        s.rule("The workbook")

        hourly_report.main([flat, small])
        path = hourly_report.WORKBOOK
        s.check(os.path.exists(path), "a workbook is written")
        with zipfile.ZipFile(path) as archive:
            s.check(archive.testzip() is None, "and the zip is not corrupt")
        grid_sheet = sheet_values(path, "Sends by hour")
        s.check(grid_sheet[0][:3] == ["Date", "Day", "Sends"]
                and len(grid_sheet[0]) == 27,
                "the grid has a column per hour", len(grid_sheet[0]))
        s.check(grid_sheet[-1][0] == "TOTAL", "and a total row")
        body = [r for r in grid_sheet[1:-1]]
        s.check(all(abs(sum(r[3:]) - r[2]) < 1e-9 for r in body),
                "every row's hours add up to its own total")
        s.check(abs(grid_sheet[-1][2] - sum(r[2] for r in body)) < 1e-9,
                "and the total row adds up the rows")

        names = [n for n in ("About", "Sends by hour", "Share of day",
                             "Hourly profile", "Flagged hours", "Bursts")]
        with zipfile.ZipFile(path) as archive:
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            present = [sheet.get("name") for sheet in book.iter(f"{ns}sheet")]
        s.check(present == names, "the sheets are the ones expected", present)

        first_bytes = open(path, "rb").read()
        hourly_report.main(["--rebuild"])
        s.check(open(path, "rb").read() == first_bytes,
                "rebuilding from the ledger writes the same bytes, so git "
                "sees no diff")
    finally:
        hourly_report.REPORTS, hourly_report.LEDGER, \
            hourly_report.WORKBOOK = original
        shutil.rmtree(work, ignore_errors=True)

    return 0 if s.report() else 1


if __name__ == "__main__":
    sys.exit(main())
