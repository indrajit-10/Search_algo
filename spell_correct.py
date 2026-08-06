"""
spell_correct.py  --  spell correction for the card search box.

Runs in PyCharm with no setup. Pure Python 3 standard library.

THE POINT OF THIS FILE
----------------------
The current system fixes misspellings by listing them one by one in a
synonym table. That table has 11 spellings of "raksha" and 3 of "halloween",
but ZERO for "anniversary" and ZERO for "valentine". So "aniversary" fails.

You cannot list your way out of this. "anniversary" has 595 misspellings
within 1 edit. This file builds the dictionary AUTOMATICALLY from the card
catalogue, then computes the distance instead of looking it up.
"""

# ---------------------------------------------------------------------------
# 1. THE CATALOGUE
#    Replaced at runtime by load_real_data.py with real scraped titles.
# ---------------------------------------------------------------------------

CATALOGUE = [
    "Happy Anniversary My Love",
    "Anniversary Wishes For Wife",
    "Silver Anniversary Celebration",
    "25th Anniversary Greetings",
    "Wedding Anniversary For Parents",
    "Happy Valentine's Day",
    "Valentine Wishes For Him",
    "Be My Valentine Forever",
    "Valentine Day Roses",
    "Secret Valentine Admirer",
    "Happy Birthday To You",
    "Belated Birthday Wishes",
    "Merry Christmas And Joy",
    "Christmas Tree Sparkle",
    "Happy Diwali Lights",
    "Raksha Bandhan Brother",
    "Thanksgiving Family Dinner",
    "Get Well Soon Friend",
    "Congratulations On Graduation",
    "Mother's Day Flowers",
    "Father's Day Fishing",
    "Halloween Pumpkin Night",
    "New Year Fireworks",
    "Friendship Day Forever",
    "Easter Bunny Eggs",
]

# ---------------------------------------------------------------------------
# 2. STOP WORDS
#    This is the actual list, copied from Algo_sphinx_search on the wiki.
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "and", "animated", "any", "e", "es", "anyone", "as", "at",
    "beautiful", "but", "by", "card", "cards", "day", "de", "e-card",
    "e-cards", "e-greetings", "e-wish", "ecard", "ecards", "egreetings",
    "everybody", "everyone", "flash", "for", "free", "from", "gandhian",
    "greeting", "greetings", "happy", "image", "images", "in", "into",
    "like", "nor", "of", "off", "on", "onto", "or", "per", "popular",
    "post", "postcard", "postcards", "printable", "so", "some", "somebody",
    "someone", "the", "this", "to", "top", "up", "via", "wishes", "wishing",
    "with", "yet", "your", "yours", "x",
}

MAX_EDIT_DISTANCE = 2   # same setting SymSpell uses; covers almost all real typos
MIN_WORD_LENGTH   = 3   # do not try to correct 1-2 letter fragments


# ---------------------------------------------------------------------------
# 3. BUILD THE DICTIONARY  --  automatically, from the catalogue
# ---------------------------------------------------------------------------

def build_dictionary(catalogue):
    """
    Returns {word: frequency}. Frequency matters: when two candidates tie on
    edit distance, the word that appears in more cards wins.
    """
    freq = {}
    for title in catalogue:
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in title)
        for word in cleaned.split():
            if len(word) < MIN_WORD_LENGTH:
                continue
            if word in STOP_WORDS:
                continue
            freq[word] = freq.get(word, 0) + 1
    return freq


# ---------------------------------------------------------------------------
# 4. EDIT DISTANCE  --  bounded Levenshtein
#    Bounded means: stop as soon as we know the distance exceeds max_distance.
# ---------------------------------------------------------------------------

def edit_distance(a, b, max_distance):
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + cost  # substitution
            ))
        if min(current) > max_distance:   # early exit
            return max_distance + 1
        previous = current
    return previous[-1]


# ---------------------------------------------------------------------------
# 5. CORRECT A SINGLE WORD
# ---------------------------------------------------------------------------

def correct_word(word, dictionary, max_distance=MAX_EDIT_DISTANCE):
    """
    Returns (corrected_word, distance, alternatives).
    distance 0 = already correct. distance -1 = no correction found.
    """
    word = word.lower()

    if word in dictionary:
        return word, 0, []

    if len(word) < MIN_WORD_LENGTH:
        return word, -1, []

    candidates = []
    for candidate in dictionary:
        d = edit_distance(word, candidate, max_distance)
        if d <= max_distance:
            candidates.append((d, -dictionary[candidate], candidate))

    if not candidates:
        return word, -1, []

    candidates.sort()                # by distance, then by frequency desc
    best = candidates[0]
    alternatives = [c[2] for c in candidates[1:4]]
    return best[2], best[0], alternatives


def correct_query(query, dictionary):
    """Correct every token in a multi-word query."""
    out_words, changed = [], False
    for token in query.lower().split():
        if token in STOP_WORDS:
            out_words.append(token)
            continue
        fixed, distance, _ = correct_word(token, dictionary)
        out_words.append(fixed)
        if distance > 0:
            changed = True
    return " ".join(out_words), changed


