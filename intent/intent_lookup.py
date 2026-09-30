#!/usr/bin/env python3
"""Look up the categories a search query is most likely asking for.

Usage:
    python3 intent/intent_lookup.py "sorry i forgot your birthday"

Reads the built tables in intent/output and the query phrases in intent/dictionaries.
It needs no card export, so search code and tests can call it directly.

How a query is scored:
    1. Normalise it: decode %xx, fold accents, lowercase, join possessives (mother's -> mothers).
    2. Match the longest known phrases, such as "get well soon" or "husband".
       A phrase maps to one or more intent terms, for example husband -> husband,
       spouse at 0.8 and him at 0.4. Words with no phrase are ignored.
    3. Score each category phrase by phrase. Full-weight terms of a phrase add up, so
       cumpleanos counts as birthday plus spanish. Terms with a weight below 1 are fallbacks,
       so husband scores spouse or him only on categories that are not labelled husband.
       Every term is weighted by its dimension and by how rare it is, so specific terms such
       as belated outweigh broad ones such as birthday.
"""
import csv
import math
import os
import re
import sys
import unicodedata
import urllib.parse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DICT_DIR = os.path.join(HERE, "dictionaries")
OUT_DIR = os.path.join(HERE, "output")

DIMENSION_WEIGHT = {"occasion": 1.0, "recipient": 1.0, "language": 0.8,
                    "tone": 0.6, "month": 0.4, "keyword": 0.5}
MAX_PHRASE_WORDS = 5


def normalise(text):
    text = urllib.parse.unquote(text, encoding="latin-1")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = text.lower().replace("'", "").replace("’", "")
    return re.findall(r"[a-z0-9]+", text)


def singular(word):
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 4 and word[-3] in "sxz":
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")


def parse_terms(value):
    out = []
    for item in value.split():
        term, _, weight = item.partition(":")
        out.append((term, float(weight) if weight else 1.0))
    return out


class IntentIndex:
    def __init__(self, out_dir=OUT_DIR, dict_dir=DICT_DIR):
        with open(os.path.join(out_dir, "intent_table.csv"), encoding="utf-8", newline="") as f:
            terms = list(csv.DictReader(f))
        with open(os.path.join(out_dir, "category_intents.csv"), encoding="utf-8", newline="") as f:
            categories = list(csv.DictReader(f))
        self.load(terms, categories, dict_dir)

    @classmethod
    def from_tables(cls, terms, categories, dict_dir=DICT_DIR):
        """Builds the index from tables already in memory, such as a search index's own copy.

        `terms` are rows of term, dimension and space-separated codes; `categories` are rows
        of code, cards and the facet columns. Both are what build_intent_table writes.
        """
        index = cls.__new__(cls)
        index.load(terms, categories, dict_dir)
        return index

    def load(self, terms, categories, dict_dir=DICT_DIR):
        self.term_dim, self.term_codes = {}, {}
        for r in terms:
            self.term_dim[r["term"]] = r["dimension"]
            self.term_codes[r["term"]] = r["codes"].split()
        self.categories = {}
        for r in categories:
            r = dict(r)
            r["cards"] = int(r["cards"])
            self.categories[r["code"]] = r
        n = len(self.categories)
        self.idf = {t: math.log(1 + n / len(codes)) for t, codes in self.term_codes.items()}

        self.phrases = {}
        with open(os.path.join(dict_dir, "query_phrases.csv"), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                self.phrases[" ".join(normalise(r["phrase"]))] = parse_terms(r["terms"])
        for term in self.term_codes:
            self.phrases.setdefault(term.replace("_", " "), [(term, 1.0)])

    def match(self, query):
        """Returns [(phrase, [(term, weight), ...])] for the phrases found in the query."""
        tokens, found, i = normalise(query), [], 0
        while i < len(tokens):
            for k in range(min(MAX_PHRASE_WORDS, len(tokens) - i), 0, -1):
                phrase = " ".join(tokens[i:i + k])
                terms = self.phrases.get(phrase)
                if terms is None and k == 1:
                    terms = self.phrases.get(singular(phrase))
                if terms is None and k == 1 and ORDINAL.match(phrase):
                    terms = [("milestone", 0.8)]
                if terms is not None:
                    found.append((phrase, [(t, w) for t, w in terms if t in self.term_codes]))
                    i += k
                    break
            else:
                i += 1
        return [(p, t) for p, t in found if t]

    def lookup(self, query, top=10):
        """Returns ranked [(code, score, cards, reasons)] for the categories the query asks for."""
        scores, reasons = defaultdict(float), defaultdict(list)
        for phrase, terms in self.match(query):
            full = defaultdict(lambda: [0.0, []])
            fallback = {}
            for term, weight in terms:
                s = weight * DIMENSION_WEIGHT[self.term_dim[term]] * self.idf[term]
                for code in self.term_codes[term]:
                    if weight >= 1.0:
                        full[code][0] += s
                        full[code][1].append(term)
                    elif s > fallback.get(code, (0.0, ""))[0]:
                        fallback[code] = (s, term)
            for code in set(full) | set(fallback):
                if code in full:
                    s, matched = full[code][0], "+".join(full[code][1])
                else:
                    s, matched = fallback[code]
                scores[code] += s
                reasons[code].append(f"{phrase} = {matched}")
        ranked = sorted(scores, key=lambda c: (-scores[c], -self.categories[c]["cards"], c))
        return [(c, round(scores[c], 2), self.categories[c]["cards"], reasons[c]) for c in ranked[:top]]

    def boost_codes(self, query, keep=0.6, top=10):
        """Categories worth boosting: those scoring at least `keep` times the best score."""
        results = self.lookup(query, top=top)
        if not results:
            return []
        cutoff = results[0][1] * keep
        return [code for code, score, _, _ in results if score >= cutoff]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    query = " ".join(sys.argv[1:])
    index = IntentIndex()
    print(f"query: {query}")
    for phrase, terms in index.match(query):
        print(f"  {phrase!r} -> " + ", ".join(f"{t} ({index.term_dim[t]}{'' if w == 1 else f', {w}'})" for t, w in terms))
    results = index.lookup(query)
    if not results:
        print("no intent found; search falls back to keywords only")
        return
    print(f"\n{'category code':42} {'score':>6} {'cards':>6}  why")
    for code, score, cards, why in results:
        print(f"{code:42} {score:6.2f} {cards:6}  {'; '.join(why)}")
    print(f"\nboost: {' '.join(index.boost_codes(query))}")


if __name__ == "__main__":
    main()
