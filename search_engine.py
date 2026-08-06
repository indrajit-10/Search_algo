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
import gzip
import io
import math
import os
import re
import sys
import time
import unicodedata
import zipfile

csv.field_size_limit(10 ** 9)


def load_rows(path):
    """
    Read a card export. Accepts .csv, .csv.gz and .zip without unpacking first.

    A full export is around 33 MB, over the limit most upload forms allow, so
    the file that actually gets moved around is usually compressed. Requiring it
    to be extracted first is a step that exists only to be forgotten.

    gzip and zip are both standard library. 7-zip is not - if you have a .7z,
    extract it, or re-compress as .gz, rather than adding a dependency for it.
    """
    lower = path.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.lower().endswith(".csv") and not n.startswith("__MACOSX")]
            if not names:
                raise ValueError(f"{path} contains no .csv: {archive.namelist()}")
            with archive.open(names[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                return list(csv.DictReader(text))
    if lower.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    if lower.endswith(".7z"):
        raise SystemExit(
            f"{path} is 7-zip, which is not in the standard library.\n"
            f"  Extract it:            7z x {path}\n"
            f"  or re-compress as gz:  gzip -9 <the .csv>\n"
            f"  .csv, .csv.gz and .zip all work directly.")
    if lower.endswith((".xlsx", ".xls", ".xlsm", ".ods")):
        raise SystemExit(
            f"{path} is a spreadsheet, and CSV is what you want here anyway.\n\n"
            "  In Excel: File > Save As > CSV UTF-8 (Comma delimited)\n"
            f"  Save it as data/card_database.csv and re-run.\n\n"
            "  Reading it directly would need a third-party library, and Excel\n"
            "  quietly damages this particular export on the way through: it\n"
            "  reformats card_created_date, strips leading zeros from\n"
            "  card_number, and mangles the %92 sequences in card_title. Export\n"
            "  straight from the database to CSV if you can.")
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# Where the export lives. Drop the file in data/ and every script finds it, so
# nothing takes a path argument unless you want it to.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
EXPORT_NAMES = ("card_database.csv", "card_database.csv.gz", "card_database.zip",
                "card_database.csv.zip", "cards.csv", "cards.csv.gz")


def find_export(explicit=None):
    """
    Locate the card export.

    Order: an explicit path, then data/ by known name, then any csv-ish file in
    data/. Raises with a readable message rather than a stack trace, because
    "which file, where" is the single most common way to get stuck on step one.
    """
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f"No such file: {explicit}")
        return explicit

    for name in EXPORT_NAMES:
        candidate = os.path.join(DATA_DIR, name)
        if os.path.exists(candidate):
            return candidate

    if os.path.isdir(DATA_DIR):
        found = sorted(f for f in os.listdir(DATA_DIR)
                       if f.lower().endswith((".csv", ".csv.gz", ".zip"))
                       and not f.startswith("."))
        if len(found) == 1:
            return os.path.join(DATA_DIR, found[0])
        if found:
            raise SystemExit(
                "More than one export in data/, so I will not guess:\n  "
                + "\n  ".join(found)
                + "\nName one card_database.csv, or pass the path as an argument.")

    raise SystemExit(
        "No card export found.\n\n"
        f"  Put your export here:  {os.path.join('data', 'card_database.csv')}\n"
        "  .csv, .csv.gz and .zip all work - no need to unpack.\n\n"
        "  Or pass a path:  python3 search_engine.py /path/to/export.csv")

LIVE_STATUS = "1"          # status_id=1 is served; every other status is dead

# Card formats kept out of results entirely.
#
# "Y" is YouTube. Those cards embed a video and have no artwork of their own:
# every one of the 955 live ones has an empty card_thumb_extn AND an empty
# card_bigimage_extn, so there is nothing to put in a results grid.
#
# Filtered on the label rather than on card_number starting with 8, which is the
# other way to spot them. The label says what a card IS; the number says only
# where it landed in an ID range. They agree exactly today - across all 107,160
# exported rows the two rules select the same 19,555 cards - but an ID range is
# an accident of allocation and would not survive a renumbering.
EXCLUDE_LABEL_TYPES = {"Y"}

# How many edits a correction may spend. A flat ceiling is the wrong shape: two
# edits is half of a four-letter word and a sixth of a twelve-letter one, so a
# single number is simultaneously too loose for short words and far too tight
# for long ones. Long words are exactly the ones people mistype by three or more
# characters - transliterated festival names above all. "roshasana" is three
# edits from "roshhashanah" and so found nothing at all, while the catalogue
# holds 240 Rosh Hashanah cards.
#
# So the ceiling scales, and the loosest tier has to earn itself: three edits
# are allowed only when they are a small share of the word. That ratio is what
# separates "roshasana" -> "roshhashanah" (3 of 12 characters, a plausible slip)
# from "mariachi" -> "march" (3 of 8, a different word entirely). Without it the
# corrector confidently answered a mariachi search with March cards.
MAX_EDIT_DISTANCE = 2      # the ordinary ceiling
LOOSE_EDIT_DISTANCE = 3    # the most any correction may ever spend
LOOSE_EDIT_RATIO = 0.3     # ...and only if the edits are under 30% of the word

# The ratio is not just a filter, it bounds the work. Three edits can only ever
# be within 30% of a word that is at least ceil(3 / 0.3) = 10 characters, so no
# shorter term can be a three-edit target and only terms that long need the
# deeper delete index. A query word can be three shorter than the term it
# reaches, so it qualifies from 7.
#
# Skipping this and indexing every term deeply built the same index three times
# larger for no extra recall - 9.4 seconds of startup instead of 2.4.
LOOSE_MIN_TARGET = int(math.ceil(LOOSE_EDIT_DISTANCE / LOOSE_EDIT_RATIO))
LOOSE_MIN_QUERY = LOOSE_MIN_TARGET - LOOSE_EDIT_DISTANCE
MIN_CORRECTABLE = 4        # never "correct" a 3-letter word, too many neighbours
MIN_COMPOUND_PART = 3      # "merrychristmas" splits; "sofa" must not
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

# Freshness. A newly uploaded card gets its relevance score multiplied by at
# most (1 + RECENCY_BOOST), decaying by half every RECENCY_HALFLIFE years.
#
# Deliberately small. The old ranker sorted on lifetime send count, which on a
# catalogue running since 2002 is a permanent incumbency: a card uploaded last
# month cannot out-accumulate one with twenty years of head start, so new work
# was structurally unrankable. The fix is not to flip that around - a large
# freshness boost would just recreate the same complaint pointing the other way,
# with 2026 cards burying a better 2011 match.
#
# At 0.15 a brand new card is worth 15% more than an identical ancient one,
# which decides ties and near-ties and nothing else. A clearly better older
# match still wins, and there is a test below that holds that line.
RECENCY_BOOST = 0.15
RECENCY_HALFLIFE = 5.0     # years for the boost to halve

# Which slot survives longest as the query is relaxed. Lower is kept longer.
SLOT_PRIORITY = {"occasion": 0, "format": 1, "tone": 2, "recipient": 3}

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

# Recipient is the single most common modifier in the real query log: daughter,
# dad, son, brother, sister, grandson, niece, son-in-law and cousin all appear in
# the top few hundred queries. Anything missing here silently degrades to a
# generic occasion result.
RECIPIENT = {
    "wife": {"wife", "wifey"}, "husband": {"husband", "hubby"},
    "mother": {"mother", "mom", "mum", "mommy", "mummy", "mama", "moms", "mothers"},
    "father": {"father", "dad", "daddy", "papa", "pop", "dads", "fathers"},
    "sister": {"sister", "sis", "sisters"}, "brother": {"brother", "bro", "brothers"},
    "daughter": {"daughter", "daughters"}, "son": {"son", "sons"},
    "friend": {"friend", "friends", "buddy", "pal", "bestie"},
    "girlfriend": {"girlfriend", "gf"}, "boyfriend": {"boyfriend", "bf"},
    "boss": {"boss", "manager", "colleague", "coworker"},
    "teacher": {"teacher", "professor", "mentor"},
    "grandmother": {"grandmother", "grandma", "granny", "nana", "grandmom"},
    "grandfather": {"grandfather", "grandpa", "grandad", "granddad"},
    "grandson": {"grandson", "grandsons"},
    "granddaughter": {"granddaughter", "granddaughters"},
    "niece": {"niece", "nieces"}, "nephew": {"nephew", "nephews"},
    "cousin": {"cousin", "cousins"},
    "uncle": {"uncle", "uncles"}, "aunt": {"aunt", "aunty", "auntie", "aunts"},
    "couple": {"couple", "newlyweds"},
    "baby": {"baby", "newborn", "babies"},
    "pet": {"pet", "dog", "dogs", "puppy", "puppies", "cat", "cats", "kitten"},
}

# Ordinals are a facet of their own. "60th birthday" returned ZERO in production
# and was searched 14 times; "50th birthday", "70th", "80th", "16th", "9th",
# "1st birthday", "happy 75th birthday" and "20 year old" are all in the log.
# The catalogue does carry them - "Musical 95th Birthday Card" - they were just
# never addressable.
MILESTONE_RE = re.compile(r"\b(\d{1,3})\s*(?:st|nd|rd|th)?\b")
MILESTONE_WORDS = {"year", "years", "old", "th", "st", "nd", "rd"}

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
#
# TREATED AS A PROPOSAL, NOT A FACT. Every entry is checked against the cards at
# index time and dropped unless that prefix really is where the phrase lives -
# see _validate_hints(). Twelve of the entries below were wrong or ambiguous the
# first time this ran, because they were written from Western assumptions about
# which holiday falls in which month, and this catalogue is global:
#
#   "independence day" was pinned to July. It spans four occasions here -
#     ejul (US), eaug (India, 15 Aug), esep (Mexico, 16 Sep), edec (UAE).
#   "new year" was pinned to January, but esep carries 124 of them: Rosh
#     Hashanah, which the query log shows searched 879 times.
#   "thanksgiving" was pinned to November, but October holds more - Canada.
#   "easter" was pinned to March, and lives in April.
#
# Leaving a wrong hint in place is worse than having none: it invents a
# confident occasion facet that then outranks the words the user actually typed.
MONTH_PREFIX_HINTS = {
    "ejan": {"new year", "january", "chinese new year", "republic day"},
    "efeb": {"valentine", "valentines", "february", "rose day", "hug day"},
    "emar": {"holi", "spring", "march", "st patrick", "patrick", "easter"},
    "eapr": {"easter", "april", "earth day", "baisakhi"},
    "emay": {"mother", "mothers day", "may", "memorial day", "nurses"},
    "ejun": {"father", "fathers day", "june", "summer"},
    "ejul": {"july", "fourth of july", "july 4th"},
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

# Festivals this catalogue files under one name and the world spells several
# ways. These are NOT misspellings: "deepavali" is the Sanskrit name for Diwali,
# five edits from it, and it is a correctly spelled word sitting on 21 real
# cards - so the corrector rightly leaves it alone, and the searcher gets 21
# cards out of 470.
#
# Every pair below is a claim that two words name the same thing, and claims
# about the world are exactly what went wrong with the month hints. So none of
# them is trusted: _validate_variants checks each against the catalogue and
# keeps it only where both spellings really do live in the same section. See
# there for what that catches.
#
# Unlike ALIASES these do not REPLACE the typed word. Both spellings stay
# searchable, so a card that genuinely says "deepavali" still ranks above one
# that only says "diwali".
VARIANTS = {
    "deepavali": "diwali", "dipavali": "diwali", "deepawali": "diwali",
    "chanukah": "hanukkah", "chanukkah": "hanukkah", "hannukah": "hanukkah",
    "dasara": "dussehra", "dashera": "dussehra", "dussera": "dussehra",
    "navratri": "navaratri",
    "shivratri": "shivaratri",
    "onam": "thiruonam",
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


# Every apostrophe variant users and editors actually produce: straight, curly,
# backtick, acute, and the backslash-escaped form that leaks out of SQL quoting.
_APOSTROPHES = "'‘’ʼ´`"
_APOSTROPHE_RE = re.compile(f"\\\\?[{_APOSTROPHES}]")


def normalise(text):
    """
    THE MOST IMPORTANT FUNCTION IN THIS FILE.

    An apostrophe returned ZERO results in production, every time. From the
    query log: "mother's day" was searched 520 times and returned 0, while
    "mothers day" returned 1,125. "father's day" - 527 searches, 0 results.
    Across the log's top queries alone that is roughly 27,000 searches a period
    landing on an empty page, and it takes out two of the largest occasions on a
    greeting-card site.

    The cause is on both sides. Stored text holds %92 (a Windows-1252 curly
    apostrophe) rather than an apostrophe, and the typed apostrophe was being
    escaped or split rather than folded. So the two never met.

    An apostrophe JOINS a word, it does not split one: "mother's" -> "mothers",
    "b'day" -> "bday". Splitting produces a stray "s" token that matches nothing
    and drags the whole AND query to zero.
    """
    text = decode_entities(text or "")
    text = text.replace("+", " ")          # URL-encoded spaces: "mother's+day"
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _APOSTROPHE_RE.sub("", text)    # join, never split
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Inflectional endings, longest first so "sistering" strips "ing" before "s".
_SUFFIXES = ("ings", "ing", "edly", "ed", "ness", "less", "fully", "ful",
             "ly", "es", "s")


def word_variants(token):
    """
    Candidate base forms for an inflected word. ALTERNATIVES, never replacements
    - each one is only used if it is a term the catalogue actually contains.

    Spell correction cannot cover this: "sistering" is three edits from "sister",
    past the distance-2 ceiling, so it corrects to nothing and the query dies.
    The production log is full of these because something upstream is inflecting
    query terms - "sistering", "brothered", "lovings", "thankgiving" - but the
    same fallback also catches a user typing "singing" when the card says "sing".
    """
    out = []
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            stem = token[: -len(suffix)]
            out.append(stem)
            out.append(stem + "e")               # loving -> love, danc -> dance
            if len(stem) > 2 and stem[-1] == stem[-2]:
                out.append(stem[:-1])            # running -> run
            break                                 # one ending is enough
    return out


def tokenise(text):
    return normalise(text).split()


# ---------------------------------------------------------------------------
# SPELL CORRECTION  --  SymSpell deletion index
#
# The principle: compute the distance, never maintain a list of typos. The old
# system's synonym table held 11 spellings of "raksha" and none of "anniversary",
# which cannot be fixed by adding more rows - "anniversary" alone has 595
# misspellings within a single edit.
#
# Brute-force Levenshtein over the vocabulary gives identical answers to this and
# is far simpler, but scans every term per query. This precomputes the deletes of
# each dictionary term once, so a lookup becomes a set intersection.
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
                 min_frequency=MIN_DICTIONARY_FREQUENCY,
                 loose_distance=LOOSE_EDIT_DISTANCE,
                 loose_ratio=LOOSE_EDIT_RATIO):
        # Only well-attested terms are allowed to be "correct" or to be a
        # correction target. See MIN_DICTIONARY_FREQUENCY.
        self.vocabulary = {t: f for t, f in vocabulary.items() if f >= min_frequency}
        self.max_distance = max_distance
        self.loose_distance = max(loose_distance, max_distance)
        self.loose_ratio = loose_ratio
        self.index = collections.defaultdict(list)
        for term in self.vocabulary:
            if len(term) < MIN_CORRECTABLE:
                continue
            # Deeper deletes only for terms long enough to be a loose-tier
            # target. See LOOSE_MIN_TARGET - anything shorter can never be
            # reached at three edits, so indexing it deeply is pure cost.
            depth = (self.loose_distance if len(term) >= LOOSE_MIN_TARGET
                     else self.max_distance)
            for variant in deletes(term, depth):
                self.index[variant].append(term)
        # Nothing longer than this can be within reach of any term, because a
        # length gap alone already exceeds the ceiling. Checking it before
        # generating deletes matters: the deletions of a 120-character payload
        # number about 280,000 at depth three, which took 1.5 seconds to build
        # and could never match anything.
        self.longest = max((len(t) for t in self.vocabulary), default=0)

    def ceiling(self, word):
        return (self.loose_distance if len(word) >= LOOSE_MIN_QUERY
                else self.max_distance)

    def _plausible(self, word, candidate, distance):
        """
        Is spending this many edits proportionate to the word?

        Only the loose tier is questioned - one and two edits behave exactly as
        they always did. Above that the edits must be a small fraction of the
        longer of the two words, which is what keeps "roshasana" reaching
        "roshhashanah" while "mariachi" stops short of "march".
        """
        if distance <= self.max_distance:
            return True
        return distance <= self.loose_ratio * max(len(word), len(candidate))

    def correct(self, word):
        """Returns (word, distance). distance 0 means it was already a real term."""
        if word in self.vocabulary:
            return word, 0
        if len(word) < MIN_CORRECTABLE:
            return word, -1

        cap = self.ceiling(word)
        if len(word) > self.longest + cap:
            return word, -1
        seen = set()
        for variant in deletes(word, cap):
            seen.update(self.index.get(variant, ()))
        if not seen:
            return word, -1

        best = None
        for candidate in seen:
            distance = edit_distance(word, candidate, cap)
            if distance > cap or not self._plausible(word, candidate, distance):
                continue
            # closest first, then the term that appears on more cards
            rank = (distance, -self.vocabulary[candidate], candidate)
            if best is None or rank < best:
                best = rank
        if best is None:
            return word, -1
        return best[2], best[0]


def split_compound(word, document_frequency, min_part=MIN_COMPOUND_PART,
                   min_frequency=MIN_DICTIONARY_FREQUENCY):
    """
    Two real words typed as one: "merrychristmas", "happyanniversary".

    Only reached when the corrector has already given up, so a plain typo is
    never split instead of fixed - "aniversary" gets corrected, not carved into
    pieces. Both halves must be words the catalogue actually uses, and the split
    maximising the rarer half wins, which prefers "merry|christmas" over the
    many ways to shave a common fragment off either end.

    Returns a (left, right) pair, or None.
    """
    best = None
    for cut in range(min_part, len(word) - min_part + 1):
        left, right = word[:cut], word[cut:]
        left_df = document_frequency.get(left, 0)
        right_df = document_frequency.get(right, 0)
        if left_df < min_frequency or right_df < min_frequency:
            continue
        strength = min(left_df, right_df)
        if best is None or strength > best[0]:
            best = (strength, left, right)
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------------------
# INDEX
# ---------------------------------------------------------------------------

def _validate_hints(rows, hints=None):
    """
    Keep only the hardcoded month hints the catalogue actually supports.

    A hint claims "this phrase means this month". That is a claim about the
    world, and the world varies by country: Thanksgiving is November in the US
    and October in Canada, New Year is January unless it is Rosh Hashanah in
    September, and Independence Day belongs to at least four different months
    depending on whose it is. A global card catalogue contains all of them.

    So each phrase is counted across the prefixes where it really occurs, on
    word boundaries so "holi" does not match "holiday", and the hint survives
    only if the claimed prefix holds a clear plurality. A wrong hint is worse
    than a missing one - it manufactures a confident occasion facet that then
    outranks the words the user actually typed.
    """
    hints = MONTH_PREFIX_HINTS if hints is None else hints
    blobs = collections.defaultdict(list)
    for row in rows:
        prefix = row["q1_value"].split("_")[0]
        blobs[prefix].append(normalise(row["card_title"] + " " +
                                       row["card_description"]))

    kept, dropped = {}, []
    for prefix, phrases in hints.items():
        survivors = set()
        for phrase in phrases:
            pattern = re.compile(r"\b" + re.escape(normalise(phrase)) + r"\b")
            counts = {p: sum(1 for b in bl if pattern.search(b))
                      for p, bl in blobs.items()}
            total = sum(counts.values())
            if total < 3:                       # too rare to judge either way
                continue
            if counts.get(prefix, 0) / total >= 0.5:
                survivors.add(phrase)
            else:
                best = max(counts, key=counts.get)
                dropped.append((phrase, prefix, best, counts.get(prefix, 0),
                                counts[best]))
        if survivors:
            kept[prefix] = survivors
    return kept, dropped


def _validate_variants(index, variants=None):
    """
    Keep only the naming variants this catalogue actually supports.

    A variant pair claims two spellings name the same thing. The test is whether
    they live in the same part of the catalogue: every section a card of the
    rare spelling sits in must be one the common spelling occupies too, and the
    two must share a dominant section.

    That is a much sharper instrument than co-occurrence. Measuring which words
    merely appear together on the same cards proposes 1,707 pairs, and they are
    mostly topical rather than synonymous - "solstice" occurs beside "family",
    "wishes" and "friends" on every single one of its 54 cards. The section test
    drops those flat: solstice cards are filed under March, family cards under
    October. It also drops a pair whose target is barely in the catalogue, which
    is why "navratri" -> "navaratri" does not survive - the target spelling sits
    on 1 card and the source on 35, so the link points the wrong way.

    Returns (kept, dropped) where kept maps word -> the word it also searches.
    """
    variants = VARIANTS if variants is None else variants

    def sections(term):
        return collections.Counter(
            index.cards[doc].category.split("_")[0]
            for doc in index.postings.get(term, ()))

    kept, dropped = {}, []
    for word, target in variants.items():
        here, there = sections(word), sections(target)
        source_df = index.document_frequency.get(word, 0)
        target_df = index.document_frequency.get(target, 0)
        if not here or not there:
            dropped.append((word, target, "one spelling is not in the catalogue"))
            continue
        if target_df <= source_df:
            dropped.append((word, target, "target is the rarer spelling"))
            continue
        shared = sum(n for section, n in here.items() if section in there)
        if here.most_common(1)[0][0] != there.most_common(1)[0][0]:
            dropped.append((word, target, "different dominant section"))
        elif shared / sum(here.values()) < 0.9:
            dropped.append((word, target, "sections mostly disagree"))
        else:
            kept[word] = target
    return kept, dropped


def derive_occasion_lexicon(rows, validated_hints=None):
    """
    Build q1_value-prefix -> {human search terms} from the catalogue itself.

    A slug like "wed_arndworld" shares no letters with "around the world", so a
    user typing the occasion cannot reach it. Rather than hand-maintain that
    table, take the words that are distinctively common in each prefix's own
    titles and tags. Month prefixes get a hint table because "edec" cannot be
    derived from anything.
    """
    if validated_hints is None:
        validated_hints, _ = _validate_hints(rows)
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
        terms.update(validated_hints.get(prefix, set()))
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
                    if r["status_id"] == LIVE_STATUS and r["invalid_card"] == "0"
                    and r["card_label_type"] not in EXCLUDE_LABEL_TYPES]
        self.validated_hints, self.dropped_hints = _validate_hints(rows)
        self.occasions = derive_occasion_lexicon(rows, self.validated_hints)
        # phrase -> how many cards carry it. Tags are already written as search
        # phrases rather than single words, which makes them the second-best
        # source of completions after the query log itself.
        self.tag_phrases = collections.Counter()
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
            for phrase in (row.get("card_tags") or "").split(","):
                phrase = normalise(phrase)
                if phrase:
                    self.tag_phrases[phrase] += 1
            self.cards.append(card)

        self.total = len(self.cards)
        self.newest_year = max((c.year for c in self.cards if c.year), default=0)
        # Newest first, most recently inserted breaking ties. This is the floor
        # of the relaxation ladder: the page is never empty, so "0 results"
        # cannot happen. See search().
        self.newest_cards = sorted(
            self.cards, key=lambda c: (-(c.year or 0), -int(c.number or 0)))
        self.idf = {
            term: math.log(1 + (self.total - freq + 0.5) / (freq + 0.5))
            for term, freq in self.document_frequency.items()
        }
        self.corrector = SpellCorrector(self.document_frequency)
        self.sorted_terms = sorted(self.postings)
        self.variants, self.dropped_variants = _validate_variants(self)

        # A facet's boost scales with how much it narrows the catalogue, for the
        # same reason IDF exists. occasion=edec covers every December card -
        # Christmas, Boxing Day, ice cream day - so a flat boost would add the
        # same 12 points to all of them and flatten the text ranking that tells
        # "christmas" apart from "boxing day". A narrow facet like tone=humour
        # really is worth more.
        reference = math.log(1 + self.total / 100.0)
        self.facet_boost = {}
        for key, docs in self.facet_docs.items():
            selectivity = math.log(1 + self.total / max(len(docs), 1))
            self.facet_boost[key] = FACET_BOOST * min(selectivity / reference, 1.6)

        # Reverse the derived occasion lexicon so a query word can name an
        # occasion. A word that maps to many occasions ("love", "wishes") is
        # not evidence of any one of them, so only keep the discriminating ones.
        reverse = collections.defaultdict(set)
        for prefix, words in self.occasions.items():
            for phrase in words:
                for word in phrase.split():
                    # A word that already names a tone, format or recipient is
                    # not an occasion. "musical" is distinctive of birth_songs,
                    # so the derivation happily concluded musical => birthday -
                    # which made "musical christmas" ask for a card that is both
                    # a birthday and a December card, and there is no such thing.
                    if len(word) >= 4 and word not in FACET_VOCABULARY:
                        reverse[word].add(prefix)
        # Only words that name exactly ONE occasion. A card has a single
        # occasion, so two occasion facets can never both hold and the query is
        # forced to relax immediately. "year" is the case that matters: it is
        # distinctive of both ejan (New Year) and edec (Christmas and New Year),
        # so as an occasion signal it says nothing while looking like it does.
        self.occasion_of = {w: p for w, p in reverse.items() if len(p) == 1}

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

    @staticmethod
    def _category_tokens(row):
        """
        Only the slug's own words. The derived occasion lexicon deliberately
        does NOT go in here.

        Injecting it made every December card contain the literal token
        "christmas", so "animated christmas card" ranked Boxing Day and ice
        cream day cards as highly as actual Christmas ones - the term stopped
        discriminating inside its own occasion. The lexicon's job is to let a
        query REACH an occasion, which is the facet's role; it is not evidence
        about an individual card.
        """
        return tokenise(row["q1_value"].replace("_", " "))

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


def _split_run_together(words, index, query):
    """
    Expand words that are two words typed as one.

    Ordering is the whole safety argument. A word is only offered to the
    splitter once it is neither a real term, nor a facet name, nor something the
    corrector can rescue - so "aniversary" is corrected to "anniversary" rather
    than carved up, and any word the catalogue already knows is left entirely
    alone. What is left is the genuine run-together case: "merrychristmas",
    "happyanniversary", "congratulationsgraduate", each of which used to reach
    the newest-cards fallback.

    Returns the expanded words and the corrections already computed getting
    there, so understand() does not correct the same word a second time.
    """
    out, corrected = [], {}
    for word in words:
        if word in FACET_VOCABULARY or word in index.document_frequency:
            out.append(word)
            continue
        result = index.corrector.correct(word)
        if result[1] >= 0:
            corrected[word] = result
            out.append(word)
            continue
        parts = split_compound(word, index.document_frequency)
        if parts:
            query.corrections[word] = " ".join(parts)
            out.extend(parts)
        else:
            corrected[word] = result
            out.append(word)
    return out[:MAX_TOKENS], corrected


def understand(raw, index):
    """Normalise, alias, spell-correct, then pull out any facet the user named."""
    query = Query(raw)
    raw = (raw or "")[:MAX_QUERY_CHARS]

    words = [ALIASES.get(w, w) for w in tokenise(raw)][:MAX_TOKENS]
    if not words:
        return query
    words, already_corrected = _split_run_together(words, index, query)

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
            corrected, distance = (already_corrected.get(word)
                                   or index.corrector.correct(word))
            if distance > 0:
                query.corrections[word] = corrected
                word = corrected
        query.terms.append(word)

        # A known word still gets prefix expansion, because a real word can also
        # be the start of a longer one the user meant.
        group = {word}
        group.update(index.prefix_terms(word))
        # A different name for the same festival. Added to the group rather than
        # replacing the word, so "deepavali" reaches all 470 Diwali cards while
        # the 21 that say "deepavali" themselves still score higher.
        variant = index.variants.get(word)
        if variant:
            group.add(variant)
            group.update(index.prefix_terms(variant))
        # "mother's day" normalises to "mothers day" but the catalogue mostly
        # says "mother"; "sistering" needs to reach "sister". Accept the base
        # form as an alternative without forcing it over the typed word.
        for base in word_variants(word):
            if base in index.document_frequency:
                group.add(base)
                group.update(index.prefix_terms(base))
                break
        query.groups[word] = group

        for kind, lexicon in (("tone", TONE), ("recipient", RECIPIENT),
                              ("format", FORMAT)):
            for value, members in lexicon.items():
                if word in members:
                    query.facets.append((kind, value, word))

        # The occasion slot. "birthday card for mom" must mean occasion=birth
        # AND recipient=mother; without this the recipient facet alone wins and
        # Mother's Day cards outrank birthday-for-mother cards.
        #
        # Inflected forms have to reach it too. "new year's" normalises to
        # "new years", and without the base form "years" names no occasion while
        # "year" does - so the possessive and the plain query answered from
        # different rungs of the ladder and shared no results at all.
        occasions = index.occasion_of.get(word)
        if not occasions and variant:
            occasions = index.occasion_of.get(variant)
        if not occasions:
            for base in word_variants(word):
                occasions = index.occasion_of.get(base)
                if occasions:
                    break
        for prefix in occasions or ():
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
                total += index.facet_boost.get((kind, value), FACET_BOOST)
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

def recency_factor(year, newest, boost=RECENCY_BOOST):
    """1.0 for an undated or very old card, up to 1+boost for the newest."""
    if not year or not newest or boost <= 0:
        return 1.0
    age = max(0, newest - year)
    return 1.0 + boost * (0.5 ** (age / RECENCY_HALFLIFE))


def rank(index, scores, limit, popularity=None, recency_boost=RECENCY_BOOST):
    """
    Relevance first, in buckets. Raw scores are so fine-grained that a secondary
    sort would never apply, so we round into tiers and break ties inside a tier.
    That keeps popularity useful without letting a 2002 card with 24 years of
    accumulated sends outrank a genuinely better match.

    Freshness is applied to the score BEFORE bucketing, so a recent card can
    climb a tier when it is already close - but the boost is capped, so it can
    never leapfrog a card that is clearly a better match.
    """
    if not scores:
        return []
    boosted = {
        doc: value * recency_factor(index.cards[doc].year, index.newest_year,
                                    recency_boost)
        for doc, value in scores.items()
    }
    top = max(boosted.values())
    ranked = []
    for doc, value in boosted.items():
        bucket = int(10 * value / top)            # 10 relevance tiers
        card = index.cards[doc]
        # Inside a tier, popularity decides when we have it, recency otherwise.
        tiebreak = popularity.get(card.number, 0) if popularity else card.year
        ranked.append((-bucket, -tiebreak, card.title, doc))
    ranked.sort()
    return [index.cards[doc] for *_, doc in ranked[:limit]]


def latest_cards(index, raw_query, limit, corrections=None, strategy="latest"):
    """
    THE FLOOR. Every search returns cards, always.

    A blank page is the worst result a search box can produce: the user has told
    you exactly what they want and you answer with nothing. Production already
    knew this - v3.2 added a carousel of popular cards - but that carousel is
    where 64% of logged searches ended up, and popular on a catalogue running
    since 2002 means old.

    Newest-first instead. It gives the freshest work its only guaranteed
    exposure, and it turns a dead end into a browse surface.

    `fallback` is set so the front end can be honest about what happened -
    "we could not find that, here is what is new" reads completely differently
    from silently pretending these are matches.
    """
    results = index.newest_cards[:limit]
    return {
        "query": raw_query,
        "strategy": strategy,
        "fallback": True,
        "message": "No matching cards. Showing the latest additions.",
        "corrections": corrections or {},
        "results": results,
        "explain": {c.doc: f"newest ({c.year})" for c in results},
    }


def search(index, raw_query, limit=MAX_RESULTS, popularity=None,
           recency_boost=RECENCY_BOOST):
    query = understand(raw_query, index)
    terms, facets = query.terms, query.facets

    if not terms and not facets:
        # Nothing searchable was typed - blank, punctuation, or a payload that
        # normalised away entirely. Show the newest cards rather than a void.
        out = latest_cards(index, raw_query, limit, strategy="empty")
        out["message"] = "Showing the latest cards."
        return out

    # Order terms by IDF so the least informative one is dropped first.
    ordered = sorted(terms, key=lambda t: index.idf.get(t, 0.0), reverse=True)

    # Words already consumed as a facet must not ALSO be required as free text.
    # "animated birthday" means format=animated AND the text "birthday"; it does
    # not mean the word "animated" has to appear in the card's prose, which
    # almost none of them do.
    consumed = {source for _, _, source in facets for source in source.split()}
    text_terms = [t for t in ordered if t not in consumed]

    # Facets relax by SLOT IMPORTANCE, not by selectivity.
    #
    # Sorting by selectivity alone gets this exactly backwards. For "funny
    # birthday for mom" the occasion birth is the broadest facet, so it would be
    # discarded first and the query would land on Mother's Day cards - losing
    # the one word that anchors what the user actually wants. Occasion is the
    # anchor of a greeting-card query and is given up last; recipient is the
    # easiest to give up, since a birthday card that is not mother-specific is
    # still a birthday card.
    ranked_facets = sorted(
        facets,
        key=lambda f: (SLOT_PRIORITY.get(f[0], 9),
                       len(index.facet_docs.get(f[:2], ()))))

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
        # Keep the single most informative word alongside the facets BEFORE
        # giving up on text entirely. "indian independence day" is the case:
        # "independence" names an occasion, and dropping straight to facets-only
        # discarded "indian" - the one word that separates 15 August from
        # 4 July - while keeping a facet that was only ever a guess. The most
        # discriminating term the user typed should outlive an inferred facet.
        if text_terms:
            ladder.append(("facets + best term", ranked_facets,
                           [group_of(text_terms[0])]))
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
        for card in rank(index, scores, limit * 2, popularity, recency_boost):
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
            "fallback": False,          # these are genuine matches
            "message": _message(landed_on, query),
            "corrections": query.corrections,
            "results": results[:limit],
            "explain": explanations,
        }

    # Second-to-last rung: the occasion the query most resembles. Still related
    # to what was typed, so it beats a generic fallback when it fires at all.
    nearest = _nearest_occasion(index, terms)
    if nearest:
        docs = list(index.facet_docs[("occasion", nearest)])
        docs.sort(key=lambda d: -index.cards[d].year)
        results = [index.cards[d] for d in docs[:limit]]
        return {"query": raw_query, "strategy": "nearest occasion", "fallback": True,
                "message": "No exact match. Showing the closest occasion.",
                "corrections": query.corrections, "results": results,
                "explain": {c.doc: f"occasion={nearest}" for c in results}}

    return latest_cards(index, raw_query, limit, query.corrections)


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
# AUTOCOMPLETE
# ---------------------------------------------------------------------------

