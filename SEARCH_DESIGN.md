# How to build the best search for 123Greetings

A design for the card search, starting from nothing and using only the columns the card
export already has. `search/` is a working implementation of stages 1 to 5 below; this
document is the reasoning, and the part that is still to come.

## 1. The diagnosis

Card search is not document search, and copying document search is why it goes wrong.

Someone shopping for a card is not looking for a document containing their words. They are
telling you about an event in their life, usually in words no card contains:

| What they type | What they want | Words shared with any card |
| --- | --- | --- |
| sorry i forgot your birthday | belated birthday | *none* |
| bday for hubby | birthday, husband | *none* — both words are slang |
| 50th | milestone birthday | *none* |
| feliz cumpleaños | birthday in Spanish | *none* after the accent breaks |
| send off | farewell | *none* |

All-words keyword search answers every one of these with an empty page. That is the whole
problem, and it is not a tuning problem. Four properties of this catalogue cause it:

1. **The query is an occasion, not a topic.** The catalogue is already organised by
   occasion, in the category code. Search ignores the code and reads the card text instead,
   which is the one place the occasion is *not* reliably written down.
2. **Queries are short, emotional and misspelled.** Two or three words, often slang
   (`hubby`, `sis`, `bff`), often a typo, often another language, often URL-encoded and
   accented (`Cumplea%F1os`).
3. **Every query has a right answer.** A bookstore can legitimately have nothing on a
   subject. This catalogue has a belated birthday card. If someone leaves empty-handed, the
   card existed and search hid it.
4. **Relevance moves with the calendar.** The best answer to "cards for the family" in
   October is not the best answer in December.

So the target is not a better keyword matcher. It is a search that reads intent, recalls
widely, ranks on several signals at once, and never shows an empty page.

## 2. Three principles

**Recall by intent, then rank.** Candidates come from the union of what matched the words
and what matched the meaning — never the intersection. Precision is the ranker's job;
recall lost at retrieval can never be recovered.

**Boost, never filter.** Intent, season and format all move cards up. None of them removes
a card. A wrong filter is invisible and unrecoverable; a wrong boost is a card two rows
lower.

**Never an empty page.** No result is a lost sale for a card that exists. There is always
something honest to show: the occasion's popular cards, then the site's.

## 3. The pipeline

### Stage 1 — Understand the query

Normalise: decode `%xx`, fold accents, lowercase, join possessives, so `Mother's Day`,
`Mothers Day` and `Mother%92s+Day` are one query. Then, in order:

- **Spell**: a word the catalogue never uses is corrected to the nearest word it uses a
  lot, one edit away. A word the intent dictionaries know (`hubby`, `rakhi`) is never
  touched, because it is spelled correctly even though no card says it.
- **Read intent**: match the longest known phrases, and map them to terms across six
  dimensions — occasion, recipient, tone, language, month, keyword. `forgot` → belated.
  `hubby` → husband, and weaker, spouse and him. `50th` → milestone. This is the existing
  [`intent/`](intent/README.md) work and it is the heart of the system.
- **Separate format from meaning**: `video`, `animated`, `printable` describe how a card is
  made, not what it is about. They must not be matched as occasions, and they must not
  filter the results either.

### Stage 2 — Recall from two sides, and take the union

- **Text**: the cards whose title, tags, category words or description match any query
  word.
- **Intent**: the best cards in each category the intent points at, whether or not their
  text matches anything.

"sorry i forgot your birthday" has no text candidates and plenty of intent candidates. A
navigational query like "feel better fast" has the reverse. Both work, because the sets are
unioned.

### Stage 3 — Make the category code searchable text

This is the highest-leverage index-time step and it costs no new column. `q1_value` is the
only taxonomy in the export, and it is dense with meaning that no card's own text carries:

```
birth_belated        -> Birthday, occasion: birthday belated
birth_hubbywife      -> Birthday, recipients: husband wife spouse
eoct_diwali_family   -> Events in October, event: Diwali, occasion: diwali,
                        recipient: family, month: october
w_spanish_birthday   -> World languages, language: spanish, occasion: birthday
```

Those words are indexed as a fourth field on every card in the category. A card filed under
`birth_belated` is findable as a belated birthday card even if its own title is "Sorry I'm
Late!". The dictionaries that read the codes already exist and are hand-maintained;
categories no dictionary covers are split by a segmenter trained on the site's own card
text and flagged `source=auto` for review.

### Stage 4 — One blended score

Four fields, weighted, under BM25F, because a word in the title means more than the same
word in the description:

```
title 5.0    tags 3.0    category words 2.5    description 1.0
```

Then every signal on a 0..1 scale, so the weights mean what they say:

```
score = 0.40 x text match          BM25F, normalised against the best hit for this query
      + 0.35 x intent match        how well the card's category fits what was asked
      + 0.14 x card quality        the popularity column, log-compressed
      + 0.08 x seasonality         how near this card's occasion is, today
      + 0.03 x freshness           how recently the card was added
      + 0.10 if the title is what the query named, in order
      + 0.08 if the card is the format the query asked for
```

Two details that matter more than their size suggests:

- **Seasonality only when the query names no date.** "cards for the family" in October
  should surface Diwali. "merry christmas" in October should not be second-guessed — the
  person said when they mean. Next month outranks last month, because people shop ahead.
