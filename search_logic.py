"""
search_logic.py  --  standalone search logic for the greeting-card search box.

No pip installs. Pure Python 3 standard library.
Run it directly:   python search_logic.py
Then type queries at the prompt, or just read the test output at the top.

This file is ONLY the logic. No Flask, no Perl, no server.
Once the logic is proven correct here, it gets wrapped in a service
and called from Perl. Do not add those layers until this behaves.
"""

import re
import unicodedata
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# 1. THE CATALOGUE
#    Replace this list with a dump from your real card database.
#    Each card needs: id, title, category, tags.
#    The tags field is what makes "funny" work - see SEARCH NOTE below.
# ---------------------------------------------------------------------------

CARDS = [
    {"id": 1,  "title": "Happy Anniversary",          "category": "Anniversary",  "tags": ["love", "romantic", "couple", "heartfelt"]},
    {"id": 2,  "title": "Anniversary For Wife",       "category": "Anniversary",  "tags": ["love", "wife", "romantic"]},
    {"id": 3,  "title": "Silly Anniversary Wishes",   "category": "Anniversary",  "tags": ["funny", "humour", "silly", "joke"]},
    {"id": 4,  "title": "Happy Birthday",             "category": "Birthday",     "tags": ["celebration", "cake", "party"]},
    {"id": 5,  "title": "Hilarious Birthday Card",    "category": "Birthday",     "tags": ["funny", "humour", "joke", "laugh"]},
    {"id": 6,  "title": "Belated Birthday Sorry",     "category": "Birthday",     "tags": ["late", "sorry", "funny"]},
    {"id": 7,  "title": "Congratulations To You",     "category": "Congrats",     "tags": ["achievement", "proud", "success"]},
    {"id": 8,  "title": "Thank You So Much",          "category": "Thank You",    "tags": ["gratitude", "grateful", "polite"]},
    {"id": 9,  "title": "Get Well Soon",              "category": "Get Well",     "tags": ["sick", "care", "heartfelt", "sympathy"]},
    {"id": 10, "title": "Wedding Bells",              "category": "Wedding",      "tags": ["marriage", "love", "bride", "groom"]},
    {"id": 11, "title": "Funny Cat Wishes",           "category": "Pets",         "tags": ["funny", "humour", "cat", "animal", "laugh"]},
    {"id": 12, "title": "Happy New Year",             "category": "New Year",     "tags": ["celebration", "party", "fireworks"]},
    {"id": 13, "title": "Miss You Every Day",         "category": "Friendship",   "tags": ["heartfelt", "love", "distance"]},
    {"id": 14, "title": "Friendship Forever",         "category": "Friendship",   "tags": ["friends", "bond", "heartfelt"]},
    {"id": 15, "title": "A Joke For Your Day",        "category": "Just Because", "tags": ["funny", "humour", "joke", "laugh", "silly"]},
]

# ---------------------------------------------------------------------------
# 2. SYNONYMS
#    Maps what people type -> what your catalogue actually calls it.
#    This is the cheapest, highest-value table in the whole system.
#    Grow it from your real search logs, not from guessing.
# ---------------------------------------------------------------------------

SYNONYMS = {
    "bday":     "birthday",
    "b-day":    "birthday",
    "anniv":    "anniversary",
    "congrats": "congratulations",
    "xmas":     "christmas",
    "thx":      "thank you",
    "ty":       "thank you",
    "humor":    "funny",
    "humour":   "funny",
    "hilarious": "funny",
    "lol":      "funny",
    "comedy":   "funny",
    "mum":      "mother",
    "mom":      "mother",
    "sorry":    "apology",
}

MIN_QUERY_LENGTH = 2     # 1 char matches everything, useless
MAX_RESULTS      = 8     # a dropdown of 40 is another browse problem
FUZZY_THRESHOLD  = 0.75  # 0..1 similarity needed to call it a typo, not a miss

# --- input caps: added after injection testing found a DoS -----------------
MAX_QUERY_CHARS  = 100   # nobody searches a 10,000 character string
MAX_TOKENS       = 6     # "a a a a a..." x5000 took 1.5s and matched everything
MIN_TOKEN_LENGTH = 2     # a 1-char token matches every card via infix


