# Intent table

An intent table turns the words someone types into the card categories they probably want.
Keyword search asks "which cards contain these words?". The intent table asks "what is this
person looking for?", such as an occasion, a recipient, a tone or a language, and answers with
category codes.

| Someone types | Intent found | Category boosted |
| --- | --- | --- |
| sorry i forgot your birthday | belated, birthday | `birth_belated` |
| funny birthday cards to husband | birthday, husband | `birth_hubbywife` |
| birthday wishes for my sister | birthday, sister | `birth_bronsis` |
| happy diwali to my family | diwali, family | `eoct_diwali_family` |
| 50th birthday | birthday, milestone | `birth_milestone` |
| feliz cumpleaños | birthday, spanish | `w_spanish_birthday` |
| send off | farewell | `bus_farewell` |

None of these needs a card to contain the words typed. "sorry i forgot your birthday" returns
nothing from all-words keyword search, yet the belated birthday category is exactly what the
person wants.

## How it is built

The table has two halves.

1. **What people type, mapped to intent terms.** `dictionaries/query_phrases.csv` maps phrases
   such as `hubby`, `sorry for your loss` or `rakhi` to terms such as `husband`, `sympathy` or
   `raksha_bandhan`. A phrase can list weaker fallbacks: `husband` also matches `spouse` at 0.8
   and `him` at 0.4, so "anniversary for my husband" finds cards for him when no husband-specific
   category exists.
2. **Intent terms, mapped to category codes.** `build_intent_table.py` reads a card export, keeps
   live cards (status_id 1, not flagged invalid), and labels every category code from its parts:
   - the prefix names the group, such as `birth` for Birthday or `eoct` for October events;
   - `dictionaries/events.csv` names festivals, holidays and recipient days, such as `diwali`,
     `hallo` for Halloween or `c` for Christmas;
   - `dictionaries/segments.csv` names recipients, tones and themes, such as `hubbywife` for
     husband and wife or `bronsis` for brother and sister;
   - `dictionaries/dimensions.csv` says which terms are recipients, tones, languages or months.
     Every other term is an occasion.

   Code parts that no dictionary covers, mostly niche "national ... day" events, are split into
   words with a segmenter trained on the export's own card text. Those categories are marked
   `source=auto` in the output so someone can review them.

Outputs, both generated and not committed because they come from a private export:

- `output/category_intents.csv` has one row per live category code, with its card count, group,
  event name and occasion, recipient, tone, language, month and keyword terms.
- `output/intent_table.csv` has one row per intent term, with its dimension and category codes.

## How a query is scored

`intent_lookup.py` normalises the query first. It decodes `%xx`, folds accents, lowercases and
joins possessives, so `Mother’s Day` becomes `mothers day`. It then matches the longest known
phrases. Ordinals such as `50th` count as a milestone. Every category earns points for each
matched phrase:

- full-weight terms of a phrase add up, so `cumpleanos` counts as birthday plus spanish;
- fallback terms count only when no full-weight term matched that category;
- each term is weighted by its dimension, with occasion and recipient highest, and by how rare
  it is across categories, so `belated` outweighs `birthday`.

`IntentIndex.boost_codes(query)` returns the categories scoring at least 60% of the best one.

## How search should use it

1. **Boost, do not filter.** Keep the keyword query and multiply the score of cards whose
   category is in `boost_codes(query)`. Keyword matches on titles, names and dates still count.
2. **Rescue zero results.** When keyword search finds nothing, show popular cards from the
   boosted categories instead of site-wide popular cards. Fall back to site-wide only when no
   intent is found.
3. **Explain the match.** Optionally show "Showing Birthday: Husband and Wife" so the person can
   widen the search.

## Commands

```sh
# Build the tables from a card export
python3 intent/build_intent_table.py path/to/card_export.csv

# Look up one query
python3 intent/intent_lookup.py "sorry i forgot your birthday"

# Run the tests; they skip when the tables have not been built
python3 -m unittest discover -s intent -v
```

## Maintaining it

- Edit the CSV files in `dictionaries/`, rebuild, and run the tests.
- After a new export, review categories with `source=auto` and add names for any that matter to
  `events.csv` or `segments.csv`.
- Add a phrase to `query_phrases.csv` whenever a frequent search finds no intent. A phrase with
  no terms, such as `video`, is consumed and ignored.
- The export has no category titles, so labels come from the codes alone. When the category
  table is available, compare its titles with `category_intents.csv`.

## Known limits

- Some recipients have no category of their own, such as granddaughter, grandson, niece,
  nephew, cousin and aunt. They map to the nearest categories, such as kids or relatives.
- Format words such as `video` and `youtube` describe a card type, not a category, so the table
  ignores them. They belong to a format filter.
