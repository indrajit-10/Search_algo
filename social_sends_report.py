#!/usr/bin/env python3
"""
Daily social-sends report, grouped by card category.

    python3 social_sends_report.py                          # finds both files in data/
    python3 social_sends_report.py sends.tsv                # name the send log
    python3 social_sends_report.py sends.tsv --csv report/  # also write the tables as CSV

The send log records which card went out. It does not record what the card was
about, and no column in it ever will. The card export does, in `q1_value`, so
the whole report is that one join:

    social send  --card_id-->  card_number  --q1_value-->  category

A q1_value is written `category_subcategory` - everything before the first
underscore is the category, everything after it is the sub-category, however
many underscores the sub-category itself carries: `birth_happybirthday` is
Birthday / happybirthday, `eaug_friendshipday_happy` is August / friendshipday
_happy, `anniv_ouranniversary_forher` is Anniversary / ouranniversary_forher.

Both halves are worth counting and they answer different questions, so both
get a table. The category answers "we sent 373 birthday cards". The
sub-category answers "and 308 of them were the plain happy-birthday kind".
--detail puts the two together: every category in full, with its own
sub-categories, channels and countries underneath it.

Sixteen categories are reported under their own name (CORE_PREFIXES below).
Every other prefix - the twelve month codes, `wed`, and anything added to the
catalogue later - is counted as Events cards, a single bucket. --split-events
turns that off and reports all 29 prefixes separately.

Either file can be .xlsx, .csv, .tsv, .csv.gz or .zip. Excel is read directly
rather than asking for a CSV save first: this report joins two numeric columns
and a slug, none of which Excel can damage, and a conversion step that exists
only to be forgotten is a conversion step worth removing. (The full card
export is a different matter - see load_rows() in search_engine.py for what
Excel does to card_title and card_created_date.)

Standard library only, like everything else here.
"""

import argparse
import collections
import csv
import gzip
import io
import os
import re
import sys
import xml.etree.ElementTree as ElementTree
import zipfile

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# The bucket every non-core category is counted under. Not a q1_value prefix -
# no card carries it - so it cannot collide with a real one.
EVENTS = "events"

# The 29 q1_value categories in the catalogue, in plain English. Only the
# twelve month codes genuinely need translating - "eaug" shares no letters with
# "August" - but a report is read by people who do not have the slug list in
# their head, so the rest are spelled out too. An unlisted category is printed
# as-is rather than guessed at: a new occasion should look new, not wrong.
PREFIX_LABEL = {
    "birth":   "Birthday",
    "anniv":   "Anniversary",
    "love":    "Love & romance",
    "wed":     "Wedding",
    "friend":  "Friendship",
    "fkt":     "Family & loved ones",
    "gen":     "Everyday greetings",
    "intouch": "Keeping in touch",
    "insp":    "Inspirational & sympathy",
    "thank":   "Thank you",
    "congrats": "Congratulations",
    "cute":    "Cute",
    "pet":     "Pets",
    "flwr":    "Flowers",
    "bus":     "Business & work",
    "invp":    "Invitations & parties",
    "w":       "World languages",
    "ejan":    "January occasions",
    "efeb":    "February occasions",
    "emar":    "March occasions",
    "eapr":    "April occasions",
    "emay":    "May occasions",
    "ejun":    "June occasions",
    "ejul":    "July occasions",
    "eaug":    "August occasions",
    "esep":    "September occasions",
    "eoct":    "October occasions",
    "enov":    "November occasions",
    "edec":    "December occasions",
    EVENTS:    "Events cards",
}

# The sixteen categories that are reported under their own name. Everything
# else - the twelve month codes, `wed`, and any category added to the
# catalogue after this list was written - is counted as Events cards. The list
# is an allow-list on purpose: a new occasion prefix lands in Events rather
# than appearing as a category nobody asked for, and moving it out is one line.
CORE_PREFIXES = frozenset({
    "birth", "thank", "gen", "love", "anniv", "insp", "cute", "congrats",
    "fkt", "bus", "pet", "w", "flwr", "friend", "intouch", "invp",
})

# Column names differ between exports - phpMyAdmin, a BI tool and a hand-saved
# spreadsheet all spell the same field differently. Match on meaning, not on
# the exact header string, so a renamed column is not a silent empty table.
ALIASES = {
    "card_id":      ("card_id", "cardid", "card", "card_number", "cardnumber"),
    "share_type":   ("share_type", "sharetype", "type", "channel"),
    "share_result": ("share_result", "shareresult", "result"),
    "error_description": ("error_description", "error", "errordescription"),
    "ip":           ("ip", "ip_address", "ipaddress"),
    "country":      ("country", "country_code", "countrycode"),
    "ua":           ("ua", "user_agent", "useragent", "device"),
    "date":         ("date", "date_sent", "datesent", "sent_at", "created"),
    "date_added":   ("date_added", "dateadded", "added"),
    "api_key":      ("api_key", "apikey", "key"),
    "status":       ("status", "status_id", "statusid"),
    "id":           ("id", "send_id", "sendid"),
}

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


# --------------------------------------------------------------------------
# Reading files
# --------------------------------------------------------------------------

XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XL_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_xlsx(path, sheet=None):
    """
    Read the first worksheet of an .xlsx into a list of dicts.

    An .xlsx is a zip of XML, and both are in the standard library, so this is
    a few dozen lines rather than a dependency. It covers what a database
    export contains - shared strings, inline strings, numbers, and gaps where
    a cell was never written - and nothing else. Dates come back as the raw
    serial number, which is why the send log's own text timestamps are used
    for the date and this reader is only ever pointed at the card list.
    """
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{XL_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{XL_NS}t")))

        names = sorted(n for n in archive.namelist()
                       if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not names:
            raise SystemExit(f"{path} has no worksheets.")
        target = names[0]
        if sheet is not None:
            wanted = f"xl/worksheets/sheet{sheet}.xml"
            if wanted not in names:
                raise SystemExit(f"{path} has no sheet {sheet}: {names}")
            target = wanted

        grid = []
        root = ElementTree.fromstring(archive.read(target))
        for row in root.iter(f"{XL_NS}row"):
            cells = {}
            for cell in row.findall(f"{XL_NS}c"):
                column = _column_index(cell.get("r") or "")
                kind = cell.get("t")
                if kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter(f"{XL_NS}t"))
                else:
                    value = cell.find(f"{XL_NS}v")
                    text = "" if value is None or value.text is None else value.text
                    if kind == "s" and text:
                        index = int(text)
                        text = shared[index] if index < len(shared) else ""
                cells[column] = text.strip()
            grid.append(cells)

    grid = [row for row in grid if any(v for v in row.values())]
    if not grid:
        return []
    width = max(max(row) for row in grid if row) + 1
    header = [grid[0].get(i, f"column{i}") or f"column{i}" for i in range(width)]
    return [{header[i]: row.get(i, "") for i in range(width)} for row in grid[1:]]


def _column_index(reference):
    """A1 -> 0, B7 -> 1, AA3 -> 26. Cells arrive sparse and out of order."""
    index = 0
    for char in reference:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - 64)
    return index - 1


