"""
audit_catalogue.py  --  evidence base for the search redesign.

Runs the real card export through the CURRENT production algorithm and measures
where it breaks. Pure Python 3 standard library, no pip installs.

    python audit_catalogue.py path/to/card_database.csv          # live rows only
    python audit_catalogue.py path/to/card_database.csv --all    # whole export

LIVE ROWS ARE THE ONLY ONES THAT MATTER. status_id=1 means the card is served;
every other status is dead or archived. In the December export that is 13,042
rows out of 107,160 - 12.2%. Auditing the full export overstates almost every
failure mode, because the dead 88% carries most of the encoding corruption and
most of the stale SEO tag spam. Default to live; use --all only to reason about
a backfill that would revive archived cards.

Every number printed here is measured, not estimated. The redesign argues from
these numbers, so re-run this against a fresh export before trusting any of it.
"""

import collections
import csv
import re
import sys

csv.field_size_limit(10 ** 9)

# ---------------------------------------------------------------------------
# The production configuration, copied verbatim from the Sphinx setup so the
# simulation below fails the same way production does.
# ---------------------------------------------------------------------------

STOP_WORDS = set("""
a an and animated any e es anyone as at beautiful but by card cards day de e-card e-cards
e-greetings e-wish ecard ecards egreetings everybody everyone flash for free from gandhian
greeting greetings happy image images in into like nor of off on onto or per popular post
postcard postcards printable so some somebody someone the this to top up via wishes wishing
with yet your yours x
""".split())

SYNONYMS = {
    "bday": "birthday", "b'day": "birthday", "cumpleanos": "birthday",
    "mom": "mother", "mommy": "mother", "mama": "mother",
    "xmas": "christmas", "x-mas": "christmas",
    "1st": "1",
}

LIVE_STATUS = "1"          # status_id=1 is served; everything else is dead
CANDIDATE_CAP = 500        # production ranks only the first 500 matches
HUMOR = {"funny", "humor", "humour", "hilarious", "joke", "jokes", "lol", "comedy",
         "silly", "laugh"}

# Fields production feeds into the full-text index, flattened into one blob.
INDEXED = ("card_title", "card_description", "card_tags", "q1_value",
           "card_page_url", "dev_id")


