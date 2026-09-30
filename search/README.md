# Search

A working search over the card export: index it, rank a query against it, and measure the
result on real queries. [`../SEARCH_DESIGN.md`](../SEARCH_DESIGN.md) is the design and the
reasoning; this is how to run it.

## Commands

```sh
# Build the index from a card export. Writes search/output/index.json.gz.
python3 search/index.py card_24092026.csv

# Search it. --explain prints the parts of every score.
python3 search/rank.py "sorry i forgot your birthday"
python3 search/rank.py --explain "bday cards for my hubby"

# Measure it over real queries from the search report, one per line, counts optional.
python3 search/eval.py queries.txt
python3 search/eval.py queries.txt --worklist   # the queries still finding nothing

# Tests. They build their own small export, so they need no private data.
python3 -m unittest discover -s search -v
```

## The files

| File | What it does |
| --- | --- |
| `columns.py` | the column contract: what search needs from the export, and what each optional column buys |
| `index.py` | one pass over the export to one index: documents, BM25F postings, category facets, intent table |
| `rank.py` | query understanding, recall, the blended score, the page layout |
| `eval.py` | zero-result rate and intent coverage over real queries, against the all-words keyword baseline |
| `test_search.py` | 31 tests on a made-up 15-card export |

`output/` is generated from a private export and is not committed.

## What it does on a query

```
$ python3 search/rank.py --explain "birthdya wishes for sistre"
query: birthdya wishes for sistre
  did you mean: birthdya -> birthday, sistre -> sister
  read as: birthday = birthday, sister = sister+sibling
  categories: birth_bronsis 1.0
  candidates: 10, above the floor: 8, showing 3

  1. 0.859  Birthday Wishes Brother And Sister    birth_bronsis    Birthday
       lexical=1.0 intent=1.0 quality=0.7803 on birthday,for,sister,wishe
  2. 0.798  Happy Birthday Sister                 birth_bronsis    Birthday
       lexical=0.721 intent=1.0 quality=0.9695 fresh=0.8 on birthday,for,sister
```

Two typos fixed against the words the catalogue really uses, the intent read from the
corrected query, the category it points at, and a score whose every part is shown.

## Tuning it

The weights are constants at the top of `rank.py`, with a comment on what each one is for:

```python
W_LEX = 0.40       # the query's words
W_INTENT = 0.35    # the categories the query asks for
W_QUALITY = 0.14   # how well the card does with people
W_SEASON = 0.08    # how near the card's occasion is
W_FRESH = 0.03     # how recently the card was added
```

Change one, run `eval.py` over the same query file, and compare. Do not tune by taste: the
zero-result rate and the worklist are the signal, and click logs will let these be fitted
rather than chosen (phase 2 of the design).

## Limits worth knowing

- The index is held in memory in one process. That is deliberate: it makes the algorithm
  easy to read, test and tune, and the design maps directly onto OpenSearch when it ships.
- Seasonality is month-granular, because the category codes only name a month.
- Card quality comes from the export's popularity column. If the export has none, every
  card starts equal and text and intent decide everything. Click logs are the real fix.
- There is no semantic or vector recall yet; it is phase 3 in the design, and the intent
  table covers the frequent head of that traffic in the meantime.
