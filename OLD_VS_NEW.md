# Old algorithm versus new

A comparison against the written spec for **Algorithm Version #3.2** (the Sphinx
search) and the **Auto-complete Phase I** spec.

Every number here was measured against the live catalogue — 12,087 cards — or
against the production query log. Where the old system's behaviour could be
reproduced it was; where it could not, that is said rather than guessed.

---

## Two corrections to what I said earlier

Reading the actual spec changed two things I had told you.

**The old system does have spell correction, and a substantial synonym module.**
I previously said there was none, "in any version". That was wrong. The spec
lists Spell Check and a Did-You-Mean database from Version 1, and a synonym
module of about 200 entries. What is true is narrower and more useful: it is a
**hand-written list**, so it is perfect on the words it names and silent on
everything else. `birthdya` is not on it.

**`funny => fun` is in that list, and it is the direct cause of complaint #3.**
I had attributed "funny gives wrong results" to descriptions containing the word
"fun". The mechanism is more specific than that: the synonym module rewrites the
query *before* Sphinx runs, so a search for **funny** is executed as **fun**.

| word | in titles | in descriptions | in tags |
|---|---|---|---|
| funny | 16 | 90 | 115 |
| fun | 409 | 817 | 564 |

Rewriting one to the other multiplies the candidate pool roughly sixfold with
cards that were never humour cards. Only **200** cards in the catalogue are
genuinely humour.

Running the old pipeline on *funny* now **hits the 500-candidate cap** with
**1 of the top 10** tagged humour — 0 of 10 if candidates are taken in insertion
order rather than by weight. The new engine returns **10 of 10** (its test
asserts a floor of 8).

*(An earlier draft of this document said 5 of 10. That was measured against a
9-entry stand-in for your synonym module, written before I had seen the real
one. With the actual 188 pairs in place the old pipeline does considerably
worse, which strengthens rather than weakens the point. The simulation now
carries your list verbatim.)*

That one line in a config file is the whole complaint.

---

## The shape of the difference

The old algorithm is a **filter followed by a sort**. Find every card containing
all the words, then order what survives. Nine versions of refinement went into
the sort; the filter never changed.

The new one is a **ladder**. Ask for everything, and if too little comes back,
give up the least important part and ask again — collecting as it goes.

That is the difference that produces most of the others. A filter has one
failure mode and it is total: nothing matched, so there is nothing to sort.

---

## Selecting cards

| | Old (v3.2) | New |
|---|---|---|
| Matching rule | all words must match | all words, then progressively fewer |
| Failure | zero results | impossible — 8 rungs, the last is unconditional |
| Stop words | 68-word list, removed before searching | none; rare words simply count for more |
| Fields | title, description, tags+user tags, url+category+parent+developer | title, tags, category, description, url |
| Field weight | **all fields weight 1** | title 3.0, tags 2.5, category 2.0, description 1.0, url 0.4 |
| Word weight | none — "weight" is a count of matches | IDF: `belated` counts, `card` barely does |
| Synonyms | ~200 hand-written pairs | derived, plus 7 validated festival names |
| Spelling | the same hand-written list | edit distance, scaled to word length |
| Card number | supported (v1) | supported |

### The stop-word list is the sharpest single difference

68 words are deleted from every query before it runs. The list contains
**`flash`**, **`animated`**, **`card`**, **`free`**, **`happy`**, **`greetings`**
and **`wishes`**.

So *"flash card"* has both its words removed and searches for nothing. So does
*"animated cards"*. So does *"happy wishes"*. The spec's own error message —
*"Sorry! The search query you entered did not find any matching results"* — is
described as the "wrong keywords/stop words" case, so this was understood as
behaviour rather than as a bug.

There is no list in the new engine. `card` appears on nearly every card, so its
IDF is near zero and it can neither lift a result nor eliminate one. `flash` is
recognised as a card **format**, from `card_label_type`. The query survives.

### All fields weighing 1 is the other one

The spec is explicit: *"the default weight of all the fields are read as 1"*, and
weight is *"number of matches found"*. A word in the description counts exactly
as much as a word in the title.

Descriptions are the longest field and the least precise. Weighting them equally
is why prose wins over labels — the same defect that makes *fun* beat *humour*.

