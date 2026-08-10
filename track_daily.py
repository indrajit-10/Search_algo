#!/usr/bin/env python3
"""
Date-wise tracking. Add a day, get the whole history back as one workbook.

    python3 track_daily.py app.tsv web.tsv                 # date read from the app log
    python3 track_daily.py app.tsv web.tsv --date 2026-08-02
    python3 track_daily.py --rebuild                       # workbook from the ledgers

Each run categorises both files exactly as social_sends_report.py does, writes
the day's numbers into the ledgers under `reports/`, and rebuilds
`reports/daily_tracking.xlsx` from every day recorded so far.

The ledgers are tab-separated and the workbook is generated from them. That
way round on purpose: an .xlsx is a zip of XML, so git can store it but cannot
show you what changed in it. The .tsv files are the record - a day's numbers
are one readable diff - and the workbook is the thing you open. If they ever
disagree, the ledgers are right; delete the workbook and --rebuild.

Re-running a date replaces that date rather than adding to it, so a corrected
export can simply be run again.

Standard library only, like everything else here.
"""

import argparse
import collections
import csv
import os
import sys

import social_sends_report as report
from xlsx_writer import Sheet, write_xlsx

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
WORKBOOK = os.path.join(REPORTS, "daily_tracking.xlsx")

# Every ledger is keyed by date first, so replacing a day is a filter on one
# column and the file sorts into date order by itself.
LEDGERS = {
    # cards_all and senders_all are the distinct counts across every surface,
    # repeated on each row. A card shared from both the app and the website is
    # one card, so these cannot be got by adding the per-surface columns up -
    # and a column that must not be summed is worth carrying explicitly.
    "totals": ("daily_totals.tsv",
               ["date", "surface", "sends", "categorised", "uncategorised",
                "cards", "senders", "cards_all", "senders_all"]),
    "categories": ("daily_categories.tsv",
                   ["date", "category", "q1_prefix", "surface", "sends",
                    "cards", "cards_all"]),
    "subcategories": ("daily_subcategories.tsv",
                      ["date", "category", "subcategory", "q1_value", "sends",
                       "cards"]),
    "platforms": ("daily_platforms.tsv",
                  ["date", "surface", "platform", "sends"]),
}

APP, WEB = "App", "Web & mobile web"


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------

def ledger_path(name):
    return os.path.join(REPORTS, LEDGERS[name][0])


def read_ledger(name):
    path = ledger_path(name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle, delimiter="\t")
                if row.get("date")]


