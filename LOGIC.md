# How the search works

Written to be read by anyone — no code, no jargon. It explains what happens
between someone typing in the search box and cards appearing on screen, and why
each step is there.

Every number below was measured against the real catalogue, not estimated.

---

## The short version

Someone types **"funny birthday for mom"**. The search does five things:

1. **Cleans up what they typed** — fixes typos, handles apostrophes, decodes junk.
2. **Works out what they meant** — this is a *birthday* card, it should be *funny*, it is for a *mother*.
3. **Finds every card that fits** — and scores each one.
4. **Broadens the search if too few fit** — giving up the least important part first.
5. **Puts the best ones first** — good match beats popular, popular beats old.

It answers in **about 2 milliseconds**, from **12,087 cards**.

---

## Why this was rebuilt

The old search had five reported problems. All five turned out to have specific,
measurable causes — and we found a sixth that was bigger than any of them.

| The complaint | What was actually happening |
|---|---|
| "Missing results" | The word list that removes filler words contained **"flash"** and **"animated"**. So *"flash card"* had both its words deleted and searched for nothing. |
| "Spelling errors find nothing" | Correction was a hand-written list of about 200 words. Perfect on the 200; nothing at all on the 201st. *"birthdya"* is not on it, and found 0 of 819 birthday cards. |
| "Funny gives wrong results" | The synonym list rewrites *funny* to *fun* before the search runs. 817 cards say *"fun"* in their blurb; 731 of them are not humour cards. Only 200 are genuinely tagged as humour. |
| "Misspellings give wrong output" | With no correction, a typo returned nothing, and a fallback quietly showed unrelated popular cards instead. |
| "Old cards always first" | Ranking used **lifetime send count**. On a catalogue running since 2002, a card uploaded last month cannot ever catch up. New work was mathematically unrankable. |
| **The one nobody reported** | **Any search containing an apostrophe returned zero results.** *"mother's day"* — 520 searches, nothing. *"father's day"* — 527 searches, nothing. Across the query log that is **22,399 searches landing on an empty page** — 96% of every empty page served. |

That last one was found by reading the query log, not by anyone reporting it —
which makes sense, because a user who gets nothing simply leaves.

---

## Step 1 — Cleaning up what was typed

Before anything else, the typed text is tidied.

**Apostrophes join words, they do not split them.** *"mother's"* becomes
*"mothers"*, not *"mother"* + *"s"*. Splitting left a stray "s" that matched
nothing and dragged the whole search to zero. This one change rescues
**22,399 searches** per period.

**Encoded junk is decoded.** Card text contains `%92` where an apostrophe
should be — *"Mother%92s Day"* could never match *"mothers day"*. Both sides are
now normalised to the same thing.

This is smaller than it first looks and worth stating accurately: `%92` occurs
20,164 times across titles, descriptions and tags in the full export, but only
**632 times on 295 of the 12,087 live cards**. The rest sits in archived rows
nobody searches. The apostrophe damage was overwhelmingly on the *query* side,
not in the catalogue.

**Web-encoded spaces are handled.** Queries arrive as *"mother's+day"*. The plus
becomes a space.

**Everything is lowercased and stripped of punctuation**, so *"BIRTHDAY!!"* and
*"birthday"* are the same search.

---

## Step 2 — Fixing typos

If a word is not one the catalogue uses, we find the closest word that is.

*"birthdya"* → *"birthday"*. *"aniversary"* → *"anniversary"*.

Two details matter here.

**Swapped letters count as one mistake, not two.** *"birthdya"* is *"birthday"*
with two letters swapped — the single commonest typing error. Counting it as two
mistakes made a nonsense word look like a closer match.

**A word must appear on at least 3 cards to count as correctly spelled.** This
sounds like a technicality; it is not. The catalogue's own tags contain
*"aniversary"*, *"chrismas"* and *"birthdy"*, put there years ago as search-engine
bait. Without this rule the system treats those typos as real words, so
*"aniversary"* stays misspelled and finds the 1 spam card instead of the 269 real
anniversary cards.

**How wrong a word is allowed to be depends on how long it is.** This sounds
fussy and it is the difference between finding a card and not. Two mistakes is
half of a four-letter word but a sixth of a twelve-letter one, so one fixed
allowance is simultaneously too generous for short words and far too mean for
long ones.