def tokens(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def preprocess(query):
    """Production pipeline: lowercase -> synonyms -> drop stop words."""
    out = []
    for token in tokens(query):
        token = SYNONYMS.get(token, token)
        if token not in STOP_WORDS:
            out.append(token)
    return out


def build_blobs(rows):
    blobs = []
    for row in rows:
        parts = []
        for field in INDEXED:
            value = row.get(field) or ""
            if field in ("q1_value", "card_page_url"):
                value = value.replace("_", " ").replace(".html", "")
            parts.append(value)
        blobs.append(" ".join(parts).lower())
    return blobs


def year_of(row):
    try:
        return int(row["card_created_date"][:4])
    except (ValueError, KeyError, TypeError):
        return 0


def has_humor_tag(row):
    tags = (row.get("card_tags") or "").lower()
    return any(word in tags for word in HUMOR)


def rule(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# ---------------------------------------------------------------------------
# A. Stop words vs titles
# ---------------------------------------------------------------------------

def audit_stopword_damage(rows):
    rule("A. STOP-WORD DAMAGE TO TITLES")
    zero = one = 0
    for row in rows:
        kept = [t for t in tokens(row["card_title"]) if t not in STOP_WORDS]
        zero += len(kept) == 0
        one += len(kept) == 1
    n = len(rows)
    print(f"  titles reduced to ZERO searchable tokens: {zero:,} ({100*zero/n:.2f}%)")
    print(f"  titles reduced to ONE token:              {one:,} ({100*one/n:.2f}%)")
    print(f"  combined: {zero+one:,} ({100*(zero+one)/n:.1f}% of catalogue)")


# ---------------------------------------------------------------------------
# B. The "funny" precision failure
# ---------------------------------------------------------------------------

def audit_funny(rows, blobs):
    rule("B. THE 'funny' PRECISION FAILURE")
    matched = [i for i, b in enumerate(blobs) if "funny" in b]
    genuine = [i for i in matched if has_humor_tag(rows[i])]
    print(f"  cards matching 'funny' anywhere in the index: {len(matched):,}")
    print(f"    with a genuine humor tag:  {len(genuine):,}")
    print(f"    matching on prose only:    {len(matched)-len(genuine):,}")

    # A stemmer collapsing funny -> fun pulls in every "fun"/"fun-filled" blurb.
    leak = [i for i, b in enumerate(blobs)
            if re.search(r"\bfun(-|\s|ny|ni)", b) and not has_humor_tag(rows[i])]
    print(f"\n  'fun'/'fun-filled' with NO humor tag (stem-leak victims): {len(leak):,}")
    if genuine:
        print(f"  noise-to-signal if the stemmer fires: {len(leak)/len(genuine):.1f} : 1")
    occasions = collections.Counter(rows[i]["q1_value"].split("_")[0] for i in leak)
    print(f"  their occasions: {dict(occasions.most_common(8))}")


# ---------------------------------------------------------------------------
# C. Why misspellings return a few WRONG cards instead of nothing
# ---------------------------------------------------------------------------

def audit_misspellings(rows, blobs):
    rule("C. MISSPELLINGS MATCH SEO TAG SPAM, NOT THE RIGHT CARDS")
    for typo, correct in [("aniversary", "anniversary"), ("chrismas", "christmas"),
                          ("birthdya", "birthday"), ("freind", "friend"),
                          ("congradulations", "congratulations")]:
        hits = [i for i, b in enumerate(blobs) if typo in b]
        right = sum(1 for b in blobs if correct in b)
        via_tags = sum(1 for i in hits if typo in (rows[i].get("card_tags") or "").lower())
        print(f"  {typo:<17} -> {len(hits):>3} cards "
              f"({via_tags} matched via tag spam)   correct spelling would find {right:,}")


# ---------------------------------------------------------------------------
# D. Strict AND across surviving terms
# ---------------------------------------------------------------------------

REAL_QUERIES = [
    "funny birthday card for wife", "happy birthday card for my beautiful mom",
    "animated birthday", "flash card", "belated birthday sorry",
    "wedding congratulations", "romantic anniversary for husband",
    "sorry card for friend", "birthday card for boss", "cute love card for girlfriend",
]


def audit_strict_and(rows, blobs):
    rule("D. STRICT AND + STOP WORDS ON REAL QUERIES")
    for query in REAL_QUERIES:
        terms = preprocess(query)
        dropped = [t for t in tokens(query) if SYNONYMS.get(t, t) in STOP_WORDS]
        hits = sum(1 for b in blobs if all(t in b for t in terms)) if terms else 0
        flag = "   <-- ZERO RESULTS" if hits == 0 else ""
        print(f"  {query!r}")
        print(f"      survives as {terms}  dropped {dropped}  -> {hits:,} matches{flag}")


# ---------------------------------------------------------------------------
# E. Encoding: URL-encoded entities, not mojibake
# ---------------------------------------------------------------------------

def audit_encoding(rows):
    rule("E. ENCODING CORRUPTION")
    artifacts = collections.Counter()
    affected = 0
    for row in rows:
        blob = " ".join(str(row.get(f) or "") for f in
                        ("card_title", "card_description", "card_tags"))
        found = re.findall(r"%[0-9A-Fa-f]{2}|&#\d+;|&[a-z]+;", blob)
        if found:
            affected += 1
            artifacts.update(found)
    print(f"  rows with URL/HTML entity artifacts: {affected:,} "
          f"({100*affected/len(rows):.2f}%)")
    print(f"  top artifacts: {dict(artifacts.most_common(8))}")
    print("  %92 is a Windows-1252 curly apostrophe: \"Mother%92s Day\" should be "
          "\"Mother's Day\".")
    print("  This also breaks the b'day -> birthday synonym, which never fires on "
          "stored text.")


# ---------------------------------------------------------------------------
# F. The 500-candidate cap
# ---------------------------------------------------------------------------

def audit_cap(rows, blobs):
    rule("F. THE 500-CANDIDATE CAP vs CATALOGUE AGE")
    order = sorted(range(len(rows)), key=lambda i: int(rows[i]["card_number"]))
    print(f"  {'query':<14}{'matches':>9}{'capped':>8}   median year: first-500 vs all")
    for query in ("birthday", "love", "christmas", "anniversary", "friend",
                  "thank", "wedding", "funny", "mother"):
        matches = [i for i in order if query in blobs[i]]
        if not matches:
            continue
        all_years = sorted(y for y in (year_of(rows[i]) for i in matches) if y)
        cap_years = sorted(y for y in (year_of(rows[i]) for i in matches[:CANDIDATE_CAP]) if y)
        if not all_years or not cap_years:
            continue
        med_all = all_years[len(all_years) // 2]
        med_cap = cap_years[len(cap_years) // 2]
        capped = "YES" if len(matches) > CANDIDATE_CAP else "no"
        gap = f"   {med_all-med_cap} yrs older" if len(matches) > CANDIDATE_CAP else ""
        print(f"  {query:<14}{len(matches):>9,}{capped:>8}   {med_cap}  vs  {med_all}{gap}")


# ---------------------------------------------------------------------------
# G. Tag coverage, quality, and the age gradient
# ---------------------------------------------------------------------------

def audit_tags(rows):
    rule("G. TAG COVERAGE AND QUALITY")
    tagged = [r for r in rows if (r.get("card_tags") or "").strip()]
    print(f"  cards with tags: {len(tagged):,} / {len(rows):,} "
          f"({100*len(tagged)/len(rows):.1f}%)")

    buckets = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        decade = "<=2010" if year_of(row) <= 2010 else ">=2011"
        buckets[decade][0] += 1
        if (row.get("card_tags") or "").strip():
            buckets[decade][1] += 1
    for label in sorted(buckets):
        total, has = buckets[label]
        print(f"    created {label}: {total:>7,} cards, {100*has/total:5.1f}% tagged")

    vocabulary = collections.Counter()
    for row in tagged:
        vocabulary.update(t.strip().lower()
                          for t in row["card_tags"].split(",") if t.strip())
    singletons = sum(1 for c in vocabulary.values() if c == 1)
    multiword = sum(1 for t in vocabulary if len(t.split()) > 1)
    print(f"\n  distinct tags: {len(vocabulary):,}")
    print(f"    multi-word phrases: {100*multiword/len(vocabulary):.1f}%")
    print(f"    used on exactly one card: {100*singletons/len(vocabulary):.1f}%")

    identical = collections.Counter(r["card_tags"].strip() for r in tagged)
    bulk = sum(c for c in identical.values() if c > 1)
    print(f"    cards sharing a byte-identical tag string: {bulk:,} "
          f"({100*bulk/len(tagged):.1f}%)")


# ---------------------------------------------------------------------------
# H. status_id, so the live-row filter can be settled with evidence
# ---------------------------------------------------------------------------

def audit_status(rows):
    rule("H. status_id PROFILE (which rows are live?)")
    print(f"  {'status':<8}{'n':>8}{'tagged':>9}{'medYr':>7}{'devs':>6}{'123g%':>7}")
    for status, count in collections.Counter(r["status_id"] for r in rows).most_common():
        subset = [r for r in rows if r["status_id"] == status]
        years = sorted(y for y in (year_of(r) for r in subset) if y)
        tagged = 100 * sum(1 for r in subset if (r.get("card_tags") or "").strip()) / len(subset)
        devs = len(set(r["dev_id"] for r in subset))
        own = 100 * sum(1 for r in subset if r["dev_id"] == "123greetings") / len(subset)
        median = years[len(years) // 2] if years else 0
        print(f"  {status:<8}{count:>8,}{tagged:>8.1f}%{median:>7}{devs:>6}{own:>6.0f}%")


def audit_index_cost(rows):
    """At 13k live cards, is an external search engine needed at all?"""
    import time

    rule("I. INDEX COST AT LIVE SCALE")
    postings = collections.defaultdict(set)
    start = time.perf_counter()
    for i, row in enumerate(rows):
        text = " ".join([row["card_title"], row["card_description"],
                         row.get("card_tags") or "", row["q1_value"].replace("_", " ")])
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            postings[word].add(i)
    build_ms = (time.perf_counter() - start) * 1000

    total = sum(len(v) for v in postings.values())
    print(f"  inverted index over {len(rows):,} cards: {len(postings):,} terms in {build_ms:.0f} ms")
    print(f"  postings memory: ~{total*8/1e6:.1f} MB")

    start = time.perf_counter()
    trials = 2000
    for _ in range(trials):
        postings.get("birthday", set()) & postings.get("funny", set())
    per_query = (time.perf_counter() - start) / trials
    print(f"  two-term AND: {per_query*1e6:.1f} microseconds per query")
    print("  -> an external engine is not required for speed at this size")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else "card_database.csv"
    live_only = "--all" not in sys.argv

    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    exported = len(rows)

    if live_only:
        rows = [r for r in rows
                if r["status_id"] == LIVE_STATUS and r["invalid_card"] == "0"]
        print(f"Loaded {exported:,} rows from {path}")
        print(f"LIVE SLICE: {len(rows):,} cards "
              f"({100*len(rows)/exported:.1f}%) -- status_id={LIVE_STATUS}, "
              f"invalid_card=0. Pass --all to audit the whole export.")
    else:
        print(f"Loaded {exported:,} rows from {path} (WHOLE EXPORT, includes dead rows)")

    blobs = build_blobs(rows)
    audit_stopword_damage(rows)
    audit_funny(rows, blobs)
    audit_misspellings(rows, blobs)
    audit_strict_and(rows, blobs)
    audit_encoding(rows)
    audit_cap(rows, blobs)
    audit_tags(rows)
    audit_index_cost(rows)
    if not live_only:
        audit_status(rows)


if __name__ == "__main__":
    main()