def write_ledger(name, rows):
    os.makedirs(REPORTS, exist_ok=True)
    header = LEDGERS[name][1]
    rows = sorted(rows, key=lambda r: [str(r.get(f, "")) for f in header[:2]]
                  + [-int(r.get("sends") or 0)])
    with open(ledger_path(name), "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, header, delimiter="\t",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def platform_of(channel):
    """`Whatsapp (Mobile Web)` -> `Whatsapp`. The two surfaces name their
    channels differently; the platform is the part they agree on."""
    return channel.split(" (")[0].strip()


# --------------------------------------------------------------------------
# A day
# --------------------------------------------------------------------------

def load(path, surface):
    raw = report.read_any(path)
    rows = (report.expand_pivot(raw) if report.looks_like_pivot(raw)
            else report.normalise(raw))
    if not rows:
        raise SystemExit(f"{path} has no rows in it.")
    for row in rows:
        row["_surface"] = surface
    return rows


def date_of(sends, given):
    if given:
        return given
    dates = sorted({(r.get("date") or "")[:10] for r in sends} - {""})
    if len(dates) == 1:
        return dates[0]
    if not dates:
        raise SystemExit(
            "Neither file carries a date, so the day has to be given:\n"
            "  python3 track_daily.py app.tsv web.tsv --date 2026-08-02")
    raise SystemExit(
        f"The files span {len(dates)} days ({dates[0]} to {dates[-1]}).\n"
        "  Name the day explicitly with --date, or split the export.")


def measure(date, sends, categories):
    """One day's numbers, as rows for each ledger."""
    built = report.build(sends, categories)
    surfaces = [name for name, _ in built["surfaces"].most_common()]
    out = collections.defaultdict(list)

    for surface in surfaces:
        rows = [r for r in sends if r.get("_surface") == surface]
        matched = sum(t.surfaces.get(surface, 0)
                      for t in built["occasions"].values())
        senders = {r.get("ip") for r in rows if r.get("ip")}
        out["totals"].append({
            "date": date, "surface": surface, "sends": len(rows),
            "categorised": matched,
            "uncategorised": built["surface_unmatched"].get(surface, 0),
            "cards": len(built["surface_cards"][surface]),
            "senders": len(senders),
            "cards_all": len(built["cards"]),
            "senders_all": len(built["senders"])})
        platforms = collections.Counter()
        for channel, count in built["surface_channels"][surface].items():
            platforms[platform_of(channel)] += count
        for name, count in platforms.most_common():
            out["platforms"].append({"date": date, "surface": surface,
                                     "platform": name, "sends": count})

    for prefix, tally in built["occasions"].items():
        label = report.PREFIX_LABEL.get(prefix, prefix)
        for surface in surfaces:
            count = tally.surfaces.get(surface, 0)
            if not count:
                continue
            cards = {c for c in tally.cards
                     if c in built["surface_cards"][surface]}
            out["categories"].append({
                "date": date, "category": label, "q1_prefix": prefix,
                "surface": surface, "sends": count, "cards": len(cards),
                "cards_all": len(tally.cards)})
        for q1, count in tally.subcategories.items():
            out["subcategories"].append({
                "date": date, "category": label,
                "subcategory": report.sub_label(prefix, q1), "q1_value": q1,
                "sends": count, "cards": len(built["subcategory_cards"][q1])})
    return out


def record(date, measured, replace=True):
    """Merge one day into the ledgers. Returns what it replaced."""
    replaced = 0
    for name in LEDGERS:
        existing = read_ledger(name)
        keep = [r for r in existing if r["date"] != date]
        replaced = max(replaced, len(existing) - len(keep))
        if not replace and len(keep) != len(existing):
            raise SystemExit(
                f"{date} is already recorded. Run again without --once to "
                "replace it, or pick another date.")
        write_ledger(name, keep + measured[name])
    return replaced


# --------------------------------------------------------------------------
# The workbook
# --------------------------------------------------------------------------

def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def pivot(rows, row_key, column_key, value_key="sends"):
    """rows -> {row: {column: total}}, and the column order by grand total."""
    grid = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    for row in rows:
        grid[row[row_key]][row[column_key]] += number(row[value_key])
        totals[row[column_key]] += number(row[value_key])
    return grid, [name for name, _ in totals.most_common()]


ABOUT = [
    ["What this is", "One row per day of card sharing, counted by what the "
     "card was for."],
    ["Where it comes from", "Two files a day: the app's share log and the "
     "website + mobile website pivot."],
    ["How a card gets its category",
     "The catalogue's q1_value is written category_subcategory and is split on "
     "the FIRST underscore only."],
    ["Named categories", "Sixteen: " + ", ".join(sorted(report.CORE_PREFIXES))],
    ["Events cards", "Every other prefix - the twelve month codes, wed, and "
     "anything added to the catalogue later."],
    ["Uncategorised", "Sends whose card id is not in the catalogue. Counted "
     "and shown, never folded into a category."],
    ["Senders", "Distinct IP addresses, from the app file only - the web pivot "
     "carries counts, not sends."],
    ["Add a day", "python3 track_daily.py app.tsv web.tsv"],
    ["Rebuild this file", "python3 track_daily.py --rebuild"],
    ["Source of truth", "reports/daily_*.tsv. This workbook is generated from "
     "them; if they disagree, they are right."],
]


def build_workbook():
    totals = read_ledger("totals")
    if not totals:
        raise SystemExit(
            "Nothing recorded yet, so there is no workbook to build.\n"
            "  python3 track_daily.py app.tsv web.tsv")
    categories = read_ledger("categories")
    subcategories = read_ledger("subcategories")
    platforms = read_ledger("platforms")
    dates = sorted({r["date"] for r in totals})
    surfaces = [APP, WEB] + sorted(
        {r["surface"] for r in totals} - {APP, WEB})

    # --- By date: the top line, one row per day
    by_date_rows = []
    for date in dates:
        day = [r for r in totals if r["date"] == date]
        per = {r["surface"]: r for r in day}
        sends = sum(number(r["sends"]) for r in day)
        by_date_rows.append(
            [date, sends]
            + [number(per.get(s, {}).get("sends", 0)) for s in surfaces]
            + [sum(number(r["categorised"]) for r in day),
               sum(number(r["uncategorised"]) for r in day),
               max(number(r.get("cards_all")) for r in day),
               max(number(r.get("senders_all")) for r in day)])
    grand = ["TOTAL", sum(r[1] for r in by_date_rows)]
    for i in range(2, 2 + len(surfaces) + 2):
        grand.append(sum(r[i] for r in by_date_rows))
    # Cards and senders are distinct counts within a day. A card shared on
    # Monday and again on Tuesday is one card, so these columns do not add up
    # across rows and are left empty rather than filled in with a wrong number.
    grand += ["", ""]
    by_date_rows.append(grand)
    by_date = Sheet(
        "By date",
        ["Date", "Sends"] + surfaces
        + ["Categorised", "Uncategorised", "Cards that day",
           "Senders that day"],
        by_date_rows,
        widths=[13, 10] + [18] * len(surfaces) + [13, 14, 15, 16])

    # --- Categories by date: the tracking view, one column per category
    grid, column_order = pivot(categories, "date", "category")
    rows = []
    for date in dates:
        counts = grid.get(date, collections.Counter())
        rows.append([date, sum(counts.values())]
                    + [counts.get(name, 0) for name in column_order])
    rows.append(["TOTAL", sum(r[1] for r in rows)]
                + [sum(grid[d].get(name, 0) for d in dates)
                   for name in column_order])
    by_category_date = Sheet(
        "Categories by date", ["Date", "Categorised"] + column_order, rows,
        widths=[13, 13] + [max(11, min(24, len(n) + 2)) for n in column_order])

    # --- Category detail: a row per day, category and surface
    detail_rows = []
    for date in dates:
        day = [r for r in categories if r["date"] == date]
        total = sum(number(r["sends"]) for r in day)
        seen = collections.Counter()
        cards = collections.Counter()
        for row in day:
            seen[(row["category"], row["q1_prefix"])] += number(row["sends"])
            key = (row["category"], row["q1_prefix"])
            cards[key] = max(cards[key], number(row.get("cards_all")))
        for (label, prefix), count in seen.most_common():
            per = {r["surface"]: number(r["sends"]) for r in day
                   if r["category"] == label}
            detail_rows.append(
                [date, label, prefix]
                + [per.get(s, 0) for s in surfaces]
                + [count, (count / total) if total else 0,
                   cards[(label, prefix)]])
        detail_rows.append(
            [f"TOTAL {date}", "", ""]
            + [sum(number(r["sends"]) for r in day if r["surface"] == s)
               for s in surfaces]
            + [total, 1.0 if total else 0, ""])
    share_column = 3 + len(surfaces) + 1
    detail = Sheet(
        "Category detail",
        ["Date", "Category", "q1 prefix"] + surfaces
        + ["Total", "Share of day", "Cards"], detail_rows,
        widths=[13, 24, 11] + [18] * len(surfaces) + [10, 13, 8],
        percent_columns=[share_column])

    # --- Sub-categories: the level below, every one of them
    sub_rows = []
    for date in dates:
        day = sorted((r for r in subcategories if r["date"] == date),
                     key=lambda r: (-number(r["sends"]), r["q1_value"]))
        for row in day:
            sub_rows.append([date, row["category"], row["subcategory"],
                             row["q1_value"], number(row["sends"]),
                             number(row["cards"])])
        sub_rows.append([f"TOTAL {date}", "", "", "",
                         sum(number(r["sends"]) for r in day), ""])
    subs = Sheet("Sub-categories",
                 ["Date", "Category", "Sub-category", "q1_value", "Sends",
                  "Cards"], sub_rows, widths=[13, 24, 30, 34, 9, 8])

    # --- Platforms: where the day's sends went
    grid, platform_order = pivot(platforms, "date", "platform")
    rows = []
    for date in dates:
        counts = grid.get(date, collections.Counter())
        rows.append([date, sum(counts.values())]
                    + [counts.get(name, 0) for name in platform_order])
    rows.append(["TOTAL", sum(r[1] for r in rows)]
                + [sum(grid[d].get(name, 0) for d in dates)
                   for name in platform_order])
    platform_sheet = Sheet("Platforms", ["Date", "Sends"] + platform_order,
                           rows, widths=[13, 10] + [12] * len(platform_order))

    about = Sheet("About", ["Field", "Value"], [list(r) for r in ABOUT]
                  + [["Days recorded", len(dates)],
                     ["First day", dates[0]], ["Latest day", dates[-1]]],
                  widths=[26, 96])

    os.makedirs(REPORTS, exist_ok=True)
    write_xlsx(WORKBOOK, [about, by_date, by_category_date, detail, subs,
                          platform_sheet])
    return WORKBOOK, dates


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Track social sends by category, one day at a time.",
        epilog="Two files a day: the app share log, then the web + mobile "
               "web pivot.")
    parser.add_argument("files", nargs="*",
                        help="the app file, then the web + mobile web file")
    parser.add_argument("--date", help="the day these files cover (YYYY-MM-DD)")
    parser.add_argument("--label", action="append", metavar="NAME",
                        help=f"override the surface names (default: {APP}, {WEB})")
    parser.add_argument("--cards", "--catalogue", dest="cards",
                        help="the card list with q1_value in it")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the workbook from the ledgers and stop")
    parser.add_argument("--once", action="store_true",
                        help="refuse to overwrite a day that is already recorded")
    args = parser.parse_args(argv)

    if args.rebuild:
        path, dates = build_workbook()
        print(f"{os.path.relpath(path)} rebuilt from {len(dates)} day(s): "
              f"{dates[0]} to {dates[-1]}.")
        return 0

    if not args.files:
        parser.error("name the day's files, or pass --rebuild")
    labels = args.label or ([APP, WEB] if len(args.files) == 2
                            else [os.path.splitext(os.path.basename(f))[0]
                                  for f in args.files])
    if len(labels) != len(args.files):
        raise SystemExit(f"{len(labels)} --label for {len(args.files)} file(s).")

    categories = report.load_categories(report.find_catalogue(args.cards))
    sends = []
    for path, label in zip(args.files, labels):
        sends.extend(load(report.find_sends(path), label))

    date = date_of(sends, args.date)
    measured = measure(date, sends, categories)
    replaced = record(date, measured, replace=not args.once)

    counted = sum(int(r["sends"]) for r in measured["totals"])
    print(f"{date}: {counted:,} sends recorded"
          + (f" (replacing {replaced} rows already held for that day)"
             if replaced else "") + ".")
    for row in measured["totals"]:
        print(f"  {row['surface']:<20}{row['sends']:>7,} sends, "
              f"{row['categorised']:,} categorised, {row['cards']:,} cards")

    path, dates = build_workbook()
    print(f"{os.path.relpath(path)} now covers {len(dates)} day(s): "
          f"{dates[0]} to {dates[-1]}.")
    print("Ledgers: " + ", ".join(
        os.path.relpath(ledger_path(name)) for name in LEDGERS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
