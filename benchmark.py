"""
benchmark.py -- brute force Levenshtein vs SymSpell, same dictionary, same queries.

Question being answered: does dropping SymSpell change the ANSWERS,
or only the SPEED?
"""

import os
import time
import symspellpy
from symspellpy import SymSpell, Verbosity

from spell_correct import edit_distance, MAX_EDIT_DISTANCE

DICT_PATH = os.path.join(
    os.path.dirname(symspellpy.__file__), "frequency_dictionary_en_82_765.txt"
)

# ---------------------------------------------------------------------------
# Load one shared dictionary so the comparison is fair
# ---------------------------------------------------------------------------
freq = {}
with open(DICT_PATH, encoding="utf-8") as fh:
    for line in fh:
        parts = line.split()
        if len(parts) == 2:
            freq[parts[0]] = int(parts[1])

print(f"Shared dictionary: {len(freq):,} words\n")

sym = SymSpell(max_dictionary_edit_distance=MAX_EDIT_DISTANCE, prefix_length=7)
sym.load_dictionary(DICT_PATH, term_index=0, count_index=1)


def brute_force_correct(word, max_distance=MAX_EDIT_DISTANCE):
    if word in freq:
        return word
    best = None
    for candidate in freq:
        d = edit_distance(word, candidate, max_distance)
        if d <= max_distance:
            score = (d, -freq[candidate])
            if best is None or score < best[0]:
                best = (score, candidate)
    return best[1] if best else word


def symspell_correct(word):
    out = sym.lookup(word, Verbosity.CLOSEST, max_edit_distance=MAX_EDIT_DISTANCE)
    return out[0].term if out else word


TESTS = [
    "aniversary", "anniversry", "annivarsary", "anniverary",
    "valentin", "valantine", "vallentine", "valintine",
    "birthdya", "birhtday", "chrismas", "cristmas",
    "graduaton", "congradulations", "hallween", "thankgiving",
]

print("=" * 74)
print(f"{'query':<18}{'brute force':<20}{'symspell':<20}{'agree?'}")
print("=" * 74)

agree = 0
bf_total = ss_total = 0.0

for word in TESTS:
    t0 = time.perf_counter()
    bf = brute_force_correct(word)
    bf_total += time.perf_counter() - t0

    t0 = time.perf_counter()
    ss = symspell_correct(word)
    ss_total += time.perf_counter() - t0

    same = bf == ss
    agree += same
    print(f"{word:<18}{bf:<20}{ss:<20}{'yes' if same else 'NO'}")

print("=" * 74)
print(f"Agreement: {agree}/{len(TESTS)}")
print()
print(f"Brute force total: {bf_total*1000:9.1f} ms   ({bf_total/len(TESTS)*1000:.1f} ms per query)")
print(f"SymSpell total:    {ss_total*1000:9.1f} ms   ({ss_total/len(TESTS)*1000:.3f} ms per query)")
print(f"SymSpell is {bf_total/ss_total:,.0f}x faster")
print()

# ---------------------------------------------------------------------------
# How does brute force scale with dictionary size?
# ---------------------------------------------------------------------------
print("=" * 74)
print("BRUTE FORCE COST vs DICTIONARY SIZE  (one query)")
print("=" * 74)

all_words = list(freq.items())
for size in (500, 2_000, 10_000, 40_000, len(all_words)):
    subset = dict(all_words[:size])
    saved, globals()["freq"] = freq, subset
    t0 = time.perf_counter()
    brute_force_correct("aniversary")
    elapsed = (time.perf_counter() - t0) * 1000
    globals()["freq"] = saved
    verdict = "fine" if elapsed < 50 else ("noticeable" if elapsed < 200 else "TOO SLOW")
    print(f"  {size:>7,} words   {elapsed:7.1f} ms   {verdict}")
print("=" * 74)
