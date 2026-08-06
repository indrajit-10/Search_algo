"""
search_engine.py  --  the replacement for Sphinx.

Pure Python 3 standard library. No pip installs, no external service.

    python search_engine.py card_database.csv              # tests, then a prompt
    python search_engine.py card_database.csv --compare    # old vs new, side by side

WHY THIS IS NOT AN ENGINE MIGRATION
-----------------------------------
The live catalogue is 13,042 cards (status_id=1). Measured: the whole inverted
index is 8,649 terms, builds in under 100 ms, occupies about 2 MB, and answers a
two-term AND in under 2 microseconds. Nothing at that size needs a search server.
What Sphinx was actually providing - typo tolerance, field weighting, ranking -
is the code below.

WHAT WAS WRONG WITH THE OLD ONE, AND WHERE IT IS FIXED
-----------------------------------------------------
  1. Stop words deleted query terms outright, so "flash card" lost both words and
     returned zero.                                  -> IDF, see score(). No hard list.
  2. Strict AND across all terms, so one bad term killed the whole query.
     -> the relaxation ladder in search().
  3. "funny" matched "fun-filled" prose in 1,105 non-humour cards against only 150
     genuinely tagged ones.                          -> facets, built from tags and
                                                        title ONLY. See build_facets().
  4. No spell correction ever existed, so "birthdya" returned 0 of 819 birthday
     cards.                                          -> SymSpell deletion index, correct().
  5. Candidates were capped at 500 in insertion order, so the ranker saw the oldest
     slice.                                          -> we rank everything, then cut.
  6. %92 curly apostrophes in stored text broke matching and the b'day synonym.
     -> decode_entities() in normalise().
"""

import collections
import csv
import math
import re
import sys
import time
import unicodedata

csv.field_size_limit(10 ** 9)

LIVE_STATUS = "1"          # status_id=1 is served; every other status is dead
MAX_EDIT_DISTANCE = 2
MIN_CORRECTABLE = 4        # never "correct" a 3-letter word, too many neighbours
MAX_QUERY_CHARS = 120
MAX_TOKENS = 10

# A term must appear on at least this many cards to be treated as correctly
# spelled. The catalogue's own tags are full of typos - "aniversary", "chrismas"
# and "birthdy" are all really in there, each on one or two cards, put there as
# SEO keyword bait. Without this floor the corrector treats those typos as valid
# words, so "aniversary" stays "aniversary" and finds the 1 spam card instead of
# the 269 real ones. The dictionary must be built from words the catalogue uses,
# not words it merely contains.
MIN_DICTIONARY_FREQUENCY = 3

# Field weights. Derived from measurement, not taste:
#   tags carry unique recall - 36% of "funny" matches and 39% of "friend" matches
#     come from tags alone, so dropping them loses real results.
#   title is precise when it hits but only 31-67% of titles name their own occasion.
#   description has the widest reach and the worst precision. It is the recall net,
#     never the reason something ranks first.
FIELD_WEIGHTS = {
    "title":       3.0,
    "tags":        2.5,
    "category":    2.0,
    "description": 1.0,
    "url":         0.4,
}

TF_SATURATION = 1.2        # BM25-style: the 4th occurrence adds little over the 3rd
FACET_BOOST = 12.0         # a facet hit outweighs any amount of prose matching
PREFIX_DISCOUNT = 0.4      # "valentin" matching "valentines" is weaker evidence
PREFIX_DOMINANCE = 5       # a completion this much more common means a partial word
MAX_RESULTS = 20

# ---------------------------------------------------------------------------
# FACET LEXICONS
#
# These are the four slots a greeting-card query decomposes into. Matching a
# facet is a different operation from matching text, which is the entire fix for
# the "funny" complaint: a card is humour because it is TAGGED humour, not
# because its blurb contains the word "fun".
# ---------------------------------------------------------------------------

TONE = {
    "humour":     {"funny", "humor", "humour", "hilarious", "joke", "jokes", "lol",
                   "comedy", "comical", "silly", "laugh", "laughs", "witty", "meme",
                   "memes", "hysterical", "prank", "goofy"},
    "romantic":   {"romantic", "romance", "passion", "passionate", "intimate",
                   "sweetheart", "darling", "flirty", "sensual"},
    "heartfelt":  {"heartfelt", "sincere", "warm", "touching", "emotional",
                   "meaningful", "sentimental", "thoughtful"},
    "cute":       {"cute", "adorable", "sweet", "lovely", "charming"},
    "formal":     {"formal", "professional", "respectful", "official", "corporate"},
    "apology":    {"sorry", "apology", "apologise", "apologize", "forgive",
                   "regret", "apologies"},
    "sympathy":   {"sympathy", "condolence", "condolences", "grief", "loss",
                   "bereavement", "mourning"},
}

