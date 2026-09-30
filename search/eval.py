#!/usr/bin/env python3
"""Measure the search over real queries, and say what to fix next.

Usage:
    python3 search/eval.py queries.txt            # one query per line, from the search report
    python3 search/eval.py queries.txt --worklist  # only the queries still failing

Nobody has labelled which card is the right answer for "cards for my sister", so this
measures what can be measured without labels, and it is enough to steer the work:

  * **zero-result rate**, the number that matters most. Today's keyword search returns
    nothing for a large share of real queries. The `AND keywords` line is that search,
    scored on the same queries, so every change can be held against it.
  * **intent coverage**, the share of queries whose occasion or recipient was recognised.
    A query with no intent and no text match is a hole in `query_phrases.csv`, and
    --worklist prints those, most frequent first, as the week's editing job.
  * **where the answers come from**: text only, intent only, or both. Intent-only answers
    are the ones keyword search could never have found.
  * **the categories the queries land in**, to catch a term that over-boosts.

A query file may carry counts, as `1843<TAB>happy birthday` or `happy birthday,1843`, and
then every rate is weighted by how often people really type it.
"""
import argparse
import collections
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "intent"))
sys.path.insert(0, HERE)

import index as index_module  # noqa: E402
from intent_lookup import normalise, singular  # noqa: E402
from rank import Search  # noqa: E402

COUNTED = re.compile(r"^\s*(\d+)[\t,;|]\s*(.+?)\s*$")
TRAILING_COUNT = re.compile(r"^\s*(.+?)[\t,;|]\s*(\d+)\s*$")


def read_queries(path):
    """Reads `query`, `count<TAB>query` or `query,count` lines. Returns [(query, count)]."""
    queries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = COUNTED.match(line)
            if match:
                queries.append((match.group(2), int(match.group(1))))
                continue
            match = TRAILING_COUNT.match(line)
            if match:
                queries.append((match.group(1), int(match.group(2))))
                continue
            queries.append((line, 1))
    return queries


def and_keyword_hits(search, query):
    """What all-words keyword search would return: the cards carrying every word."""
    words = [singular(w) for w in normalise(query) if len(w) > 1 or w.isdigit()]
    if not words:
        return 0
    sets = []
    for word in dict.fromkeys(words):
        postings = search.postings.get(word)
        if not postings:
            return 0
        sets.append({doc for doc, _ in postings})
    hits = sets[0]
    for other in sets[1:]:
        hits &= other
        if not hits:
            return 0
    return len(hits)


def run(path, index_path=index_module.INDEX_PATH, top=24, worklist=False, limit=0):
    search = Search(path=index_path)
    queries = read_queries(path)
    if limit:
        queries = queries[:limit]
    if not queries:
        raise SystemExit(f"no queries in {path}")

    total = sum(count for _, count in queries)
    tally = collections.Counter()
    codes = collections.Counter()
    holes = collections.Counter()
    elapsed = []

    for query, count in queries:
        started = time.perf_counter()
        found = search.query(query, top=top)
        elapsed.append((time.perf_counter() - started) * 1000)

        has_intent = bool(found["intent"])
        has_text = bool(found["results"]) and not found["fallback"] and any(
            r["score"] > 0 for r in found["results"])
        keyword_hits = and_keyword_hits(search, query)

        tally["queries"] += count
        tally["keyword_zero"] += count if keyword_hits == 0 else 0
        tally["zero"] += count if found["fallback"] else 0
        tally["intent"] += count if has_intent else 0
        tally["corrected"] += count if found["corrections"] else 0
        if found["fallback"]:
            holes[query] += count
        elif keyword_hits == 0 and has_intent:
            tally["rescued"] += count
        elif has_intent:
            tally["both"] += count
        else:
            tally["text_only"] += count
        for result in found["results"][:5]:
            codes[result["code"]] += count
        tally["shown"] += len(found["results"]) * count

    if worklist:
        print(f"# {len(holes)} queries with no result and no intent: add phrases for these")
        for query, count in holes.most_common():
            print(f"{count}\t{query}")
        return tally

    def share(key):
        return f"{tally[key] / total:6.1%}  ({tally[key]:,} of {total:,})"

    print(f"queries: {len(queries):,} lines, {total:,} searches, "
          f"index: {search.meta['live_cards']:,} live cards")
    print(f"  AND keywords found nothing:   {share('keyword_zero')}   <- today's search")
    print(f"  this search found nothing:    {share('zero')}")
    print(f"  rescued by intent alone:      {share('rescued')}   <- keywords could not")
    print(f"  intent recognised:            {share('intent')}")
    print(f"  spelling corrected:           {share('corrected')}")
    print(f"  answered on text and intent:  {share('both')}")
    print(f"  answered on text alone:       {share('text_only')}")
    print(f"  mean results shown: {tally['shown'] / total:.1f} of {top}")
    elapsed.sort()
    print(f"  latency: median {elapsed[len(elapsed) // 2]:.0f} ms, "
          f"p95 {elapsed[int(len(elapsed) * 0.95)]:.0f} ms")
    print("\ncategories most often in the top 5:")
    for code, count in codes.most_common(15):
        category = search.categories.get(code, {})
        label = " / ".join(x for x in (category.get("group", ""), category.get("event", "")) if x)
        print(f"  {count:8,}  {code:40} {label}")
    if holes:
        print(f"\n{len(holes)} queries still find nothing. Run with --worklist for the list; "
              "the top few:")
        for query, count in holes.most_common(10):
            print(f"  {count:8,}  {query}")
    return tally


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", help="file of queries, one per line, counts optional")
    parser.add_argument("--index", default=index_module.INDEX_PATH)
    parser.add_argument("-n", "--top", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0, help="only the first N lines")
    parser.add_argument("--worklist", action="store_true",
                        help="print the failing queries instead of the summary")
    args = parser.parse_args()
    run(args.queries, args.index, args.top, args.worklist, args.limit)


if __name__ == "__main__":
    main()