Long words are exactly the ones people get wrong by three or more letters —
transliterated festival names above all. *"roshasana"* is three slips from
*"roshhashanah"*, so it found **nothing at all**, while 240 Rosh Hashanah cards
sat in the catalogue. Allowing three mistakes on long words takes the success
rate on that kind of word from **21% to 96%** — measured on words of ten letters
or more, which is where a third mistake stays under 30% of the word. Between
seven and nine letters the ratio still refuses most third edits, and the gain is
a smaller **27% to 39%**. One- and two-letter mistakes are unaffected either way.

The catch is that three mistakes is a lot of licence, so it has to be earned:
the mistakes must be **under 30% of the word**. That is what separates
*"roshasana"* → *"roshhashanah"* — three letters out of twelve, a believable
slip — from *"mariachi"* → *"march"*, three out of eight, which is a different
word. Answering a mariachi search with March cards is worse than answering it
with nothing, and that is precisely what happened before the rule was added.

**Two words typed as one are pulled apart.** *"merrychristmas"*,
*"happyanniversary"*, *"congratulationsgraduate"* all used to reach the
newest-cards fallback. They are only split once the spell corrector has given up,
so a genuine typo is always fixed rather than carved up — *"aniversary"* becomes
*"anniversary"*, never *"ani versary"*.

**Partial words are not typos.** Someone typing *"valentin"* is mid-word, not
wrong — and *"Valentín"* is a real Spanish word in this catalogue. Partial words
are completed, not corrected.

### The same festival under another name

Some words are not misspelled at all and still find almost nothing.
*"Deepavali"* is the Sanskrit name for Diwali. It is spelled correctly, sits on
21 real cards, and is five letters away from *"diwali"* — so the spell corrector
rightly leaves it alone, and the searcher gets 21 cards instead of 470. Same for
*"Chanukah"* against *"Hanukkah"*, and *"Dasara"* against *"Dussehra"*.

These are linked by name, so searching either spelling reaches all the cards.
Both spellings stay searchable — a card that really does say *"deepavali"* still
ranks above one that only says *"diwali"*.

**Each link is checked against the catalogue before it is believed.** A claim
that two words mean the same thing is a claim about the world, and that is
exactly the kind of claim that went wrong with Independence Day. The test is
whether both spellings live in the same part of the catalogue. Of twelve pairs
offered, **five were dropped** — including one where the catalogue showed the
link pointing the wrong way round.

The obvious alternative — link any two words that keep appearing on the same
cards — was tried and rejected. It proposes 1,707 pairs, and they are mostly
topical rather than synonymous: *"solstice"* sits beside *"family"*, *"wishes"*
and *"friends"* on every one of its 54 cards without meaning any of them.

---

## Step 3 — Working out what they meant

This is the part that makes the search understand rather than just match.

Almost every greeting-card search fills some combination of four slots:

| Slot | Question it answers | Examples |
|---|---|---|
| **Occasion** | What is the event? | birthday, anniversary, diwali, get well |
| **Recipient** | Who is it for? | wife, mom, niece, grandson, boss |
| **Tone** | How should it feel? | funny, romantic, heartfelt, sorry |
| **Format** | What kind of card? | animated, flash, musical |

So *"funny birthday for mom"* is understood as
**occasion = birthday, tone = funny, recipient = mother** — not as four
unrelated words to look for.

The catalogue already knows all four. Occasion comes from the category each card
sits in. Recipient and tone come from its tags. Format comes from a column that
records whether a card is animated, Flash or musical.

### The single most important rule in the system

**Tone and recipient are read from a card's tags and title only — never from its
description.**

This is the entire fix for *"funny gives wrong results"*, together with dropping
the `funny => fun` synonym that caused it. There are 817 cards whose blurb
says something like *"send this fun ecard"*, and 731 of them are not humour
cards at all — they are weddings, sympathy, anything. Only 200 are genuinely
tagged as humour.

A card is funny because someone **labelled** it funny — not because the word
"fun" appears in a sentence about it. The description still helps a card be
*found*; it is never allowed to decide what a card *is*.

---

## Step 4 — Finding and scoring the cards

### There is no list of banned words

The old system deleted "filler" words like *"card"*, *"free"* and *"happy"*
before searching. That is why *"flash card"* returned nothing — both words were
on the list.

Instead, every word is weighed by how rare it is. *"card"* appears on nearly
every card, so it carries almost no weight — but it is never deleted, so it can
never annihilate a search. *"belated"* is rare, so it counts for a lot.

