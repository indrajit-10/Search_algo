#!/usr/bin/env python3
"""Rank cards for a query against the index built by search/index.py.

Usage:
    python3 search/rank.py "sorry i forgot your birthday"
    python3 search/rank.py --explain "funny bday cards for hubby"

Four stages, in this order:

1. **Understand the query.** Normalise it, fix a misspelled word against the words the
   catalogue actually uses, then read its intent: the occasion, recipient, tone, language
   or milestone it asks for, and the categories that serve them.

2. **Recall widely.** Take the union of two candidate sets, never the intersection: the
   cards whose text matches the words, and the best cards in the categories the intent
   points at. A query whose words appear on no card, such as "sorry i forgot your
   birthday", still has candidates. Keyword search alone returns nothing for it.

3. **Rank on one blended score**, every part of it on a 0..1 scale so the weights below
   mean what they say: text match, intent match, how good the card is, whether its
   occasion is near, how new it is, plus two small bonuses for a title the query names
   outright and a format the query asked for.

4. **Lay out the page.** Cap how many cards one category may take, so the first page shows
   the range of what the site has rather than twelve variations of one card.

Every stage reports why it did what it did, and --explain prints it. A search nobody can
explain is a search nobody can improve.
"""
import argparse
import collections
import datetime
import heapq
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "intent"))
sys.path.insert(0, HERE)

import index as index_module  # noqa: E402
from intent_lookup import IntentIndex, normalise, singular  # noqa: E402

# BM25 constants. k1 caps how much a repeated word can add, b how much length is punished.
K1, B = 1.2, 0.75

# The blended score. These sum to 1, so every result lands in 0..1 before bonuses.
# Tune them with search/eval.py and the click logs, not by taste.
W_LEX = 0.40       # the query's words, matched on title, tags, category and description
W_INTENT = 0.35    # the categories the query asks for, whether or not its words appear
W_QUALITY = 0.14   # how well the card does with people, from the export's popularity column
W_SEASON = 0.08    # how near the card's occasion is, for queries that name no date
W_FRESH = 0.03     # how recently the card was added

TITLE_PHRASE_BONUS = 0.10   # the query, in order, inside the title: someone named a card
FORMAT_BONUS = 0.08         # the query asked for a video and this is one

FLOOR = 0.30                # a result scoring under this much of the best one is not shown
FLOOR_KEEPS = 8             # unless the page would fall below this many results

INTENT_TOP = 10             # categories considered per query
INTENT_KEEP = 0.6           # and kept, if they score this much of the best one
PER_CODE_CANDIDATES = 60    # best cards pulled from each of those categories
MAX_LEX_CANDIDATES = 400    # text matches carried into the blended score, best BM25 first
PER_CODE_RESULTS = 3        # and shown from each, before the rest are pushed down
MIN_CORRECTION_LENGTH = 4   # shorter words are left alone; the fix is worse than the typo
MIN_CORRECTION_FREQUENCY = 3  # and a fix has to be a word the catalogue really uses

ALPHABET = "abcdefghijklmnopqrstuvwxyz"

# Words that name how a card is made, not what it is about. They describe a filter.
FORMAT_WORDS = {"video", "animated", "animation", "musical", "music", "song", "songs",
                "printable", "print", "talking", "slideshow", "photo", "gif", "flash",
                "interactive", "youtube", "static", "postcard", "scrapbook"}


def edits1(word):
    """Every word one edit away: the typos worth fixing are nearly all one edit."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    out |= {a + c + b[1:] for a, b in splits if b for c in ALPHABET}
    out |= {a + c + b for a, b in splits for c in ALPHABET}
    return out - {word}


def season_weight(months, today_month):
    """How near a card's occasion is. People shop ahead, so next month beats last month."""
    ahead = {0: 1.0, 1: 0.7, 2: 0.35, 11: 0.25}
    return max((ahead.get((m - today_month) % 12, 0.0) for m in months), default=0.0)


