#!/usr/bin/env python3
"""Build the search index from a card export.

Usage:
    python3 search/index.py path/to/card_export.csv

One pass over the export produces one file, search/output/index.json.gz, holding
everything ranking needs:

  * a document per live card: its id, title, tags, description snippet, category code,
    quality prior, format and the months it belongs to;
  * a BM25F posting list over four fields at different weights, so a word in the title
    counts for more than the same word in the description;
  * the category facets -- group, event, occasion, recipient, tone, language, month --
    read from the category code by the same dictionaries the intent table uses, so a card
    filed under `birth_belated` is searchable as a belated birthday card even though no
    column on it says so;
  * the intent table itself, so ranking needs no second build step.

Index time is where search earns its results. Anything that can be decided from the export
once is decided here rather than per query.
"""
import argparse
import collections
import csv
import datetime
import gzip
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "intent"))
sys.path.insert(0, HERE)

import columns as columns_module  # noqa: E402
from build_intent_table import DIMENSION_ORDER, Dictionaries, Segmenter, parse_code  # noqa: E402
from intent_lookup import normalise, singular  # noqa: E402

OUT_DIR = os.path.join(HERE, "output")
INDEX_PATH = os.path.join(OUT_DIR, "index.json.gz")

# A word in the title says far more about a card than the same word in the description.
# The category field carries the facets read from the code, so "belated" is searchable text.
FIELD_WEIGHT = {"title": 5.0, "tags": 3.0, "category": 2.5, "description": 1.0}

# Kept for display only; the whole description is still indexed.
SNIPPET_CHARS = 240

MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
          "december": 12}


def tokens(text):
    """Query and document text go through the same normaliser, so they always agree."""
    return [singular(t) for t in normalise(text or "") if len(t) > 1 or t.isdigit()]


def read_export(path, cols=None):
    """Streams the export once. Returns (docs, vocab, stats).

    Text is tokenised as it is read and the raw description is dropped, so memory stays
    flat on a large export. `vocab` counts words across every row, live or not, because
    the word segmenter that names niche categories learns from the site's own writing.
    """
    csv.field_size_limit(10 ** 9)
    docs, vocab = [], collections.Counter()
    stats = collections.Counter()
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = cols or columns_module.resolve(reader.fieldnames)
        for row in reader:
            stats["rows"] += 1
            title = cols.get(row, "title")
            description = cols.get(row, "description")
            tags = cols.get(row, "tags")
            vocab.update(t for t in normalise(f"{title} {description} {tags}") if t.isalpha())
            if not cols.is_live(row):
                stats["skipped"] += 1
                continue
            code = cols.get(row, "code").strip()
            if not code:
                stats["no_category"] += 1
            docs.append({
                "id": cols.get(row, "id") or str(stats["rows"]),
                "title": title.strip()[:SNIPPET_CHARS],
                "snippet": " ".join(description.split())[:SNIPPET_CHARS],
                "code": code,
                "format": cols.get(row, "format").strip().lower(),
                "created": cols.get(row, "created").strip(),
                "fields": {"title": tokens(title), "tags": tokens(tags),
                           "description": tokens(description)},
                "popularity": to_number(cols.get(row, "popularity")),
            })
    return docs, vocab, stats, cols


