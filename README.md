# Card search

A replacement for the Sphinx search on 123Greetings. Pure Python 3 standard
library — no pip installs, no search server, no external service.

The searchable catalogue is 12,087 cards — `status_id = 1`, minus YouTube cards
(`card_label_type = 'Y'`), which embed a video and have no artwork to show in a
grid. The whole inverted index is about 2 MB and builds in under 1.5 seconds; a
query answers in 1–4 ms. Nothing at that size needs a search engine, so there
isn't one.

---

## Testing it, step by step

### Step 1 — clone, add your export, run

```bash
git clone https://github.com/indrajit-10/Search_algo
cd Search_algo
```

Now put your card export in the `data` folder. **Use your real path** — the
examples below are placeholders, not literal:

| | |
|---|---|
| **Windows** (PowerShell / VS Code terminal) | `copy C:\Users\you\Downloads\card_database.csv data\` |
| **macOS / Linux** | `cp ~/Downloads/card_database.csv data/` |

Or just drag the file into `data/` in Explorer or Finder. Then:

| | |
|---|---|
| **Windows** | `.\run.bat` |
| **macOS / Linux** | `./run.sh` |

In PowerShell the leading `.\` is required — `run.bat` on its own is not found.
And `./run.sh` is the Unix script; it will not run on Windows.

Open **http://localhost:8000**.

#### If a script gives you trouble, skip it

The scripts are a convenience, not a requirement. There are **no dependencies**,
so calling Python directly always works and is the thing to fall back on:

```
python serve.py              # the interface
python search_engine.py      # tests, then a prompt
```

On Windows try `py serve.py` if `python` is not found.

#### In VS Code

**Terminal → Run Task…** gives you a menu — open the interface, compare old vs
new, run all tests, audit the catalogue. They call Python directly, so they work
the same on every platform and use whichever interpreter is selected in the
status bar.

#### About the environment

`run.sh` / `run.bat` find a suitable Python, build an isolated `.venv` on first
run, and use it thereafter. **Nothing is installed into it** — every import is
standard library, verified by walking the AST of every file rather than by
reading them. Confirm it on your own machine:

```bash
./run.sh doctor          # .\run.bat doctor on Windows
```
```
python      /path/to/Search_algo/.venv/bin/python
version     Python 3.13.12
environment .venv (isolated)
third-party none - pure standard library
export      card_database.csv
```

So the virtualenv is not holding packages. It guards against a broken host:
`python` pointing at Python 2, a `PYTHONPATH` from another project shadowing a
stdlib module, user site-packages overriding something. If `python3-venv` is
missing (Debian and Ubuntu split it out) it falls back to the interpreter
directly and says so — with no dependencies that is perfectly safe.

Needs Python 3.7+. No walrus, no `match`, no builtin generics anywhere.

**CSV, not xlsx.** Excel reformats `card_created_date`, strips leading zeros
from `card_number`, and mangles the `%92` apostrophe sequences the whole
normalisation path depends on.

`data/` and `.venv/` are gitignored — production data cannot be committed.

### Every command

| Windows | macOS / Linux | What it does |
|---|---|---|
| `.\run.bat` | `./run.sh` | the browser interface |
| `.\run.bat test` | `./run.sh test` | all three suites in order |
| `.\run.bat compare` | `./run.sh compare` | old Sphinx pipeline vs new |
| `.\run.bat search` | `./run.sh search` | interactive prompt |
| `.\run.bat evaluate` | `./run.sh evaluate` | replay the query log |
| `.\run.bat audit` | `./run.sh audit` | measure the old system |
| `.\run.bat doctor` | `./run.sh doctor` | check the environment |

If you only run two, make them `compare` and the interface.

### Step 2 — run the engine's own tests

```bash
./run.sh search
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
./run.sh compare
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
./run.sh evaluate
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
./run.sh edge
```

513 assertions across nine sections: crash resistance, the no-empty-page
guarantee, limit enforcement, timing, normalisation, determinism. Payloads are
real — OWASP probes plus the actual garbage from your log, including the XSS
attempt someone ran 25 times. Expect `513 passed, 0 failed`.

### Step 6 — the browser test bench

```bash
./run.sh
```

Open **http://localhost:8000**. Type and results appear as you go.

- **Autocomplete** — suggestions appear from two characters in. Arrow keys to
  move, Enter to pick, Escape to dismiss. Ranked by how often your users
  actually searched each phrase.
- **New / Compare** — Compare shows old and new side by side with thumbnails.
- **Freshness** — off / subtle / strong / heavy. The status bar shows the mean
  result year, so you can pick the setting by looking rather than guessing.
- **Image URLs** — see below.

**Thumbnails work out of the box.** The template is confirmed against card
123057 (`q1_value` = `birth_happybirthday`), which really is served at
`https://i.123g.us/c/birth_happybirthday/pc/123057_pc.jpg`:

```python
IMAGE_TEMPLATE = "https://i.123g.us/c/{q1}/pc/{number}_pc.jpg"
```

The `_pc` derivative may be jpg for every card or may follow `card_thumb_extn`.
The page tries jpg first and retries once with the card's own extension, so both
conventions work. Only if both miss does the tile show the URL it tried.

**Card links are off.** The card page for that same card is
`/birthday/happy_birthday/birthday191.html`, but its `q1_value` is
`birth_happybirthday` — `birth` has to become `birthday` and `happybirthday` has
to become `happy_birthday`. Neither follows from splitting the slug, and the
export has no column holding the URL path. `PAGE_TEMPLATE` is left blank so
tiles are not dressed as links to a guessed 404. Set it once the slug-to-path
mapping exists, or point it at a redirect that resolves a card by number.

Both are editable live via **Image URLs** in the page. Placeholders:

```
{number} {q1} {occasion} {subcat} {page} {thumb_extn} {big_extn}
```

### Step 7 — check the claims for yourself

```bash
./run.sh audit
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
| `LOGIC.md` | How the search works, in plain English. No code. |
| `run.sh` / `run.bat` | One entry point. Builds `.venv`, then runs. |

---

> **Want the reasoning rather than the commands?** [LOGIC.md](LOGIC.md) explains
> the whole thing in plain English — what happens between someone typing and
> cards appearing, and why each decision was made. No code, no jargon.

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

**Autocomplete** is separate, at `GET /api/suggest?q=`. Phrases come from the
query log weighted by search volume, plus card tags at a heavy discount, and
every one is verified to return cards before it can be offered. Sub-millisecond.
The log is raw user input, so it also holds an XSS probe, path traversal, and the
upstream rewriter's output (`motherings`, `brothered`, `1cards`) — all filtered
out, since a suggestion is the search box speaking in its own voice.

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
