# Search_algo

Work on the 123Greetings card search algorithm.

- [`SEARCH_DESIGN.md`](SEARCH_DESIGN.md) is the design: why card search is not document
  search, the pipeline that fixes it, what each column of the card export buys, and what to
  build next, in order of payoff.
- [`search/`](search/README.md) is a working implementation of that design. It indexes a card
  export, ranks a query on text, intent, quality and season together, and measures itself on
  real queries against the all-words keyword baseline.
- [`demo/`](demo/README.md) builds a synthetic stand-in catalogue and a browser console, so the
  search can be run and shown without the private card export.
- [`intent/`](intent/README.md) builds the intent table, which maps what people type, such as
  "sorry i forgot your birthday", to the card categories they want, such as belated birthday.
  It includes the builder, the lookup used at search time, the hand-edited dictionaries and tests.

Everything reads only the columns the card export already has: `card_title`,
`card_description`, `card_tags`, `q1_value`, `status_id` and `invalid_card`, plus an id,
popularity, format or date column when the export carries one.

```sh
python3 search/index.py card_export.csv                # build the index
python3 search/rank.py --explain "bday for my hubby"   # search it
python3 search/eval.py queries.txt                     # measure it
python3 -m unittest discover -s search -v              # tests, no export needed
```

Card exports and search reports are private and are not committed. See `.gitignore`.
