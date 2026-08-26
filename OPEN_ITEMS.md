# Every open problem, and what to do about each

You were right to ask for this. These have been mentioned in passing across
several conversations and never collected anywhere, which made them look like
excuses rather than a work list. This is the work list.

Each entry says what the problem is, what causes it, what has been done, what is
left, and who can close it. Nothing here is speculative — every number was
measured against the live catalogue or the query log.

**Nine items.** Two are closed and one is nearly there. Four are blocked on a
column this export does not carry. One is a layer above the search that should
be switched off. One is not built yet.

| # | Problem | State | Who closes it |
|---|---|---|---|
| 1 | Card page URLs | **mostly solved** — 7 of 8 verified | 30 min on the live site |
| 2 | Card image URLs | **closed** | — |
| 3 | Send counts for ranking | blocked on data | one SQL export |
| 4 | Event dates / seasonal relevance | blocked on data | one column |
| 5 | The upstream query rewriter | needs deleting | whoever owns that layer |
| 6 | Ratings and view counts | blocked on data | one SQL export |
| 7 | Developer-name search | blocked on data | the UserData table |
| 8 | YouTube cards excluded | **closed, by decision** | reversible in one line |
| 9 | Autocomplete seasonality | not built | half a day |

---

## 1. Card page URLs — the slug-to-path problem

### The problem

A search result should link to the card. Nothing in the export gives the link.

```
q1_value        birth_happybirthday
card_page_url   birthday191.html
the real page   /birthday/happy_birthday/birthday191.html
```

Two things are missing. **`birth` has to become `birthday`**, and
**`happybirthday` has to become `happy_birthday`**. `card_page_url` holds only
the filename; the directory is nowhere.

I said several times that this was not derivable. That was true of the approach
I had tried, and it was not true in general — I had not looked hard enough.

### What causes it

The slug is a compressed identifier, not a URL. Underscores were dropped from
inside words (`lemonjuice_day` for `lemon_juice_day`) and the top-level section
was abbreviated (`birth`, `thank`, `eaug`).

### The solution

**The word boundaries are in the data, just not in the slug.** Categories whose
cards were given descriptive filenames leak them:

```
q1_value      eaug_lemonjuice_day
a filename    happy_lemon_juice_day.html
```

Strip underscores from `lemonjuice_day` and from `lemon_juice_day` and both are
`lemonjuiceday`. So for each category, scan its own filenames for a run of
underscore-separated parts whose letters match the slug tail. That run, with its
underscores, is the path.

Where no filename gives it away, the tail is split against the catalogue's own
prose vocabulary — the same splitter the search uses for `merrychristmas`.

Two rules in that splitter had to be got right, and both were wrong first:

- **Only split a chunk that is not already a word.** The search index tokenises
  `q1_value`, so `nationaldog` is itself a "vocabulary word" with 47 cards
  behind it, and a splitter trusting that vocabulary never splits it. Counting
  only title, description and tags gives a dictionary of words humans wrote.
- **Fewest pieces wins, not most.** `happybirthday` cuts into `happy|birthday`
  and also into `happy|birth|day`, all three being real words. Preferring more
  pieces produced `/happy_birth_day/`.

`python derive_card_urls.py --write` produces `data/card_urls.tsv`, 1,197 rows.

### Where it stands

Against the 8 real URLs available — from the search page you sent — **7 are
correct.** Coverage over all 12,087 cards:

| | categories | cards | |
|---|---|---|---|
| Path recovered from the data | 164 | 1,446 | trust it |
| Slug had no boundaries to recover | 521 | 6,166 | almost certainly fine |
| Path is a vocabulary split | 512 | 4,475 | **spot-check these** |

And the first segment separately:

| | categories | cards |
|---|---|---|
| Confirmed against a real URL | 925 | 9,770 |
| Guessed from the abbreviation | 272 | 2,317 |

### What is left

**Fifteen first segments need confirming**, covering the 2,317 cards whose
section is a guess. The script prints a ready-made URL to test for each. This is
the highest-value half hour on this list, because one wrong first segment sends
every card in that section to a 404:

