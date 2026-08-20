#!/usr/bin/env python3
"""
hourly_report.py  --  the day broken into hours, and what looks wrong in it.

    python3 hourly_report.py data/social_sends_2026-08-*.tsv
    python3 hourly_report.py --rebuild

App sends only. The app log stamps every send to the second; the web export is
a card-by-channel pivot with no time in it at all, so there is no honest way to
put a web send in an hour. Every number here is the app half of the day, and
the workbook says so on its first sheet.

Two things come out of it.

The grid: a row per day, a column per hour, in the server's own clock. Written
to reports/daily_hourly.tsv, which is the record, and rendered into
reports/hourly_tracking.xlsx, which is the thing to open.

The flags: hours that do not fit. Fitting means an expected count for each
hour - the day's own volume spread over the hourly shape of comparable days -
and a standardised residual against it. Weekdays and weekends get separate
shapes because the shapes genuinely differ: the pre-work send window is a
weekday thing. Bursts are found separately and by their own signature, one IP
sending one card over and over inside an hour, because they distort the very
average they would otherwise be measured against.

Standard library only, like everything else here.
"""

import argparse
import collections
import csv
import datetime
import math
import os
import sys

import social_sends_report as report
from xlsx_writer import Sheet, write_xlsx

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
LEDGER = os.path.join(REPORTS, "daily_hourly.tsv")
WORKBOOK = os.path.join(REPORTS, "hourly_tracking.xlsx")

HEADER = ["date", "hour", "sends", "cards", "senders", "bursts", "burst_sends"]
HOURS = range(24)

# Same IP, same card, this many times inside one clock hour. Five is the point
# where the behaviour stops looking like a person with a big family: across
# nineteen days the median IP sends once, and 5+ repeats of one card in an hour
# is a different activity, not the tail of the same one.
BURST_MIN = 5

# |z| past this gets flagged. Three is roughly one false positive per 370
# hours, so about one a fortnight at 24 hours a day - rare enough to be worth
# reading, common enough that a quiet fortnight means something.
FLAG_Z = 3.0


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def timestamped(rows):
    """App rows that carry a usable `date`. A row without one cannot be put in
    an hour, and silently dropping it into hour 0 would be a lie."""
    out = []
    for row in rows:
        stamp = str(row.get("date") or "").strip()
        if len(stamp) >= 13 and stamp[10] == " " and stamp[11:13].isdigit():
            out.append(row)
    return out


def bursts_in(rows):
    """(ip, card, hour) keys that repeat at least BURST_MIN times.

    Keyed on the hour string rather than a rolling window: the grid is hourly,
    so a burst that straddles two hours is two bursts, which is the same way
    the counts treat it."""
    counts = collections.Counter(
        (r.get("ip", ""), r.get("card_id", ""), r["date"][:13]) for r in rows)
    return {key for key, n in counts.items() if n >= BURST_MIN}


def summarise(paths):
    """One record per (date, hour) that has any send in it."""
    rows = []
    for path in paths:
        rows += timestamped(report.read_any(path))
    if not rows:
        raise SystemExit("No timestamped app sends in those files.")

    burst_keys = bursts_in(rows)
    cells = collections.defaultdict(list)
    for row in rows:
        cells[(row["date"][:10], int(row["date"][11:13]))].append(row)

    out = []
    for (date, hour), group in sorted(cells.items()):
        keys = collections.Counter(
            (r.get("ip", ""), r.get("card_id", ""), r["date"][:13])
            for r in group)
        hit = {k for k in keys if k in burst_keys}
        out.append({
            "date": date,
            "hour": hour,
            "sends": len(group),
            "cards": len({r.get("card_id", "") for r in group}),
            "senders": len({r.get("ip", "") for r in group}),
            "bursts": len(hit),
            "burst_sends": sum(keys[k] for k in hit),
        })
    return out


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle, delimiter="\t")
                if row.get("date")]