def to_number(value):
    try:
        return max(0.0, float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return 0.0


def quality_priors(docs, has_popularity):
    """Maps the popularity column onto 0..1, or gives every card the same neutral prior.

    Popularity is long-tailed, so it is compressed with log1p before scaling: the most
    sent card in the catalogue should outrank a quiet one, not bury every other result.
    """
    if not has_popularity:
        for doc in docs:
            doc["quality"] = 0.5
        return
    top = math.log1p(max((d["popularity"] for d in docs), default=0.0)) or 1.0
    for doc in docs:
        doc["quality"] = round(min(1.0, math.log1p(doc["popularity"]) / top), 4)


def recency_priors(docs):
    """0..1 by how recently a card was added, when the export dates them. Newest = 1."""
    dates = {}
    for doc in docs:
        stamp = parse_date(doc["created"])
        dates[id(doc)] = stamp
    known = [d for d in dates.values() if d is not None]
    if not known:
        for doc in docs:
            doc["recency"] = 0.0
        return
    newest, oldest = max(known), min(known)
    span = max(1, (newest - oldest).days)
    for doc in docs:
        stamp = dates[id(doc)]
        doc["recency"] = 0.0 if stamp is None else round(1 - (newest - stamp).days / span, 4)


def parse_date(value):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y",
                "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(value[:len(fmt) + 2].strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def label_categories(codes, vocab):
    """Reads every live category code into facets, using the intent dictionaries."""
    d = Dictionaries()
    seg = Segmenter(vocab)
    categories = {}
    for code in sorted(codes):
        group, event, labels, source = parse_code(code, d, seg) if code else ("", "", {}, "none")
        categories[code] = {
            "code": code,
            "cards": codes[code],
            "group": group,
            "event": event,
            "occasions": " ".join(sorted(labels.get("occasion", ()))),
            "recipients": " ".join(sorted(labels.get("recipient", ()))),
            "tones": " ".join(sorted(labels.get("tone", ()))),
            "languages": " ".join(sorted(labels.get("language", ()))),
            "months": " ".join(sorted(labels.get("month", ()))),
            "keywords": " ".join(sorted(labels.get("keyword", ()))),
            "source": source,
        }
    return categories


def intent_terms(categories):
    """The intent table: one row per term, with the categories it points at."""
    term_codes, term_dim = collections.defaultdict(set), {}
    for category in categories.values():
        for dim in DIMENSION_ORDER:
            for term in category[dim_field(dim)].split():
                term_codes[term].add(category["code"])
                if term_dim.get(term, "keyword") == "keyword":
                    term_dim[term] = dim
    rows = []
    for term in sorted(term_codes, key=lambda t: (DIMENSION_ORDER.index(term_dim[t]), t)):
        codes = sorted(term_codes[term], key=lambda c: (-categories[c]["cards"], c))
        rows.append({"term": term, "dimension": term_dim[term], "codes": " ".join(codes),
                     "categories": len(codes),
                     "cards": sum(categories[c]["cards"] for c in codes)})
    return rows


def dim_field(dim):
    return {"occasion": "occasions", "recipient": "recipients", "tone": "tones",
            "language": "languages", "month": "months", "keyword": "keywords"}[dim]


def category_text(category):
    """The words a category contributes to a card's searchable text."""
    parts = [category["group"], category["event"]]
    for dim in DIMENSION_ORDER:
        parts.append(category[dim_field(dim)].replace("_", " "))
    return " ".join(parts)


def category_months(category):
    return sorted({MONTHS[t] for t in category["months"].split() if t in MONTHS})


def build_postings(docs, categories):
    """BM25F postings: one weighted term frequency per (term, card), plus card lengths."""
    postings = collections.defaultdict(list)
    lengths = []
    for i, doc in enumerate(docs):
        category = categories.get(doc["code"], {})
        fields = dict(doc["fields"])
        fields["category"] = tokens(category_text(category)) if category else []
        weighted = collections.defaultdict(float)
        for field, field_tokens in fields.items():
            weight = FIELD_WEIGHT[field]
            for token in field_tokens:
                weighted[token] += weight
        for token, tf in weighted.items():
            postings[token].append([i, round(tf, 3)])
        lengths.append(round(sum(weighted.values()), 3))
    return postings, lengths


def build(export_path, out_path=INDEX_PATH, quiet=False):
    docs, vocab, stats, cols = read_export(export_path)
    if not docs:
        raise SystemExit("no live cards in the export: nothing to index")
    say = (lambda *a: None) if quiet else print
    say(cols.report())

    quality_priors(docs, "popularity" in cols.found)
    recency_priors(docs)

    counts = collections.Counter(doc["code"] for doc in docs if doc["code"])
    categories = label_categories(counts, vocab)
    postings, lengths = build_postings(docs, categories)

    code_docs = collections.defaultdict(list)
    for i, doc in enumerate(docs):
        if doc["code"]:
            code_docs[doc["code"]].append(i)
    for code in code_docs:
        code_docs[code].sort(key=lambda i: -docs[i]["quality"])

    for doc in docs:
        category = categories.get(doc["code"], {})
        doc["months"] = category_months(category) if category else []
        del doc["fields"], doc["popularity"], doc["created"]

    index = {
        "meta": {
            "built": datetime.datetime.now().isoformat(timespec="seconds"),
            "export": os.path.basename(export_path),
            "columns": cols.found,
            "missing_columns": cols.missing,
            "rows": stats["rows"],
            "live_cards": len(docs),
            "categories": len(categories),
            "field_weight": FIELD_WEIGHT,
            "avg_length": round(sum(lengths) / len(lengths), 3),
        },
        "docs": docs,
        "postings": postings,
        "lengths": lengths,
        "code_docs": code_docs,
        "categories": [categories[c] for c in sorted(categories)],
        "terms": intent_terms(categories),
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(index, f)

    auto = sum(1 for c in categories.values() if c["source"] == "auto")
    say(f"export rows: {stats['rows']}, live cards: {len(docs)}, "
        f"skipped as not live: {stats['skipped']}")
    if stats["no_category"]:
        say(f"cards with no category code: {stats['no_category']} (keyword search only)")
    say(f"categories: {len(categories)} ({auto} with auto-derived parts to review), "
        f"intent terms: {len(index['terms'])}")
    say(f"index terms: {len(postings)} -> {out_path} "
        f"({os.path.getsize(out_path) / 1e6:.1f} MB)")
    return index


def load(path=INDEX_PATH):
    if not os.path.exists(path):
        raise SystemExit(f"no index at {path}: run python3 search/index.py <card_export.csv>")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", help="card export CSV, for example card_24092026.csv")
    parser.add_argument("-o", "--out", default=INDEX_PATH, help="where to write the index")
    args = parser.parse_args()
    build(args.export, args.out)


if __name__ == "__main__":
    main()