# Query-log entries that must never be offered back to a user. The log is raw
# input, so it contains an XSS probe someone ran 25 times, path traversal
# attempts, URL-encoded plus signs, bare years, and the "s's x" forms produced
# by the broken upstream rewriter. Suggesting any of that would be handing
# people someone else's attack, or nonsense that cannot match.
_SUGGEST_REJECT = re.compile(
    r"""[<>"'`;\\|${}()\[\]/]      # quoting, shell, path, markup
      | script | alert | passwd     # obvious probes
      | \bs's\b                     # the rewriter's output
      | \+                          # URL-encoded spaces
    """, re.X | re.I)
_SUGGEST_ALLOWED = re.compile(r"[a-z0-9 &.,'-]+")

MIN_SUGGEST_CHARS = 2       # do not open a dropdown on a single letter
SUGGEST_LIMIT = 8


def _suggestable(phrase):
    """Is this phrase safe and sensible to offer as a completion?"""
    if not (3 <= len(phrase) <= 40):
        return False
    if _SUGGEST_REJECT.search(phrase):
        return False
    if not _SUGGEST_ALLOWED.fullmatch(phrase):
        return False
    if phrase.replace(" ", "").isdigit():       # bare years and counts
        return False
    return True


class Suggester:
    """
    Google-style completions, ranked by how often people actually search them.

    Sources, in descending trust:

      1. The production query log. What real users typed, weighted by volume.
         Nothing else comes close for relevance - "birthday cards free" is not a
         phrase any catalogue field contains, but 158 people a period type it.
      2. Card tags. Already phrases rather than single words ("funny birthday
         cards", "birthday wishes for dad"), weighted by how many cards carry
         them, and heavily discounted against the log.

    Every candidate is then checked against the index and dropped unless it
    actually returns cards. A suggestion that leads to an empty page is worse
    than no suggestion: it is the search box promising something it cannot
    deliver, in its own voice.
    """

    LOG_WEIGHT = 1.0
    TAG_WEIGHT = 0.05           # a tag on 20 cards ~= a query searched once

    def _canonical(self, index, phrase):
        """
        Fold a phrase onto the wording the catalogue itself uses.

        "birthday's" normalises to "birthdays", which the log records 735 times
        purely because the apostrophe bug sent people back to try again. Left
        alone it outranks "birthday", searched 344 times and actually answered.
        Folding each word to whichever form the catalogue uses more often merges
        the two and ranks the canonical spelling.
        """
        out = []
        for word in phrase.split():
            best, best_df = word, index.document_frequency.get(word, 0)
            for base in word_variants(word):
                df = index.document_frequency.get(base, 0)
                if df > best_df:
                    best, best_df = base, df
            out.append(best)
        return " ".join(out)

    def _in_vocabulary(self, index, phrase):
        """
        Every word must be one the catalogue actually uses.

        This is what removes the upstream rewriter's output. "motherings" was
        logged 58 times, "brothered" 65, "sistering" 41, "lovings" 38, "1cards"
        535 - all high enough to rank well, none of them typed by a human, and
        none of them words any card contains. Suggesting them would put machine
        noise in front of users in the search box's own voice.
        """
        return all(index.document_frequency.get(w, 0) >= MIN_DICTIONARY_FREQUENCY
                   for w in phrase.split())

    def __init__(self, index, query_log=None, verify=True):
        # Merge on the canonical form, but DISPLAY the wording people actually
        # use. Folding is only there to stop "birthdays" and "birthday" being
        # ranked as rivals; shown literally it turns "mothers day" into "mother
        # day", which no one writes. So each canonical key keeps the
        # highest-weighted spelling that produced it, and that is what is
        # offered back.
        weights = collections.Counter()
        surface = {}

        def add(raw, weight):
            phrase = normalise(raw)
            if not (_suggestable(phrase) and self._in_vocabulary(index, phrase)):
                return
            key = self._canonical(index, phrase)
            weights[key] += weight
            best, best_w = surface.get(key, (None, -1))
            if weight > best_w:
                surface[key] = (phrase, weight)

        for phrase, times in (query_log or []):
            add(phrase, times * self.LOG_WEIGHT)
        # Tag phrases come off the cards themselves, so they always match and
        # need no verification pass.
        for phrase, cards in index.tag_phrases.items():
            add(phrase, cards * self.TAG_WEIGHT)

        # Only log-derived phrases need checking, and only those no tag backs.
        # Verifying all ~10,000 candidates cost 19 seconds of startup for
        # nothing.
        if verify:
            for key in [k for k in list(weights) if k not in index.tag_phrases]:
                out = search(index, surface[key][0], limit=1)
                if not out["results"] or out.get("fallback"):
                    del weights[key]
                    surface.pop(key, None)

        # Re-key everything on the spelling that will actually be shown.
        self.weights = collections.Counter()
        for key, weight in weights.items():
            self.weights[surface[key][0]] += weight
        self.phrases = sorted(self.weights)
        # Word -> phrases, so "birthday" can still suggest "funny birthday
        # cards" even though the phrase does not start with it.
        self.by_word = collections.defaultdict(list)
        for phrase in self.phrases:
            for word in set(phrase.split()):
                self.by_word[word].append(phrase)

    def suggest(self, typed, limit=SUGGEST_LIMIT):
        typed = normalise(typed)
        if len(typed) < MIN_SUGGEST_CHARS:
            return []

        import bisect
        seen, out = set(), []

        def take(phrase, rank):
            if phrase in seen or phrase == typed:
                return
            seen.add(phrase)
            out.append((rank, -self.weights[phrase], len(phrase), phrase))

        # 1. The phrase starts with exactly what was typed. Strongest.
        start = bisect.bisect_left(self.phrases, typed)
        for phrase in self.phrases[start:start + 400]:
            if not phrase.startswith(typed):
                break
            take(phrase, 0)

        # 2. Some word inside the phrase starts with the last word typed, and
        #    every earlier word typed appears somewhere in it. This is what lets
        #    "birthday f" reach "funny birthday cards".
        words = typed.split()
        head, last = words[:-1], words[-1]
        for word, phrases in self.by_word.items():
            if not word.startswith(last):
                continue
            for phrase in phrases:
                if all(h in phrase for h in head):
                    take(phrase, 1)

        out.sort()
        return [phrase for _, _, _, phrase in out[:limit]]