class Search:
    def __init__(self, index=None, path=index_module.INDEX_PATH, today=None):
        self.index = index if index is not None else index_module.load(path)
        self.docs = self.index["docs"]
        self.postings = self.index["postings"]
        self.lengths = self.index["lengths"]
        self.code_docs = self.index["code_docs"]
        self.meta = self.index["meta"]
        self.avgdl = self.meta["avg_length"] or 1.0
        self.today = today or datetime.date.today()
        self.intent = IntentIndex.from_tables(self.index["terms"], self.index["categories"])
        self.categories = self.intent.categories
        self.has_format = "format" not in self.meta["missing_columns"]
        # Words the intent dictionaries know. A phrase such as "hubby" is spelled correctly
        # even when no card says it, so spelling must not touch it.
        self.phrase_words = {w for phrase in self.intent.phrases for w in phrase.split()}
        self.popular = sorted(range(len(self.docs)),
                              key=lambda i: (-self.docs[i]["quality"], i))

    # -- stage 1: understand the query ------------------------------------------------

    def frequency(self, word):
        return len(self.postings.get(word) or self.postings.get(singular(word)) or ())

    def correct(self, words):
        """Fixes a word the catalogue never uses to the nearest word it uses a lot."""
        fixed, corrections = [], []
        for word in words:
            known = (word in self.phrase_words or self.frequency(word) > 0)
            if known or len(word) < MIN_CORRECTION_LENGTH or not word.isalpha():
                fixed.append(word)
                continue
            best, best_frequency = None, MIN_CORRECTION_FREQUENCY - 1
            for candidate in edits1(word):
                frequency = self.frequency(candidate)
                if frequency > best_frequency or (best is not None
                                                  and frequency == best_frequency
                                                  and candidate < best):
                    best, best_frequency = candidate, frequency
            if best is None:
                for candidate in edits1(word):
                    if candidate in self.phrase_words:
                        best = candidate
                        break
            fixed.append(best or word)
            if best:
                corrections.append((word, best))
        return fixed, corrections

    # -- stage 2 and 3: recall, then rank ---------------------------------------------

    def lexical(self, words):
        """BM25F over the four indexed fields. Returns scores and the words each card matched."""
        scores = collections.defaultdict(float)
        matched = collections.defaultdict(list)
        total = len(self.docs)
        for word in dict.fromkeys(words):
            postings = self.postings.get(word)
            if not postings:
                continue
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for doc, tf in postings:
                norm = tf + K1 * (1 - B + B * self.lengths[doc] / self.avgdl)
                scores[doc] += idf * tf * (K1 + 1) / norm
                matched[doc].append(word)
        return scores, matched

    def intent_codes(self, query):
        """The categories the query asks for, scored 0..1 against the best one."""
        results = self.intent.lookup(query, top=INTENT_TOP)
        if not results:
            return {}, []
        best = results[0][1] or 1.0
        codes = {code: round(score / best, 4) for code, score, _, _ in results
                 if score >= best * INTENT_KEEP}
        why = [f"{code} ({'; '.join(reasons)})" for code, _, _, reasons in results
               if code in codes]
        return codes, why

    def query(self, text, top=24, per_category=PER_CODE_RESULTS, explain=False):
        words = normalise(text)
        fixed, corrections = self.correct(words)
        understood = " ".join(fixed)
        lex_words = [singular(w) for w in fixed if len(w) > 1 or w.isdigit()]

        phrases = self.intent.match(understood)
        codes, why = self.intent_codes(understood)

        # A query that names a date or an occasion has said when it means. Only a query that
        # does not -- "cards for my sister", "funny" -- gets the calendar's opinion.
        dated = any(self.intent.term_dim.get(term) in ("month", "occasion")
                    for _, terms in phrases for term, _ in terms)
        formats = {w for w in fixed if w in FORMAT_WORDS} if self.has_format else set()

        scores, matched = self.lexical(lex_words)
        best_lex = max(scores.values(), default=0.0)

        # Retrieve, then rerank. A word like "happy" matches a large part of the catalogue,
        # and the cards it matches weakly are not answers, so only the best text matches are
        # carried into the blended score. Cards found by intent are added whole, below.
        candidates = set(heapq.nlargest(MAX_LEX_CANDIDATES, scores, key=scores.get)
                         if len(scores) > MAX_LEX_CANDIDATES else scores)
        for code in codes:
            candidates.update(self.code_docs.get(code, ())[:PER_CODE_CANDIDATES])

        fallback = None
        if not candidates:
            fallback = "popular"
            candidates = set(self.popular[:top * 4])

        ranked = []
        for doc_id in candidates:
            doc = self.docs[doc_id]
            lex = scores.get(doc_id, 0.0) / best_lex if best_lex else 0.0
            intent = codes.get(doc["code"], 0.0)
            season = 0.0 if dated else season_weight(doc["months"], self.today.month)
            score = (W_LEX * lex + W_INTENT * intent + W_QUALITY * doc["quality"]
                     + W_SEASON * season + W_FRESH * doc["recency"])
            bonuses = []
            if lex_words and self.title_names(doc["title"], lex_words):
                score += TITLE_PHRASE_BONUS
                bonuses.append("title")
            if formats and any(f in doc["format"] for f in formats):
                score += FORMAT_BONUS
                bonuses.append("format")
            result = {
                "id": doc["id"],
                "title": doc["title"],
                "snippet": doc["snippet"],
                "code": doc["code"],
                "group": self.categories.get(doc["code"], {}).get("group", ""),
                "event": self.categories.get(doc["code"], {}).get("event", ""),
                "score": round(score, 4),
            }
            if explain:
                result["why"] = {
                    "lexical": round(lex, 4), "intent": intent,
                    "quality": doc["quality"], "season": round(season, 4),
                    "fresh": doc["recency"], "bonus": bonuses,
                    "words": sorted(set(matched.get(doc_id, ()))),
                }
            ranked.append(result)

        ranked.sort(key=lambda r: (-r["score"], r["code"], r["id"]))
        kept = apply_floor(ranked)
        page = diversify(kept, per_category)[:top]
        return {
            "query": text,
            "understood": understood,
            "corrections": corrections,
            "phrases": [(phrase, [t for t, _ in terms]) for phrase, terms in phrases],
            "intent": sorted(codes.items(), key=lambda kv: -kv[1]),
            "why_intent": why,
            "seasonal": not dated,
            "candidates": len(candidates),
            "kept": len(kept),
            "fallback": fallback,
            "results": page,
            "facets": facets(page, self.categories),
        }

    @staticmethod
    def title_names(title, lex_words):
        """True when the query's words appear in the title, in order and together."""
        if len(lex_words) > 8:
            return False
        title_words = [singular(w) for w in normalise(title)]
        n = len(lex_words)
        return any(title_words[i:i + n] == lex_words for i in range(len(title_words) - n + 1))


