# Card search

A replacement for the Sphinx search on 123Greetings. Pure Python 3 standard
library — no pip installs, no search server, no external service.

The searchable catalogue is 12,087 cards — `status_id = 1`, minus YouTube cards
(`card_label_type = 'Y'`), which embed a video and have no artwork to show in a
grid. The whole index builds in about 5 seconds; a query answers in 1–2 ms.
Nothing at that size needs a search engine, so there isn't one.

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

120 tests, one per reported complaint. You should see `120 passed, 0 failed`, then
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

## The daily sends report

Which categories are people actually sharing? The social-sends log knows which
card went out but not what it was about; `q1_value` in the card list is the only
column that does. Join the two and the day sorts itself into occasions.

A `q1_value` is written `category_subcategory`, and the split is on the **first**
underscore only — everything before it is the category, everything after it is the
sub-category, however many underscores the sub-category carries of its own:

| `q1_value` | Category | Sub-category |
|---|---|---|
| `birth_happybirthday` | Birthday | `happybirthday` |
| `eaug_friendshipday_happy` | August occasions | `friendshipday_happy` |
| `anniv_ouranniversary_forher` | Anniversary | `ouranniversary_forher` |

Splitting on every underscore instead would invent categories that do not exist,
so that rule is asserted in the tests rather than left to read correctly.

Sixteen categories are reported under their own name:

```
birth_  thank_  gen_   love_  anniv_  insp_  cute_  congrats_
fkt_    bus_    pet_   w_     flwr_   friend_  intouch_  invp_
```

Every other prefix — the twelve month codes, `wed_`, and anything added to the
catalogue after that list was written — is counted as **Events cards**, one
bucket. It is an allow-list on purpose: a new occasion lands in Events rather
than appearing as a category nobody asked for, and moving it out is one line in
`CORE_PREFIXES`. `--split-events` turns the bucketing off and reports all 29
prefixes separately.

Under a named category the sub-category drops the prefix the heading already
carries (`birth_happybirthday` → `happybirthday`). Under Events cards it keeps
it, because the prefix is the only thing separating an August card from a
December one.

```bash
python3 social_sends_report.py
```

Put two files in `data/` and there is nothing to configure:

| File | What it is |
|---|---|
| `data/ACTIVE_CARDS.xlsx` | card number and `q1_value`. A full `card_database.csv` works too. |
| `data/social_sends_2026-08-01.tsv` | the day's sends. Newest **dated** file wins, so a second kind of export in `data/` cannot quietly become the default. |

The send log can be `.tsv`, `.csv`, `.xlsx`, `.csv.gz`, or the block of text you
get from selecting the rows in a database browser and hitting copy — one cell per
line, header and all. All three read to the same report, which is asserted in
`test_social_sends_report.py` rather than hoped for, because a log parsed into the
wrong columns still prints a tidy and completely wrong set of tables.

**Name more than one file and the report is cumulative** across them. The app's
own share sheet and the website's are two surfaces of the same product; they do
not overlap, so they add:

```bash
python3 social_sends_report.py app.tsv web.tsv \
        --label App --label "Web & mobile web" --detail
```

Every total is then broken back down by surface — a `BY SURFACE` table, a
`CATEGORY BY SURFACE` table, a surface line in each category block, and a
`by_surface.csv`. A total that cannot be broken back down is a total nobody
trusts. Columns the files disagree about are handled rather than averaged: if
only one file carries IP addresses, the sender and country counts say which
sends they were counted from instead of quietly describing a subset.

A **card × channel pivot** reads too — one row per card, one column per channel,
counts in the cells, and a `Total`:

```
Cardnumber   Whatsapp (Mobile Web)   SMS (App)   Total
359583       3                       0           3
```

It is recognised by its shape and expanded, a cell of 4 becoming four sends of
that card on that channel, so everything downstream is unchanged. Each row is
checked against its own `Total` column and a disagreement stops the run. A pivot
carries no timestamp, address or country, so the report leaves out the lines it
cannot answer rather than printing `1 sender` for a column that was never there.
Name it explicitly — `python3 social_sends_report.py data/pivot.tsv`.

```
Category                    Sends   Share  Cards  Senders  Top channel
Birthday                      373   63.1%     95      254  Text 50.9%
Events cards                  102   17.3%     39       39  More 41.2%
Anniversary                    38    6.4%     22       27  Text 60.5%
...
TOTAL                         591  100.0%    211      359
```