def read_delimited(text):
    """
    Read CSV or TSV, whichever it is.

    Sniffing on the header alone is enough here and safer than sniffing on the
    body: these files carry free-text user agents and error descriptions, and
    a comma inside one of those has talked csv.Sniffer into the wrong answer
    before.
    """
    header = text.split("\n", 1)[0]
    delimiter = "\t" if header.count("\t") >= header.count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def read_pasted(text):
    """
    Read the send log in the shape it has when copied out of a database
    browser: every cell on its own line, no header, one blank-ish line where
    error_description is empty.

    This is the format that actually lands in a chat window, so it is worth
    reading, but it is also the format with nothing in it to say which line is
    which - and a report built from misaligned columns is confidently wrong
    rather than obviously broken. So the columns are found by anchor instead
    of by counting:

        ... ip, country, ua, DATE, DATE_ADDED, api_key, status

    The two timestamps are unmistakable and always adjacent, which fixes the
    end of every record: ip, country and ua are the three lines before them,
    api_key and status the two after. The front is fixed by the first line
    that is not a number, which is share_type - everything numeric before it
    is the id, optionally preceded by the row counter, and share_result
    follows it. Whatever is left over sits in the middle, which is exactly
    where error_description belongs, however many lines it runs to.

    Anything that does not fit that shape stops the run rather than producing
    a plausible table.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line != ""]
    # A paste normally carries the header row, tab-separated, above the
    # values. Drop it, and any repeat of it from a paginated copy.
    while lines and _looks_like_header(lines[0]):
        lines.pop(0)
    lines = [line for line in lines if not _is_header_row(line)]

    pairs = [i for i in range(len(lines) - 1)
             if TIMESTAMP.match(lines[i]) and TIMESTAMP.match(lines[i + 1])]
    if not pairs:
        return []

    rows, start = [], 0
    for i in pairs:
        head = lines[start:i]
        tail = lines[i:i + 4]
        start = i + 4
        if len(tail) < 4 or len(head) < 6:
            raise SystemExit(
                "Could not read the pasted send log: a record near "
                f"{lines[i]} has {len(head)} fields before its timestamps and "
                f"{len(tail)} after. Expected at least 6 and exactly 4.\n"
                "  Export it as CSV or TSV instead - that always reads.")
        body = head[:-3]
        split = next((j for j, value in enumerate(body) if not value.isdigit()), -1)
        if split < 2:
            raise SystemExit(
                "Could not read the pasted send log: the record sent "
                f"{tail[0]} has no id and card id in front of its share type.\n"
                f"  Read as: {body}\n"
                "  Export it as CSV or TSV instead - that always reads.")
        rows.append({
            "id": body[split - 2], "card_id": body[split - 1],
            "share_type": body[split],
            "share_result": body[split + 1] if len(body) > split + 1 else "",
            "error_description": " ".join(body[split + 2:]),
            "ip": head[-3], "country": head[-2], "ua": head[-1],
            "date": tail[0], "date_added": tail[1],
            "api_key": tail[2], "status": tail[3],
        })

    bad = [r for r in rows if not r["card_id"].isdigit()]
    if bad:
        raise SystemExit(
            "Could not read the pasted send log: card_id came out as "
            f"{bad[0]['card_id']!r} on the record sent {bad[0]['date']}.\n"
            "  Export it as CSV or TSV instead - that always reads.")
    return rows


def _fields(line):
    return [field for field in re.split(r"[\t,]", line) if field.strip()]


def _is_header_row(line):
    """A line naming the columns, wherever it turns up in a paginated paste."""
    lowered = line.lower()
    return "card_id" in lowered and "share_type" in lowered


def _looks_like_header(line):
    """Leading junk above the values: the column names, in any spelling."""
    return _is_header_row(line) or len(_fields(line)) >= 3


def read_any(path):
    """Read a table from whatever the file happens to be."""
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return read_xlsx(path)
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.lower().endswith((".csv", ".tsv"))
                     and not n.startswith("__MACOSX")]
            if not names:
                raise SystemExit(f"{path} contains no csv or tsv: {archive.namelist()}")
            return read_delimited(archive.read(names[0]).decode("utf-8", "replace"))
    if lower.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return read_delimited(handle.read())
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    # Which shape is it? Not the extension - a paste gets saved as .txt, .csv
    # and .tsv about equally often. Judge it on the body, not the first line:
    # a paste usually keeps the tab-separated header row above one value per
    # line, so the header alone says "delimited" when the file is not. A real
    # delimited file has three or more fields on its body lines too.
    body = [line for line in text.splitlines()[1:] if line.strip()][:20]
    if body and max(len(_fields(line)) for line in body) < 3:
        pasted = read_pasted(text)
        if pasted:
            return pasted
    return read_delimited(text)


TOTAL_COLUMN = ("total", "grand total", "sum", "all")


def looks_like_pivot(rows):
    """True for a card x channel matrix rather than one row per send.

    The shape is a card number, a column per channel holding a count, and a
    Total. Recognised by that Total plus two or more columns that are numeric
    all the way down - a send log has dates, IPs and result words in it, so it
    cannot pass this test by accident.
    """
    # One data row is still a pivot. Requiring two would send a quiet day's
    # single-card export down the send-log path, where its channel columns
    # mean nothing and every send comes out on channel "unknown".
    if not rows:
        return False
    fields = [(f or "").strip() for f in rows[0]]
    lowered = [f.lower() for f in fields]
    if not any(f in TOTAL_COLUMN for f in lowered):
        return False
    if not any(f.replace(" ", "").replace("_", "") in
               ("cardnumber", "cardid", "card", "cardno") for f in lowered):
        return False
    numeric = 0
    for field in fields:
        if field.lower() in TOTAL_COLUMN:
            continue
        values = [str(row.get(field, "")).strip() for row in rows]
        if values and all(v.lstrip("-").isdigit() for v in values if v):
            numeric += 1
    return numeric >= 2


def expand_pivot(rows):
    """A card x channel matrix -> one row per send, which is what build() eats.

    A cell of 4 becomes four sends of that card on that channel. The counts
    are all the file has: no timestamp, no IP, no country. Those stay empty
    rather than being invented, and the report leaves out the lines it cannot
    answer.
    """
    fields = [(f or "").strip() for f in rows[0]]
    lowered = {f: f.lower() for f in fields}
    card_field = next(f for f in fields if lowered[f].replace(" ", "").replace("_", "")
                      in ("cardnumber", "cardid", "card", "cardno"))
    channels = [f for f in fields
                if f != card_field and lowered[f] not in TOTAL_COLUMN
                and not lowered[f].startswith(("sl", "s.no", "#"))]
    total_field = next((f for f in fields if lowered[f] in TOTAL_COLUMN), None)

    out = []
    for row in rows:
        card = str(row.get(card_field, "")).strip()
        if not card:
            continue
        counted = 0
        for channel in channels:
            value = str(row.get(channel, "")).strip()
            if not value.isdigit():
                continue
            counted += int(value)
            for _ in range(int(value)):
                out.append({"card_id": card, "share_type": channel,
                            "share_result": "success"})
        # The Total column is the file's own arithmetic. Disagreeing with it is
        # a transcription error, not a rounding one, so it stops here.
        if total_field:
            stated = str(row.get(total_field, "")).strip()
            if stated.isdigit() and int(stated) != counted:
                raise SystemExit(
                    f"Card {card} adds up to {counted} across its channels but "
                    f"its Total column says {stated}.\n"
                    "  The file is inconsistent with itself - fix it or drop "
                    "the Total column.")
    return out


def normalise(rows):
    """
    Rename columns to the names the rest of this file uses, and drop the
    row-number column that spreadsheet exports carry.
    """
    if not rows:
        return rows
    lookup = {}
    for field in rows[0]:
        key = (field or "").strip().lower().replace(" ", "_").replace(".", "")
        for canonical, spellings in ALIASES.items():
            if key in spellings and canonical not in lookup.values():
                lookup[field] = canonical
                break
    out = []
    for row in rows:
        clean = {}
        for field, value in row.items():
            name = lookup.get(field)
            if name:
                clean[name] = (value or "").strip()
        out.append(clean)
    return out


# --------------------------------------------------------------------------
# Finding the two files
# --------------------------------------------------------------------------

CATALOGUE_NAMES = ("ACTIVE_CARDS.xlsx", "active_cards.xlsx", "active_cards.csv",
                   "card_database.csv", "card_database.csv.gz", "card_database.zip")


def find_catalogue(explicit=None):
    """The card list: anything with a card number and a q1_value in it."""
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f"No such file: {explicit}")
        return explicit
    for name in CATALOGUE_NAMES:
        candidate = os.path.join(DATA_DIR, name)
        if os.path.exists(candidate):
            return candidate
    raise SystemExit(
        "No card list found, and without one there are no categories to report.\n\n"
        f"  Put it here:  {os.path.join('data', 'ACTIVE_CARDS.xlsx')}\n"
        "  It needs a card number column and a q1_value column. The ACTIVE_CARDS\n"
        "  sheet is exactly that; a full card_database.csv works too.\n\n"
        "  Or name it:  python3 social_sends_report.py sends.tsv --cards cards.xlsx")


def find_sends(explicit=None):
    """
    The send log. Today's file is whichever dated one in data/ sorts last, so
    a folder of daily exports needs no argument.

    A dated name wins over an undated one whatever the alphabet says. Once a
    second kind of export lands in data/ - a card x channel pivot, a one-off
    extract - "the newest name" would quietly change which file the bare
    command reports on, and a report on the wrong file is worse than an error.
    """
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f"No such file: {explicit}")
        return explicit
    if os.path.isdir(DATA_DIR):
        found = sorted(f for f in os.listdir(DATA_DIR)
                       if not f.startswith(".")
                       and f not in CATALOGUE_NAMES
                       and re.search(r"send|share|social", f, re.I)
                       and f.lower().endswith((".tsv", ".csv", ".txt", ".xlsx",
                                               ".csv.gz", ".zip")))
        dated = [f for f in found if re.search(r"\d{4}-\d{2}-\d{2}", f)]
        if dated or found:
            return os.path.join(DATA_DIR, (dated or found)[-1])
    raise SystemExit(
        "No send log found.\n\n"
        f"  Put it here:  {os.path.join('data', 'social_sends_YYYY-MM-DD.tsv')}\n"
        "  .tsv, .csv, .xlsx and a copy-paste out of the database browser all read.\n\n"
        "  Or name it:  python3 social_sends_report.py /path/to/sends.tsv")


def load_categories(path):
    """card_number -> q1_value, from whichever card file was found."""
    rows = read_any(path)
    if not rows:
        raise SystemExit(f"{path} is empty.")
    fields = {(f or "").strip().lower(): f for f in rows[0]}
    number = next((fields[k] for k in ("card_number", "card_id", "cardnumber", "number")
                   if k in fields), None)
    category = next((fields[k] for k in ("q1_value", "q1", "category")
                     if k in fields), None)
    if not number or not category:
        raise SystemExit(
            f"{path} does not look like a card list.\n"
            f"  Columns found: {', '.join(str(f) for f in rows[0])}\n"
            "  Needs a card number column and a q1_value column.")
    return {str(row[number]).strip(): str(row[category]).strip()
            for row in rows if str(row.get(number) or "").strip()}


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def split_q1(q1):
    """`category_subcategory` -> ("category", "subcategory").

    On the first underscore only. A sub-category is allowed to carry more of
    them - `eaug_friendshipday_happy` is one August sub-category, not two - so
    splitting on every underscore would invent categories that do not exist.
    """
    category, _, subcategory = (q1 or "").partition("_")
    return category, subcategory


def bucket_of(q1, split_events=False):
    """The category a q1_value is reported under.

    Its own prefix when that prefix is one of the sixteen core categories,
    EVENTS otherwise. --split-events turns the bucketing off and reports every
    prefix under its own name, which is how the catalogue is actually keyed.
    """
    prefix = split_q1(q1)[0]
    if split_events or prefix in CORE_PREFIXES:
        return prefix
    return EVENTS


def label_of(q1, split_events=False):
    """The plain-English category a q1_value is reported under."""
    bucket = bucket_of(q1, split_events)
    return PREFIX_LABEL.get(bucket, bucket)


def sub_label(bucket, q1):
    """How a sub-category is written under its category.

    Under a real category the prefix is redundant - it is the heading - so
    `birth_happybirthday` reads `happybirthday`. Under Events cards the prefix
    is the only thing separating an August card from a December one, so the
    whole slug stays.
    """
    return q1 if bucket == EVENTS else split_q1(q1)[1]


class Tally:
    """Counts for one category: sends, which cards, which senders, by channel."""

    def __init__(self):
        self.sends = 0
        self.cards = set()
        self.senders = set()
        self.channels = collections.Counter()
        self.countries = collections.Counter()
        self.subcategories = collections.Counter()
        # Which real q1 prefixes fed this bucket. Only interesting for Events,
        # where the answer is the whole point of the bucket.
        self.sources = collections.Counter()
        # Which file - which sharing surface - each send came from. One entry
        # when a single file was read, which is the ordinary case.
        self.surfaces = collections.Counter()


def build(sends, categories, split_events=False):
    """Join every send to its category and count. Nothing is dropped silently."""
    report = {
        "sends": sends,
        "split_events": split_events,
        # A card x channel pivot carries counts and nothing else. Whether the
        # file had senders and countries in it decides which lines the report
        # prints, so that an absent column never reads as "1 sender".
        "has_senders": False,
        "has_countries": False,
        "occasions": collections.defaultdict(Tally),
        "subcategories": collections.Counter(),
        "subcategory_cards": collections.defaultdict(set),
        "subcategory_senders": collections.defaultdict(set),
        "card_category": {},
        "card_senders": collections.defaultdict(set),
        "channels": collections.Counter(),
        "countries": collections.Counter(),
        "devices": collections.Counter(),
        "api_keys": collections.Counter(),
        "cards": collections.Counter(),
        "dates": collections.Counter(),
        "failures": [],
        "unmatched": collections.Counter(),
        "senders": set(),
        # A cumulative run reads more than one file: the app's own share sheet
        # and the website's are the same product counted on different surfaces,
        # and they add up. Kept separately as well as summed, because a total
        # that cannot be broken back down is a total nobody trusts.
        "surfaces": collections.Counter(),
        "surface_channels": collections.defaultdict(collections.Counter),
        "surface_cards": collections.defaultdict(set),
        "surface_unmatched": collections.Counter(),
        "sender_surfaces": set(),
    }
    for row in sends:
        card = row.get("card_id", "")
        channel = (row.get("share_type") or "unknown").strip()
        result = (row.get("share_result") or "").strip().lower()
        country = (row.get("country") or "").strip().upper()
        sender = row.get("ip", "")

        surface = row.get("_surface") or "all shares"
        report["surfaces"][surface] += 1
        report["surface_channels"][surface][channel] += 1
        report["surface_cards"][surface].add(card)
        report["channels"][channel] += 1
        # A file without a country column has no unknown country in it - it has
        # no country question. Counting a blank would put "??" in the table as
        # though it were a place.
        if country:
            report["countries"][country] += 1
        report["devices"][(row.get("ua") or "unknown").replace("-User-Agent", "")] += 1
        report["api_keys"][row.get("api_key") or "unknown"] += 1
        report["cards"][card] += 1
        report["dates"][(row.get("date") or "")[:10]] += 1
        # An absent IP is not a sender. Counting the empty string would add a
        # phantom sender to every category a pivot touches, and in a cumulative
        # run it would sit right beside 254 real ones.
        if sender:
            report["has_senders"] = True
            report["sender_surfaces"].add(surface)
            report["senders"].add(sender)
            report["card_senders"][card].add(sender)
        if country:
            report["has_countries"] = True
        if result and result != "success":
            report["failures"].append(row)

        category = categories.get(card)
        report["card_category"][card] = category or ""
        if not category:
            # An unknown card_id is a real finding, not a rounding error: it
            # means a retired or mistyped card is still being shared. Counted
            # and printed, never folded into an "other" bucket.
            report["unmatched"][card] += 1
            report["surface_unmatched"][surface] += 1
            continue

        prefix = bucket_of(category, split_events)
        tally = report["occasions"][prefix]
        tally.sends += 1
        tally.cards.add(card)
        if sender:
            tally.senders.add(sender)
        tally.channels[channel] += 1
        if country:
            tally.countries[country] += 1
        tally.subcategories[category] += 1
        tally.sources[split_q1(category)[0]] += 1
        tally.surfaces[surface] += 1
        report["subcategories"][category] += 1
        report["subcategory_cards"][category].add(card)
        if sender:
            report["subcategory_senders"][category].add(sender)
    return report


def percent(part, whole):
    return f"{(100.0 * part / whole):5.1f}%" if whole else "    -"


def plural(count, noun):
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def note(add, label, text, width):
    """Emit a sentence under a label, folded on spaces."""
    room = max(20, width - len(label))
    indent, line = label, ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > room:
            add(indent + line)
            indent, line = " " * len(label), word
        else:
            line = f"{line} {word}".strip()
    if line:
        add(indent + line)


def wrap(add, label, items, width):
    """Emit `label: item, item, ...`, folding under the label when it is long.

    Breaks between items, never inside one - "SMS 6 (6.4%)" split across two
    lines reads as two different numbers.
    """
    room = width - len(label) - 1          # -1 leaves space for the line's comma
    lines, current = [], ""
    for item in items:
        candidate = f"{current}, {item}" if current else str(item)
        if current and len(candidate) > room:
            lines.append(current)
            current = str(item)
        else:
            current = candidate
    lines.append(current)
    for i, line in enumerate(lines):
        indent = label if i == 0 else " " * len(label)
        add(indent + line + ("," if i < len(lines) - 1 else ""))


def render(report, top=15, width=78):
    """The report as text. Widths are fixed so columns line up in a terminal."""
    out = []
    add = out.append
    sends = report["sends"]
    total = len(sends)
    matched = total - sum(report["unmatched"].values())
    dates = sorted(d for d in report["dates"] if d)
    span = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}" if dates else "unknown"
    channels = report["channels"]

    surfaces = [name for name, _ in report["surfaces"].most_common()]
    cumulative = len(surfaces) > 1

    add("=" * width)
    add(("SOCIAL SENDS - CUMULATIVE CATEGORY REPORT" if cumulative
         else "SOCIAL SENDS - CATEGORY REPORT").center(width))
    add("=" * width)
    add(f"Date            {span}")
    add(f"Cards sent      {total:,} sends of {len(report['cards']):,} different cards")
    if report["has_senders"]:
        add(f"Senders         {len(report['senders']):,} distinct IPs "
            f"in {len(report['countries'])} countries")
        missing = [n for n in surfaces if n not in report["sender_surfaces"]]
        if missing:
            # Otherwise the sender and country counts silently describe a
            # subset of the sends and read as though they described all of them.
            covered = sum(report["surfaces"][n] for n in report["sender_surfaces"])
            note(add, " " * 16,
                 f"addresses and countries come from {plural(covered, 'send')} "
                 f"only - {', '.join(missing)} carries neither", width)
    else:
        add("Senders         not in this file - it carries counts, not sends")
    add(f"Channels        " + ", ".join(f"{name} {count}"
                                        for name, count in channels.most_common()))
    if report["failures"]:
        add(f"Failed          {len(report['failures']):,} sends did not report success")
    else:
        add("Failed          none - every send reported success")
    if report["unmatched"]:
        add(f"Uncategorised   {sum(report['unmatched'].values()):,} sends of "
            f"{len(report['unmatched']):,} cards not in the card list")
    add("")

    if cumulative:
        add("-" * width)
        add("BY SURFACE")
        add("-" * width)
        add(f"{'Surface':<26}{'Sends':>7}{'Share':>8}{'Cards':>7}  {'Top channel':<18}")
        for name in surfaces:
            count = report["surfaces"][name]
            channel, n = report["surface_channels"][name].most_common(1)[0]
            add(f"{name:<26}{count:>7,}{percent(count, total):>8}"
                f"{len(report['surface_cards'][name]):>7}"
                f"  {channel} {percent(n, count).strip()}")
        add(f"{'TOTAL':<26}{total:>7,}{'100.0%':>8}{len(report['cards']):>7}")
        add("")

    # The table the report exists for.
    add("-" * width)
    add("BY CATEGORY")
    add("-" * width)
    add(f"{'Category':<26}{'Sends':>7}{'Share':>8}{'Cards':>7}{'Senders':>9}"
        f"  {'Top channel':<18}")
    order = sorted(report["occasions"].items(),
                   key=lambda kv: (-kv[1].sends, kv[0]))
    for prefix, tally in order:
        label = PREFIX_LABEL.get(prefix, prefix)
        channel, count = tally.channels.most_common(1)[0]
        senders = f"{len(tally.senders):,}" if report["has_senders"] else "-"
        add(f"{label:<26}{tally.sends:>7,}{percent(tally.sends, matched):>8}"
            f"{len(tally.cards):>7}{senders:>9}"
            f"  {channel} {percent(count, tally.sends).strip()}")
    all_senders = f"{len(report['senders']):,}" if report["has_senders"] else "-"
    add(f"{'TOTAL':<26}{matched:>7,}{'100.0%':>8}"
        f"{len(set().union(*(t.cards for _, t in order)) if order else set()):>7}"
        f"{all_senders:>9}")
    add("")

    # The cumulative total, broken back down the way it was added up.
    if cumulative:
        add("-" * width)
        add("CATEGORY BY SURFACE")
        add("-" * width)
        column = max(10, (width - 34) // (len(surfaces) + 1))
        # Truncation is marked, not silent: BY SURFACE above carries the full
        # names, so ".." says "look up there" rather than "this is the name".
        heads = [n if len(n) < column else n[:column - 3] + ".." for n in surfaces]
        add(f"{'Category':<26}"
            + "".join(f"{name:>{column}}" for name in heads)
            + f"{'Total':>{column}}{'Share':>8}")
        for prefix, tally in order:
            label = PREFIX_LABEL.get(prefix, prefix)
            add(f"{label:<26}"
                + "".join(f"{tally.surfaces.get(name, 0):>{column},}" for name in surfaces)
                + f"{tally.sends:>{column},}{percent(tally.sends, matched):>8}")
        add(f"{'TOTAL categorised':<26}"
            + "".join(f"{sum(t.surfaces.get(name, 0) for _, t in order):>{column},}"
                      for name in surfaces)
            + f"{matched:>{column},}{'100.0%':>8}")
        if report["unmatched"]:
            add(f"{'Not in the card list':<26}"
                + "".join(f"{report['surface_unmatched'].get(name, 0):>{column},}"
                          for name in surfaces)
                + f"{sum(report['unmatched'].values()):>{column},}{'':>8}")
        add(f"{'TOTAL read':<26}"
            + "".join(f"{report['surfaces'][name]:>{column},}" for name in surfaces)
            + f"{total:>{column},}{'':>8}")
        add("")

    # Same join, one level down.
    add("-" * width)
    add(f"BY SUB-CATEGORY  (top {top})")
    add("-" * width)
    add(f"{'Sub-category':<38}{'Sends':>7}{'Share':>8}{'Cards':>7}")
    for category, count in report["subcategories"].most_common(top):
        add(f"{category:<38}{count:>7,}{percent(count, matched):>8}"
            f"{len(report['subcategory_cards'][category]):>7}")
    remainder = len(report["subcategories"]) - top
    if remainder > 0:
        rest = matched - sum(n for _, n in report["subcategories"].most_common(top))
        add(f"{f'... and {remainder} more sub-categories':<38}{rest:>7,}"
            f"{percent(rest, matched):>8}")
    add("")

    # Category against channel: the same sends, cut the other way.
    names = [name for name, _ in channels.most_common()]
    add("-" * width)
    add("CATEGORY BY CHANNEL")
    add("-" * width)
    add(f"{'Category':<26}" + "".join(f"{name:>10}" for name in names) + f"{'Total':>10}")
    for prefix, tally in order:
        label = PREFIX_LABEL.get(prefix, prefix)
        add(f"{label:<26}"
            + "".join(f"{tally.channels.get(name, 0):>10,}" for name in names)
            + f"{tally.sends:>10,}")
    add(f"{'TOTAL':<26}"
        + "".join(f"{channels[name]:>10,}" for name in names)
        + f"{total:>10,}")
    add("")

    add("-" * width)
    add(f"MOST-SENT CARDS  (top {top})")
    add("-" * width)
    add(f"{'Card':<10}{'Sends':>7}{'Senders':>9}  {'Category':<40}")
    for card, count in report["cards"].most_common(top):
        category = report["card_category"].get(card) or "not in card list"
        add(f"{card:<10}{count:>7,}{len(report['card_senders'][card]):>9}"
            f"  {category:<40}")
    add("")

    add("-" * width)
    add("WHERE AND HOW")
    add("-" * width)
    columns = (("Country", report["countries"]), ("Device", report["devices"]),
               ("API key", report["api_keys"]))
    for title, counter in columns:
        top_items = counter.most_common(6)
        rest = sum(counter.values()) - sum(n for _, n in top_items)
        line = ", ".join(f"{name} {count:,}" for name, count in top_items)
        if rest:
            line += f", other {rest:,}"
        add(f"{title:<10}{line}")
    add("")

    if report["unmatched"]:
        add("-" * width)
        add("NOT IN THE CARD LIST")
        add("-" * width)
        add("These card ids were shared but are not in the card list, so they")
        add("could not be categorised. Usually a retired card still in circulation.")
        for card, count in report["unmatched"].most_common():
            add(f"  {card:<10}{count:>6,} sends")
        add("")

    if report["failures"]:
        add("-" * width)
        add("FAILED SENDS")
        add("-" * width)
        for row in report["failures"][:top]:
            add(f"  {row.get('date', ''):<20}{row.get('card_id', ''):<10}"
                f"{row.get('share_type', ''):<10}{row.get('share_result', '')}"
                f"  {row.get('error_description', '')}")
        if len(report["failures"]) > top:
            add(f"  ... and {len(report['failures']) - top} more")
        add("")

    return "\n".join(out)


def render_detail(report, width=78):
    """One block per category: its sub-categories, channels and countries.

    Nothing is truncated here. The top-N tables above answer "what was big
    today"; this section answers "what happened inside Birthday", and a
    category read in full with three of its sub-categories hidden would be
    answering neither.
    """
    out = []
    add = out.append
    matched = len(report["sends"]) - sum(report["unmatched"].values())
    order = sorted(report["occasions"].items(), key=lambda kv: (-kv[1].sends, kv[0]))

    add("=" * width)
    add("EVERY CATEGORY IN FULL".center(width))
    add("=" * width)
    add("Each category is the q1_value up to the first underscore; the rows")
    add("under it are the rest of the slug. Share of day is out of all")
    add(f"{matched:,} categorised sends.")
    add("")

    for prefix, tally in order:
        label = PREFIX_LABEL.get(prefix, prefix)
        slug = "any other prefix" if prefix == EVENTS else prefix + "_*"
        add("-" * width)
        add(f"{label.upper():<34}{slug:>18}"
            f"{tally.sends:>10,} sends{percent(tally.sends, matched):>10}")
        add("-" * width)
        add(f"  {plural(len(tally.cards), 'card')}"
            + (f", {plural(len(tally.senders), 'sender')}"
               if report["has_senders"] else ""))
        if len(report["surfaces"]) > 1:
            wrap(add, "  Surfaces   ",
                 [f"{name} {count} ({percent(count, tally.sends).strip()})"
                  for name, count in tally.surfaces.most_common()], width)
        if prefix == EVENTS:
            # Which real prefixes ended up here. Without this the bucket is a
            # number nobody can act on.
            wrap(add, "  Made up of ",
                 [f"{PREFIX_LABEL.get(name, name)} {count}"
                  for name, count in tally.sources.most_common()], width)
        wrap(add, "  Channels   ",
             [f"{name} {count} ({percent(count, tally.sends).strip()})"
              for name, count in tally.channels.most_common()], width)
        if report["has_countries"]:
            countries = tally.countries.most_common(8)
            items = [f"{name} {count}" for name, count in countries]
            rest = len(tally.countries) - len(countries)
            if rest:
                items.append(f"and {rest} more")
            wrap(add, "  Countries  ", items, width)
        add("")
        add(f"  {'Sub-category':<40}{'Sends':>6}{'Of cat':>8}{'Of day':>8}"
            f"{'Cards':>6}{'Senders':>8}")
        for q1, count in tally.subcategories.most_common():
            # Not truncated. Four slugs in the catalogue run past the column
            # and push their own row out by a character or two; a sub-category
            # you cannot tell apart from its neighbour is the worse outcome.
            subcategory = sub_label(prefix, q1) or "(no sub-category)"
            add(f"  {subcategory:<40}{count:>6,}"
                f"{percent(count, tally.sends):>8}{percent(count, matched):>8}"
                f"{len(report['subcategory_cards'][q1]):>6}"
                f"{len(report['subcategory_senders'][q1]):>8}")
        add(f"  {'TOTAL ' + label:<40}{tally.sends:>6,}"
            f"{'100.0%':>8}{percent(tally.sends, matched):>8}"
            f"{len(tally.cards):>6}{len(tally.senders):>8}")
        add("")

    add("-" * width)
    add(f"  {'TOTAL - every category':<40}{matched:>6,}"
        f"{'':>8}{'100.0%':>8}"
        f"{len(set().union(*(t.cards for _, t in order)) if order else set()):>6}"
        f"{len(report['senders']):>8}")
    add("-" * width)
    add("")
    return "\n".join(out)


def write_csv(report, directory, categories):
    """The same tables as CSV, for anyone who wants them in a spreadsheet."""
    os.makedirs(directory, exist_ok=True)
    matched = len(report["sends"]) - sum(report["unmatched"].values())
    written = []

    path = os.path.join(directory, "by_category.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "q1_prefix", "sends", "share_of_sends",
                         "distinct_cards", "distinct_senders"])
        for prefix, tally in sorted(report["occasions"].items(),
                                    key=lambda kv: -kv[1].sends):
            writer.writerow([PREFIX_LABEL.get(prefix, prefix), prefix, tally.sends,
                             round(100.0 * tally.sends / matched, 2) if matched else "",
                             len(tally.cards), len(tally.senders)])
        writer.writerow(["TOTAL", "", matched, 100.0 if matched else "",
                         len(report["cards"]), len(report["senders"])])
    written.append(path)

    # Only worth a file when there is more than one surface to compare.
    if len(report["surfaces"]) > 1:
        surfaces = [name for name, _ in report["surfaces"].most_common()]
        path = os.path.join(directory, "by_surface.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["category", "q1_prefix"] + surfaces
                            + ["total", "share_of_categorised"])
            for prefix, tally in sorted(report["occasions"].items(),
                                        key=lambda kv: (-kv[1].sends, kv[0])):
                writer.writerow(
                    [PREFIX_LABEL.get(prefix, prefix), prefix]
                    + [tally.surfaces.get(name, 0) for name in surfaces]
                    + [tally.sends,
                       round(100.0 * tally.sends / matched, 2) if matched else ""])
            writer.writerow(
                ["TOTAL categorised", ""]
                + [sum(t.surfaces.get(name, 0) for t in report["occasions"].values())
                   for name in surfaces]
                + [matched, 100.0 if matched else ""])
            writer.writerow(["Not in the card list", ""]
                            + [report["surface_unmatched"].get(name, 0)
                               for name in surfaces]
                            + [sum(report["unmatched"].values()), ""])
            writer.writerow(["TOTAL read", ""]
                            + [report["surfaces"][name] for name in surfaces]
                            + [len(report["sends"]), ""])
        written.append(path)

    path = os.path.join(directory, "by_subcategory.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["q1_value", "category", "subcategory", "sends",
                         "share_of_sends"])
        for q1, count in report["subcategories"].most_common():
            bucket = bucket_of(q1, report["split_events"])
            writer.writerow([q1, PREFIX_LABEL.get(bucket, bucket),
                             sub_label(bucket, q1), count,
                             round(100.0 * count / matched, 2) if matched else ""])
        writer.writerow(["TOTAL", "", "", matched, 100.0 if matched else ""])
    written.append(path)

    # The detail section as a table: one row per sub-category, carrying its
    # category's totals so the file can be pivoted on either half of the slug.
    path = os.path.join(directory, "category_detail.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "q1_prefix", "subcategory", "q1_value",
                         "sends", "share_of_category", "share_of_day",
                         "distinct_cards", "distinct_senders", "category_sends"])
        for prefix, tally in sorted(report["occasions"].items(),
                                    key=lambda kv: (-kv[1].sends, kv[0])):
            for q1, count in tally.subcategories.most_common():
                writer.writerow([
                    PREFIX_LABEL.get(prefix, prefix), prefix,
                    sub_label(prefix, q1), q1,
                    count,
                    round(100.0 * count / tally.sends, 2) if tally.sends else "",
                    round(100.0 * count / matched, 2) if matched else "",
                    len(report["subcategory_cards"][q1]),
                    len(report["subcategory_senders"][q1]),
                    tally.sends])
    written.append(path)

    path = os.path.join(directory, "by_card.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["card_id", "q1_value", "category", "subcategory", "sends"])
        for card, count in report["cards"].most_common():
            q1 = categories.get(card, "")
            bucket = bucket_of(q1, report["split_events"]) if q1 else ""
            writer.writerow([card, q1, PREFIX_LABEL.get(bucket, bucket) if q1 else "",
                             sub_label(bucket, q1) if q1 else "", count])
    written.append(path)

    path = os.path.join(directory, "category_by_channel.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        names = [name for name, _ in report["channels"].most_common()]
        writer.writerow(["category"] + names + ["total"])
        for prefix, tally in sorted(report["occasions"].items(),
                                    key=lambda kv: -kv[1].sends):
            writer.writerow([PREFIX_LABEL.get(prefix, prefix)]
                            + [tally.channels.get(name, 0) for name in names]
                            + [tally.sends])
        writer.writerow(["TOTAL"] + [report["channels"][name] for name in names]
                        + [len(report["sends"])])
    written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Category-wise report of the cards sent through social sharing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Both files are looked for in data/ when not named.")
    parser.add_argument("sends", nargs="*",
                        help="the send file(s) - .tsv, .csv, .xlsx, a paste, or a "
                             "card x channel pivot. Name more than one and the "
                             "report is cumulative across them.")
    parser.add_argument("--label", action="append", metavar="NAME",
                        help="what to call each file in the report, in the same "
                             "order: --label App --label Web")
    parser.add_argument("--cards", "--catalogue", dest="cards",
                        help="the card list with q1_value in it")
    parser.add_argument("--csv", dest="csv_dir", metavar="DIR",
                        help="also write the tables as CSV into DIR")
    parser.add_argument("--top", type=int, default=15,
                        help="how many rows in the top-N tables (default 15)")
    parser.add_argument("--detail", action="store_true",
                        help="add a block per category, with its sub-categories")
    parser.add_argument("--split-events", action="store_true",
                        help="report every q1 prefix under its own name instead "
                             "of bucketing the non-core ones as Events cards")
    parser.add_argument("--out", metavar="FILE", help="write the report to FILE too")
    args = parser.parse_args(argv)

    cards_path = find_catalogue(args.cards)
    paths = [find_sends(p) for p in args.sends] or [find_sends(None)]
    labels = args.label or []
    if labels and len(labels) != len(paths):
        raise SystemExit(
            f"{len(labels)} --label given for {len(paths)} file(s).\n"
            "  Give one label per file, in the same order, or none at all.")
    categories = load_categories(cards_path)

    sends = []
    for i, sends_path in enumerate(paths):
        raw = read_any(sends_path)
        rows = expand_pivot(raw) if looks_like_pivot(raw) else normalise(raw)
        if not rows:
            raise SystemExit(f"{sends_path} has no rows in it.")
        if "card_id" not in rows[0]:
            raise SystemExit(
                f"{sends_path} has no card id column, so nothing can be "
                f"categorised.\n"
                f"  Columns found: {', '.join(rows[0]) or '(none recognised)'}")
        # Unlabelled and alone, the surface is not worth naming. Unlabelled and
        # one of several, the file name is the only handle there is.
        if labels:
            surface = labels[i]
        elif len(paths) > 1:
            surface = os.path.splitext(os.path.basename(sends_path))[0]
        else:
            surface = "all shares"
        for row in rows:
            row["_surface"] = surface
        sends.extend(rows)

    report = build(sends, categories, split_events=args.split_events)
    text = render(report, top=args.top)
    if args.detail:
        text += "\n" + render_detail(report)
    print(text)
    print(f"{len(sends):,} sends read from "
          f"{', '.join(os.path.relpath(p) for p in paths)}, "
          f"categorised against {len(categories):,} cards "
          f"in {os.path.relpath(cards_path)}.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"Report written to {args.out}")
    if args.csv_dir:
        for path in write_csv(report, args.csv_dir, categories):
            print(f"Table written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