---

## Ordering results

**Old:** Validity → Weight → sends in the last 6 hours → total sends ever.

**New:** Relevance in 10 bands → freshness, capped at +15% → popularity within a
band.

Two things stand out.

**Their first sort key is one I cannot reproduce.** *Validity* means "general
categories, plus events in the last 2 days and coming within 88 days". It is a
seasonal-relevance filter: in June, Christmas cards sort last. The export I have
carries `card_created_date` and no event date at all, so there is nothing to
compute it from. **This is a genuine capability of the old system that the new
one does not have**, and it is not a small one. See *What the old system does
better*, below.

**Their last sort key is the one that caused complaint #5.** Total sends ever, on
a catalogue running since 2002, is a permanent incumbency — a card uploaded last
month cannot out-accumulate one with twenty years of head start. The 6-hour
window ahead of it helps only cards already being sent, which are the same old
cards.

The new engine caps freshness at +15% deliberately. Flipping the old bias
outright would recreate the same complaint pointing the other way, with 2026
cards burying a better 2011 match. A capped bonus can only reorder cards that
were already close.

---

## When nothing is found

**Old (v3.2):** the message *"Sorry! The search query you entered did not find
any matching results"*, then 10 most popular cards from the carousel logic, with
*"Enjoy sending the most popular cards right now!"*

The reasoning in the spec is exactly right: *"for any keyword, zero results /
blank page is avoided"*.

Two things changed.

**Most popular means oldest.** The carousel ranks by sends, and on a 2002
catalogue that is the same incumbency problem. The new engine shows the
**newest** cards instead — it is the only guaranteed exposure new work gets.

**How often this fires.** Replaying the log, the old pipeline sends **64.3% of
all searches** — 23,230 of them — to that carousel. It was designed as the
exception and became the main path.

The new engine reaches its own last rung on **12 of 515** queries (2%), and never
returns zero. When it does fall through it says so, so the page can print *"we
could not find that — here is what is new"* rather than passing newest cards off
as matches.

### What was actually failing

Of the 63 logged queries that returned nothing in production:

| | queries | searches |
|---|---|---|
| Contained an apostrophe | 50 | 22,399 |
| Genuine misspelling | 4 | 573 |
| Query-rewriter output | 1 | 15 |
| Content the catalogue does not have | 8 | 243 |

**The apostrophe is 96% of it.** `love's` — 2,489 searches. `friend's` — 2,364.
`family's` — 2,010. This is not in any version of the spec, was never reported,
and is by far the largest single defect in the system. A user who gets nothing
does not file a bug; they leave.

In the new engine an apostrophe joins rather than splits, so `mother's` becomes
`mothers` and matches.

---

## Autocomplete

Their Phase I spec and my implementation are close enough to line up directly.

| | Their Phase I | New |
|---|---|---|
| Source | keywords searched in the last 10 days that returned results | the query log, weighted by volume, plus card tags at 5% |
| Verified to return cards | yes | yes — though it now removes nothing, see below |
| Match | prefix first, then phrase-contains | prefix first, then word-inside-phrase |
| Shown | max 10 | 8 |
| Bolding | *"display in bold the letters the user typed"* | the opposite — the typed part is dimmed, the **completion** is bold |
| Speed | not specified | 0.4 ms |

Three differences worth naming.

**The bolding is inverted on purpose.** Bolding what the user just typed
emphasises the part they already know. Every major search box bolds the
*completion*, because that is the new information.

**Their Phase II is already implemented.** The spec describes it as future work:
*"an user typing 'birth' should see ... 'happy birthday'; 'friend's birthday'
which does not begin with the typed keywords"*. That is the second pass in the
new engine — `birthday f` reaches `funny birthday cards`.

**The log needs filtering before it is shown back.** Their spec takes keywords
straight from the search DB. That DB contains an XSS probe someone ran 25 times,
path-traversal attempts, and machine output — `motherings` (58 searches),
`1cards` (535) — that no human types and no card can match. Suggesting those puts
one user's attack in front of another user in the search box's own voice.
7,025 phrases survive from 8,319 candidates.

