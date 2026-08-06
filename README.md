# Card search

A replacement for the Sphinx search on 123Greetings. Pure Python 3 standard
library — no pip installs, no search server, no external service.

The live catalogue is 13,042 cards (`status_id = 1`). The whole inverted index is
about 2 MB and builds in under 1.5 seconds; a query answers in 1–4 ms. Nothing at
that size needs a search engine, so there isn't one.

---

## Testing it, step by step

### Step 1 — drop your export in `data/`

You need Python 3.8+ and your card export. Nothing else — no pip install, no
virtualenv, no config file.

```bash
git clone <this repo>
cd Search_algo
cp /wherever/card_database.csv data/
```

That is the whole setup. Every script looks in `data/` on its own, so none of
them need a path argument. If the file is missing you get a message saying
exactly where to put it, not a stack trace.

The file must be the `cards` table with its normal columns — the same export you
already have. `data/` is gitignored, so production data can never be committed.

### Step 2 — run the engine's own tests

```bash
python3 search_engine.py
```

71 tests, one per reported complaint. You should see `71 passed, 0 failed`, then
a `search>` prompt. Type anything — each result shows *why* it matched and which
ladder rung answered:

```
search> funny birthday for mom
  Funny Happy Birthday Song Monkeys...  [birth_fun]  2018  tone=humour, occasion=birth
  -- 5 results in 1.4 ms via 2 facet(s) + best term
```

Press Enter on a blank line to quit.

### Step 3 — see old versus new, side by side

```bash
python3 search_engine.py --compare
```

Runs the same query through a faithful simulation of the current Sphinx pipeline
and through the new engine. This is the one that shows what actually changed:

```
QUERY: 'flash card'
  OLD      0 matches   ZERO RESULTS
         -> falls through to the popular-cards carousel
  NEW      5 shown     format=flash, matched card
```

### Step 4 — replay your real query log

```bash
python3 evaluate.py
```

Replays every query in `fixtures/real_queries.tsv` through both engines, weighted
by how often users actually searched it. Six sections; the two that matter are
**A. zero-result rate** and **B. regressions**.

```
queries returning nothing:     OLD 63/516  ->  NEW 0/516
searches hitting the carousel: OLD 23,230 (64.3%)  ->  NEW 0 (0.0%)
```

**Use your own log instead of mine.** The fixture holds 516 queries lifted by
hand; your full export is far bigger. Replace `fixtures/real_queries.tsv` with a
tab-separated file of `query`, `times`, `results` — where `results` is what
production returned — and re-run. 5,000 queries replay in about 9 seconds.

If your export is too large to move around, `gzip -9 -k card_database.csv` takes
33 MB to 8 MB, and `.csv.gz` and `.zip` are both read directly. Or export only
what the site serves, which is all the engine uses anyway:

```sql
SELECT * FROM cards WHERE status_id = 1 AND invalid_card = 0;   -- 13,042 rows, 3.6 MB
```

### Step 5 — hostile input

```bash
python3 test_edge_cases.py
```

513 assertions across nine sections: crash resistance, the no-empty-page
guarantee, limit enforcement, timing, normalisation, determinism. Payloads are
real — OWASP probes plus the actual garbage from your log, including the XSS
attempt someone ran 25 times. Expect `513 passed, 0 failed`.

### Step 6 — the browser test bench

```bash
python3 serve.py
```

Open **http://localhost:8000**. Type and results appear as you go.

- **New / Compare** — Compare shows old and new side by side with thumbnails.
- **Freshness** — off / subtle / strong / heavy. The status bar shows the mean
  result year, so you can pick the setting by looking rather than guessing.
- **Image URLs** — see below.

**Thumbnails will not load until you set the URL template.** The one in the code
is a guess, reverse-engineered from `inhouse_music` paths like
`/c/ejul_barnday/mp3/12440.mp3`. Click **Image URLs**, paste your real pattern,
press Apply. Each broken tile prints the URL it tried, so a wrong template is
obvious rather than silently blank. Placeholders available:

```
{number} {q1} {occasion} {subcat} {page} {thumb_extn} {big_extn}
```

Once you know the correct pattern, set `IMAGE_TEMPLATE` at the top of `serve.py`
and it becomes the default for everyone.

### Step 7 — check the claims for yourself

```bash
python3 audit_catalogue.py
```

Every number in the design was measured, not assumed. This prints them from your
data: stop-word damage, the `funny` precision failure, the 500-candidate cap
against catalogue age, tag coverage, encoding corruption, index cost.

---

## What each file is

| File | What it does |
|---|---|
| `search_engine.py` | The engine. Index, query understanding, ranking, relaxation ladder, 71 tests, interactive prompt, `--compare`. |
| `serve.py` | Browser test bench. Stdlib HTTP server, no framework. |
| `evaluate.py` | Replays the production query log through both engines. |
| `test_edge_cases.py` | 513 assertions on hostile and malformed input. |
| `audit_catalogue.py` | Measures the failures in the current system from the raw export. |
| `fixtures/real_queries.tsv` | Real queries with volumes and production result counts. |
| `data/` | Where your export goes. Gitignored. |

---

## How a query is answered

**1. Understand.** Normalise (apostrophes join words, `%92` and `+` decoded) →
alias → spell-correct → extract facets.

A query fills four slots, and each maps to a column you already have:

| Slot | Source | Example |
|---|---|---|
| Occasion | `q1_value` | birthday, anniversary, diwali |
| Recipient | tags, title | wife, mom, niece, grandson |
| Tone | tags, title | funny, romantic, heartfelt |
| Format | `card_label_type`, `card_music_extn` | animated, flash, musical |

Facets come from **tags and title only, never the description**. That single rule
is the fix for "funny returns wrong results": 1,105 cards say "fun ecard" in
their blurb without being humour cards, and they can no longer outrank the 150
that are genuinely tagged.

**2. Match and score.** Field-weighted, IDF-scaled. There is no stop-word list —
`card` simply carries near-zero weight, so `flash card` can no longer annihilate
to an empty query.

**3. Relax, accumulating.** All terms → drop the weakest → facets only → relax
facets by slot importance (occasion is given up last) → best term → **newest
cards**. The last rung is unconditional, so zero results cannot happen.

**4. Rank.** Relevance in ten buckets, freshness as a capped multiplier
(max +15%), then popularity or recency inside a bucket.

---

## Two things still open

**Send counts.** `search(..., popularity={card_number: score})` already accepts
them and will take over as the in-tier tiebreaker. Until that table is wired in,
recency stands in for popularity.

**Your query rewriter.** The log contains `musicality's`, `littleness's`,
`christmastide's`, `germanic's` — something upstream is inflecting query terms
into forms no human types, and they can never match anything. Roughly 900
searches a period. That layer lives outside this repo and should be deleted.

---

## Configuration

All at the top of `search_engine.py`:

```python
LIVE_STATUS   = "1"     # status_id that is actually served
FIELD_WEIGHTS = {...}   # title 3.0, tags 2.5, category 2.0, description 1.0
FACET_BOOST   = 12.0    # a facet hit outweighs prose matching
RECENCY_BOOST = 0.15    # newest card worth at most 15% more
```