This is why *"flash card"* now works: "flash" is recognised as a format, "card"
contributes nearly nothing, and the search still has something to go on.

### Where a word is found matters

The same word is worth different amounts depending on where it appears:

| Where | Weight | Why |
|---|---|---|
| Title | 3.0 | Short and deliberate — but unreliable alone: 83% of Halloween titles say *"halloween"*, only 16% of Valentine's say *"valentine"* |
| Tags | 2.5 | Carry recall nothing else does: 44% of the 209 *"funny"* cards are reachable through their tags and no other field |
| Category | 2.0 | Reliable but broad |
| Description | 1.0 | Widest reach, worst precision — the safety net, never the reason a card ranks first |
| Web address | 0.4 | Weak hint |

These weights come from measurement. Titles here are the greeting *printed on
the card* — *"Across The Miles…"*, *"Mazel Tov!"* — so they frequently say
nothing about the occasion, which is why tags carry so much of the load.

### Matching a slot beats matching text

If a card is tagged humour and someone asked for something funny, that counts for
far more than the word appearing in a sentence. That is what stops the 731
"fun" blurbs outranking the 200 genuinely funny cards.

The boost scales with how much a slot narrows things down. "Tagged humour" is
specific and worth a lot. "Is a December card" covers Christmas, Boxing Day and
ice-cream day, so it is worth much less — otherwise every December card would
tie and the words would stop mattering.

---

## Step 5 — Broadening the search when too little fits

The old search was all-or-nothing: every word had to match, and if that failed
you got an empty page. **That is a cliff, and 64% of all logged searches fell off
it.**

Instead the search gives things up gradually, in order of least important first,
and keeps collecting results until the page is full:

**Full is 20 cards.** That is the number the whole ladder is aimed at: it climbs
down rung by rung until 20 unique cards have been collected, then stops. (The
browser test bench asks for 24; it is one setting, `MAX_RESULTS`.)

```
1.  everything the user asked for
2.  drop the least informative word          ("card", "for", "my")
3.  keep the slots, keep only the best word
4.  keep the slots only
5.  give up the vaguest slot, then the next
6.  the single strongest word alone
7.  any word at all
8.  the newest cards
```

**Which slot is given up last is deliberate.** Occasion survives longest — it is
the anchor of a card search. Recipient goes first: a birthday card that is not
mother-specific is still a birthday card, but a Mother's Day card is no use to
someone who asked for a birthday.

### How many cards actually come back

Replaying the 515 real queries in the log:

| | Cards | Queries |
|---|---|---|
| Filled the page | 20 | 495 — 96% |
| Ran out of rungs with fewer | 1–13 | 20 — 4% |
| Came back empty | 0 | **0** |
| Nothing matched, so the newest were shown | 20 | 12 — 2% |

The more interesting number is how many cards match **before** any widening. The
typical query matches **24** — just over a page. But that average hides the
spread: 10% of queries match more than 500 cards, and **17% match nothing at all**
until the search widens.

That is the whole argument for the ladder. Just under two thirds of queries (62%)
are answered on the first rung and never need it. For the rest it is the
difference between a full page and an empty one.

### Zero results is now impossible

The last rung always returns something — **the newest cards**.

The old system had a version of this: a carousel of *popular* cards. But
"popular" on a catalogue running since 2002 means "old", and that carousel is
where nearly two thirds of searches ended up.

Newest instead. It gives fresh work its only guaranteed exposure, and turns a
dead end into something worth browsing.

Critically, **the page is told when it is showing a fallback**, so it can say
*"we could not find that — here is what is new"* rather than silently passing
newest cards off as matches. Pretending is worse than an empty page.

---

## Step 6 — Deciding the order

Three things decide the order, in this priority:

**1. How well the card matches.** Scores are grouped into ten bands rather than
compared exactly — otherwise the ordering would be decided by meaningless
decimal differences and nothing else could ever break a tie.

**2. How new it is.** A brand-new card is worth up to **15% more** than an
identical old one, halving every 5 years.

The 15% cap is the whole point. The old system ranked by lifetime sends, which
gave 2002 cards a permanent, unbeatable head start. Flipping that around with a
large freshness bonus would just recreate the same complaint pointing the other
way — 2026 cards burying a better 2011 match. A capped bonus can only reorder
cards that were already close; anything clearly better still wins regardless of
date.

**3. How popular it is** — within a band. Send counts are not in the export yet.
The system is ready for them; until then, newer stands in.

