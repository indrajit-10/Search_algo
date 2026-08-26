# Upgrading the Sphinx search in place

You do not have to replace anything to get most of this. Seven changes to the
existing pipeline — mostly config and query options — close about two thirds of
the gap. This document is what each one buys, where it goes, and **which two
pairs must ship together**, because half of either pair on its own makes the
search measurably worse.

The last section answers the better question: whether a feedback loop can
replace the hand-written synonym list entirely.

---

## What each step buys

Each stage is the one before it plus one change, replayed over the 516 real
queries.

| | zero results | funny, top 10 | `flash card` | mean result year |
|---|---|---|---|---|
| **0** as it is today | 73 | 1 of 10 | zero | 2013.4 |
| **1** + apostrophes join | **81** ⚠ | 1 of 10 | zero | 2014.0 |
| **2** + stemming | 61 | 1 of 10 | zero | 2014.0 |
| **3** + drop `funny => fun` | 63 | **9 of 10** | zero | 2013.7 |
| **4** + trim the stop list | **83** ⚠ | 9 of 10 | zero | 2015.0 |
| **5** + quorum relaxation | **23** | 9 of 10 | **2,176 hits** | 2015.9 |
| **6** + field weights and IDF | 23 | **10 of 10** | 2,176 | 2015.3 |
| **7** + relevance bands, newest breaks ties | 23 | 10 of 10 | 2,176 | **2017.1** |
| | | | | |
| *the new engine, for reference* | **0** | 10 of 10 | works | **2018.4** |

**73 → 23 zero-result queries. `funny` from 1 in 10 to 10 in 10. `flash card`
from nothing to 2,176 cards. Results four years newer on average.**

### The two ⚠ rows are the point of this document

**Stage 1 alone is a regression.** Making apostrophes join words takes
`love's` to `loves` — and `loves` is not a word the index holds, so it matches
*less* than the broken behaviour did. Zero-result queries go **up**, 73 to 81.

It only pays once **stemming** lands with it, folding `loves` onto `love`.
Together, 73 → 61.

**Stage 4 alone is a regression too.** Deleting fewer stop words means more
words survive into the query — and every surviving word is another thing the
strict AND demands. Zero-result queries go **up** again, 63 to 83.

It only pays once **quorum relaxation** lands with it, so a query can match most
of its words instead of all. Together, 63 → 23.

So this is not seven independent changes. It is **two paired changes and three
singles**:

```
  ship together:   apostrophes  +  stemming
  ship alone:      drop funny => fun
  ship together:   trim stop list  +  quorum relaxation
  ship alone:      field weights and IDF
  ship alone:      relevance bands, newest breaks ties
```

### One number the table understates badly

The simulation cannot reproduce the apostrophe bug — it normalises text before
applying the old rules, so its baseline is far healthier than production. It
shows 8.5% of searches reaching the carousel; **production sends 64.3%**.

From the query log, which is the authority: **22,399 searches a period** land on
the carousel purely because of the apostrophe. That is 96% of the carousel
problem and 62% of all your search traffic.

**Stage 1+2 is the single highest-value change on this list by a wide margin.**
The table cannot show it. The log can.

---

## Where each change goes

Directive names below are Sphinx; verify against your version, since Sphinx 2.x,
3.x and Manticore differ in places. "Reindex" means a full `indexer` run.

| | Change | Where | Reindex | Size |
|---|---|---|---|---|
| 1 | Apostrophes stop splitting words | `sphinx.conf` → `ignore_chars` | yes | 1 line |
| 1b | Decode `%92` in card text (295 live cards — minor) | `regexp_filter`, or fix the data | yes | 1 line |
| 2 | Stemming | `sphinx.conf` → `morphology = stem_en` | yes | 1 line |
| 3 | Drop 3 synonym entries | your synonym module | no | 3 lines deleted |
| 4 | Trim the stop list | `sphinx.conf` → `stopwords` file | yes | delete ~50 lines |
| 5 | Quorum relaxation | search code | no | ~20 lines |
| 6 | Field weights and BM25 | query `OPTION` | no | 1 line |
| 7 | Relevance bands + recency | `ORDER BY` | no | ~5 lines |
| 8 | Carousel shows newest, not most popular | `carousel_thank_xml_gen.pl` | no | 1 line |

### 1 — apostrophes

```ini
ignore_chars = U+0027, U+2019, U+02BC, U+0060
```

`ignore_chars` removes the character *without* splitting the word, which is
exactly what is wanted: `mother's` indexes and queries as `mothers`. Making it a
separator instead is what you have now.

The index side has the same corruption, but it is far smaller than the query
side and worth keeping in proportion: `%92` appears 20,164 times across titles,
descriptions and tags in the full 107,160-row export — but only **632 times on
295 of the 12,087 cards you actually serve**. A `regexp_filter` at index time or
a one-off repair of the column will do; it is a tidy-up, not the fix.