def apply_floor(ranked, floor=FLOOR, keeps=FLOOR_KEEPS):
    """Drops the long tail that matched a query's least meaningful word.

    A card matching only "your" in a search for "sorry i forgot your birthday" is not a
    result, it is noise below a good answer. The floor is relative to the best score, so it
    adapts to the query, and it never cuts the page below `keeps` results.
    """
    if not ranked:
        return ranked
    cutoff = ranked[0]["score"] * floor
    above = [r for r in ranked if r["score"] >= cutoff]
    return above if len(above) >= keeps else ranked[:keeps]


def diversify(ranked, per_category):
    """Holds each category to `per_category` cards, pushing the rest below the others.

    Nothing is dropped: page one shows the range, deeper pages show the depth.
    """
    if per_category <= 0:
        return ranked
    kept, overflow, seen = [], [], collections.Counter()
    for result in ranked:
        if seen[result["code"]] < per_category:
            seen[result["code"]] += 1
            kept.append(result)
        else:
            overflow.append(result)
    return kept + overflow


def facets(results, categories):
    """What the results are, so the page can offer a narrower search."""
    counts = {"group": collections.Counter(), "occasion": collections.Counter(),
              "recipient": collections.Counter(), "tone": collections.Counter(),
              "language": collections.Counter()}
    for result in results:
        category = categories.get(result["code"])
        if not category:
            continue
        counts["group"][category["group"]] += 1
        for dim, column in (("occasion", "occasions"), ("recipient", "recipients"),
                            ("tone", "tones"), ("language", "languages")):
            for term in category[column].split():
                counts[dim][term] += 1
    return {dim: c.most_common(8) for dim, c in counts.items() if c}


def report(found, explain=False):
    print(f"query: {found['query']}")
    if found["corrections"]:
        print("  did you mean: " + ", ".join(f"{a} -> {b}" for a, b in found["corrections"]))
    if found["phrases"]:
        print("  read as: " + ", ".join(f"{p} = {'+'.join(t)}" for p, t in found["phrases"]))
    if found["intent"]:
        print("  categories: " + ", ".join(f"{c} {s}" for c, s in found["intent"]))
    else:
        print("  no intent found: ranking on words, quality and season only")
    if found["fallback"]:
        print(f"  nothing matched: showing {found['fallback']} cards")
    print(f"  candidates: {found['candidates']}, above the floor: {found['kept']}, "
          f"showing {len(found['results'])}"
          f"{', seasonal boost on' if found['seasonal'] else ''}")
    print()
    for rank, result in enumerate(found["results"], 1):
        label = " / ".join(x for x in (result["group"], result["event"]) if x)
        print(f"{rank:3}. {result['score']:.3f}  {result['title'][:58]:58} "
              f"{result['code'][:34]:34} {label}")
        if explain:
            why = result["why"]
            parts = " ".join(f"{k}={v}" for k, v in why.items()
                             if k not in ("bonus", "words") and v)
            extra = ((" +" + "+".join(why["bonus"])) if why["bonus"] else "")
            words = (" on " + ",".join(why["words"])) if why["words"] else ""
            print(f"       {parts}{extra}{words}")
    if found["facets"]:
        print()
        for dim, values in found["facets"].items():
            print(f"  narrow by {dim}: " + ", ".join(f"{v} ({n})" for v, n in values))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="+", help="what someone typed")
    parser.add_argument("-n", "--top", type=int, default=24, help="results to show")
    parser.add_argument("-e", "--explain", action="store_true", help="show the score's parts")
    parser.add_argument("--index", default=index_module.INDEX_PATH)
    parser.add_argument("--per-category", type=int, default=PER_CODE_RESULTS,
                        help="0 turns the diversity cap off")
    args = parser.parse_args()
    search = Search(path=args.index)
    found = search.query(" ".join(args.query), top=args.top,
                         per_category=args.per_category, explain=args.explain)
    report(found, args.explain)


if __name__ == "__main__":
    main()