---

## Autocomplete

Suggestions appear from the **second character**, and at most **eight** are
shown. They are ranked by **how often your users actually searched each phrase** —
which is why *"birthday cards free"* appears even though no card contains that
phrase. 158 people a period type it.

The list is built once when the server starts and takes about 1.5 seconds. It
starts from 8,319 candidates — 516 phrases from the query log, weighted by search
volume, plus 7,803 card tags at a heavy discount — and four filters cut it down:

| | Removed | Left |
|---|---|---|
| Safe and sensible in shape | 63 | 8,256 |
| Every word used by 3 or more cards | 769 | 7,487 |
| Merged onto one spelling | 462 | 7,025 |
| Verified to return cards | 0 | **7,025** |

A lookup then takes about **0.4 milliseconds**. Two passes run: phrases that start
with what was typed, then phrases with a word inside starting with the last word
typed — which is what lets *"birthday f"* reach *"funny birthday cards"*. 82% of
the time all eight rows fill; 3% of the time nothing matches and the dropdown
simply stays shut.

Three of those four filters are worth explaining, because the query log is raw
user input and none of it can be trusted:

**Attacks are removed.** The log contains an attempted script injection someone
ran 25 times, along with path-traversal attempts. Suggesting those would hand one
user another user's attack, in the search box's own voice.

**Machine noise is removed.** Something upstream of the old search rewrites
queries into forms no human types — *"motherings"* (58 searches), *"brothered"*
(65), *"1cards"* (535). They rank well and can never match anything. Every word in
a suggestion must be a word the catalogue actually uses. **This upstream rewriter
still exists and should be switched off** — it is generating roughly 900
guaranteed-empty searches a period on its own.

**Every suggestion is checked to return cards.** This one currently removes
nothing — the word filter above already guarantees it, since a phrase made
entirely of words the catalogue uses will always find something. It is kept as a
backstop rather than deleted: a suggestion leading to an empty page is the search
box breaking its own promise, and that guarantee should not depend on a side
effect of another rule.

---

## What the search deliberately does not do

**It is not a recommendation engine.** The old ranking was, in effect — recent
popularity and lifetime sends decided almost everything, which is precisely why
old cards dominated. Recommendation belongs *after* finding, never instead of it.

**It does not do sentiment analysis.** "Funny" versus "romantic" is not positive
versus negative — it is a label, and labels belong on the cards.

**It does not assume where a holiday falls.** An earlier version had a built-in
table saying "Independence Day means July". This catalogue serves the world:
independence days fall in July, August, September and December depending on the
country; New Year is January unless it is Rosh Hashanah in September;
Thanksgiving is November in the US and October in Canada. Twelve such assumptions
turned out to be wrong here.

Those hints are now **checked against the cards** and dropped where the catalogue
disagrees — nine were dropped. A confident wrong guess is worse than no guess,
because it overrides the words the user actually typed. That was why *"indian
independence day"* returned Bastille Day.

---

## The numbers

| | |
|---|---|
| Cards searched | 12,087 |
| Rows in the export | 107,160 (the rest are archived or YouTube) |
| Distinct words indexed | 7,463 |
| Slot values | 64 across occasion, tone, recipient, format |
| Cards on a page | 20 |
| Cards matching a typical query outright | 24 |
| Autocomplete phrases | 7,025, from 8,319 candidates |
| Autocomplete suggestions shown | 8, from the 2nd character |
| Index build time | about 5 seconds |
| Typical query | ~1.2 milliseconds |
| Typical autocomplete lookup | ~0.4 milliseconds |
| Searches now hitting an empty page | **0**, down from 64% |

**Tests:** 120 on the engine, 513 on hostile and malformed input, plus every
query in the log replayed through both the old and new systems with no
regressions.

---

## Still open

**Send counts.** The one genuinely useful signal not in the export. The ranking
already accepts them and will use them the moment the table is available.

**The upstream query rewriter.** Not part of this system, and actively harmful —
it turns real queries into nonsense that can never match.

**Card page links.** Thumbnails work, but a card's web address cannot be worked
out from the export. The card at `/birthday/happy_birthday/birthday191.html` is
filed as `birth_happybirthday` — *"birth"* has to become *"birthday"* and
*"happybirthday"* has to become *"happy_birthday"*, and nothing in the data says
so. Either a lookup table or a link-by-card-number redirect would solve it.
