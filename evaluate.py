"""
evaluate.py  --  run EVERY query in the production log through both engines.

    python evaluate.py card_database.csv
    python evaluate.py card_database.csv --show 40    # list more of each section

The test suite in search_engine.py asserts on a couple of dozen hand-picked
queries. That proves the fixes work; it does not prove they work across the
whole head of real traffic, and it cannot find a query that USED to work and
now does not. This sweeps the entire fixture, weights everything by how often
users actually searched it, and reports regressions as loudly as wins.

Read the fixture with QUOTE_NONE. The log contains

    "><script>alert('xss by vipe

which is an unbalanced double quote, and Python's csv module will happily
swallow the next several hundred rows into that one field. A silent 528 -> 126
row drop is exactly the kind of thing that makes an evaluation look clean.
"""

import collections
import csv
import sys
import time

import search_engine as se

FIXTURE = "fixtures/real_queries.tsv"
OVER_MATCH = 0.20        # matching >20% of the catalogue tells the user nothing


def load_queries(path=FIXTURE):
    rows = []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = (l for l in handle if not l.startswith("#"))
        for row in csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE):
            if not row.get("query") and row.get("query") != "":
                continue
            try:
                rows.append((row["query"], int(row["times"]), int(row["results"])))
            except (TypeError, ValueError):
                continue
    return rows


def rule(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else "card_database.csv"
    show = 25
    for arg in sys.argv[1:]:
        if arg.startswith("--show="):
            show = int(arg.split("=", 1)[1])

    csv.field_size_limit(10 ** 9)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    index = se.SearchIndex(rows)
    queries = load_queries()

    print(f"Indexed {index.total:,} live cards")
    print(f"Replaying {len(queries):,} real queries "
          f"({sum(t for _, t, _ in queries):,} searches)")

    results = []
    start = time.perf_counter()
    for query, times, old_n in queries:
        out = se.search(index, query, limit=20)
        results.append((query, times, old_n, len(out["results"]),
                        out["strategy"], out["corrections"]))
    elapsed = time.perf_counter() - start
    print(f"Replayed in {elapsed:.1f}s ({1000*elapsed/max(len(queries),1):.1f} ms/query)")

    searches = sum(t for _, t, _, _, _, _ in results) or 1

    # ---------------------------------------------------------------- zero rate
    rule("A. ZERO-RESULT RATE  (the number that matters)")
    old_zero = [r for r in results if r[2] == 0]
    new_zero = [r for r in results if r[3] == 0]
    old_vol = sum(r[1] for r in old_zero)
    new_vol = sum(r[1] for r in new_zero)
    print(f"  queries returning nothing:   OLD {len(old_zero):>4}/{len(results)}"
          f"   ->   NEW {len(new_zero):>4}/{len(results)}")
    print(f"  searches hitting the carousel: OLD {old_vol:>6,} ({100*old_vol/searches:.1f}%)"
          f"  ->  NEW {new_vol:>6,} ({100*new_vol/searches:.1f}%)")
    print(f"  searches rescued from an empty page: {old_vol - new_vol:,}")

    # ------------------------------------------------------------- regressions
    rule("B. REGRESSIONS  (worked before, broken now - these block a rollout)")
    # The empty query is not a regression. Production answered it with all
    # 13,035 cards; returning nothing for nothing typed is the correct answer.
    broke = sorted([r for r in results if r[2] > 0 and r[3] == 0 and r[0].strip()],
                   key=lambda r: -r[1])
    if broke:
        for query, times, old_n, _, _, _ in broke[:show]:
            print(f"  {times:>5} searches  {query!r:<44} old={old_n:,} -> NEW 0")
        print(f"\n  {len(broke)} regressions, {sum(r[1] for r in broke):,} searches affected")
    else:
        print("  None. No query that returned results before returns nothing now.")

    # -------------------------------------------------------------- still zero
    rule("C. STILL ZERO  (content gaps, not search bugs)")
    still = sorted([r for r in results if r[2] == 0 and r[3] == 0], key=lambda r: -r[1])
    for query, times, _, _, _, _ in still[:show]:
        print(f"  {times:>5} searches  {query!r}")
    print(f"\n  {len(still)} queries, {sum(r[1] for r in still):,} searches.")
    print("  These need CARDS, not code - hand them to whoever commissions artwork.")

    # -------------------------------------------------------------------- wins
    rule("D. FIXED  (returned nothing before, returns results now)")
    fixed = sorted([r for r in results if r[2] == 0 and r[3] > 0], key=lambda r: -r[1])
    for query, times, _, new_n, strategy, corr in fixed[:show]:
        note = f"  [{list(corr.items())[0][0]} -> {list(corr.items())[0][1]}]" if corr else ""
        print(f"  {times:>5} searches  {query!r:<40} -> {new_n:>2} results "
              f"via {strategy!r}{note}")
    print(f"\n  {len(fixed)} queries fixed, {sum(r[1] for r in fixed):,} searches.")

    # ------------------------------------------------------------- over-match
    rule("E. OVER-MATCHING IN THE OLD ENGINE  (results, but useless ones)")
    limit = index.total * OVER_MATCH
    over = sorted([r for r in results if r[2] > limit], key=lambda r: -r[1])
    for query, times, old_n, new_n, _, _ in over[:show]:
        print(f"  {times:>5} searches  {query!r:<40} old matched {old_n:>6,} "
              f"({100*old_n/index.total:>4.0f}% of catalogue)")
    print(f"\n  {len(over)} queries matched over {OVER_MATCH:.0%} of the catalogue,"
          f" {sum(r[1] for r in over):,} searches.")
    print("  A query matching half the shelf is indistinguishable from a random page.")

    # ------------------------------------------------------------- strategies
    rule("F. WHICH LADDER RUNG ANSWERED  (how often the query was relaxed)")
    tally = collections.Counter()
    for _, times, _, _, strategy, _ in results:
        tally[strategy] += times
    for strategy, volume in tally.most_common():
        print(f"  {100*volume/searches:>5.1f}%  {volume:>7,} searches   {strategy}")

    corrected = sum(t for _, t, _, _, _, c in results if c)
    print(f"\n  spell correction fired on {100*corrected/searches:.1f}% of searches")


if __name__ == "__main__":
    main()