RECIPIENT = {
    "wife": {"wife", "wifey"}, "husband": {"husband", "hubby"},
    "mother": {"mother", "mom", "mum", "mommy", "mummy", "mama", "ma"},
    "father": {"father", "dad", "daddy", "papa", "pop"},
    "sister": {"sister", "sis"}, "brother": {"brother", "bro"},
    "daughter": {"daughter"}, "son": {"son"},
    "friend": {"friend", "friends", "buddy", "pal", "bestie"},
    "girlfriend": {"girlfriend", "gf"}, "boyfriend": {"boyfriend", "bf"},
    "boss": {"boss", "manager", "colleague", "coworker"},
    "teacher": {"teacher", "professor", "mentor"},
    "grandmother": {"grandmother", "grandma", "granny", "nana"},
    "grandfather": {"grandfather", "grandpa", "grandad"},
    "couple": {"couple", "newlyweds"},
}

# Format lives in card_label_type and card_music_extn. The old stop-word list
# deleted "animated" and "flash" from queries, throwing away information the
# database already had.
FORMAT = {
    "animated": {"animated", "animation", "moving", "gif"},
    "flash":    {"flash", "swf"},
    "musical":  {"musical", "music", "song", "singing", "sound", "audio"},
}

# Month-coded q1_value prefixes. Everything else is derived from the data by
# derive_occasion_lexicon(); these cannot be, because "edec" shares no letters
# with "christmas".
MONTH_PREFIX_HINTS = {
    "ejan": {"new year", "january", "chinese new year", "republic day"},
    "efeb": {"valentine", "valentines", "february", "rose day", "hug day"},
    "emar": {"holi", "spring", "march", "st patrick", "patrick", "easter"},
    "eapr": {"easter", "april", "earth day", "baisakhi"},
    "emay": {"mother", "mothers day", "may", "memorial day", "nurses"},
    "ejun": {"father", "fathers day", "june", "summer"},
    "ejul": {"july", "independence day", "friendship"},
    "eaug": {"friendship", "august", "raksha bandhan", "rakhi"},
    "esep": {"september", "autumn", "labor day", "grandparents"},
    "eoct": {"halloween", "diwali", "october", "dussehra", "boss day"},
    "enov": {"thanksgiving", "november", "veterans"},
    "edec": {"christmas", "xmas", "december", "new year eve", "hanukkah"},
}

# Every single word that names a facet. These are protected from spell
# correction: they are correct by definition, whatever the card prose contains.
FACET_VOCABULARY = {
    word
    for lexicon in (TONE, RECIPIENT, FORMAT)
    for members in lexicon.values()
    for word in members
    if " " not in word
}

# Query-side aliases only. The catalogue side is handled by spell correction and
# by the derived occasion lexicon, so this table stays small on purpose - the
# old system's failure was trying to enumerate every spelling by hand.
ALIASES = {
    "bday": "birthday", "bdays": "birthday", "b'day": "birthday",
    "xmas": "christmas", "x-mas": "christmas", "chistmas": "christmas",
    "congrats": "congratulations", "anniv": "anniversary",
    "thx": "thanks", "ty": "thanks", "pls": "please",
    "vday": "valentine", "v-day": "valentine",
    "cumpleanos": "birthday", "feliz": "happy",
}


# ---------------------------------------------------------------------------
# NORMALISATION
# ---------------------------------------------------------------------------

_ENTITY_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_NUMERIC_ENTITY_RE = re.compile(r"&#(\d+);")

# Windows-1252 bytes that were URL-encoded into the text. %92 alone appears
# 20,164 times across the export - it is a curly apostrophe, which is why
# "Mother%92s Day" never matched "mothers day" and why the b'day alias was dead.
_CP1252 = {
    0x91: "'", 0x92: "'", 0x93: '"', 0x94: '"', 0x96: "-", 0x97: "-",
    0x85: "...", 0xA0: " ", 0xAE: " ", 0x99: " ",
}


def decode_entities(text):
    def replace_hex(match):
        code = int(match.group(1), 16)
        if code in _CP1252:
            return _CP1252[code]
        if code < 0x80:
            return chr(code)
        try:
            return bytes([code]).decode("cp1252")
        except (UnicodeDecodeError, ValueError):
            return " "
    text = _ENTITY_RE.sub(replace_hex, text)
    text = _NUMERIC_ENTITY_RE.sub(
        lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x11000 else " ", text)
    for entity, char in (("&amp;", "&"), ("&quot;", '"'), ("&apos;", "'"),
                         ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">")):
        text = text.replace(entity, char)
    return text