**The query side is the fix.** 22,399 searches a period are lost because the
user's apostrophe splits their word, not because a card's does.

### 2 — stemming

```ini
morphology = stem_en
```

Ship with step 1, not after it. See the ⚠ above.

### 3 — the three synonym entries

```
funny   => fun      ← delete
funnies => fun      ← delete
humor   => fun      ← delete
```

The module rewrites the query before Sphinx runs, so a search for **funny**
executes as **fun** — a word on 409 titles and 817 descriptions against funny's
16 and 90. It hits the 500-candidate cap with essentially no humour cards in the
top 10. Deleting three lines takes it to 9 of 10.

Consider `friend => friendship` and `romance => love` too: both replace a
specific word with a broader one and lose precision the same way.

### 4 and 5 — stop words and quorum

Cut the 68-word list back to genuine noise (`a`, `an`, `the`, `of`, `to`…).
Everything that describes a card — `flash`, `animated`, `card`, `free`, `happy`,
`greetings`, `wishes`, `printable`, `popular`, `beautiful` — comes out of the
file. BM25 already discounts common words by frequency; deleting them is doing
badly what the ranker does well.

Then relax the AND, using Sphinx's quorum operator:

```
try  "flash animated card"        →  if 0 rows
try  "flash animated card"/2      →  if 0 rows
try  "flash animated card"/1
```

Three queries worst case, each a millisecond. First non-empty wins.

This is the cheap version of the new engine's ladder — it relaxes by *count*
where the ladder relaxes by *what the words mean*, giving up recipient before
occasion. Counting gets you most of the way.

### 6 — field weights

```sql
SELECT ... OPTION ranker = proximity_bm25,
  field_weights = (card_title=10, card_tags=8, q1_value=6,
                   card_description=2, card_page_url=1)
```

No reindex. This is one line and it is the fix for "descriptions dominate" —
today every field weighs 1 and weight is a count of matches, so the longest,
vaguest field wins on volume. BM25 also brings IDF, so rare words start counting
for more than common ones.

### 7 — ordering

```sql
SELECT *, WEIGHT() DIV <step> AS band
ORDER BY validity DESC, band DESC, card_created_date DESC, sent_total DESC
```

Banding the relevance score is what lets anything else break a tie. Compared
exactly, scores differ in the third decimal and nothing after them can ever
matter — which is why sends decided almost everything.

Keep `validity` first. It is your best idea and the new engine cannot do it,
because the export carries no event date.

### 8 — the carousel

One `ORDER BY` in `carousel_thank_xml_gen.pl`: newest instead of most sent. On a
catalogue running since 2002, "most popular" means "oldest", so the page that
fires when nothing matches is also the page least likely to show anything new.

---

## What this does not get you

Three things in the table stay out of reach, and they are why the gap closes to
about two thirds rather than all the way.

**Spell correction.** Sphinx has no fuzzy matching. `birthdya` will still find
nothing. Two ways forward:

- **Manticore** (a Sphinx fork, largely drop-in) has `CALL SUGGEST` built on a
  trigram index of your own dictionary. This is the cheap path if a migration is
  acceptable.
- **Do it in the app.** Dump the dictionary with `indextool --dumpdict`, build a
  deletion index once at startup, correct before querying. That is what the new
  engine does, in about 120 lines, and it is portable to PHP or Perl.

**Never returning zero.** Quorum `/1` gets close, but it still fails when no
word is in the index at all. The guarantee needs an unconditional last step.
That is cheap to add — it is step 8 above, used as a rung rather than an error
page.

**Slots.** Knowing that *funny* is a tone and *mom* is a recipient, and giving up
recipient before occasion when relaxing. This is the part that is genuinely a
rewrite, and it is worth the least of the three: quorum already recovers most of
the recall.

---

## The synonym list, and whether feedback can replace it

Short answer: **yes, and it mostly already has.**

### The list is not the mechanism any more

Your module holds 188 hand-written pairs. Measured against the new engine:

| | pairs |
|---|---|
| The engine already reaches the same cards without any rule | 59 |
| Reaches them partly | 31 |
| Misses them | 52 |
| Target is not in the catalogue at all | 24 |

The new engine carries **7** hand-written pairs, not 188 — and each of those 7
is checked against the catalogue before it is believed, with 5 of the 12 offered
being dropped as unsupported.

Everything else that the list was doing is now *derived*:

- **Misspellings** come from edit distance against the catalogue's own
  vocabulary. `hasana`, `hashana`, `hashanna`, `hashannah`, `hashona`,
  `hashonah` are six list entries; the corrector needs none of them, and also
  handles `roshasana`, which is not on the list.
- **Run-together words** are split against that same vocabulary.
- **Inflections** (`dads`, `moms`, `grads`) come from suffix folding.
- **Occasion vocabulary** is derived from the catalogue's own categories.

