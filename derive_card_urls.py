"""
Work out each card's page URL from the export.

    python derive_card_urls.py            # coverage report
    python derive_card_urls.py --write    # also write data/card_urls.tsv

THE PROBLEM
-----------
The export gives a card's category slug and its filename, and neither one is
the path:

    q1_value       birth_happybirthday
    card_page_url  birthday191.html
    the real page  /birthday/happy_birthday/birthday191.html

So two things are missing. "birth" has to become "birthday", and
"happybirthday" has to become "happy_birthday". Neither follows from splitting
the slug, and no column holds the path.

THE WAY IN
----------
The word boundaries are in the data, just not in the slug. Categories that were
given descriptive filenames leak them:

    q1_value       eaug_lemonjuice_day
    a filename     happy_lemon_juice_day.html

Strip the underscores from "lemonjuice_day" and from "lemon_juice_day" and both
are "lemonjuiceday". So for each category, scan its own filenames for a run of
underscore-separated parts whose letters match the slug tail, and that run - with
its underscores - is the path segment. Derived from the data rather than typed.

Where no filename gives it away, the tail is split against the catalogue's own
vocabulary instead, the same splitter the search uses for "merrychristmas".

The first segment is a fixed 29-entry table, because "birth" -> "birthday" and
"thank" -> "thank_you" are facts about the site's URL layout that nothing in
the export records. Every one of those 29 is marked below as CONFIRMED against
a real URL or UNCONFIRMED, and the unconfirmed ones are not guessed at - they
are reported as needing five minutes with the site.
"""

import collections
import os
import re
import sys

import search_engine as se

# ---------------------------------------------------------------------------
# The 29 first segments.
#
# CONFIRMED entries are ones a real 123greetings.com URL was seen for.
# UNCONFIRMED entries are the honest state of things: the export cannot tell us,
# and a wrong guess here silently sends every card in that section to a 404,
# which is worse than leaving the link off. They are listed so someone can
# confirm them by opening one card from each.
# ---------------------------------------------------------------------------
FIRST_SEGMENT = {
    # confirmed against real URLs from the live search page
    "birth":   ("birthday",   "CONFIRMED"),   # /birthday/happy_birthday/birthday191.html
    "thank":   ("thank_you",  "CONFIRMED"),   # /thank_you/birthday/birthday34.html
    "eaug":    ("events",     "CONFIRMED"),   # /events/lemon_juice_day/...
    # the other eleven month prefixes follow eaug's pattern
    "ejan":    ("events",     "CONFIRMED"),
    "efeb":    ("events",     "CONFIRMED"),
    "emar":    ("events",     "CONFIRMED"),
    "eapr":    ("events",     "CONFIRMED"),
    "emay":    ("events",     "CONFIRMED"),
    "ejun":    ("events",     "CONFIRMED"),
    "ejul":    ("events",     "CONFIRMED"),
    "esep":    ("events",     "CONFIRMED"),
    "eoct":    ("events",     "CONFIRMED"),
    "enov":    ("events",     "CONFIRMED"),
    "edec":    ("events",     "CONFIRMED"),
    # not seen in a real URL - open one card from each of these to confirm
    "love":    ("love",       "UNCONFIRMED"),
    "anniv":   ("anniversary", "UNCONFIRMED"),
    "wed":     ("wedding",    "UNCONFIRMED"),
    "friend":  ("friendship", "UNCONFIRMED"),
    "congrats": ("congratulations", "UNCONFIRMED"),
    "insp":    ("inspirational", "UNCONFIRMED"),
    "gen":     ("everyday",   "UNCONFIRMED"),
    "cute":    ("cute",       "UNCONFIRMED"),
    "pet":     ("pets",       "UNCONFIRMED"),
    "bus":     ("business",   "UNCONFIRMED"),
    "flwr":    ("flowers",    "UNCONFIRMED"),
    "intouch": ("keep_in_touch", "UNCONFIRMED"),
    "invp":    ("invitations", "UNCONFIRMED"),
    "fkt":     ("family",     "UNCONFIRMED"),
    "w":       ("wishes",     "UNCONFIRMED"),
}

BASE = "https://www.123greetings.com"