# ---------------------------------------------------------------------------
# 6. DICTIONARY COVERAGE AUDIT
# ---------------------------------------------------------------------------

def coverage_audit(dictionary, test_words):
    print("=" * 72)
    print("DICTIONARY COVERAGE AUDIT")
    print("=" * 72)
    print(f"Dictionary built from {len(CATALOGUE)} card titles")
    print(f"Contains {len(dictionary)} unique searchable words")
    print(f"Stop words excluded: {len(STOP_WORDS)}")
    print()
    print("Top words by frequency:")
    for word, count in sorted(dictionary.items(), key=lambda x: -x[1])[:8]:
        print(f"    {word:<20} appears in {count} cards")
    print()

    missing = [w for w in test_words if w not in dictionary]
    print(f"Checked {len(test_words)} expected keywords.")
    if missing:
        print(f"  NOT IN DICTIONARY ({len(missing)}): {', '.join(missing)}")
        print("  -> these keywords have no cards, or the cards are titled differently")
    else:
        print("  All present.")
    print()


# ---------------------------------------------------------------------------
# 7. DEMONSTRATIONS
# ---------------------------------------------------------------------------

MISSPELLINGS = [
    "aniversary", "anniversry", "annivarsary", "anniverary", "aniverssary",
    "valentin", "valantine", "vallentine", "valentime", "valintine",
    "birthdya", "birhtday", "chrismas", "cristmas", "halloween",
    "diwlai", "graduaton", "congradulations",
]

MULTI_WORD = [
    "aniversary card for wife",
    "valentin day roses",
    "happy birthdya",
    "silver aniversary",
    "valantine for him",
]


def demo_single_words(dictionary):
    print("=" * 72)
    print("SINGLE WORD CORRECTION")
    print("=" * 72)
    fixed = unfixed = 0
    for word in MISSPELLINGS:
        result, distance, alts = correct_word(word, dictionary)
        if distance == 0:
            print(f"  {word:<18} already correct")
            fixed += 1
        elif distance > 0:
            extra = f"   (also considered: {', '.join(alts)})" if alts else ""
            print(f"  {word:<18} -> {result:<18} {distance} edit(s){extra}")
            fixed += 1
        else:
            print(f"  {word:<18} -> NO MATCH  (nothing within {MAX_EDIT_DISTANCE} edits)")
            unfixed += 1
    print()
    print(f"  {fixed} corrected, {unfixed} not correctable")
    print()


def demo_multi_word(dictionary):
    print("=" * 72)
    print("MULTI WORD QUERY CORRECTION")
    print("=" * 72)
    for query in MULTI_WORD:
        corrected, changed = correct_query(query, dictionary)
        flag = "CORRECTED" if changed else "unchanged"
        print(f"  {query!r}")
        print(f"      -> {corrected!r}   [{flag}]")
    print()


def demo_why_lists_fail(dictionary):
    """
    Counts how many strings within 2 edits of 'anniversary' a hand-maintained
    table would need to cover. This is the argument for computing over listing.
    """
    print("=" * 72)
    print("WHY A HAND-MAINTAINED TYPO LIST CANNOT WORK")
    print("=" * 72)
    target = "anniversary"
    letters = "abcdefghijklmnopqrstuvwxyz"

    one_edit = set()
    splits = [(target[:i], target[i:]) for i in range(len(target) + 1)]
    for left, right in splits:
        if right:
            one_edit.add(left + right[1:])                        # deletion
        if len(right) > 1:
            one_edit.add(left + right[1] + right[0] + right[2:])  # transposition
        for c in letters:
            if right:
                one_edit.add(left + c + right[1:])                # substitution
            one_edit.add(left + c + right)                        # insertion
    one_edit.discard(target)

    two_edit = set()
    for variant in list(one_edit)[:400]:      # sample; full set is very large
        for i in range(len(variant) + 1):
            for c in letters:
                two_edit.add(variant[:i] + c + variant[i:])

    print(f"  Misspellings of '{target}' within 1 edit:  {len(one_edit):,}")
    print(f"  Within 2 edits (sampled, actual is far higher): {len(two_edit):,}+")
    print()
    print(f"  Entries currently in the synonym table for anniversary: 0")
    print(f"  Entries currently in the synonym table for valentine:   2  (v'day, vday)")
    print(f"  Entries currently in the synonym table for raksha:      11")
    print()
    print("  The table is not wrong, it is unmaintainable. Edit distance covers")
    print("  every one of those variants with no list to maintain.")
    print()


if __name__ == "__main__":
    dictionary = build_dictionary(CATALOGUE)

    coverage_audit(dictionary, [
        "anniversary", "valentine", "birthday", "christmas",
        "diwali", "halloween", "graduation", "hanukkah", "easter",
    ])
    demo_single_words(dictionary)
    demo_multi_word(dictionary)
    demo_why_lists_fail(dictionary)

    print("=" * 72)
    print("TRY YOUR OWN  (blank line or 'quit' to stop)")
    print("=" * 72)
    while True:
        try:
            q = input("misspell> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("quit", "exit"):
            break
        corrected, changed = correct_query(q, dictionary)
        print(f"   -> {corrected}   [{'corrected' if changed else 'unchanged'}]\n")