def write_ledger(rows):
    os.makedirs(REPORTS, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["date"], int(r["hour"])))
    with open(LEDGER, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, HEADER, delimiter="\t",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge(existing, fresh):
    """Re-running a date replaces it, the same way track_daily does, so a
    corrected export is just the same command again."""
    replaced = {r["date"] for r in fresh}
    return [r for r in existing if r["date"] not in replaced] + fresh


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------

def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def grid_of(rows):
    """{date: {hour: sends}}, with the missing hours filled in as zero.

    An hour with no sends is a real observation - a quiet hour, or an outage -
    and leaving it out of the grid would quietly exclude the only shape worth
    raising an alarm about."""
    out = {}
    for row in rows:
        out.setdefault(row["date"], {h: 0 for h in HOURS})
        out[row["date"]][int(row["hour"])] = number(row["sends"])
    return out


def kind_of(date):
    return "weekend" if datetime.date.fromisoformat(date).weekday() >= 5 \
        else "weekday"


def shapes(grid, exclude_bursts=None):
    """The hourly shape of a weekday and of a weekend day, as fractions.

    exclude_bursts, when given as {(date, hour): burst_sends}, is subtracted
    first. A single IP firing twenty copies of one card would otherwise pull
    the shape towards itself and then be measured as normal against it."""
    totals = collections.defaultdict(collections.Counter)
    for date, hours in grid.items():
        kind = kind_of(date)
        for hour, sends in hours.items():
            if exclude_bursts:
                sends -= exclude_bursts.get((date, hour), 0)
            totals[kind][hour] += max(0, sends)

    out = {}
    for kind, counts in totals.items():
        whole = sum(counts.values())
        out[kind] = {h: (counts[h] / whole if whole else 0.0) for h in HOURS}
    return out


def residuals(rows):
    """(z, date, hour, observed, expected) for every hour, burst-adjusted.

    Expected is the day's own total spread over the shape of its kind of day,
    so a quiet day is not flagged for being quiet - only for being the wrong
    shape. z is the Poisson standardised residual, (o - e) / sqrt(e), which is
    the right scale for counts: an hour that expects 3 and sees 9 is stranger
    than one that expects 30 and sees 36."""
    grid = grid_of(rows)
    burst = {(r["date"], int(r["hour"])): number(r["burst_sends"])
             for r in rows}
    shape = shapes(grid, exclude_bursts=burst)

    out = []
    for date in sorted(grid):
        kind = kind_of(date)
        clean_day = sum(max(0, grid[date][h] - burst.get((date, h), 0))
                        for h in HOURS)
        for hour in HOURS:
            observed = max(0, grid[date][hour] - burst.get((date, hour), 0))
            expected = clean_day * shape[kind][hour]
            z = ((observed - expected) / math.sqrt(expected)
                 if expected > 0 else 0.0)
            out.append((z, date, hour, observed, expected))
    return out


def dispersion(values):
    """Root-mean-square z. Independent arrivals give 1.0; anything much above
    means the sends are clumping into some hours and out of others."""
    if not values:
        return 0.0
    return math.sqrt(sum(z * z for z in values) / len(values))


# --------------------------------------------------------------------------
# Workbook
# --------------------------------------------------------------------------

ABOUT = [
    ("What this is", "App share sends broken down by hour of the day, one row "
                     "per day, rebuilt from reports/daily_hourly.tsv."),
    ("App only", "The web and mobile-web export is a card-by-channel pivot "
                 "with no timestamp, so it cannot be placed in an hour. "
                 "Nothing on these sheets includes it."),
    ("Clock", "The server's own clock, as written in the app log. Sends are "
              "not converted to the sender's local time."),
    ("Expected", "A day's own total spread over the hourly shape of "
                 "comparable days - weekdays and weekends fitted separately, "
                 "because the early-morning window differs between them."),
    ("z", "(observed - expected) / sqrt(expected). Around 1 is ordinary "
          "scatter for counts; past 3 is worth reading."),
    ("Bursts", f"One IP sending one card {BURST_MIN}+ times inside one hour. "
               "Counted apart, and taken out before anything is fitted, so "
               "that a burst cannot set the average it is measured against."),
]


def build_workbook(rows):
    if not rows:
        raise SystemExit("Nothing recorded yet - run it over an app log first.")

    grid = grid_of(rows)
    dates = sorted(grid)
    burst = {(r["date"], int(r["hour"])): number(r["burst_sends"])
             for r in rows}
    resid = residuals(rows)
    by_cell = {(d, h): (z, o, e) for z, d, h, o, e in resid}

    hour_labels = [f"{h:02d}" for h in HOURS]

    # --- Sends by hour: the grid itself
    grid_rows = []
    for date in dates:
        day = [grid[date][h] for h in HOURS]
        grid_rows.append([date, kind_of(date), sum(day)] + day)
    grid_rows.append(
        ["TOTAL", "", sum(r[2] for r in grid_rows)]
        + [sum(grid[d][h] for d in dates) for h in HOURS])
    by_hour = Sheet("Sends by hour", ["Date", "Day", "Sends"] + hour_labels,
                    grid_rows, widths=[13, 10, 9] + [5] * 24)

    # --- Share of day: the same grid as percentages, so days compare
    share_rows = []
    for date in dates:
        whole = sum(grid[date].values())
        share_rows.append(
            [date, kind_of(date)]
            + [(grid[date][h] / whole if whole else 0.0) for h in HOURS])
    whole = sum(sum(grid[d].values()) for d in dates)
    share_rows.append(
        ["TOTAL", ""]
        + [(sum(grid[d][h] for d in dates) / whole if whole else 0.0)
           for h in HOURS])
    share = Sheet("Share of day", ["Date", "Day"] + hour_labels, share_rows,
                  widths=[13, 10] + [6] * 24,
                  percent_columns=range(2, 26))

    # --- Hourly profile: what an average day of each kind looks like
    counts = collections.defaultdict(collections.Counter)
    days = collections.Counter()
    for date in dates:
        counts[kind_of(date)].update(grid[date])
        days[kind_of(date)] += 1
    kinds = [k for k in ("weekday", "weekend") if days[k]]
    profile_rows = []
    for hour in HOURS:
        total = sum(counts[k][hour] for k in kinds)
        profile_rows.append(
            [f"{hour:02d}:00", total, total / len(dates)]
            + [counts[k][hour] / days[k] for k in kinds])
    profile_rows.append(
        ["TOTAL", sum(r[1] for r in profile_rows),
         sum(r[2] for r in profile_rows)]
        + [sum(counts[k].values()) / days[k] for k in kinds])
    profile = Sheet(
        "Hourly profile",
        ["Hour", "Sends", "Per day"] + [f"Per {k}" for k in kinds],
        profile_rows, widths=[9, 10, 11] + [13] * len(kinds))

    # --- Flagged hours: what does not fit
    flagged = sorted((r for r in resid if abs(r[0]) >= FLAG_Z),
                     key=lambda r: -abs(r[0]))
    flag_rows = [
        [d, f"{h:02d}:00", kind_of(d), o, round(e, 1), round(z, 2),
         "high" if z > 0 else "low", burst.get((d, h), 0)]
        for z, d, h, o, e in flagged]
    if not flag_rows:
        flag_rows = [["none", "", "", "", "", "", "", ""]]
    flags = Sheet("Flagged hours",
                  ["Date", "Hour", "Day", "Observed", "Expected", "z",
                   "Direction", "Burst sends that hour"],
                  flag_rows, widths=[13, 8, 10, 10, 10, 8, 11, 22])

    # --- Bursts: the other kind of abnormal, counted on its own terms
    burst_rows = []
    for date in dates:
        day = [(h, burst.get((date, h), 0)) for h in HOURS]
        n = sum(v for _, v in day)
        if not n:
            continue
        worst = max(day, key=lambda hv: hv[1])
        whole = sum(grid[date].values())
        burst_rows.append([date, n, (n / whole if whole else 0.0),
                           f"{worst[0]:02d}:00", worst[1]])
    total_burst = sum(burst.values())
    total_sends = sum(sum(grid[d].values()) for d in dates)
    burst_rows.append(["TOTAL", total_burst,
                       (total_burst / total_sends if total_sends else 0.0),
                       "", ""])
    burst_sheet = Sheet(
        "Bursts", ["Date", "Burst sends", "Share of day's sends",
                   "Worst hour", "Sends in it"],
        burst_rows, widths=[13, 13, 20, 12, 13], percent_columns=[2])

    zs = [r[0] for r in resid]
    about = Sheet("About", ["Field", "Value"], [list(r) for r in ABOUT] + [
        ["Days recorded", len(dates)],
        ["First day", dates[0]],
        ["Latest day", dates[-1]],
        ["App sends", total_sends],
        ["Burst sends", f"{total_burst} "
                        f"({total_burst / total_sends * 100:.1f}%)"
                        if total_sends else "0"],
        ["Hours flagged", f"{len(flagged)} of {len(resid)}"],
        ["Dispersion", f"{dispersion(zs):.2f} (1.00 = independent arrivals)"],
    ], widths=[26, 96])

    os.makedirs(REPORTS, exist_ok=True)
    write_xlsx(WORKBOOK, [about, by_hour, share, profile, flags, burst_sheet])
    return WORKBOOK, dates


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Break app sends into hours and flag the ones that do not "
                    "fit.")
    parser.add_argument("files", nargs="*", help="app share logs")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the workbook from the ledger alone")
    args = parser.parse_args(argv)

    if not args.files and not args.rebuild:
        parser.error("give at least one app log, or --rebuild")

    rows = read_ledger()
    if args.files:
        fresh = summarise(args.files)
        rows = merge(rows, fresh)
        write_ledger(rows)
        days = sorted({r["date"] for r in fresh})
        print(f"Recorded {len(days)} day(s): {days[0]} to {days[-1]}")

    path, dates = build_workbook(rows)
    resid = residuals(rows)
    flagged = [r for r in resid if abs(r[0]) >= FLAG_Z]
    burst = sum(number(r["burst_sends"]) for r in rows)
    sends = sum(number(r["sends"]) for r in rows)

    print(f"{path}: {len(dates)} day(s), {sends:,} app sends")
    print(f"  bursts     : {burst:,} sends "
          f"({burst / sends * 100:.1f}%)" if sends else "  bursts     : 0")
    print(f"  dispersion : {dispersion([r[0] for r in resid]):.2f} "
          f"(1.00 = independent arrivals)")
    print(f"  flagged    : {len(flagged)} hour(s) at |z| >= {FLAG_Z}")
    for z, date, hour, observed, expected in sorted(
            flagged, key=lambda r: -abs(r[0]))[:10]:
        print(f"     {date} {hour:02d}:00  {observed:>4} vs {expected:>6.1f} "
              f"expected   z={z:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