Then the same sends by sub-category (`birth_happybirthday`, 308), by channel,
the most-shared cards, and where they went. Two things are called out rather
than absorbed: sends whose card id is not in the card list, and sends that did
not report success.

`--detail` adds a block per category — every category, every one of its
sub-categories, nothing truncated:

```
BIRTHDAY                                     birth_*       373 sends     63.1%
  95 cards, 254 senders
  Channels   Text 190 (50.9%), More 112 (30.0%), Whatsapp 69 (18.5%),
             SMS 2 (0.5%)
  Sub-category                             Sends  Of cat  Of day Cards Senders
  happybirthday                              308   82.6%   52.1%    51     213
  fun                                         13    3.5%    2.2%     6       9
```

| Option | What it does |
|---|---|
| `--detail` | adds the per-category blocks, with sub-categories and a total underneath |
| `--label App` | names each file in the report; give one per file, in order |
| `--split-events` | reports all 29 prefixes separately instead of bucketing them |
| `--csv report/` | writes the five tables as CSV for a spreadsheet |
| `--out report.txt` | saves the text report as well as printing it |
| `--top 30` | lengthens the top-N tables (default 15) |
| `--cards FILE` | name the card list explicitly |

`--csv` writes `by_category.csv`, `by_subcategory.csv`, `category_detail.csv`
(one row per sub-category, carrying its category's totals so it pivots either
way), `by_card.csv` and `category_by_channel.csv`.

---

## Tracking it day by day

`social_sends_report.py` reports one day. `track_daily.py` keeps the history:
give it the day's two files and it adds them to a workbook covering every day
recorded so far.

```bash
python3 track_daily.py app.tsv web.tsv
```

```
2026-08-01: 1,059 sends recorded.
  App                     591 sends, 591 categorised, 211 cards
  Web & mobile web        468 sends, 427 categorised, 171 cards
reports/daily_tracking.xlsx now covers 1 day(s): 2026-08-01 to 2026-08-01.
```

The date comes from the app log. If neither file carries one — or they span
more than a day — it stops and asks for `--date 2026-08-02` rather than
guessing. **Running the same date twice replaces it**, so a corrected export is
just run again; `--once` refuses instead, if you would rather be told.

`reports/daily_tracking.xlsx` has six sheets:

| Sheet | What it holds |
|---|---|
| About | what the file is, the category rule, how to add a day |
| By date | one row per day: sends, app, web, categorised, uncategorised, cards, senders |
| Categories by date | the tracking view — a column per category, a row per day |
| Category detail | per day and category: app, web, total, share of day, cards |
| Sub-categories | every sub-category, every day |
| Platforms | Whatsapp, Text, More, SMS, Telegram, Facebook, Skype by day |

Alongside it are four `reports/daily_*.tsv` ledgers, and **those are the
record** — the workbook is generated from them. An `.xlsx` is a zip of XML, so
git can store it but cannot show you what changed inside it; a tab-separated
ledger makes a day's numbers one readable diff. Delete the workbook and
`python3 track_daily.py --rebuild` puts it back byte for byte.

Both live in `reports/`, which **is** committed — unlike `data/`, these are
counts by category and carry no card text and no addresses.

| Option | What it does |
|---|---|
| `--date 2026-08-02` | the day these files cover, when the files do not say |
| `--rebuild` | regenerate the workbook from the ledgers and stop |
| `--once` | refuse to overwrite a day already recorded |
| `--label` | rename the surfaces (default `App`, `Web & mobile web`) |

The workbook is written by `xlsx_writer.py` — about a hundred lines of zip and
XML, because adding openpyxl to a project whose whole promise is "no pip
installs" is a dependency every machine that ever runs this would have to
install. Its output is byte-identical for identical data, so a day that changes
nothing shows no diff.

---

## Letting someone else try it

The server already listens on every interface and handles requests in parallel,
so nothing needs changing to put it in front of someone. On start it prints both
addresses:

```
  this machine     http://localhost:8000
  same network     http://192.168.1.24:8000
                   (anyone who can reach it can search - there is no login)
```

### Same office or same wifi

Send them the **same network** line. That is the whole procedure. If it does not
open, the port is being blocked — allow 8000 through the firewall on the machine
running it, or use `--port=` to pick one that is already open.

### Anywhere else

Two ways, depending on whether this is a demo or something that has to stay up.