| segment | cards | guessing | | segment | cards | guessing |
|---|---|---|---|---|---|---|
| `love` | 595 | `/love/` | | `insp` | 136 | `/inspirational/` |
| `gen` | 354 | `/everyday/` | | `friend` | 126 | `/friendship/` |
| `w` | 344 | `/wishes/` | | `congrats` | 86 | `/congratulations/` |
| `anniv` | 201 | `/anniversary/` | | `cute` | 83 | `/cute/` |
| `fkt` | 79 | `/family/` | | `bus` | 73 | `/business/` |
| `invp` | 68 | `/invitations/` | | `wed` | 60 | `/wedding/` |
| `pet` | 47 | `/pets/` | | `intouch` | 37 | `/keep_in_touch/` |
| `flwr` | 28 | `/flowers/` | | | | |

**One case is genuinely underivable** and shows the shape of the risk.
`eaug_rakshabandhan_interactive` really lives at
`/events/rakshabandhan/interactive_cards/`. Two things happen that no rule can
see: the second segment becomes its own directory, and `interactive` is renamed
to `interactive_cards`. Nothing in the export records either.

The same over-splitting shows in `congrats_businessandworkplace`, which the
splitter renders `business_and_work_place` where the site probably writes
`workplace`. That is why those 512 categories are flagged rather than trusted.

### The alternative that avoids all of this

**A redirect by card number.** `/card/123057` → the real page, resolved from
the database that already knows. One route, no table to maintain, correct
forever. If that exists or can exist, set `PAGE_TEMPLATE` in `serve.py` to it
and item 1 closes completely today.

---

## 2. Card image URLs — closed

### The problem

The export does not spell out the CDN layout, so the grid had no images.

### The solution

Confirmed exactly from the search page you sent, and checked against
`card_thumb_extn` for six cards — **6 of 6 match**:

```
thumbnail   https://i.123g.us/c/{q1_value}/th/{card_number}_th.{card_thumb_extn}
large       https://i.123g.us/c/{q1_value}/pc/{card_number}_pc.jpg
```

Both are set in `serve.py`. The page uses the large one, tries `.jpg` first and
retries once with the card's own extension, so it works whichever convention is
in force. Nothing further needed.

---

## 3. Send counts — blocked on data

### The problem

Ranking has no popularity signal. Newness stands in for it, which is a proxy,
not the real thing.

### What causes it

The export has no `card_sent_count`. Your Algorithm v3 spec sorts on
*"unique card_sent_count of every card till date"* and on 6-hour sends, so the
data exists — it is just not in what was exported.

### The solution

Already built and waiting:

```python
search(index, query, popularity={card_number: sends})
```

Pass the table and it becomes the in-tier tiebreaker immediately. No code change.

### One thing to be careful about

Do **not** let it become the primary sort, which is what the old system did.
Lifetime sends on a catalogue running since 2002 is a permanent incumbency — a
card uploaded last month cannot out-accumulate twenty years of head start. That
is the mechanism behind the "old cards always first" complaint. As a tiebreaker
inside a relevance band it is useful; ahead of relevance it recreates the bug.

---

## 4. Event dates and seasonal relevance — blocked on data

### The problem

**This is the one thing your old system does that the new one cannot.**

Algorithm v1 sorts by *Validity* first: *"all general categories and the events
in last 2 days and coming within 88 days are valid"*. In June, Christmas cards
sort last. It is a good idea and it is your *first* sort key.

### What causes it

The export carries `card_created_date` — when the card was uploaded — and no
event date at all. There is nothing to compute an 88-day window from.

### The solution

Add the event date to the export. The ranking has an obvious slot for it, ahead
of freshness:

```
validity  →  relevance band  →  freshness  →  popularity
```

Until then the new engine is season-blind. On a catalogue that is more than half
event cards, that is a real gap and worth closing.

---

## 5. The upstream query rewriter — needs deleting

### The problem

Something between the user and the search is inflecting queries into forms no
human types, and they can never match anything:

| query | searches | can it ever match? |
|---|---|---|
| `s's x` | 1,975 | no |
| `goodness's` | 823 | no |
| `friendship's` | 598 | no |
| `1cards` | 535 | no |
| `musicality's` | 129 | no |
| `littleness's` | 107 | no |
| `christmastide's` | 90 | no |