# ---------------------------------------------------------------------------
# 3. NORMALISATION
#    Everything that goes in and everything stored gets flattened the same way.
#    Skip this and "Anniversary " with a trailing space becomes a bug report.
# ---------------------------------------------------------------------------

def normalise(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)   # strip punctuation
    text = re.sub(r"\s+", " ", text)           # collapse whitespace
    return text


def expand_synonyms(query):
    """Swap known aliases token by token. 'bday funny' -> 'birthday funny'."""
    return " ".join(SYNONYMS.get(tok, tok) for tok in query.split())


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# 4. THE SCORER  --  this is the actual logic
#
#    One card, one query token, one number out. Higher is better, 0 = no match.
#    Every rule is separate and named so you can tune one without breaking others.
# ---------------------------------------------------------------------------

def score_token(card, token):
    title    = normalise(card["title"])
    category = normalise(card["category"])
    tags     = [normalise(t) for t in card["tags"]]
    words    = title.split()

    # --- exact whole-title match -------------------------------------------
    if token == title:
        return 100, "exact title"

    # --- exact category match ----------------------------------------------
    if token == category:
        return 95, "exact category"

    # --- prefix on the title: "an" -> "Anniversary For Wife" ---------------
    if title.startswith(token):
        return 90, "title prefix"

    # --- prefix on any word inside the title: "birth" -> "Happy Birthday" --
    if any(w.startswith(token) for w in words):
        return 85, "word prefix"

    # --- prefix on the category --------------------------------------------
    if category.startswith(token):
        return 80, "category prefix"

    # --- exact tag hit: "funny" -> anything tagged funny --------------------
    #     SEARCH NOTE: this is the rule that fixes your "funny" complaint.
    #     "Hilarious Birthday Card" has no letter-match for "funny" anywhere
    #     in its title. Only the tag connects them.
    if token in tags:
        return 75, "tag match"

    # --- tag prefix: "fun" -> funny ----------------------------------------
    if any(t.startswith(token) for t in tags):
        return 70, "tag prefix"

    # --- INFIX on title: "nn" -> "A-nn-iversary" ---------------------------
    #     This is the rule most naive search boxes are missing.
    if token in title:
        return 60, "title infix"

    # --- infix on category --------------------------------------------------
    if token in category:
        return 55, "category infix"

    # --- infix on tags ------------------------------------------------------
    if any(token in t for t in tags):
        return 50, "tag infix"

    # --- FUZZY: "aniversary" -> "anniversary" ------------------------------
    #     Last resort. Only fires if nothing above matched.
    best = 0.0
    for candidate in words + [category] + tags:
        best = max(best, similarity(token, candidate))
    if best >= FUZZY_THRESHOLD:
        return int(40 * best), "fuzzy (typo)"

    return 0, None


def score_card(card, tokens):
    """
    A card must match EVERY token to qualify.
    'funny birthday' should not return every funny card and every birthday card.
    Score is the sum, so matching strongly on both beats matching weakly.
    """
    total = 0
    reasons = []
    for token in tokens:
        points, reason = score_token(card, token)
        if points == 0:
            return 0, []          # one miss disqualifies the card
        total += points
        reasons.append(reason)
    return total, reasons


# ---------------------------------------------------------------------------
# 5. THE ENTRY POINT  --  the only function the rest of the app calls
# ---------------------------------------------------------------------------