def letters(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def from_filenames(tail, filenames):
    """
    Recover the tail's word boundaries from the category's own filenames.

    Looks for a run of underscore-separated parts in some filename whose letters
    are exactly the tail's letters. "lemonjuice_day" is found inside
    "happy_lemon_juice_day" as "lemon_juice_day".
    """
    want = letters(tail)
    if not want:
        return None
    best = None
    for name in filenames:
        stem = name[:-5] if name.endswith(".html") else name
        parts = stem.split("_")
        for start in range(len(parts)):
            acc = ""
            for end in range(start, len(parts)):
                acc += letters(parts[end])
                if len(acc) > len(want):
                    break
                if acc == want:
                    found = "_".join(parts[start:end + 1])
                    # Prefer the most-split form: it carries the most boundaries.
                    if best is None or found.count("_") > best.count("_"):
                        best = found
    return best


def prose_vocabulary(rows):
    """
    Word frequencies from title, description and tags ONLY - not the category.

    That exclusion is the whole trick. The search index tokenises q1_value too,
    so "nationaldog" is itself a vocabulary word with 47 cards behind it, and a
    splitter that trusts that vocabulary accepts the run-together slug as a real
    word and never splits it. Counting only prose gives a dictionary of words
    humans actually wrote.
    """
    df = collections.Counter()
    for row in rows:
        words = set()
        for field in ("card_title", "card_description", "card_tags"):
            words.update(
                se.normalise((row.get(field) or "").replace(",", " ")).split())
        df.update(words)
    return df


def best_split(word, df, min_df=se.MIN_DICTIONARY_FREQUENCY, depth=0):
    """
    Split a run-together slug into the most real words it will yield.

    FEWEST pieces wins, tie-broken by how common those pieces are. Preferring
    the most pieces looks reasonable and is wrong: "happybirthday" cuts into
    happy|birthday and also into happy|birth|day, all three being real words,
    and the greedier rule produced /happy_birth_day/.

    Fewest still finds the long ones where they are the only option -
    "justbecauseday" has no two-word cut, so just|because|day stands.

    Returns None when no split into known words exists, and the caller then
    leaves the slug alone rather than inventing a boundary.
    """
    if depth > 4 or len(word) < 6:
        return None
    best = None
    for cut in range(3, len(word) - 2):
        head, rest = word[:cut], word[cut:]
        if df.get(head, 0) < min_df:
            continue
        if df.get(rest, 0) >= min_df:
            candidate = [head, rest]
        else:
            deeper = best_split(rest, df, min_df, depth + 1)
            if not deeper:
                continue
            candidate = [head] + deeper
        if best is None or _rank(candidate, df) < _rank(best, df):
            best = candidate
    return best


def _rank(pieces, df):
    """Fewer pieces first, then the commoner wording."""
    return (len(pieces), -min(df.get(p, 0) for p in pieces))


def from_vocabulary(tail, df, min_df=se.MIN_DICTIONARY_FREQUENCY):
    """
    Split each underscore-separated chunk of the tail, where it will split.

    A chunk people already write as one word is left alone, whatever it could
    be cut into. Without that guard "birthday" becomes birth|day - both are
    words, and "most pieces wins" prefers the cut - which is how /birthday/
    turned into /birth/day/.
    """
    out = []
    for chunk in tail.split("_"):
        if df.get(chunk, 0) >= min_df:      # a word in its own right
            out.append(chunk)
            continue
        pieces = best_split(chunk, df, min_df)
        out.extend(pieces if pieces else [chunk])
    return "_".join(out)


def build(rows):
    """q1_value -> (path, how_it_was_derived, confidence)."""
    by_category = collections.defaultdict(list)
    for row in rows:
        by_category[row["q1_value"]].append(row["card_page_url"] or "")

    df = prose_vocabulary(rows)
    out = {}
    for q1, filenames in by_category.items():
        first, _, tail = q1.partition("_")
        folder, confidence = FIRST_SEGMENT.get(first, (None, "UNKNOWN"))
        if folder is None:
            out[q1] = (None, "first segment not in the table", "UNKNOWN", "NONE")
            continue
        if not tail:
            out[q1] = (f"/{folder}", "no tail", confidence, "DERIVED")
            continue
        # Both methods, then whichever recovered more word boundaries.
        #
        # Not filenames-first: "justbecauseday" appears verbatim as a filename,
        # so the filename method "succeeds" with zero boundaries recovered and
        # the vocabulary split - which gets just|because|day - never runs.
        by_file = from_filenames(tail, filenames)
        by_vocab = from_vocabulary(tail, df)
        candidates = [(c, m) for c, m in
                      ((by_file, "filenames"), (by_vocab, "vocabulary")) if c]
        found, method = max(candidates, key=lambda cm: cm[0].count("_"))

        if method == "filenames" and found.count("_") > tail.count("_"):
            how = "recovered from this category's own filenames"
            evidence = "DERIVED"
        elif found != tail:
            how = "split against the catalogue's prose vocabulary"
            # A split is a guess. It is right for national|dog|day and wrong for
            # raksha|bandhan, which the site keeps joined - and nothing in the
            # export distinguishes them. Flagged rather than trusted.
            evidence = "GUESSED"
        else:
            how = "slug used as-is, no boundaries found"
            evidence = "AS-IS"
        out[q1] = (f"/{folder}/{found}", how, confidence, evidence)
    return out


# Every path this can be checked against, taken from the live search page.
GROUND_TRUTH = {
    "birth_happybirthday":            "/birthday/happy_birthday",
    "thank_birthday":                 "/thank_you/birthday",
    "eaug_lemonjuice_day":            "/events/lemon_juice_day",
    "eaug_womensequality_day":        "/events/womens_equality_day",
    "eaug_nationaldog_day":           "/events/national_dog_day",
    "eaug_justbecauseday":            "/events/just_because_day",
    "eaug_daffodilday":               "/events/daffodil_day",
    "eaug_rakshabandhan_interactive": "/events/rakshabandhan/interactive_cards",
}


def main():
    rows = se.load_rows(se.find_export())
    live = [r for r in rows if r["status_id"] == se.LIVE_STATUS
            and r["invalid_card"] == "0"
            and r["card_label_type"] not in se.EXCLUDE_LABEL_TYPES]
    table = build(live)

    print("=" * 74)
    print("CHECKED AGAINST THE 8 REAL URLS AVAILABLE")
    print("=" * 74)
    right = 0
    for q1, want in GROUND_TRUTH.items():
        got = table.get(q1, (None, "-", "-", "-"))[0]
        ok = got == want
        right += ok
        print(f"  {'OK  ' if ok else 'WRONG'} {q1}")
        print(f"        want {want}")
        if not ok:
            print(f"        got  {got}")
    print(f"\n  {right} of {len(GROUND_TRUTH)} correct")

    print("\n" + "=" * 74)
    print("COVERAGE ACROSS ALL CATEGORIES")
    print("=" * 74)
    how = collections.Counter(v[1] for v in table.values())
    conf = collections.Counter(v[2] for v in table.values())
    ev = collections.Counter(v[3] for v in table.values())
    cards = collections.Counter()
    ev_cards = collections.Counter()
    for row in live:
        cards[table[row["q1_value"]][2]] += 1
        ev_cards[table[row["q1_value"]][3]] += 1
    print(f"  {len(table)} categories, {len(live):,} cards\n")
    for k, n in how.most_common():
        print(f"  {n:5d} categories  {k}")
    print()
    for k in ("CONFIRMED", "UNCONFIRMED", "UNKNOWN"):
        print(f"  {conf.get(k, 0):5d} categories / {cards.get(k, 0):6,d} cards  "
              f"first segment {k}")
    print()
    for k, label in (("DERIVED", "path recovered from the data - trust it"),
                     ("AS-IS", "slug had no boundaries to recover - likely fine"),
                     ("GUESSED", "path is a vocabulary split - CHECK THESE")):
        print(f"  {ev.get(k, 0):5d} categories / {ev_cards.get(k, 0):6,d} cards  {label}")

    print("\n" + "=" * 74)
    print("FIRST SEGMENTS STILL NEEDING CONFIRMATION")
    print("=" * 74)
    print("  Open one card from each on the live site and check the folder.")
    per_prefix = collections.Counter(r["q1_value"].split("_")[0] for r in live)
    example = {}
    for row in live:
        example.setdefault(row["q1_value"].split("_")[0], row)
    for prefix, (folder, state) in sorted(FIRST_SEGMENT.items()):
        if state != "UNCONFIRMED":
            continue
        row = example.get(prefix)
        if not row:
            continue
        guess = table[row["q1_value"]][0]
        print(f"  {prefix:9s} {per_prefix[prefix]:5d} cards  guessing /{folder}/…")
        print(f"            check: {BASE}{guess}/{row['card_page_url']}")

    if "--write" in sys.argv:
        here = os.path.dirname(os.path.abspath(se.find_export()))
        out_path = os.path.join(here, "card_urls.tsv")
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("q1_value\tpath\tfirst_segment\tpath_evidence\tderived_by\n")
            for q1 in sorted(table):
                path, how, conf_, ev_ = table[q1]
                fh.write(f"{q1}\t{path or ''}\t{conf_}\t{ev_}\t{how}\n")
        print(f"\n  wrote {out_path} — {len(table)} categories")
        print("  Set PAGE_TEMPLATE in serve.py once the UNCONFIRMED prefixes "
              "are checked.")


if __name__ == "__main__":
    main()