What is left is the genuinely irreducible case: **two different names for the
same thing**. `deepavali` and `diwali` share no letters worth speaking of.
`chanukah` and `hanukkah` are the same festival under two transliterations. No
spelling algorithm can derive those, because they are not spelling errors — they
are facts about the world.

**That is the only thing a list is still doing, and it is exactly what a
feedback system is good at.**

### What a feedback system would learn

Three signals, in increasing order of value.

**1. Reformulation pairs — this is the synonym miner.**

Within one session: someone searches `deepavali`, gets 21 cards, does not click,
searches `diwali`, gets 470, clicks one. That sequence is the user telling you
`deepavali → diwali`. Seen from enough distinct people, it is a synonym you
never had to think of.

This is how large search engines build their synonym tables. It finds what no
list anticipates — `roshasana` was never on yours and never would have been.

**2. The click graph — this beats text matching.**

Query → the cards people actually open and send. A card that gets sent for
"funny birthday" *is* a funny birthday card, whatever its tags say. This is a
better relevance signal than any amount of field weighting, and the new engine
already accepts it: `search(..., popularity={card_number: score})` is waiting for
exactly this table.

**3. The zero-result feed — the shortest path to money.**

Every query returning nothing, ranked by volume, is a worklist. Some entries are
synonyms to add; some are content to commission. Either way you find out
*before* someone reports it — which matters, because they do not report it. That
is the entire reason the apostrophe bug survived: a user who gets nothing leaves.

### Do you have the traffic for it

Yes, for the part that matters.

| queries searched | count | share of all searches |
|---|---|---|
| 500+ times | 12 | 37.7% |
| 100+ times | 58 | 70.4% |
| 20+ times | 247 | 89.5% |

**89.5% of your search traffic is in queries seen 20 or more times.** That is
comfortably enough repetition to learn a reformulation from behaviour rather
than guess it. The head is where the value is and the head is dense.

(The sample I have was lifted by hand and holds nothing below 10 searches, so it
cannot show you the true long tail. The tail will not learn — it never does, and
that is what derived correction is for. The two are complements, not rivals.)

### What it needs that you do not log today

The query log I was given has `query`, `times`, `results`. To learn from
behaviour it needs four more columns:

| | why |
|---|---|
| **session id** | reformulation is a pair of searches *by the same person* |
| **timestamp** | orders the pair, and gives seasonality |
| **card clicked** | the relevance signal; empty is a signal too |
| **card sent** | the conversion signal, stronger than a click |

Nothing exotic — but without the session id, none of it works, because you
cannot tell a reformulation from two unrelated people searching.

### What it will not do

Being honest about the limits, because a feedback loop oversold becomes a
feedback loop distrusted.

**Cold start.** A new card has no clicks, so it does not rank, so it gets no
clicks. Left alone, a click-driven ranker recreates the exact complaint you
started with — old cards first — by a new route. It needs deliberate
exploration: a slice of traffic shown new cards regardless of history. The
capped freshness bonus in the new engine is doing this job today.

**Rich get richer.** Position bias means the top result gets clicked because it
is on top. Raw click counts encode that. It has to be corrected for, or the
ranking freezes.

**Your rewriter poisons it.** Something upstream is turning queries into
`motherings`, `goodness's`, `1cards` — that is the top of your log by volume.
Feed that into a learner and it will confidently learn nonsense. **Turn it off
before you turn learning on**, not after.

**It is slow to react.** A festival that comes once a year gets one chance a
year to teach anything. Derived correction works on day one.

### So: is it intent-based?

The four slots the new engine fills — occasion, recipient, tone, format — are
intent, and the vocabulary for them is currently derived from your tags.

The interesting thing a feedback loop adds is that it lets **users** define that
vocabulary instead. If people searching *funny* reliably send cards tagged
humour, that confirms the slot. If they reliably send cards tagged *cute*, you
have discovered a synonym nobody wrote down — and one no dictionary would give
you, because it is true of your catalogue and your audience rather than of
English.

That is the real answer to your question. The list was never the right shape.
Most of it is already gone. What replaces the rest is not a longer list — it is
watching what people do when the search gets it wrong.

---

## What I would do, in order

1. **Turn off the query rewriter.** Costs nothing, removes ~900 guaranteed-empty
   searches a period, and it has to go before any learning is switched on.
2. **Apostrophes + stemming.** One reindex. Recovers ~22,000 searches a period —
   62% of all traffic. Nothing else on this list comes close.
3. **Delete the three synonym lines.** Three lines, fixes a filed complaint.
4. **Start logging session id, timestamp, click, send.** Do it now even if
   nothing consumes it for six months — you cannot mine history you did not keep.
5. **Stop words + quorum.** One reindex plus ~20 lines. Takes zero-result
   queries from 63 to 23.
6. **Field weights.** One line, no reindex.
7. **Ordering: bands, then validity, then recency.**
8. **Then decide** whether spell correction and the never-empty guarantee are
   worth Manticore, an app-side corrector, or moving to the new engine — with
   six months of click data by then to tell you where you actually stand.
