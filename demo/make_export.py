#!/usr/bin/env python3
"""Make a demo card export with the same columns as the real one.

The real export is private, so this builds a stand-in from the repository's own
dictionaries: real group prefixes, real event keys, real segment keys, so every category
code parses exactly as a real one does.

The point of the demo is the hard case, so card text is written the way real card text is:
about 40% of cards have an oblique title that never names the occasion ("Sorry I'm Late!",
"Thinking Of You"), tags are sparse, and descriptions often say nothing searchable. Those
are the cards keyword search cannot find and intent can.
"""
import csv
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "intent"))

from build_intent_table import EVENT_PREFIXES, Dictionaries  # noqa: E402

random.seed(20260930)

HEADER = ["card_id", "card_title", "card_description", "card_tags", "q1_value",
          "status_id", "invalid_card", "views", "card_type", "created_date"]

DIRECT = [
    "Happy {occasion}!", "Happy {occasion} {to}", "{occasion} Wishes",
    "{occasion} Wishes For {title_recipient}", "Warm {occasion} Wishes",
    "A Special {occasion} Card", "{occasion} Greetings {to}", "Wishing You A {occasion}",
    "{occasion} Joy", "For You On {occasion}", "My {occasion} Wish {to}",
]

# Cards whose own words never name the occasion. These are the reason intent exists.
OBLIQUE = {
    "belated": ["Sorry I'm Late!", "Better Late Than Never", "Oops, Time Ran Away",
                "Late But Heartfelt", "I Did Not Forget You", "Fashionably Late"],
    "get_well": ["Thinking Of You", "Hoping For Better Days", "Here For You",
                 "Rest Up, Recover Well", "Feel Better Fast"],
    "sympathy": ["Thinking Of You", "With Deepest Care", "Holding You In My Thoughts",
                 "No Words, Just Love"],
    "farewell": ["Until We Meet Again", "A New Chapter Ahead", "Off You Go",
                 "The Door Stays Open"],
    "milestone": ["What A Year To Celebrate", "Look How Far You've Come",
                  "A Very Big Number", "Cheers To The Big One"],
    "congratulations": ["You Did It!", "Take A Bow", "Well Earned", "Proud Of You"],
    None: ["Sending Sunshine Your Way", "Thinking Of You Today", "Just Because",
           "From My Heart To Yours", "A Little Something For You", "Made You Smile?",
           "Something Warm For You", "Because You Matter", "Caught You Looking",
           "A Quiet Moment For You", "Hello From Me", "You Came To Mind"],
}

DESCRIPTIONS = [
    "A warm card to send to someone who matters.",
    "Animated greeting with music and a personal message.",
    "Send this along with a few words of your own.",
    "A gentle card for a moment that deserves one.",
    "Bright colours, kind words, and a smile at the end.",
    "Let them know you were thinking of them.",
    "Wishing you all the best, from me to you.",
    "A short animation that ends on your message.",
]
DIRECT_DESCRIPTIONS = [
    "A {occasion} card to send today.",
    "Celebrate {occasion} with this animated greeting.",
    "Wishing you a wonderful {occasion}.",
    "For {occasion}, with warm wishes.",
]

FORMATS = ["animated"] * 5 + ["video"] * 3 + ["static"] * 3 + ["musical"] * 2
TO = {"mother": "Mom", "father": "Dad", "sister": "Sis", "brother": "Bro",
      "husband": "Dear Husband", "wife": "Dear Wife", "friend": "My Friend",
      "family": "The Family", "boss": "Boss", "teacher": "Teacher"}


def titlecase(term):
    return " ".join(w.capitalize() for w in term.replace("_", " ").split())


# Real codes, taken from intent/test_intent_lookup.py, which asserts on them because they
# are in the real catalogue. Random composition alone leaves them out, and then the demo
# judges ranking against a catalogue missing its own right answers.
SEED_CODES = [
    "birth_belated", "birth_bronsis", "birth_hubbywife", "birth_milestone", "birth_frnd",
    "birth_forher", "birth_forhim", "birth_momndad", "birth_kids", "birth_funny",
    "anniv_ouranniversary_forhim", "anniv_ouranniversary_forher", "anniv_hubbywife",
    "eoct_diwali_family", "eoct_diwali", "eoct_hallo", "edec_c_family", "edec_c",
    "w_spanish_birthday", "w_spanish_teachersday", "w_french_birthday",
    "emay_teachersday", "esep_teachersdayindia", "emay_mothersday", "ejun_fathersday",
    "insp_sympathy", "pet_lossofpet", "gen_getwell", "insp_recovery", "pet_getwell",
    "bus_farewell", "congrats_graduation", "thank_teacher", "love_hubbywife",
    "friend_friends", "wed_congratulations", "eaug_iforgotday",
]