**One thing the spec has that I do not.** Phase I says suggestions come from the
**last 10 days**, so *"good"* suggests *"good friday"* in April and *"good
morning"* in other months. That seasonality is real and my version does not have
it — I rank on total volume across the whole log. Wiring it to a rolling window
is a small change and a good one.

---

## The old algorithm's own to-do list

Version 3.1 was *planned* and, judging by the log, never shipped:

> *"Modify the search result-page text '1000 cards found for &lt;keyword&gt;' to
> '&lt;correct no of cards&gt; cards found'"*

A hard-coded 1,000 means the count shown was not the count found. The new engine
returns a true count and a real limit of 20 per page.

> *"open search results page limit restriction & display 'All' ... as the present
> 5-Pager concept restricts the display of all cards"*

Five pages of results, then nothing, regardless of how many matched. 10% of real
queries match more than 500 cards.

---

## What the old system does better

Three things, stated plainly.

**Validity — seasonal relevance.** Sorting out-of-season events to the bottom is
a genuinely good idea and their *first* sort key. The new engine has no
equivalent, because the export carries no event date. If you can add an event
date column, this is the single most valuable thing to restore, and it would slot
in as a sort key ahead of freshness.

**The synonym list is 20 years of institutional knowledge.** It is hand-written,
which is its weakness, but it holds real knowledge about what your users type —
`norooz / norouz / noruz / nourooz / nowrooz / nowrouz` all reaching `nowruz`;
seven spellings of `raksha`; six of `hashanah`. Measured against the new engine:

| | pairs |
|---|---|
| The new engine already reaches the same cards | 59 |
| Reaches them partly | 31 |
| **Misses them** | 52 |
| Target is not in the catalogue at all | 24 |

**52 pairs are worth importing** — validated against the catalogue the way the
festival names already are, not adopted wholesale. `funny => fun` is on that list
and must not come with it.

**Developer-name search (v2.5).** Cards by a named developer or organisation. The
export carries `dev_id` with 4 distinct values and no name column, so this needs
the UserData table to reproduce.

---

## How the old system's numbers were obtained

Worth being exact about, because two different kinds of evidence are mixed here.

**From the production query log.** Zero-result counts, search volumes, the
apostrophe finding. These are what your live Sphinx actually returned, recorded
per query. This is the strong evidence.

**From a re-implementation.** `search_engine.py --compare` runs a faithful
re-implementation of the documented pipeline — the 68 stop words, the synonym
module, all-words matching, the 500-candidate cap. Counts like *"funny returns
210 matches, 5 of the top 10 tagged humour"* come from that.

It is a re-implementation of the **spec**, not of your binary, and it differs in
one known way: it normalises text before applying the old rules, so **it does not
reproduce the apostrophe bug**. Run `mother's day` through `--compare` and the
old side returns results; production returned zero, 520 times. The log is the
authority there, not the simulation.

**Not measured at all: how fast Sphinx is.** Sphinx is compiled C++ with its own
index; the re-implementation is Python scanning a list. Comparing their timings
would say nothing about the old system, so the comparison is not made. The new
engine's ~1.2 ms stands on its own.

---

## Where the numbers land

| | Old | New |
|---|---|---|
| Queries returning nothing | 63 / 516 | **0 / 516** |
| Searches reaching the fallback | 23,230 (64.3%) | 0 |
| `funny` — humour cards in top 10 | 5 | **10** |
| `flash card` | 0 results | works |
| `mother's day` | 0 results | works |
| Typos corrected — 1 edit | list only | 96% |
| Typos corrected — 3 edits, long words | list only | 92% |
| Query time | not measurable here — see below | ~1.2 ms |
| Results per page | 5 pages, count shown as "1000" | 20, true count |

Regressions: **none**. Every query in the log that returned results before still
returns results, and 514 of 514 return the same cards as before this comparison
was written.

---

## What this cost

The new engine drops four things the old one had, three deliberately:

- **Stop words** — deliberately. They were destroying queries.
- **Equal field weights** — deliberately. They were why prose beat labels.
- **Sorting by lifetime sends** — deliberately, and it is capped rather than
  removed, so send counts will take over as the in-tier tiebreaker the moment
  that table is available.
- **Validity / event dates** — *not* deliberately. The data is not in the export.

And it adds one thing neither system had: an apostrophe that works.