**A tunnel** is right for a demo and needs no server at all. `cloudflared tunnel
--url http://localhost:8000`, or ngrok, or Tailscale — each gives you an HTTPS
address that reaches the laptop you are already running it on. Start it, send the
link, close it when you are done.

**A small VM** is right if it has to outlive your laptop. Any $5 box will do.
Copy the repo and the CSV up — remember `data/` is gitignored, so the export
will not come with a `git clone` and has to be copied separately. Run it under
systemd or tmux so it survives you logging out, and put Caddy or nginx in front
if you want HTTPS and a hostname.

### Before you expose it, four things are true

**There is no login.** Anyone with the address can search. That is the entire
access model. It is your own public card catalogue so nothing secret is on show,
but the decision should be deliberate rather than a surprise.

**Ship only the live rows.** A full export is 107,160 rows, including archived
and invalid cards that are not served anywhere. The engine ignores them, so
there is no reason to put them on a box you do not control:

```sql
SELECT * FROM cards WHERE status_id = 1 AND invalid_card = 0;   -- 13,042 rows, 3.6 MB
```

**This is a test bench, not production.** Python's built-in HTTP server is
explicitly not meant to face the open internet — no rate limiting, no request
size limits, no protection against a client that connects and then goes quiet
beyond a 30-second timeout. Behind a tunnel, shown to a client, it is fine. As
the search endpoint for the live site, it is not; that is a job for the engine
plus whatever already serves 123greetings.

**Budget 512 MB and about 5 seconds.** The process settles around 320 MB with
the catalogue indexed, and the index builds once at startup. A 512 MB instance
is tight but works; 1 GB is comfortable. It must stay running — restarting per
request would mean a 5-second index build every time.

### How many people at once

Eight testers searching simultaneously answer in about 19 ms each. That is with
**Compare** off. Compare re-runs the old Sphinx pipeline, which rescans every
live row and costs roughly thirty times the new engine, so it is only run when
the page is actually showing it. Leave everyone on **New** unless they want the
side-by-side and it will stay quick.

---

## What each file is

| File | What it does |
|---|---|
| `search_engine.py` | The engine. Index, query understanding, ranking, relaxation ladder, 120 tests, interactive prompt, `--compare`. |
| `serve.py` | Browser test bench. Stdlib HTTP server, no framework. |
| `evaluate.py` | Replays the production query log through both engines. |
| `test_edge_cases.py` | 513 assertions on hostile and malformed input. |
| `audit_catalogue.py` | Measures the failures in the current system from the raw export. |
| `social_sends_report.py` | Daily social-sends log, counted by card category. |
| `test_social_sends_report.py` | 90 assertions on the shapes a send file arrives in, the category split, the Events bucket and the cumulative total. |
| `track_daily.py` | Adds a day to `reports/daily_tracking.xlsx` and its ledgers. |
| `xlsx_writer.py` | Multi-sheet .xlsx from the standard library. |
| `test_track_daily.py` | 29 assertions on accumulating days without double-counting or losing one. |
| `fixtures/real_queries.tsv` | Real queries with volumes and production result counts. |
| `fixtures/social_sends_paste.txt` | A send log copied out of the database browser, for the parser. |
| `data/` | Where your export goes. Gitignored. |
| `LOGIC.md` | How the search works, in plain English. No code. |
| `FLOWCHART.html` | The same thing as a diagram. Open in a browser, Ctrl/Cmd-P to print. |
| `run.sh` / `run.bat` | One entry point. Builds `.venv`, then runs. |

---

> **Want the reasoning rather than the commands?** [LOGIC.md](LOGIC.md) explains
> the whole thing in plain English — what happens between someone typing and
> cards appearing, and why each decision was made. No code, no jargon.
>
> **Want it on paper?** [FLOWCHART.html](FLOWCHART.html) is the same explanation
> as a diagram — the query pipeline, the relaxation ladder, and a what-changed
> table. Open it in a browser and print: three A4 pages, one per section, and the
> colours were chosen to stay readable in greyscale.

---

## How a query is answered

**1. Understand.** Normalise (apostrophes join words, `%92` and `+` decoded) →
alias → spell-correct → split run-together words → extract facets.

Correction spends up to 3 edits, but only where they are under 30% of the word.
A flat ceiling of 2 is half a four-letter word and a sixth of a twelve-letter
one: `roshasana` is 3 edits from `roshhashanah` and so found nothing, while the
ratio still blocks `mariachi` → `march`. Success on 3-edit typos of long words
goes from 27% to 92%; 1- and 2-edit behaviour is untouched.

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