def normalise(text):
    text = decode_entities(text or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenise(text):
    return normalise(text).split()


# ---------------------------------------------------------------------------
# SPELL CORRECTION  --  SymSpell deletion index
#
# spell_correct.py proved the principle (compute the distance, do not list the
# typos) and benchmark.py proved brute force gives the same answers as SymSpell,
# only slower. This is the fast version: precompute deletes of every dictionary
# term once, then a lookup is a set intersection instead of a scan.
# ---------------------------------------------------------------------------

def deletes(word, max_distance):
    """Every string reachable by deleting up to max_distance characters."""
    out = {word}
    queue = {word}
    for _ in range(max_distance):
        nxt = set()
        for item in queue:
            for i in range(len(item)):
                candidate = item[:i] + item[i + 1:]
                if candidate and candidate not in out:
                    out.add(candidate)
                    nxt.add(candidate)
        queue = nxt
    return out


def edit_distance(a, b, max_distance):
    """
    Bounded Damerau-Levenshtein. Returns max_distance+1 once certain to exceed.

    The transposition case matters more than it looks. "birthdya" -> "birthday"
    is a single swapped pair, which plain Levenshtein scores as 2 edits - far
    enough that the junk term "birthdy" (one deletion away) wins instead.
    Adjacent transposition is one of the most common real typos, so it gets to
    cost 1.
    """
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    previous_previous = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = min(previous[j] + 1,
                       current[j - 1] + 1,
                       previous[j - 1] + (0 if ca == cb else 1))
            if (previous_previous is not None and i > 1 and j > 1
                    and ca == b[j - 2] and a[i - 2] == cb):
                cost = min(cost, previous_previous[j - 2] + 1)
            current.append(cost)
        if min(current) > max_distance:
            return max_distance + 1
        previous_previous, previous = previous, current
    return previous[-1]


class SpellCorrector:
    def __init__(self, vocabulary, max_distance=MAX_EDIT_DISTANCE,
                 min_frequency=MIN_DICTIONARY_FREQUENCY):
        # Only well-attested terms are allowed to be "correct" or to be a
        # correction target. See MIN_DICTIONARY_FREQUENCY.
        self.vocabulary = {t: f for t, f in vocabulary.items() if f >= min_frequency}
        self.max_distance = max_distance
        self.index = collections.defaultdict(list)
        for term in self.vocabulary:
            if len(term) < MIN_CORRECTABLE:
                continue
            for variant in deletes(term, max_distance):
                self.index[variant].append(term)

    def correct(self, word):
        """Returns (word, distance). distance 0 means it was already a real term."""
        if word in self.vocabulary:
            return word, 0
        if len(word) < MIN_CORRECTABLE:
            return word, -1

        seen = set()
        for variant in deletes(word, self.max_distance):
            seen.update(self.index.get(variant, ()))
        if not seen:
            return word, -1

        best = None
        for candidate in seen:
            distance = edit_distance(word, candidate, self.max_distance)
            if distance > self.max_distance:
                continue
            # closest first, then the term that appears on more cards
            rank = (distance, -self.vocabulary[candidate], candidate)
            if best is None or rank < best:
                best = rank
        if best is None:
            return word, -1
        return best[2], best[0]


# ---------------------------------------------------------------------------
# INDEX
# ---------------------------------------------------------------------------

def derive_occasion_lexicon(rows):
    """
    Build q1_value-prefix -> {human search terms} from the catalogue itself.

    A slug like "wed_arndworld" shares no letters with "around the world", so a
    user typing the occasion cannot reach it. Rather than hand-maintain that
    table, take the words that are distinctively common in each prefix's own
    titles and tags. Month prefixes get a hint table because "edec" cannot be
    derived from anything.
    """
    per_prefix = collections.defaultdict(collections.Counter)
    global_counts = collections.Counter()
    for row in rows:
        prefix = row["q1_value"].split("_")[0]
        words = set(tokenise(row["card_title"]) + tokenise(row.get("card_tags") or ""))
        per_prefix[prefix].update(words)
        global_counts.update(words)

    total = sum(len(v) for v in per_prefix.values()) or 1
    lexicon = {}
    for prefix, counts in per_prefix.items():
        size = sum(counts.values()) or 1
        scored = []
        for word, count in counts.items():
            if len(word) < 4 or count < 3:
                continue
            # distinctive = common here, uncommon overall
            lift = (count / size) / max(global_counts[word] / total, 1e-9)
            if lift > 1.5:
                scored.append((lift * math.log(1 + count), word))
        scored.sort(reverse=True)
        terms = {w for _, w in scored[:12]}
        terms.update(MONTH_PREFIX_HINTS.get(prefix, set()))
        # the prefix itself is often a real word: birth, love, wed, thank
        if len(prefix) >= 3 and not prefix.startswith("e"):
            terms.add(prefix)
        lexicon[prefix] = terms
    return lexicon


class Card:
    __slots__ = ("doc", "number", "title", "description", "url", "category",
                 "year", "facets", "label")

    def __init__(self, doc, row):
        self.doc = doc
        self.number = row["card_number"]
        self.title = decode_entities(row["card_title"])
        self.description = decode_entities(row["card_description"])
        self.url = row["card_page_url"]
        self.category = row["q1_value"]
        self.label = row["card_label_type"]
        try:
            self.year = int(row["card_created_date"][:4])
        except (ValueError, TypeError):
            self.year = 0
        self.facets = {}


class SearchIndex:
    """
    postings[term][doc] = accumulated field-weighted term frequency.
    Everything is normalised once, at build time. The old scorer re-normalised
    every card on every token of every query; that is what stopped it scaling.
    """

    def __init__(self, rows, live_only=True):
        if live_only:
            rows = [r for r in rows
                    if r["status_id"] == LIVE_STATUS and r["invalid_card"] == "0"]
        self.occasions = derive_occasion_lexicon(rows)
        self.cards = []
        self.postings = collections.defaultdict(dict)
        self.document_frequency = collections.Counter()
        self.facet_docs = collections.defaultdict(set)   # (kind, value) -> docs

        for doc, row in enumerate(rows):
            card = Card(doc, row)
            fields = {
                "title":       tokenise(row["card_title"]),
                "description": tokenise(row["card_description"]),
                "tags":        tokenise((row.get("card_tags") or "").replace(",", " ")),
                "category":    self._category_tokens(row),
                "url":         self._url_tokens(row),
            }
            self._index_fields(doc, fields)
            self._build_facets(card, row, fields)
            self.cards.append(card)

        self.total = len(self.cards)
        self.idf = {
            term: math.log(1 + (self.total - freq + 0.5) / (freq + 0.5))
            for term, freq in self.document_frequency.items()
        }
        self.corrector = SpellCorrector(self.document_frequency)
        self.sorted_terms = sorted(self.postings)

        # Reverse the derived occasion lexicon so a query word can name an
        # occasion. A word that maps to many occasions ("love", "wishes") is
        # not evidence of any one of them, so only keep the discriminating ones.
        reverse = collections.defaultdict(set)
        for prefix, words in self.occasions.items():
            for phrase in words:
                for word in phrase.split():
                    if len(word) >= 4:
                        reverse[word].add(prefix)
        self.occasion_of = {w: p for w, p in reverse.items() if len(p) <= 2}

    def prefix_terms(self, prefix, limit=24):
        """
        Terms beginning with `prefix`, most common first.

        People type partial words - "valentin", "anniv", "bday" mid-keystroke -
        and a partial word is not a typo, so the spell corrector will not and
        should not touch it. "valentin" is a real token here anyway: it is the
        Spanish "San Valentin". Prefix expansion is what connects it to
        "valentine" and "valentines" without corrupting either.
        """
        if len(prefix) < 3:
            return []
        import bisect
        start = bisect.bisect_left(self.sorted_terms, prefix)
        found = []
        for term in self.sorted_terms[start:start + 400]:
            if not term.startswith(prefix):
                break
            found.append(term)
        found.sort(key=lambda t: -self.document_frequency[t])
        return found[:limit]

    @staticmethod
    def _url_tokens(row):
        """
        URL paths are sequence-numbered: valentine64.html, valentinstag1.html.
        Keeping the digits injects thousands of junk terms that appear on one
        card each, which then pollute both the vocabulary and the spell
        corrector. Strip the trailing counter.
        """
        raw = row["card_page_url"].replace(".html", "").replace("_", " ").replace("-", " ")
        return [re.sub(r"\d+$", "", w) or w for w in tokenise(raw)]

    def _category_tokens(self, row):
        prefix = row["q1_value"].split("_")[0]
        words = tokenise(row["q1_value"].replace("_", " "))
        words += sorted(self.occasions.get(prefix, ()))
        return [w for chunk in words for w in chunk.split()]

    def _index_fields(self, doc, fields):
        for field, words in fields.items():
            if not words:
                continue
            weight = FIELD_WEIGHTS[field]
            counts = collections.Counter(words)
            for term, tf in counts.items():
                # saturating tf: the 4th hit barely beats the 3rd
                contribution = weight * (tf * (TF_SATURATION + 1)) / (tf + TF_SATURATION)
                bucket = self.postings[term]
                bucket[doc] = bucket.get(doc, 0.0) + contribution
        for term in {w for words in fields.values() for w in words}:
            self.document_frequency[term] += 1

    def _build_facets(self, card, row, fields):
        """
        THE FIX FOR "funny".

        Facets come from tags and title ONLY - never from the description. The
        1,105 cards whose blurb says "fun ecard" are not humour cards, and the
        150 that are tagged funny should never lose to them. Description still
        contributes to the text score, just not to what a card IS.
        """
        evidence = set(fields["tags"]) | set(fields["title"])

        for tone, words in TONE.items():
            if evidence & words:
                card.facets.setdefault("tone", set()).add(tone)
                self.facet_docs[("tone", tone)].add(card.doc)

        for person, words in RECIPIENT.items():
            if evidence & words:
                card.facets.setdefault("recipient", set()).add(person)
                self.facet_docs[("recipient", person)].add(card.doc)

        # Format is structural - it comes from columns, not from prose.
        formats = set()
        if row["card_label_type"] == "A":
            formats.add("animated")
        if row["card_label_type"] == "F":
            formats.add("flash")
        if (row.get("card_music_extn") or "").strip():
            formats.add("musical")
        for value in formats:
            card.facets.setdefault("format", set()).add(value)
            self.facet_docs[("format", value)].add(card.doc)

        prefix = row["q1_value"].split("_")[0]
        card.facets["occasion"] = {prefix}
        self.facet_docs[("occasion", prefix)].add(card.doc)


# ---------------------------------------------------------------------------
# QUERY UNDERSTANDING
# ---------------------------------------------------------------------------

class Query:
    def __init__(self, raw):
        self.raw = raw
        self.terms = []          # text terms, after correction
        self.groups = {}         # term -> {term} | its prefix completions
        self.corrections = {}    # original -> corrected
        self.facets = []         # (kind, value, source_term)
        self.notes = []


def understand(raw, index):
    """Normalise, alias, spell-correct, then pull out any facet the user named."""
    query = Query(raw)
    raw = (raw or "")[:MAX_QUERY_CHARS]

    words = [ALIASES.get(w, w) for w in tokenise(raw)][:MAX_TOKENS]
    if not words:
        return query

    # Multi-word facet values ("thank you", "get well") before single words.
    joined = " ".join(words)
    for kind, lexicon in (("tone", TONE), ("recipient", RECIPIENT), ("format", FORMAT)):
        for value, phrases in lexicon.items():
            for phrase in phrases:
                if " " in phrase and phrase in joined:
                    query.facets.append((kind, value, phrase))

    for word in words:
        # Never "correct" a word that names a facet. "flash" is rare in card
        # prose, so the corrector happily rewrote it to "last" - but flash is a
        # card FORMAT, and the database knows it via card_label_type. A word
        # that means something structurally is already spelled correctly.
        if word not in FACET_VOCABULARY:
            corrected, distance = index.corrector.correct(word)
            if distance > 0:
                query.corrections[word] = corrected
                word = corrected
        query.terms.append(word)

        # A known word still gets prefix expansion, because a real word can also
        # be the start of a longer one the user meant.
        group = {word}
        group.update(index.prefix_terms(word))
        query.groups[word] = group

        for kind, lexicon in (("tone", TONE), ("recipient", RECIPIENT),
                              ("format", FORMAT)):
            for value, members in lexicon.items():
                if word in members:
                    query.facets.append((kind, value, word))

        # The occasion slot. "birthday card for mom" must mean occasion=birth
        # AND recipient=mother; without this the recipient facet alone wins and
        # Mother's Day cards outrank birthday-for-mother cards.
        for prefix in index.occasion_of.get(word, ()):
            query.facets.append(("occasion", prefix, word))

    # de-duplicate facets, keep order
    seen = set()
    unique = []
    for facet in query.facets:
        key = facet[:2]
        if key not in seen:
            seen.add(key)
            unique.append(facet)
    query.facets = unique
    return query


# ---------------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------------

def score(index, terms, facets, required):
    """
    Score every document that matches.

    `required` is a list of term GROUPS. A document must match at least one term
    from every group. A group is usually a single word, but a partial word
    expands to all its completions, so "valentin" is satisfied by "valentine" or
    "valentines" without either being treated as a correction.

    No stop-word list is involved - a term like "card" simply has an IDF near
    zero, so it can neither drag a result up nor push one out. That is what
    stops "flash card" from annihilating to nothing.
    """
    if not terms and not facets:
        return {}

    candidates = None
    for group in required:
        docs = set()
        for term in group:
            docs |= set(index.postings.get(term, {}))
        candidates = docs if candidates is None else (candidates & docs)
        if not candidates:
            return {}

    for kind, value, _ in facets:
        docs = index.facet_docs.get((kind, value))
        if docs:
            candidates = docs.copy() if candidates is None else (candidates & docs)
        if candidates is not None and not candidates:
            return {}

    if candidates is None:
        candidates = set()
        for term in terms:
            candidates |= set(index.postings.get(term, {}))

    scores = {}
    for doc in candidates:
        total = 0.0
        for term, multiplier in terms.items():
            weight = index.postings.get(term, {}).get(doc)
            if weight:
                total += index.idf.get(term, 0.0) * weight * multiplier
        for kind, value, _ in facets:
            if doc in index.facet_docs.get((kind, value), ()):
                total += FACET_BOOST
        if total > 0:
            scores[doc] = total
    return scores


def scoring_weights(query, index):
    """
    Exact and corrected terms score in full; prefix completions at a discount -
    unless the completion is far more common than the exact term, in which case
    the user was mid-word and the completion is what they meant.

    Concretely: "valentin" is a real token in this catalogue, the Spanish "San
    Valentin", on 3 cards. Rarity gives it a large IDF, so without this rule the
    3 Spanish cards bury the 440 "valentines" cards the user was typing towards.
    Matching exactly is not the same as being the best answer.
    """
    weights = {}
    for term in query.terms:
        weights[term] = 1.0
    for term, group in query.groups.items():
        exact_df = index.document_frequency.get(term, 0)
        for completion in group:
            if completion in weights:
                continue
            completion_df = index.document_frequency.get(completion, 0)
            dominant = completion_df > max(exact_df, 1) * PREFIX_DOMINANCE
            weights[completion] = 1.0 if dominant else PREFIX_DISCOUNT
    return weights


def explain(index, card, terms, facets):
    reasons = []
    for kind, value, _ in facets:
        if card.doc in index.facet_docs.get((kind, value), ()):
            reasons.append(f"{kind}={value}")
    hits = [t for t in terms if index.postings.get(t, {}).get(card.doc)]
    if hits:
        reasons.append("matched " + "+".join(hits[:4]))
    return ", ".join(reasons)


# ---------------------------------------------------------------------------
# THE RELAXATION LADDER  --  this is what makes it stable on edge cases
#
# The old system had a cliff: strict AND, and if that missed, zero results and a
# carousel of unrelated popular cards. Every rung here degrades a little and
# reports which rung it landed on, so the UI can say what it did.
# ---------------------------------------------------------------------------

def rank(index, scores, limit, popularity=None):
    """
    Relevance first, in buckets. Raw scores are so fine-grained that a secondary
    sort would never apply, so we round into tiers and break ties inside a tier.
    That keeps popularity useful without letting a 2002 card with 24 years of
    accumulated sends outrank a genuinely better match.
    """
    if not scores:
        return []
    top = max(scores.values())
    ranked = []
    for doc, value in scores.items():
        bucket = int(10 * value / top)            # 10 relevance tiers
        card = index.cards[doc]
        tiebreak = popularity.get(card.number, 0) if popularity else card.year
        ranked.append((-bucket, -tiebreak, card.title, doc))
    ranked.sort()
    return [index.cards[doc] for *_, doc in ranked[:limit]]


def search(index, raw_query, limit=MAX_RESULTS, popularity=None):
    query = understand(raw_query, index)
    terms, facets = query.terms, query.facets

    if not terms and not facets:
        return {"query": raw_query, "strategy": "empty", "message": None,
                "corrections": {}, "results": [], "explain": {}}

    # Order terms by IDF so the least informative one is dropped first.
    ordered = sorted(terms, key=lambda t: index.idf.get(t, 0.0), reverse=True)

    # Words already consumed as a facet must not ALSO be required as free text.
    # "animated birthday" means format=animated AND the text "birthday"; it does
    # not mean the word "animated" has to appear in the card's prose, which
    # almost none of them do.
    consumed = {source for _, _, source in facets for source in source.split()}
    text_terms = [t for t in ordered if t not in consumed]

    # Facets are ordered most-selective first, so the vaguest one relaxes first.
    ranked_facets = sorted(
        facets, key=lambda f: len(index.facet_docs.get(f[:2], ())))

    weights = scoring_weights(query, index)

    def group_of(term):
        return query.groups.get(term, {term})

    ladder = []
    if text_terms:
        ladder.append(("all terms", ranked_facets, [group_of(t) for t in text_terms]))
    # Progressively drop the least informative text term, keeping every facet.
    for drop in range(1, len(text_terms)):
        kept = text_terms[:-drop]
        if kept:
            ladder.append((f"dropped {drop} weak term(s)", ranked_facets,
                           [group_of(t) for t in kept]))
    if ranked_facets:
        ladder.append(("facets only", ranked_facets, []))
        # Then relax the facets themselves, vaguest first.
        for keep in range(len(ranked_facets) - 1, 0, -1):
            subset = ranked_facets[:keep]
            if text_terms:
                ladder.append((f"{keep} facet(s) + best term", subset,
                               [group_of(text_terms[0])]))
            ladder.append((f"{keep} facet(s) only", subset, []))
    if text_terms:
        ladder.append(("best term only", [], [group_of(text_terms[0])]))
    if terms:
        ladder.append(("any term", [], []))

    # Descend the ladder ACCUMULATING results rather than stopping at the first
    # rung that returns anything. "sorry card for friend" has exactly one card
    # tagged both apology and friend; stopping there would show a one-result
    # page. The precise match still ranks first - later rungs only ever append
    # below it - but the page fills out. This is what makes the behaviour stable
    # regardless of how specific the query was.
    results, seen, explanations = [], set(), {}
    landed_on = None
    for strategy, use_facets, required in ladder:
        if len(results) >= limit:
            break
        scores = score(index, weights, use_facets, required)
        if not scores:
            continue
        for card in rank(index, scores, limit * 2, popularity):
            if card.doc in seen:
                continue
            seen.add(card.doc)
            results.append(card)
            explanations[card.doc] = explain(index, card, query.terms, use_facets)
            if len(results) >= limit:
                break
        if landed_on is None:
            landed_on = strategy

    if results:
        return {
            "query": raw_query,
            "strategy": landed_on,
            "message": _message(landed_on, query),
            "corrections": query.corrections,
            "results": results[:limit],
            "explain": explanations,
        }

    # Last rung: the occasion the query most resembles, so the page is never blank.
    fallback = _nearest_occasion(index, terms)
    if fallback:
        docs = list(index.facet_docs[("occasion", fallback)])
        docs.sort(key=lambda d: -index.cards[d].year)
        results = [index.cards[d] for d in docs[:limit]]
        return {"query": raw_query, "strategy": "nearest occasion",
                "message": f"No exact match. Showing {fallback} cards.",
                "corrections": query.corrections, "results": results,
                "explain": {c.doc: f"occasion={fallback}" for c in results}}

    return {"query": raw_query, "strategy": "exhausted", "corrections": query.corrections,
            "message": "No matching cards.", "results": [], "explain": {}}


def _message(strategy, query):
    if query.corrections:
        pairs = ", ".join(f"{k} \u2192 {v}" for k, v in query.corrections.items())
        return f"Showing results for {pairs}"
    if strategy not in ("all terms",):
        return f"Broadened your search ({strategy})"
    return None


def _nearest_occasion(index, terms):
    best, best_score = None, 0
    for prefix, words in index.occasions.items():
        overlap = len(set(terms) & words)
        if overlap > best_score:
            best, best_score = prefix, overlap
    return best


# ---------------------------------------------------------------------------
# THE OLD ALGORITHM, for side-by-side comparison
# ---------------------------------------------------------------------------

OLD_STOP_WORDS = set("""
a an and animated any e es anyone as at beautiful but by card cards day de e-card e-cards
e-greetings e-wish ecard ecards egreetings everybody everyone flash for free from gandhian
greeting greetings happy image images in into like nor of off on onto or per popular post
postcard postcards printable so some somebody someone the this to top up via wishes wishing
with yet your yours x
""".split())
OLD_SYNONYMS = {"bday": "birthday", "b'day": "birthday", "mom": "mother",
                "mommy": "mother", "mama": "mother", "xmas": "christmas",
                "x-mas": "christmas", "1st": "1", "cumpleanos": "birthday"}
OLD_CAP = 500


def old_search(rows, raw_query, limit=10):
    """Faithful simulation of production: stop words, synonyms, strict AND, 500 cap."""
    terms = []
    for token in re.findall(r"[a-z0-9']+", (raw_query or "").lower()):
        token = OLD_SYNONYMS.get(token, token)
        if token not in OLD_STOP_WORDS:
            terms.append(token)
    if not terms:
        return [], 0
    hits = []
    for row in rows:
        blob = " ".join([row["card_title"], row["card_description"],
                         row.get("card_tags") or "", row["q1_value"].replace("_", " "),
                         row["card_page_url"]]).lower()
        if all(t in blob for t in terms):
            hits.append(row)
            if len(hits) >= OLD_CAP:
                break
    return hits[:limit], len(hits)


# ---------------------------------------------------------------------------
# TESTS  --  one per complaint, plus the edge cases that used to crash or hang
# ---------------------------------------------------------------------------

def run_tests(index):
    print("=" * 76)
    print("SEARCH ENGINE TESTS")
    print("=" * 76)
    passed = failed = 0

    def check(label, condition, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  [PASS] {label}")
        else:
            failed += 1
            print(f"  [FAIL] {label}   {detail}")

    print("\n-- complaint 3: 'funny' must return actually-funny cards --")
    out = search(index, "funny", limit=10)
    humour = index.facet_docs.get(("tone", "humour"), set())
    top10 = out["results"][:10]
    hits = sum(1 for c in top10 if c.doc in humour)
    check(f"8+ of top 10 for 'funny' are tagged humour (got {hits})", hits >= 8)
    check("'funny' returns something", len(out["results"]) > 0)

    print("\n-- complaints 2 and 4: misspellings must find the right cards --")
    for typo, expect in [("birthdya", "birthday"), ("aniversary", "anniversary"),
                         ("chrismas", "christmas"), ("freind", "friend"),
                         ("congradulations", "congratulations")]:
        out = search(index, typo, limit=5)
        corrected = out["corrections"].get(typo, "")
        check(f"{typo!r} -> {expect!r} (got {corrected!r}, {len(out['results'])} results)",
              corrected.startswith(expect[:6]) and len(out["results"]) > 0)

    print("\n-- partial words are not typos: prefix expansion, not correction --")
    # "valentin" is a real token here - it is the Spanish "San Valentin" - so
    # correcting it would be wrong. Prefix expansion is what reaches "valentine".
    # Assert on the OCCASION, not on the words: a congratulations card is titled
    # "Congrats! You Did It!" and sits in congrats_foreveryone, so checking for
    # the literal string "congratulations" would test the catalogue's phrasing
    # rather than the search.
    for partial, occasion in [("valentin", "efeb"), ("anniv", "anniv"),
                              ("birthd", "birth"), ("congratul", "congrats")]:
        out = search(index, partial, limit=5)
        landed = sum(1 for c in out["results"] if c.category.startswith(occasion))
        check(f"{partial!r} -> {occasion}_* cards ({landed}/{len(out['results'])})",
              out["results"] and landed >= len(out["results"]) / 2)

    print("\n-- complaint 1: queries that used to return zero --")
    for query in ["flash card", "animated birthday", "funny birthday card for wife",
                  "happy birthday card for my beautiful mom", "sorry card for friend",
                  "musical anniversary card"]:
        out = search(index, query, limit=5)
        check(f"{query!r} -> {len(out['results'])} results via {out['strategy']!r}",
              len(out["results"]) > 0)

    print("\n-- format facets the old stop-word list deleted --")
    out = search(index, "animated birthday", limit=10)
    animated = index.facet_docs.get(("format", "animated"), set())
    n = sum(1 for c in out["results"] if c.doc in animated)
    check(f"'animated birthday' returns animated cards ({n}/{len(out['results'])})",
          n >= max(1, len(out["results"]) // 2))

    print("\n-- complaint 5: relevance must beat age --")
    out = search(index, "funny birthday", limit=10)
    check("'funny birthday' top result is tagged humour",
          bool(out["results"]) and out["results"][0].doc in humour)

    print("\n-- stability: nothing may crash, hang, or dump the catalogue --")
    payloads = ["", "   ", "a", "'", "' OR '1'='1", "<script>alert(1)</script>",
                "../../etc/passwd", "\x00", "%92", "&#x27;", "zzzqqq",
                "a " * 500, "ANNIVERSARY" * 200, "()" * 500, "\u202eyradhtrib",
                "\U0001d51e\U0001d52b\U0001d52b", "!!!", "%%%%", "--", "\\"]
    for payload in payloads:
        start = time.perf_counter()
        try:
            out = search(index, payload, limit=10)
            ok = len(out["results"]) <= 10
            detail = ""
        except Exception as exc:                     # noqa: BLE001 - test harness
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - start
        label = payload[:26].replace("\x00", "\\x00").replace("\n", " ")
        check(f"payload {label!r:30} {elapsed*1000:6.1f} ms", ok and elapsed < 1.0, detail)

    print("\n" + "=" * 76)
    print(f"{passed} passed, {failed} failed")
    print("=" * 76)
    return failed == 0


def compare(index, rows, queries):
    live = [r for r in rows if r["status_id"] == LIVE_STATUS and r["invalid_card"] == "0"]
    print("=" * 76)
    print("OLD (Sphinx pipeline)  vs  NEW")
    print("=" * 76)
    for query in queries:
        old_hits, old_total = old_search(live, query, limit=5)
        new = search(index, query, limit=5)
        print(f"\n  QUERY: {query!r}")
        print(f"    OLD  {old_total:>5} matches   {'ZERO RESULTS' if not old_hits else ''}")
        for row in old_hits:
            print(f"           {decode_entities(row['card_title'])[:52]:<54} [{row['q1_value']}]")
        if not old_hits:
            print("           -> falls through to the popular-cards carousel")
        note = new["message"] or new["strategy"]
        print(f"    NEW  {len(new['results']):>5} shown     ({note})")
        for card in new["results"]:
            why = new["explain"].get(card.doc, "")
            print(f"           {card.title[:52]:<54} [{card.category}]  {why}")


def interactive(index):
    print("\nType a query. Blank line or 'quit' to stop.\n")
    while True:
        try:
            raw = input("search> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw or raw.lower() in ("quit", "exit"):
            break
        start = time.perf_counter()
        out = search(index, raw)
        elapsed = (time.perf_counter() - start) * 1000
        if out["message"]:
            print(f"  {out['message']}")
        if not out["results"]:
            print("  no results\n")
            continue
        for card in out["results"]:
            why = out["explain"].get(card.doc, "")
            print(f"  {card.title[:46]:<48} [{card.category:<22}] {card.year}  {why}")
        print(f"  -- {len(out['results'])} results in {elapsed:.1f} ms "
              f"via {out['strategy']}\n")


COMPARE_QUERIES = [
    "funny", "funny birthday", "flash card", "animated birthday",
    "birthdya", "aniversary", "congradulations",
    "funny birthday card for wife", "happy birthday card for my beautiful mom",
    "musical christmas card", "sorry card for friend", "thank you teacher",
]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else "card_database.csv"
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    start = time.perf_counter()
    index = SearchIndex(rows)
    build_ms = (time.perf_counter() - start) * 1000
    print(f"Indexed {index.total:,} live cards from {len(rows):,} exported rows "
          f"in {build_ms:.0f} ms")
    print(f"  {len(index.postings):,} terms, "
          f"{len(index.facet_docs):,} facet buckets, "
          f"{sum(len(v) for v in index.corrector.index):,} spell-correction keys\n")

    if "--compare" in sys.argv:
        compare(index, rows, COMPARE_QUERIES)
        return
    run_tests(index)
    if sys.stdin.isatty():
        interactive(index)


if __name__ == "__main__":
    main()
