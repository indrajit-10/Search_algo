#!/usr/bin/env python3
"""Build the intent table from the live category codes in a card export.

Usage:
    python3 intent/build_intent_table.py path/to/card_export.csv

Reads the export, keeps live cards (status_id 1, not flagged invalid), labels every
category code (q1_value) with occasion, recipient, tone, language and month terms,
and writes:

    intent/output/category_intents.csv   one row per live category code
    intent/output/intent_table.csv       one row per intent term, with its category codes

Labels come from the hand-edited CSV files in intent/dictionaries. Code parts that no
dictionary covers, mostly niche "national ... day" events, are split into words with a
segmenter trained on the export's own card text, and marked source=auto for review.
"""
import argparse
import collections
import csv
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DICT_DIR = os.path.join(HERE, "dictionaries")
OUT_DIR = os.path.join(HERE, "output")

EVENT_PREFIXES = {"ejan", "efeb", "emar", "eapr", "emay", "ejun",
                  "ejul", "eaug", "esep", "eoct", "enov", "edec"}

# Words that name no intent on their own. Kept in readable names, never used as terms.
NOISE_WORDS = {"day", "days", "week", "month", "national", "international", "world",
               "the", "of", "and", "a", "an", "to", "in", "with", "for", "like", "your",
               "you", "my", "etc", "ecard", "ecards", "card", "cards", "happy", "wishes",
               "all", "or", "on", "at", "by", "is", "it", "be", "go", "up", "out", "are",
               "new", "make", "get", "send", "bring", "visit", "have", "great", "good", "best",
               "take", "give", "share", "awareness", "recognition", "reminder", "electronic",
               "greeting", "greet", "email"}

# Shorthand used inside codes, expanded after word segmentation.
ABBREVIATIONS = {"flwr": "flower", "flwrs": "flowers", "frnd": "friend", "fnd": "friend",
                 "choco": "chocolate", "valen": "valentine", "hallo": "halloween",
                 "bday": "birthday", "nat": "national", "ques": "questions"}

# Auto-split words that name a recipient or tone. Anything else stays a plain keyword.
AUTO_WORD_TERMS = {"hug": "hugs", "hugs": "hugs", "kiss": "kisses", "kisses": "kisses",
                   "angel": "angels", "angels": "angels", "flower": "flowers", "flowers": "flowers",
                   "rose": "roses", "roses": "roses", "smile": "smile", "smiles": "smile",
                   "pet": "pet", "pets": "pet", "dog": "dog", "dogs": "dog", "cat": "cat",
                   "cats": "cat", "kitten": "cat", "teacher": "teacher", "teachers": "teacher",
                   "student": "student", "students": "student", "women": "women", "woman": "women",
                   "men": "men", "relatives": "relatives", "parents": "parent", "parent": "parent",
                   "grandparents": "grandparent", "friend": "friend", "friends": "friend",
                   "family": "family", "families": "family", "mother": "mother", "father": "father",
                   "kids": "kid", "kid": "kid", "child": "child", "children": "child",
                   "elders": "senior", "seniors": "senior", "fun": "funny", "funny": "funny",
                   "laugh": "funny", "joke": "funny", "jokes": "funny", "prayer": "religious",
                   "communion": "religious", "teddy": "teddy"}

DIMENSION_ORDER = ["occasion", "recipient", "tone", "language", "month", "keyword"]


def title_words(words):
    return " ".join(w[:1].upper() + w[1:] for w in words)