def build_codes(d, want=1200):
    """Composes category codes from real dictionary keys, so every one parses."""
    segments = list(d.segments)
    events = [k for k in d.events if k.isalpha()]
    languages = ["spanish", "french", "german", "hindi", "italian", "russian"]
    plain = [p for p in d.groups if p not in EVENT_PREFIXES and p != "w"]
    event_prefixes = sorted(EVENT_PREFIXES)

    codes = set(SEED_CODES)
    while len(codes) < want:
        roll = random.random()
        if roll < 0.35:
            codes.add(f"{random.choice(plain)}_{random.choice(segments)}")
        elif roll < 0.55:
            codes.add(f"{random.choice(plain)}_{random.choice(segments)}"
                      f"_{random.choice(segments)}")
        elif roll < 0.80:
            codes.add(f"{random.choice(event_prefixes)}_{random.choice(events)}")
        elif roll < 0.92:
            codes.add(f"{random.choice(event_prefixes)}_{random.choice(events)}"
                      f"_{random.choice(segments)}")
        else:
            codes.add(f"w_{random.choice(languages)}_{random.choice(events)}")
    return sorted(codes)


def facets(code, d):
    """The occasion and recipient words a code carries, for writing plausible card text."""
    parts = [p for p in code.split("_") if p]
    prefix, rest = parts[0], parts[1:]
    group, group_terms = d.groups.get(prefix, (prefix, []))
    occasion, recipients, terms = "", [], list(group_terms)
    if prefix == "w" and rest:
        rest = rest[1:]
    for k in range(len(rest), 0, -1):
        key = "_".join(rest[:k])
        if key in d.events:
            occasion = d.events[key][0]
            terms += d.events[key][1]
            rest = rest[k:]
            break
    for token in rest:
        terms += d.segments.get(token, [])
    for term in terms:
        if d.dimension.get(term) == "recipient":
            recipients.append(term)
        elif not occasion and d.dimension.get(term, "occasion") == "occasion":
            occasion = titlecase(term)
    return group, occasion or group, sorted(set(recipients)), sorted(set(terms))


def oblique_pool(terms):
    for key in ("belated", "get_well", "sympathy", "farewell", "milestone",
                "congratulations"):
        if key in terms:
            return OBLIQUE[key]
    return OBLIQUE[None]


def make_card(index, code, d):
    group, occasion, recipients, terms = facets(code, d)
    recipient = recipients[0] if recipients else ""
    to = TO.get(recipient, titlecase(recipient)) if recipient else "To You"

    if random.random() < 0.6:
        title = random.choice(DIRECT).format(occasion=occasion, to=to,
                                             title_recipient=titlecase(recipient) or "You")
    else:
        title = random.choice(oblique_pool(terms))

    if random.random() < 0.45:
        description = random.choice(DIRECT_DESCRIPTIONS).format(occasion=occasion.lower())
    else:
        description = random.choice(DESCRIPTIONS)

    # Tags are sparse and inconsistent, as editorial fields always are.
    roll = random.random()
    if roll < 0.35:
        tags = ""
    elif roll < 0.7:
        tags = " ".join(random.sample(terms, min(len(terms), 2))).replace("_", " ")
    else:
        tags = " ".join(terms).replace("_", " ")

    popular = 1 if "birthday" in terms else 0
    views = int(random.paretovariate(1.3) * (900 if popular else 220))
    year = random.choice([2019, 2020, 2021, 2022, 2023, 2023, 2024, 2024, 2025])
    return [f"C{index:06d}", title, description, tags, code, "1", "0", str(views),
            random.choice(FORMATS),
            f"{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"]


def main(out_path, cards_wanted=14000):
    d = Dictionaries()
    codes = build_codes(d)
    rows, index = [], 0
    while len(rows) < cards_wanted:
        for code in codes:
            if len(rows) >= cards_wanted:
                break
            for _ in range(random.randint(2, 18)):
                if len(rows) >= cards_wanted:
                    break
                index += 1
                rows.append(make_card(index, code, d))
    # Some cards are retired or flagged, as in any real export.
    for row in random.sample(rows, len(rows) // 12):
        row[5] = "0"
    for row in random.sample(rows, len(rows) // 40):
        row[6] = "1"
    random.shuffle(rows)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)
    live = sum(1 for r in rows if r[5] == "1" and r[6] != "1")
    print(f"{len(rows):,} rows, {live:,} live, {len(codes):,} category codes -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "demo_export.csv"))