def search(raw_query, limit=MAX_RESULTS):
    raw_query = raw_query or ""

    # CAP 1: truncate before doing any work. Protects every layer below.
    if len(raw_query) > MAX_QUERY_CHARS:
        raw_query = raw_query[:MAX_QUERY_CHARS]

    query = normalise(raw_query)

    if len(query) < MIN_QUERY_LENGTH:
        return {"query": raw_query, "safe_query": normalise(raw_query),
                "corrected": None, "results": []}

    expanded = expand_synonyms(query)

    # CAP 2: drop 1-char tokens. "a" matches every card via infix, so a query
    # of "a a a a a..." was returning the entire catalogue - a filter bypass.
    tokens = [t for t in expanded.split() if len(t) >= MIN_TOKEN_LENGTH]

    # CAP 3: limit token count. Cost is tokens x cards x fuzzy comparisons.
    tokens = tokens[:MAX_TOKENS]

    if not tokens:
        return {"query": raw_query, "safe_query": query,
                "corrected": None, "results": []}

    hits = []
    for card in CARDS:
        points, reasons = score_card(card, tokens)
        if points > 0:
            hits.append({
                "id":       card["id"],
                "title":    card["title"],
                "category": card["category"],
                "score":    points,
                "why":      ", ".join(r for r in reasons if r),
            })

    # highest score first, then alphabetical so ties are stable, not random
    hits.sort(key=lambda h: (-h["score"], h["title"]))

    return {
        # RAW - never render this without HTML-escaping it first.
        # Injection testing confirmed XSS payloads pass through untouched.
        "query":      raw_query,
        # NORMALISED - safe to render. Punctuation already stripped.
        # Use this for "No results for X" messages.
        "safe_query": query,
        "corrected":  expanded if expanded != query else None,
        "results":    hits[:limit],
    }


# ---------------------------------------------------------------------------
# 6. TEST SUITE  --  run this every time you touch the scorer above
# ---------------------------------------------------------------------------

TESTS = [
    # (query,          a title that MUST appear,        what is being proven)
    ("an",             "Happy Anniversary",       "prefix"),
    ("nn",             "Happy Anniversary",       "infix - the main bug"),
    ("sary",           "Happy Anniversary",       "suffix"),
    ("ANN",            "Happy Anniversary",       "case insensitive"),
    ("  anniversary ", "Happy Anniversary",       "whitespace trimmed"),
    ("aniversary",     "Happy Anniversary",       "typo tolerance"),
    ("bday",           "Happy Birthday",          "synonym"),
    ("funny",          "Hilarious Birthday Card", "tag match - no letters shared"),
    ("humour",         "Funny Cat Wishes",        "synonym into tag"),
    ("fun",            "A Joke For Your Day",     "tag prefix"),
    ("funny birthday", "Hilarious Birthday Card", "multi-token AND"),
    ("birth",          "Happy Birthday",          "word prefix mid-title"),
]

NEGATIVE_TESTS = [
    ("a",            "single char returns nothing"),
    ("",             "empty query returns nothing"),
    ("zzzzqqq",      "nonsense returns nothing"),
    ("!!!",          "punctuation only returns nothing"),
]


def run_tests():
    print("=" * 68)
    print("SEARCH LOGIC TEST RUN")
    print("=" * 68)
    passed = failed = 0

    for query, expected_title, description in TESTS:
        out = search(query)
        titles = [r["title"] for r in out["results"]]
        ok = expected_title in titles
        print(f"[{'PASS' if ok else 'FAIL'}]  {query!r:18} -> expects {expected_title!r}")
        print(f"        {description}")
        print(f"        got: {titles if titles else 'nothing'}")
        print()
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)

    for query, description in NEGATIVE_TESTS:
        out = search(query)
        ok = len(out["results"]) == 0
        print(f"[{'PASS' if ok else 'FAIL'}]  {query!r:18} -> expects no results")
        print(f"        {description}")
        print()
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)

    print("=" * 68)
    print(f"{passed} passed, {failed} failed")
    print("=" * 68)
    return failed == 0


def interactive():
    print("\nType a query and press Enter. Blank line or 'quit' to stop.\n")
    while True:
        try:
            q = input("search> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("quit", "exit"):
            break
        out = search(q)
        if out["corrected"]:
            print(f"  (searched as: {out['corrected']})")
        if not out["results"]:
            print("  no results\n")
            continue
        for r in out["results"]:
            print(f"  {r['score']:>4}  {r['title']:<26} [{r['category']}]  ({r['why']})")
        print()


if __name__ == "__main__":
    run_tests()
    interactive()