def read_csv(name):
    with open(os.path.join(DICT_DIR, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def split_terms(value):
    return [t for t in (value or "").split() if t]


class Dictionaries:
    def __init__(self):
        self.dimension = {r["term"]: r["dimension"] for r in read_csv("dimensions.csv")}
        self.groups = {r["prefix"]: (r["group"], split_terms(r["terms"]))
                       for r in read_csv("groups.csv")}
        self.events = {}
        for r in read_csv("events.csv"):
            entry = (r["name"], split_terms(r["terms"]))
            old = self.events.get(r["key"])
            if old and old != entry:
                print(f"warning: events.csv has conflicting rows for {r['key']!r}", file=sys.stderr)
            self.events[r["key"]] = entry
        self.segments = {r["segment"]: split_terms(r["terms"]) for r in read_csv("segments.csv")}

    def dimension_of(self, term):
        return self.dimension.get(term, "occasion")


class Segmenter:
    """Splits joined words such as 'hugandbearday' using word frequencies from card text."""

    def __init__(self, word_counts):
        total = sum(word_counts.values())
        self.cost = {w: math.log(total / c) for w, c in word_counts.items()
                     if len(w) > 1 or w in ("a", "i")}
        self.max_len = 20

    def word_cost(self, w):
        return self.cost.get(w, 10.0 + 3.0 * len(w))

    def split(self, text):
        n = len(text)
        best = [0.0] + [math.inf] * n
        back = [0] * (n + 1)
        for i in range(1, n + 1):
            for j in range(max(0, i - self.max_len), i):
                c = best[j] + self.word_cost(text[j:i])
                if c < best[i]:
                    best[i], back[i] = c, j
        words, i = [], n
        while i > 0:
            words.append(text[back[i]:i])
            i = back[i]
        return [ABBREVIATIONS.get(w, w) for w in reversed(words)]


def load_export(path):
    csv.field_size_limit(10 ** 9)
    cards = collections.Counter()
    vocab = collections.Counter()
    total_rows = 0
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            total_rows += 1
            text = " ".join([row.get("card_title", ""), row.get("card_description", ""),
                             row.get("card_tags", "")]).lower()
            vocab.update(re.findall(r"[a-z]+", text))
            if row.get("status_id") == "1" and row.get("invalid_card") != "1":
                cards[row["q1_value"].strip()] += 1
    return total_rows, cards, vocab


def parse_code(code, d, seg):
    """Returns (group, readable event name, {dimension: set(terms)}, source)."""
    parts = [p for p in code.split("_") if p]
    prefix, rest = parts[0], parts[1:]
    group, group_terms = d.groups.get(prefix, (prefix, []))
    labels = collections.defaultdict(set)
    source = "curated"
    event_name = ""

    def add(terms):
        for t in terms:
            labels[d.dimension_of(t)].add(t)

    def add_keywords(words):
        for w in words:
            if w in AUTO_WORD_TERMS:
                add([AUTO_WORD_TERMS[w]])
            elif len(w) >= 3 and w not in NOISE_WORDS:
                labels["keyword"].add(w)

    add(group_terms)
    if prefix == "w" and rest:
        add([rest[0]])
        rest = rest[1:]

    if prefix in EVENT_PREFIXES or prefix == "w":
        for k in range(len(rest), 0, -1):
            key = "_".join(rest[:k])
            if key in d.events:
                event_name, terms = d.events[key]
                add(terms)
                rest = rest[k:]
                break
        else:
            if prefix in EVENT_PREFIXES and rest:
                event_name = title_words([w for t in rest for w in seg.split(t)])
                words = []
                while rest and rest[0] not in d.segments:
                    words += seg.split(rest[0])
                    rest = rest[1:]
                add_keywords(words)
                source = "auto"

    i = 0
    while i < len(rest):
        for k in (3, 2, 1):
            key = "_".join(rest[i:i + k])
            if len(rest[i:i + k]) == k and key in d.segments:
                add(d.segments[key])
                i += k
                break
        else:
            token = rest[i]
            if token in d.events:
                add(d.events[token][1])
            elif token not in NOISE_WORDS:
                add_keywords(seg.split(token))
                source = "auto"
            i += 1
    return group, event_name, labels, source


def build(export_path):
    d = Dictionaries()
    total_rows, cards, vocab = load_export(export_path)
    seg = Segmenter(vocab)
    os.makedirs(OUT_DIR, exist_ok=True)

    rows, term_codes, term_dim = [], collections.defaultdict(set), {}
    for code in sorted(cards):
        group, event_name, labels, source = parse_code(code, d, seg)
        rows.append({
            "code": code,
            "cards": cards[code],
            "group": group,
            "event": event_name,
            "occasions": " ".join(sorted(labels["occasion"])),
            "recipients": " ".join(sorted(labels["recipient"])),
            "tones": " ".join(sorted(labels["tone"])),
            "languages": " ".join(sorted(labels["language"])),
            "months": " ".join(sorted(labels["month"])),
            "keywords": " ".join(sorted(labels["keyword"])),
            "source": source,
        })
        for dim, terms in labels.items():
            for t in terms:
                term_codes[t].add(code)
                if term_dim.get(t, "keyword") == "keyword":
                    term_dim[t] = dim

    with open(os.path.join(OUT_DIR, "category_intents.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(OUT_DIR, "intent_table.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term", "dimension", "categories", "cards", "codes"])
        for t in sorted(term_codes, key=lambda t: (DIMENSION_ORDER.index(term_dim[t]), t)):
            codes = sorted(term_codes[t], key=lambda c: (-cards[c], c))
            w.writerow([t, term_dim[t], len(codes), sum(cards[c] for c in codes), " ".join(codes)])

    live = sum(cards.values())
    auto = [r for r in rows if r["source"] == "auto"]
    labelled = [r for r in rows if r["occasions"] or r["recipients"] or r["tones"]]
    print(f"export rows: {total_rows}, live cards: {live}, live category codes: {len(rows)}")
    print(f"codes with an occasion, recipient or tone label: {len(labelled)} "
          f"({sum(r['cards'] for r in labelled)} cards)")
    print(f"codes with auto-derived parts to review: {len(auto)} ({sum(r['cards'] for r in auto)} cards)")
    print(f"intent terms: {len(term_codes)} -> {os.path.relpath(OUT_DIR)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", help="card export CSV, for example card_24092026.csv")
    build(parser.parse_args().export)


if __name__ == "__main__":
    main()
