"""What does each upgrade step to the EXISTING Sphinx pipeline actually buy?

    python upgrade_stages.py

Backs the table in UPGRADE_PATH.md. Re-run it after changing a stage to see
what moves. Takes about four minutes.

Runs the documented v3.2 pipeline over the real catalogue, then re-runs it with
one more fix applied at each stage, measuring the same things every time. The
point is to find where the curve flattens, so the work can stop there.
"""
import collections
import csv
import math
import re
import sys
import time

sys.path.insert(0, "/home/user/Search_algo")
import search_engine as se

rows = se.load_rows(se.find_export())
live = [r for r in rows if r["status_id"] == "1" and r["invalid_card"] == "0"
        and r["card_label_type"] not in se.EXCLUDE_LABEL_TYPES]
ix = se.SearchIndex(rows)
HUMOUR = {ix.cards[d].number for d in ix.facet_docs.get(("tone", "humour"), ())}
THIS_YEAR = max(c.year for c in ix.cards if c.year)

# ---------------------------------------------------------------------------
# Pre-tokenise once, two ways: as production does it, and with apostrophes
# joined. Everything below is then set operations rather than substring scans.
# ---------------------------------------------------------------------------
FIELDS = ("title", "tags", "category", "description", "url")

def tok_prod(text):
    """Production tokenisation: apostrophe is a separator, %92 is not decoded."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

def tok_fixed(text):
    """With the apostrophe fix: entities decoded, apostrophes join."""
    return set(se.normalise(text).split())

DOCS = []
for r in live:
    raw = {
        "title": r["card_title"],
        "tags": (r.get("card_tags") or "").replace(",", " "),
        "category": r["q1_value"].replace("_", " "),
        "description": r["card_description"],
        "url": r["card_page_url"].replace("_", " ").replace("/", " "),
    }
    try:
        year = int(r["card_created_date"][:4])
    except (ValueError, TypeError):
        year = 0
    DOCS.append({
        "number": r["card_number"], "year": year,
        "prod": {f: tok_prod(t) for f, t in raw.items()},
        "fixed": {f: tok_fixed(t) for f, t in raw.items()},
    })


# document frequency for IDF, on the fixed tokenisation
DF = collections.Counter()
for d in DOCS:
    DF.update(set().union(*d["fixed"].values()))
N = len(DOCS)
IDF = {t: math.log(1 + (N - f + 0.5) / (f + 0.5)) for t, f in DF.items()}

# Stop words that are genuinely noise, versus the 68 production deletes.
KEEP_OUT = set("a an and any as at but by de for from in into like nor of off on "
               "onto or per so some the this to up via with yet".split())

STEM = {}
def stem(tok):
    if tok in STEM:
        return STEM[tok]
    best, best_df = tok, DF.get(tok, 0)
    for base in se.word_variants(tok):
        if DF.get(base, 0) > best_df:
            best, best_df = base, DF[base]
    STEM[tok] = best
    return best

for d in DOCS:
    d["stems"] = {f: {stem(t) for t in toks} for f, toks in d["fixed"].items()}

WEIGHTS = {"title": 3.0, "tags": 2.5, "category": 2.0, "description": 1.0, "url": 0.4}


def run(query, cfg):
    """One search under a given configuration. Returns (top10_numbers, total)."""
    fix = cfg.get("apostrophe")
    key = "fixed" if fix else "prod"
    text = se.normalise(query) if fix else (query or "").lower()
    raw_terms = re.findall(r"[a-z0-9]+", text)

    syn = cfg.get("synonyms", se.OLD_SYNONYMS)
    stops = cfg.get("stops", se.OLD_STOP_WORDS)
    terms = []
    for t in raw_terms:
        t = syn.get(t, t)
        if t not in stops:
            terms.append(t)
    if cfg.get("stem"):
        terms = [stem(t) for t in terms]
    terms = list(dict.fromkeys(terms))
    if not terms:
        return [], 0

    # --- selection -------------------------------------------------------
    need = len(terms)
    ladder = [need]
    if cfg.get("relax") and need > 1:
        # Sphinx quorum: "a b c"/2 then /1. Three tries, first non-empty wins.
        ladder = [need, max(1, math.ceil(need * 2 / 3)), 1]

    for required in ladder:
        hits = []
        for d in DOCS:
            fields = d[key]
            matched = [t for t in terms
                       if any(t in fields[f] or
                              (cfg.get("stem") and t in d["stems"][f])
                              for f in FIELDS)]
            if len(matched) >= required:
                hits.append((d, matched))
                if not cfg.get("uncapped") and len(hits) >= se.OLD_CAP:
                    break
        if hits:
            break
    if not hits:
        return [], 0
    total = len(hits)

    # --- ranking ---------------------------------------------------------
    if not cfg.get("weights"):
        # Production: weight = number of field matches, all fields equal.
        def score(d, matched):
            return sum(1 for t in matched for f in FIELDS
                       if t in d[key][f] or
                          (cfg.get("stem") and t in d["stems"][f]))
    else:
        def score(d, matched):
            s = 0.0
            for t in matched:
                idf = IDF.get(t, 1.0) if cfg.get("idf") else 1.0
                for f in FIELDS:
                    if t in d[key][f] or (cfg.get("stem") and t in d["stems"][f]):
                        s += WEIGHTS[f] * idf
            return s

    scored = [(score(d, m), d) for d, m in hits]
    if cfg.get("buckets"):
        top = max(s for s, _ in scored) or 1.0
        scored = [(round(s / top * 10), d) for s, d in scored]
        scored.sort(key=lambda x: (-x[0], -(x[1]["year"] or 0), -int(x[1]["number"])))
    else:
        # Production tail: sends6h then lifetime sends. Neither is in the export;
        # card_number ascending stands in, which is the same incumbency (low
        # numbers are old cards) and is what makes "old cards first" visible.
        scored.sort(key=lambda x: (-x[0], int(x[1]["number"])))
    return [d["number"] for _, d in scored[:10]], total


# ---------------------------------------------------------------------------
STAGES = [
    ("0  as it is today",            dict()),
    ("1  + apostrophes join",        dict(apostrophe=1)),
    ("2  + stemming",                dict(apostrophe=1, stem=1)),
    ("3  + drop funny=>fun & co",    dict(apostrophe=1, stem=1, synonyms={k: v for k, v in se.OLD_SYNONYMS.items() if k not in ("funny", "funnies", "humor", "friend", "romance")})),
    ("4  + trim the stop list",      dict(apostrophe=1, stem=1, synonyms={k: v for k, v in se.OLD_SYNONYMS.items() if k not in ("funny", "funnies", "humor", "friend", "romance")},
                                          stops=KEEP_OUT)),
    ("5  + quorum relaxation",       dict(apostrophe=1, stem=1, synonyms={k: v for k, v in se.OLD_SYNONYMS.items() if k not in ("funny", "funnies", "humor", "friend", "romance")},
                                          stops=KEEP_OUT, relax=1, uncapped=1)),
    ("6  + field weights + IDF",     dict(apostrophe=1, stem=1, synonyms={k: v for k, v in se.OLD_SYNONYMS.items() if k not in ("funny", "funnies", "humor", "friend", "romance")},
                                          stops=KEEP_OUT, relax=1, uncapped=1,
                                          weights=1, idf=1)),
    ("7  + bucket, newest breaks ties", dict(apostrophe=1, stem=1, synonyms={k: v for k, v in se.OLD_SYNONYMS.items() if k not in ("funny", "funnies", "humor", "friend", "romance")},
                                          stops=KEEP_OUT, relax=1, uncapped=1,
                                          weights=1, idf=1, buckets=1)),
]
QUERIES = se.load_query_log()
TOTAL_SEARCHES = sum(t for _, t in QUERIES)

print(f"{len(DOCS):,} cards | {len(QUERIES)} queries | "
      f"{TOTAL_SEARCHES:,} searches\n")
print(f"{'stage':30s} {'zero':>10s} {'carousel':>18s} {'funny':>7s} "
      f"{'flash card':>11s} {'mean yr':>8s}")
print("-" * 90)

for name, cfg in STAGES:
    t0 = time.perf_counter()
    zero = zero_vol = 0
    years = []
    for q, times in QUERIES:
        got, total = run(q, cfg)
        if not got:
            zero += 1
            zero_vol += times
        else:
            years += [d["year"] for d in DOCS
                      if d["number"] in set(got) and d["year"]]
    funny, _ = run("funny", cfg)
    fn = sum(1 for n in funny if n in HUMOUR)
    flash, ftot = run("flash card", cfg)
    print(f"{name:30s} {zero:4d}/{len(QUERIES):<5d} "
          f"{zero_vol:8,d} ({zero_vol/TOTAL_SEARCHES*100:4.1f}%) "
          f"{fn:2d}/10  {('%d hits' % ftot) if flash else '  ZERO':>11s} "
          f"{(sum(years)/len(years) if years else 0):8.1f}"
          f"   [{time.perf_counter()-t0:.0f}s]")

print("\nfor reference, the new engine:")
zero = sum(1 for q, _ in QUERIES if not se.search(ix, q, limit=10)["results"])
fb = sum(t for q, t in QUERIES if se.search(ix, q, limit=10)["fallback"])
fn = sum(1 for c in se.search(ix, "funny", limit=10)["results"]
         if c.number in HUMOUR)
yrs = [c.year for q, _ in QUERIES for c in se.search(ix, q, limit=10)["results"] if c.year]
print(f"{'   new engine':30s} {zero:4d}/{len(QUERIES):<5d} "
      f"{fb:8,d} ({fb/TOTAL_SEARCHES*100:4.1f}%) {fn:2d}/10 "
      f"{'works':>11s} {sum(yrs)/len(yrs):8.1f}")