def load_query_log(path=None):
    """Read fixtures/real_queries.tsv if it is there. Autocomplete is better
    with it and still works without it, so a missing file is not an error."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "real_queries.tsv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = (l for l in handle if not l.startswith("#"))
        # QUOTE_NONE: the log contains an unbalanced double quote from an XSS
        # probe, which otherwise swallows several hundred rows into one field.
        for row in csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE):
            try:
                rows.append((row["query"], int(row["times"])))
            except (TypeError, ValueError, KeyError):
                continue
    return rows

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

    print("\n-- long words: the ceiling has to scale with the word --")
    # "roshasana" is three edits from "roshhashanah" and so found nothing at all
    # under a flat two-edit ceiling, while 240 Rosh Hashanah cards sat in the
    # catalogue. Two edits is half a four-letter word and a sixth of a
    # twelve-letter one; transliterated festival names are exactly where people
    # slip by three.
    for typo, expect in [("roshasana", "roshhashanah"), ("roshashana", "roshhashanah"),
                         ("thanksgivin", "thanksgiving"), ("annivarsery", "anniversary")]:
        out = search(index, typo, limit=5)
        got = out["corrections"].get(typo, "")
        check(f"{typo!r} -> {expect!r} (got {got!r}, {len(out['results'])} results)",
              got == expect and out["results"] and not out["fallback"])

    # ...and the loose tier must not become a licence to guess. Three edits are
    # only proportionate on a long word: "mariachi" is three from "march", and
    # answering a mariachi search with March cards is worse than answering it
    # with nothing.
    for word, must_not in [("mariachi", "march"), ("sobriety", "society")]:
        got = index.corrector.correct(word)[0]
        check(f"{word!r} is not rewritten to {must_not!r} (got {got!r})",
              got != must_not)

    print("\n-- two words typed as one --")
    for typed, expect in [("merrychristmas", "merry christmas"),
                          ("happyanniversary", "happy anniversary"),
                          ("congratulationsgraduate", "congratulations graduate")]:
        out = search(index, typed, limit=5)
        got = out["corrections"].get(typed, "")
        check(f"{typed!r} -> {expect!r} ({len(out['results'])} results)",
              got == expect and out["results"] and not out["fallback"])
    # A typo must be corrected, never carved up: "aniversary" splits into
    # "ani"+"versary" if the splitter is allowed to run first.
    check("'aniversary' is corrected, not split",
          search(index, "aniversary", limit=3)["corrections"]
          .get("aniversary") == "anniversary")

    print("\n-- the same festival under another name --")
    # Not misspellings: "deepavali" is the Sanskrit name for Diwali, five edits
    # away and correctly spelled on 21 real cards, so the corrector rightly
    # leaves it alone and the searcher used to get 21 cards out of 470.
    for word, twin in index.variants.items():
        mine = {c.doc for c in search(index, word, limit=40)["results"]}
        theirs = {c.doc for c in search(index, twin, limit=40)["results"]}
        check(f"{word!r} reaches {twin!r} cards ({len(mine & theirs)}/40 shared)",
              len(mine & theirs) >= 10)
    check("unsupported variant claims are dropped, not trusted",
          any(why == "target is the rarer spelling"
              for _, _, why in index.dropped_variants))

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

    print("\n-- THE APOSTROPHE BUG: real queries that returned ZERO in production --")
    # Volumes are from the production query log. Every one of these returned no
    # results at all, and together they are ~27,000 searches a period landing on
    # the fallback carousel - including the two biggest occasions on the site.
    for query, volume in [("mother's day", 520), ("father's day", 527),
                          ("love's", 2489), ("friend's", 2364), ("family's", 2010),
                          ("birthday's", 735), ("it's", 475), ("new year's", 305),
                          ("i'm sorry", 288), ("valentine's", 272),
                          ("mother's+day", 345), ("b'day cards", 226),
                          ("father\\'s day cards", 125), ("season's greetings", 145)]:
        out = search(index, query, limit=5)
        check(f"{query!r:<26} ({volume:>4}/period, was 0) -> {len(out['results'])}",
              len(out["results"]) > 0)

    print("\n-- occasion queries must survive their apostrophe --")
    for apostrophe, plain in [("mother's day", "mothers day"),
                              ("father's day", "fathers day"),
                              ("new year's", "new year"),
                              ("valentine's day", "valentines day")]:
        a = {c.doc for c in search(index, apostrophe, limit=10)["results"]}
        b = {c.doc for c in search(index, plain, limit=10)["results"]}
        overlap = len(a & b) / max(len(b), 1)
        check(f"{apostrophe!r} matches {plain!r} ({overlap:.0%} overlap)", overlap >= 0.6)

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

    print("\n-- freshness: newer cards lifted, but only a little --")
    # Direction: with the boost on, results should skew newer than with it off.
    for query in ["birthday", "anniversary", "thank you", "get well soon"]:
        warm = search(index, query, limit=20, recency_boost=RECENCY_BOOST)["results"]
        cold = search(index, query, limit=20, recency_boost=0.0)["results"]
        warm_year = sum(c.year for c in warm) / max(len(warm), 1)
        cold_year = sum(c.year for c in cold) / max(len(cold), 1)
        check(f"{query!r} mean year {cold_year:.0f} -> {warm_year:.0f} with boost",
              warm_year >= cold_year)

    # The line that must hold: a boost big enough to bury a better match is a
    # bug, not a feature. It would be complaint 5 again, pointing the other way.
    #
    # Top-10 overlap is the wrong way to check that. For a query like
    # "mother's day" hundreds of cards tie on relevance, so WHICH ten of an
    # equally-good set you show is arbitrary - reordering them by date is
    # precisely the thing being asked for, not a regression. What actually
    # matters is that the single best match is never displaced.
    for query in ["funny", "funny birthday", "sympathy", "mother's day",
                  "60th birthday", "animated birthday"]:
        cold = search(index, query, limit=40, recency_boost=0.0)["results"]
        warm = search(index, query, limit=10, recency_boost=RECENCY_BOOST)["results"]
        survived = not cold or cold[0].doc in {c.doc for c in warm}
        check(f"{query!r} best unboosted match still in the boosted top 10", survived)

    # The hard guarantee is on the MULTIPLIER, so that is what gets checked.
    # Freshness is a bounded factor in [1, 1+boost], which means it can only
    # reorder cards already within 15% of each other on relevance; anything
    # further apart keeps its order whatever the dates say. Counting how far
    # cards move in the list would measure the bucketing instead, which quantises
    # to ten tiers and shifts positions for reasons that have nothing to do with
    # dates.
    newest = index.newest_year
    factors = [recency_factor(y, newest) for y in range(1995, newest + 1)]
    factors.append(recency_factor(0, newest))          # undated cards
    check(f"multiplier stays in 1.00-{1+RECENCY_BOOST:.2f} "
          f"(measured {min(factors):.3f}-{max(factors):.3f})",
          min(factors) >= 1.0 and max(factors) <= 1 + RECENCY_BOOST + 1e-9)

    advantage = recency_factor(newest, newest) / recency_factor(2002, newest) - 1
    check(f"a {newest} card beats an equally relevant 2002 card by "
          f"{100*advantage:.0f}%, not more", advantage <= RECENCY_BOOST + 1e-9)

    check("an undated card is neither helped nor punished",
          recency_factor(0, newest) == 1.0)

    # And the tone facet must still dominate: "funny" cannot become "recent".
    warm = search(index, "funny", limit=10, recency_boost=RECENCY_BOOST)["results"]
    still_funny = sum(1 for c in warm if c.doc in humour)
    check(f"'funny' still {still_funny}/10 tagged humour with the boost on",
          still_funny >= 8)

    print("\n-- the country that owns the holiday must win --")
    # A global catalogue holds several Independence Days, several New Years and
    # two Thanksgivings. The word naming the holiday cannot decide the occasion
    # on its own; the word naming the country has to survive to do it.
    for query, expect in [("indian independence day", "eaug_indindependenceday"),
                          ("mexican independence day", "esep_mexicanindependence"),
                          ("4th of july", "ejul_fourthjuly"),
                          ("rosh hashanah", "esep_roshhashanah"),
                          ("easter", "eapr_easter")]:
        out = search(index, query, limit=5)
        top = out["results"][0].category if out["results"] else ""
        check(f"{query!r} -> {top!r} starts with {expect!r}", top.startswith(expect))

    # Hardcoded month hints are proposals, not facts. Any the catalogue
    # contradicts must be dropped before they can invent a wrong facet.
    dropped = index.dropped_hints
    check(f"{len(dropped)} unsupported month hints were dropped", dropped)
    for phrase, claimed, actual, _, _ in dropped:
        if phrase == "easter":
            check(f"'easter' hint ({claimed}) dropped in favour of {actual}",
                  actual == "eapr")

    print("\n-- autocomplete --")
    sugg = Suggester(index, load_query_log())
    check(f"built {len(sugg.phrases):,} suggestion phrases", len(sugg.phrases) > 1000)
    for typed, expect in [("bir", "birthday"), ("anniv", "anniversary"),
                          ("mother", "mothers day"), ("get w", "get well"),
                          ("60th", "60th birthday"), ("funny bir", "funny birthday")]:
        hits = sugg.suggest(typed)
        check(f"{typed!r} suggests {expect!r} (got {hits[:3]})",
              any(h.startswith(expect) for h in hits))

    # Never hand a user back someone else's attack, or the rewriter's noise.
    for junk in ["motherings", "brothered", "sistering", "lovings", "1cards",
                 "thankgiving", "s's x"]:
        check(f"{junk!r} is never suggested", junk not in sugg.weights)
    everything = " ".join(sugg.phrases)
    check("no markup or quoting survives into suggestions",
          not set("<>\"'`;\\|${}()[]/").intersection(everything))

    # A suggestion that leads to an empty page is the search box breaking its
    # own promise, so every one of them has to return cards.
    import random as _r
    sample = sorted(sugg.weights, key=lambda p: -sugg.weights[p])[:60]
    dead = [p for p in sample if not search(index, p, limit=1)["results"]]
    check(f"top 60 suggestions all return cards ({len(dead)} dead)", not dead)
    check("a single letter opens nothing", sugg.suggest("b") == [])

    print("\n-- YouTube cards must never appear --")
    # They embed a video and carry no artwork of their own, so they would render
    # as permanently broken tiles. Checked against the index rather than trusted:
    # nothing indexed may carry an excluded label.
    excluded = [c for c in index.cards if c.label in EXCLUDE_LABEL_TYPES]
    check(f"no excluded label ({'/'.join(sorted(EXCLUDE_LABEL_TYPES))}) in the "
          f"index (found {len(excluded)})", not excluded)
    eights = [c for c in index.cards if str(c.number).startswith("8")]
    check(f"no card_number starting with 8 survived (found {len(eights)})", not eights)
    # And they must not sneak back in through the never-empty fallback.
    floor = latest_cards(index, "zzzzqqqxyz", 20)["results"]
    check("the latest-cards fallback is free of them",
          not [c for c in floor if c.label in EXCLUDE_LABEL_TYPES])
    for query in ["youtube", "youtube birthday", "birthday videos", "you tube"]:
        out = search(index, query, limit=10)
        bad = [c for c in out["results"] if c.label in EXCLUDE_LABEL_TYPES]
        check(f"{query!r} returns {len(out['results'])} cards, none excluded", not bad)

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
    live = [r for r in rows
            if r["status_id"] == LIVE_STATUS and r["invalid_card"] == "0"
            and r["card_label_type"] not in EXCLUDE_LABEL_TYPES]
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
    path = find_export(args[0] if args else None)
    rows = load_rows(path)

    start = time.perf_counter()
    index = SearchIndex(rows)
    build_ms = (time.perf_counter() - start) * 1000
    print(f"Indexed {index.total:,} live cards from {len(rows):,} rows "
          f"in {os.path.relpath(path)} ({build_ms:.0f} ms)")
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