- **A relevance floor.** A card matching only the word "your" is noise, not a result. Drop
  anything under 30% of the top score, unless that would cut the page below eight results.

### Stage 5 — Lay out the page

- **Cap one category at three cards.** Page one should show the range of what the site has,
  not twelve variations of one card. Nothing is dropped; the rest are pushed below.
- **Facet the results**: group, occasion, recipient, tone, language, and a format filter.
  This is where `video` belongs.
- **Say what you did**: "Showing Birthday: Husband and Wife", "did you mean birthday", so
  the person can widen or correct the search themselves.
- **Fall back, visibly**: popular cards in the intended categories, then site-wide popular
  cards, and never a blank page.

## 4. What the columns buy, and what is missing

Search reads only the export's columns. Six are required:

| Column | What it does |
| --- | --- |
| `card_title` | the strongest text signal, and the navigational match |
| `card_tags` | the second strongest; the editorial lever on relevance |
| `card_description` | recall, weakly weighted |
| `q1_value` | every facet, every intent boost, the diversity cap and seasonality |
| `status_id`, `invalid_card` | which cards are live |

Four more are used when present, and their absence costs a signal, not a search: an id
column, a popularity column, a format column and a created-date column. `search/columns.py`
finds each by any of several spellings and reports what it could not find.

Two gaps are worth naming, because they cap how good this can get:

- **There is no click or send log in the export.** Popularity is the export's own column,
  which is a proxy. Real engagement data is the single biggest available improvement —
  see phase 2.
- **There are no category titles.** Facet labels are read from the codes. When the category
  table is available, its titles should replace the derived ones.

## 5. What is built, and how to run it

```sh
python3 intent/build_intent_table.py card_export.csv   # the intent tables, for review
python3 search/index.py card_export.csv                # the search index
python3 search/rank.py --explain "sorry i forgot your birthday"
python3 search/eval.py queries.txt                     # measure it on real queries
python3 -m unittest discover -s search -v              # 31 tests, no export needed
```

`search/rank.py --explain` prints the parts of every score. A ranking nobody can explain is
a ranking nobody can improve, so every stage reports its reasoning, and the tests assert on
behaviours rather than on numbers.

The implementation is a single-process reference: it holds the index in memory and answers
in about a millisecond on a small catalogue. It is meant to prove and tune the algorithm,
and to be portable to OpenSearch or Elasticsearch, where the design maps directly —
BM25F to a `multi_match` over the four fields, intent to a `should` clause of
`terms` on the category code with per-category boosts, quality and season to
`function_score`, the cap to `collapse`.

## 6. What comes next, in order of payoff

**Phase 1 — ship it and measure.** Run `search/eval.py` over a month of real queries from
the search report. The headline number is the share of searches that return nothing, next
to what the current search returns nothing for. Then fix the top of the `--worklist` output:
every frequent query with no intent and no match is a missing line in
`query_phrases.csv`, and each one is minutes of work.

**Phase 2 — learn from clicks.** Log `query → shown → clicked → sent`. That gives, in
rising order of value: a real quality prior per card, replacing the popularity column; a
per-query-per-category click-through rate that corrects intent boosts the dictionaries got
wrong; a spelling dictionary from what people retype; and enough labelled data to fit the
six weights in stage 4 instead of setting them by hand. Fitted weights on real clicks are
worth more than any further hand-tuning.

**Phase 3 — semantic recall.** Embed title, tags and category label per card, and the query
at search time, and add nearest neighbours as a third candidate source. This is what catches
the paraphrase no dictionary anticipated — "my dog died", "thinking of you at this difficult
time". It is phase 3 and not phase 1 because the intent table already covers the frequent
head of that traffic at a fraction of the cost and with none of the opacity; embeddings are
for the tail.

**Phase 4 — the details that compound.**
- **Autocomplete** off the same intent table, so the query is fixed before it is submitted.
  It is cheaper to prevent a bad query than to rescue one.
- **A real occasion calendar.** Seasonality is month-granular today because the codes only
  say the month. Actual dates would let the boost ramp in the two weeks before a festival
  and fall away the day after.
- **Language.** Detect the query's language and prefer cards in it, rather than treating
  `spanish` as one term among many.
- **Recipient gaps.** Granddaughter, nephew, cousin and aunt have no category of their own
  and currently fall back to kids or relatives. Either add the categories or make the
  fallback explicit in the interface.

**Phase 5 — A/B everything.** Each of the weights in stage 4, the floor, the diversity cap
and every phase above is a hypothesis. Hold them against sends per search, not clicks per
search: a click is interest, a sent card is the product working.

## 7. How to know it is working

In priority order:

1. **Zero-result rate**, weighted by how often each query is really typed. This is the
   number the whole design exists to move.
2. **Sends per search**, the business metric. Clicks are a proxy for it.
3. **Searches per session.** Falling is good: it means the first page answered.
4. **NDCG@10** against a golden set of a few hundred labelled queries — worth building once
   phase 2 provides the clicks to label from.
5. **Latency**, p95, on the real catalogue.

## 8. The one thing not to do

Do not require every query word to match. All-words matching is the cause of the empty
pages, and every other improvement here is worth less than removing it.