Roughly **900 guaranteed-empty searches a period**, and they sit at the *top* of
your query log by volume — `s's x` alone outranks `father's day`.

### What causes it

Not this repo. It is a layer above the search, and I have not seen its code.
The pattern — possessives and odd suffixes on real stems — looks like a
stemmer or query-expander applied in the wrong direction.

### The solution

**Switch it off.** Nothing downstream needs it: the new engine does its own
correction, and Sphinx with `morphology = stem_en` would do its own stemming.

**Do this before any feedback-learning work**, not after. A learner fed this log
will confidently learn that `goodness's` is a popular query worth suggesting.

---

## 6. Ratings and view counts — blocked on data

### The problem

Your live results page shows `Rated 4.15 | 6,370K views | Liked by 100% Users`
under each card. The new results page cannot, so a side-by-side looks thinner
than production even where the ranking is better.

### What causes it

No `rating` or `views` column in the export.

### The solution

Same shape as item 3 — one SQL export, and the same warning applies: useful as
display, dangerous as a primary sort on a 2002 catalogue.

---

## 7. Developer-name search — blocked on data

### The problem

Algorithm v2.5 added searching by developer or organisation name — *"pulling the
cards he/she/organization/company has made"*. The new engine cannot.

### What causes it

The export has `dev_id` with **4 distinct values** across 12,087 live cards
(`123greetings` on 10,451 of them) and **no name column**. There is nothing to
match a typed name against.

### The solution

Export the UserData join — `dev_id` → developer and username. Then it is a
5-line change: add the name to the indexed fields at a low weight, the way the
URL field already works.

Worth checking the demand first. Searches for developer names are not visible in
the query log sample I have.

---

## 8. YouTube cards excluded — closed, by decision

### The problem

**955 live cards are not searchable.** That is 7% of the catalogue.

### What causes it

Deliberate, at your instruction. `card_label_type = 'Y'` cards embed a video and
have no artwork — every one has an empty `card_thumb_extn` **and** an empty
`card_bigimage_extn` — so there is nothing to put in a results grid.

Filtered on the label rather than on card numbers starting with 8, which is the
other way to spot them. The two agree exactly across all 107,160 rows, but a
number range is an accident of allocation and would not survive a renumbering;
the label says what a card *is*.

### If you want them back

One line — remove `"Y"` from `EXCLUDE_LABEL_TYPES` in `search_engine.py`. They
would need a poster image or a video-shaped tile in the results page, or they
appear as blanks.

Demand looks low: **11 searches** in the log sample mention video or YouTube.
That is one sample, not the whole log — worth re-checking against your full
export before deciding either way.

---

## 9. Autocomplete seasonality — not built

### The problem

Your Auto-complete Phase I spec draws suggestions from *"the last 10 days"*, so
`good` suggests `good friday` in April and `good morning` in other months. The
new autocomplete ranks on total volume across the whole log and has no such
sense of season.

### What causes it

I built it from a static query-log fixture with no timestamps, so there was
nothing to window on.

### The solution

Two parts, both small:

1. Add a timestamp to the query log. `serve.py` already writes one — production
   needs the same.
2. Weight the phrase scores by a rolling window rather than lifetime volume.
   About half a day inside `Suggester.__init__`.

Worth doing. It is the one place your spec is ahead of what I built.

---

## What I would do first

1. **Half an hour on the live site** confirming the 15 first segments in item 1.
   9,770 cards already sit under a segment seen in a real URL; this covers the
   remaining 2,317 and lets links go on for all 12,087 with confidence. It needs
   nobody but a browser.
2. **Ask whether `/card/{number}` can redirect.** If yes, item 1 closes entirely
   and the table becomes unnecessary.
3. **Turn off the query rewriter** (item 5). Costs nothing, removes ~900 dead
   searches a period, and it blocks the feedback work until it is gone.
4. **Export send counts and the event date** (items 3 and 4). One is a
   tiebreaker the ranking already accepts; the other is the only capability the
   old system has that this one lacks.

Items 6, 7 and 9 are worth doing and none of them is urgent.
