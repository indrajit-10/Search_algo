# Demo

A stand-in catalogue and a browser console for showing the search working, so nobody needs
the private card export to see it or to try a query.

**The catalogue is synthetic.** `make_export.py` builds it from this repository's own
dictionaries, so every category code is real and parses exactly as a real one does, but card
titles come from templates and the month-by-event combinations are random: a category labelled
`Events in April / Diwali` is an artefact, not a real card. Judge the mechanism, not the card
names. What it does model faithfully is the hard case: about 40% of cards get an oblique title
that never names their occasion, tags are sparse and inconsistent, and popularity is
long-tailed. Those are the cards keyword search cannot find.

## Run it

```sh
python3 demo/make_export.py /tmp/cards.csv              # 14,000 rows, 12,510 live, 1,200 codes
python3 search/index.py /tmp/cards.csv -o /tmp/ix.json.gz
python3 search/rank.py --index /tmp/ix.json.gz --explain "sorry i forgot your birthday"
python3 search/eval.py queries.txt --index /tmp/ix.json.gz
```

## The browser console

`console/index.html` runs the whole search client-side: type a query and it shows what the
query was understood to mean, which categories were boosted, the ranked cards with every
score broken into its parts, and what all-words keyword search would have returned instead.
The month is selectable, so the seasonal rule can be watched changing, and the five score
weights are sliders, so they can be tuned against real queries.

```sh
python3 demo/pack_for_web.py /tmp/ix.json.gz demo/console/search-data.json
python3 -m http.server -d demo/console 8000     # then open localhost:8000
```

`search-data.json` is generated and not committed.

## Keeping the port honest

The console's JavaScript is a port of `search/rank.py`, and a port that quietly disagrees with
the real ranker is worse than no demo. `verify_console.mjs` runs the console's engine in node
and `verify_console.py` runs the Python ranker, both printing the same structure — the
corrected query, the phrases matched, the categories boosted with their scores, the candidate
and floor counts, and the top cards with their scores — so the two can be diffed:

```sh
echo '["sorry i forgot your birthday","bday cards for my hubby","cards for the family"]' > /tmp/q.json
node demo/verify_console.mjs demo/console/index.html demo/console/search-data.json /tmp/q.json 9 > /tmp/js.json
python3 demo/verify_console.py /tmp/ix.json.gz /tmp/q.json 9 > /tmp/py.json
diff <(python3 -m json.tool /tmp/js.json) <(python3 -m json.tool /tmp/py.json) && echo identical
```

The last argument is the month, 1 to 12, for the seasonal boost. Run it for several months:
season changes which categories are recalled, so it is the part most likely to drift.

Three things had to match exactly before the two agreed, and they are worth knowing if the
port is ever changed. `IntentIndex.lookup` rounds its scores to two decimals before they are
normalised, `rank.py` rounds each final score to four, and Python rounds halves to even — all
three decide the order of close results, so the JavaScript reproduces them rather than
rounding once at the end.
