"""
Show the arithmetic behind one card's position in one search.

    python explain_score.py                       # the default example
    python explain_score.py "funny birthday"      # any query

Prints every number that goes into a card's score, from the raw word counts up
to its final place on the page. Nothing is copied from the source: it re-derives
each figure from the same index the search uses, so if the constants change the
output changes with them.

Backs LOGIC_FLOW.html, which is this document's arithmetic in printable form.
"""

import collections
import sys

import search_engine as se


def field_words(row, index):
    """The four text fields as the index tokenises them, plus the url."""
    return {
        "title":       se.tokenise(row["card_title"]),
        "description": se.tokenise(row["card_description"]),
        "tags":        se.tokenise((row.get("card_tags") or "").replace(",", " ")),
        "category":    index._category_tokens(row),
        "url":         index._url_tokens(row),
    }


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "funny birthday"
    rows = se.load_rows(se.find_export())
    index = se.SearchIndex(rows)
    live = [r for r in rows if r["status_id"] == se.LIVE_STATUS
            and r["invalid_card"] == "0"
            and r["card_label_type"] not in se.EXCLUDE_LABEL_TYPES]

    out = se.search(index, query, limit=10)
    if not out["results"]:
        print("no results")
        return
    card = out["results"][0]
    row = live[card.doc]

    understood = se.understand(query, index)
    weights = se.scoring_weights(understood, index)

    print("=" * 74)
    print(f"QUERY   {query!r}")
    print(f"CARD    #{card.number}  {card.title}")
    print(f"        {card.category} · {card.year} · ranked #1 of "
          f"{len(out['results'])} shown")
    print("=" * 74)

    print(f"\nCONSTANTS  (search_engine.py)")
    for field, w in se.FIELD_WEIGHTS.items():
        print(f"   FIELD_WEIGHTS[{field!r}] = {w}")
    print(f"   TF_SATURATION  = {se.TF_SATURATION}")
    print(f"   FACET_BOOST    = {se.FACET_BOOST}")
    print(f"   RECENCY_BOOST  = {se.RECENCY_BOOST}   halflife "
          f"{se.RECENCY_HALFLIFE} years")
    print(f"   catalogue size = {index.total:,} cards")

    # ---- 1. what the card says, per field -------------------------------
    fields = field_words(row, index)
    print(f"\n1. WHERE EACH QUERY WORD APPEARS ON THIS CARD")
    print(f"   {'word':14s} {'field':12s} {'times':>5s} {'field wt':>9s} "
          f"{'saturated':>10s}")
    per_term = collections.defaultdict(float)
    for term in understood.terms:
        for field, words in fields.items():
            tf = collections.Counter(words)[term]
            if not tf:
                continue
            fw = se.FIELD_WEIGHTS[field]
            sat = fw * (tf * (se.TF_SATURATION + 1)) / (tf + se.TF_SATURATION)
            per_term[term] += sat
            print(f"   {term:14s} {field:12s} {tf:5d} {fw:9.1f} {sat:10.2f}")
    print(f"\n   saturated total per word (this is postings[word][card]):")
    for term, v in per_term.items():
        stored = index.postings.get(term, {}).get(card.doc, 0.0)
        flag = "" if abs(stored - v) < 1e-9 else f"   <-- index says {stored:.4f}"
        print(f"      {term:14s} {v:8.3f}{flag}")

    # ---- 2. how rare each word is ---------------------------------------
    print(f"\n2. HOW RARE EACH WORD IS")
    print(f"   idf = log(1 + (N - df + 0.5) / (df + 0.5))")
    print(f"   {'word':14s} {'on cards':>9s} {'idf':>7s}")
    for term in understood.terms:
        df = index.document_frequency.get(term, 0)
        print(f"   {term:14s} {df:9d} {index.idf.get(term, 0.0):7.3f}")

    # ---- 3. the text score ----------------------------------------------
    print(f"\n3. TEXT SCORE  =  sum of  idf x saturated x multiplier")
    text_total = 0.0
    for term in understood.terms:
        stored = index.postings.get(term, {}).get(card.doc)
        if not stored:
            continue
        idf = index.idf.get(term, 0.0)
        mult = weights.get(term, 1.0)
        part = idf * stored * mult
        text_total += part
        print(f"   {term:14s} {idf:6.3f} x {stored:7.3f} x {mult:4.2f} "
              f"= {part:8.3f}")
    print(f"   {'':14s} {'':>6s}   {'':>7s}   {'':>4s}   {'-' * 8}")
    print(f"   {'':14s} {'':>6s}   {'':>7s}   {'':>4s} = {text_total:8.3f}")

    # ---- 4. slot bonuses -------------------------------------------------
    print(f"\n4. SLOT BONUSES  (only slots this card actually carries)")
    print(f"   boost = {se.FACET_BOOST} x min(selectivity / reference, 1.6)")
    facet_total = 0.0
    for kind, value, source in understood.facets:
        docs = index.facet_docs.get((kind, value), ())
        if card.doc not in docs:
            print(f"   {kind}={value:14s} not on this card")
            continue
        boost = index.facet_boost.get((kind, value), se.FACET_BOOST)
        facet_total += boost
        print(f"   {kind}={value:14s} on {len(docs):5d} cards -> {boost:6.2f}")
    print(f"   {'':32s} {'-' * 6}")
    print(f"   {'':32s} {facet_total:6.2f}")

    raw = text_total + facet_total
    engine_raw = None
    consumed = {s for _, _, src in understood.facets for s in src.split()}
    text_terms = [t for t in sorted(understood.terms,
                                    key=lambda t: index.idf.get(t, 0.0),
                                    reverse=True) if t not in consumed]
    required = [understood.groups.get(t, {t}) for t in text_terms]
    engine_scores = se.score(index, weights, understood.facets, required)
    engine_raw = engine_scores.get(card.doc)

    print(f"\n5. RAW SCORE  = {text_total:.3f} + {facet_total:.3f} = {raw:.3f}")
    if engine_raw is not None:
        agree = "matches the engine" if abs(engine_raw - raw) < 1e-6 else \
                f"ENGINE SAYS {engine_raw:.3f}"
        print(f"   {agree}")

    # ---- 6. freshness ----------------------------------------------------
    factor = se.recency_factor(card.year, index.newest_year)
    age = index.newest_year - (card.year or index.newest_year)
    print(f"\n6. FRESHNESS  = 1 + {se.RECENCY_BOOST} x 0.5^(age / "
          f"{se.RECENCY_HALFLIFE:.0f})")
    print(f"   card year {card.year}, newest in catalogue {index.newest_year}, "
          f"age {age}")
    print(f"   factor = {factor:.4f}   ->  {raw:.3f} x {factor:.4f} = "
          f"{raw * factor:.3f}")
    print(f"\n   the curve:")
    for a in (0, 5, 10, 20):
        f = 1 + se.RECENCY_BOOST * (0.5 ** (a / se.RECENCY_HALFLIFE))
        print(f"      {a:2d} years old   x{f:.4f}   (+{(f - 1) * 100:.1f}%)")

    # ---- 7. bands --------------------------------------------------------
    boosted = {d: v * se.recency_factor(index.cards[d].year, index.newest_year)
               for d, v in engine_scores.items()}
    best = max(boosted.values())
    print(f"\n7. BAND  = int(10 x boosted / best)")
    print(f"   best score in this result set = {best:.3f}")
    print(f"   this card: int(10 x {boosted[card.doc]:.3f} / {best:.3f}) = "
          f"{int(10 * boosted[card.doc] / best)}")
    spread = collections.Counter(int(10 * v / best) for v in boosted.values())
    print(f"\n   how the {len(boosted):,} matching cards fall into bands:")
    for band in range(10, -1, -1):
        n = spread.get(band, 0)
        if n:
            print(f"      band {band:2d}  {'#' * min(n // max(1, len(boosted) // 40), 40):40s} {n:5d}")

    print(f"\n8. ORDER  = (band desc, then tiebreak desc, then title)")
    print(f"   tiebreak is send count when supplied, card year otherwise")
    print(f"   send counts are not in the export - see OPEN_ITEMS.md item 3")
    print("=" * 74)


if __name__ == "__main__":
    main()
