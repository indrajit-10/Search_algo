#!/usr/bin/env python3
"""
merge_catalogue.py  --  keep one card list, topped up as new cards go live.

    python3 merge_catalogue.py data/ACTIVE_CARDS.xlsx data/new_cards_*.tsv

The full export is a snapshot. Cards published after it was taken have no
q1_value, so their sends land in "uncategorised" and drop out of every category
figure - on 8 August that was 29 sends, one card alone taking 19 of them.

So: merge. Later files win, which makes this safe to re-run with the same
arguments and safe to hand a re-categorised card. The result goes to
data/cards_catalogue.tsv, which the report and the tracker now prefer over the
raw export.

Any input readable by social_sends_report works - .xlsx, .csv, .tsv - as long
as it has a card number column and a q1_value column.
"""

import argparse
import os
import sys

import social_sends_report as report

OUT = os.path.join(report.DATA_DIR, "cards_catalogue.tsv")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge card exports into one catalogue.")
    parser.add_argument("sources", nargs="+",
                        help="card lists, oldest first - later files win")
    parser.add_argument("--out", default=OUT, help=f"where to write ({OUT})")
    args = parser.parse_args(argv)

    merged, origin = {}, {}
    for source in args.sources:
        cards = report.load_categories(source)
        added = changed = 0
        for number, q1 in cards.items():
            if number not in merged:
                added += 1
            elif merged[number] != q1:
                changed += 1
                print(f"  {number}: {merged[number]} -> {q1}")
            merged[number] = q1
            origin[number] = source
        print(f"{source}: {len(cards):,} cards, {added:,} new, {changed} recategorised")

    if not merged:
        raise SystemExit("Nothing to merge - no card had both a number and a q1_value.")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("card_number\tq1_value\n")
        # Shortest first, then lexicographic: card numbers are strings, and
        # sorting them as strings alone would put 1000 before 999.
        for number in sorted(merged, key=lambda n: (len(n), n)):
            handle.write(f"{number}\t{merged[number]}\n")

    print(f"\n{args.out}: {len(merged):,} cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
