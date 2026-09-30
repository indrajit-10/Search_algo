#!/usr/bin/env python3
"""Pack the search index and the query phrases into one compact JSON file for the browser.

Same data the Python ranker uses, just smaller: repeated title strings are pooled, posting
lists are flattened with delta-encoded card numbers, and floats are rounded to the precision
ranking actually needs.
"""
import csv
import gzip
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "intent"))
from intent_lookup import normalise, parse_terms  # noqa: E402

DIMENSIONS = ["occasion", "recipient", "tone", "language", "month", "keyword"]
FACET_COLUMNS = ["occasions", "recipients", "tones", "languages", "months", "keywords"]


def pack(index_path, out_path):
    with gzip.open(index_path, "rt", encoding="utf-8") as f:
        index = json.load(f)

    codes = [c["code"] for c in index["categories"]]
    code_number = {code: i for i, code in enumerate(codes)}

    titles, title_number = [], {}
    formats, format_number = [], {}
    docs = []
    for doc in index["docs"]:
        if doc["title"] not in title_number:
            title_number[doc["title"]] = len(titles)
            titles.append(doc["title"])
        if doc["format"] not in format_number:
            format_number[doc["format"]] = len(formats)
            formats.append(doc["format"])
        docs.append([
            int(doc["id"].lstrip("C") or 0),
            title_number[doc["title"]],
            code_number.get(doc["code"], -1),
            round(doc["quality"], 4),
            round(doc["recency"], 4),
            format_number[doc["format"]],
        ])

    # term -> [firstCard, tf*10, deltaToNextCard, tf*10, ...]
    postings = {}
    for term, entries in index["postings"].items():
        flat, previous = [], 0
        for card, tf in entries:
            flat.append(card - previous)
            flat.append(int(round(tf * 10)))
            previous = card
        postings[term] = flat

    categories = []
    for category in index["categories"]:
        categories.append([
            category["group"],
            category["event"],
            category["cards"],
        ] + [category[column] for column in FACET_COLUMNS])

    terms = [[row["term"], DIMENSIONS.index(row["dimension"]),
              [code_number[c] for c in row["codes"].split()]]
             for row in index["terms"]]

    phrases = []
    with open(os.path.join(REPO, "intent/dictionaries/query_phrases.csv"),
              encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = " ".join(normalise(row["phrase"]))
            if key:
                phrases.append([key, [[t, w] for t, w in parse_terms(row["terms"])]])

    packed = {
        "meta": {
            "cards": index["meta"]["live_cards"],
            "rows": index["meta"]["rows"],
            "categories": len(codes),
            "avgLength": index["meta"]["avg_length"],
            "fieldWeight": index["meta"]["field_weight"],
        },
        "dimensions": DIMENSIONS,
        "codes": codes,
        "cats": categories,
        "titles": titles,
        "formats": formats,
        "docs": docs,
        "lengths": index["lengths"],
        "postings": postings,
        "codeDocs": [index["code_docs"].get(code, []) for code in codes],
        "terms": terms,
        "phrases": phrases,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(packed, f, separators=(",", ":"))
    size = os.path.getsize(out_path) / 1e6
    print(f"{len(docs):,} cards, {len(codes):,} categories, {len(postings):,} terms, "
          f"{len(titles):,} distinct titles, {len(phrases):,} phrases")
    print(f"-> {out_path} ({size:.2f} MB)")


if __name__ == "__main__":
    pack(sys.argv[1], sys.argv[2])
