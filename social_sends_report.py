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
}

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
    The send log. Today's file is whichever one in data/ looks like a send log
    and sorts last, so a folder of daily exports needs no argument.
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
        if found:
            return os.path.join(DATA_DIR, found[-1])
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


def label_of(q1):
    """The plain-English category a q1_value belongs to."""
    category = split_q1(q1)[0]
    return PREFIX_LABEL.get(category, category)


class Tally:
    """Counts for one category: sends, which cards, which senders, by channel."""

    def __init__(self):
        self.sends = 0
        self.cards = set()
        self.senders = set()
        self.channels = collections.Counter()
        self.countries = collections.Counter()
        self.subcategories = collections.Counter()


def build(sends, categories):
    """Join every send to its category and count. Nothing is dropped silently."""
    report = {
        "sends": sends,
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
    }
    for row in sends:
        card = row.get("card_id", "")
        channel = (row.get("share_type") or "unknown").strip()
        result = (row.get("share_result") or "").strip().lower()
        country = (row.get("country") or "??").strip().upper()
        sender = row.get("ip", "")

        report["channels"][channel] += 1
        report["countries"][country] += 1
        report["devices"][(row.get("ua") or "unknown").replace("-User-Agent", "")] += 1
        report["api_keys"][row.get("api_key") or "unknown"] += 1
        report["cards"][card] += 1
        report["dates"][(row.get("date") or "")[:10]] += 1
        report["senders"].add(sender)
        report["card_senders"][card].add(sender)
        if result and result != "success":
            report["failures"].append(row)

        category = categories.get(card)
        report["card_category"][card] = category or ""
        if not category:
            # An unknown card_id is a real finding, not a rounding error: it
            # means a retired or mistyped card is still being shared. Counted
            # and printed, never folded into an "other" bucket.
            report["unmatched"][card] += 1
            continue

        prefix = split_q1(category)[0]
        tally = report["occasions"][prefix]
        tally.sends += 1
        tally.cards.add(card)
        tally.senders.add(sender)
        tally.channels[channel] += 1
        tally.countries[country] += 1
        tally.subcategories[category] += 1
        report["subcategories"][category] += 1
        report["subcategory_cards"][category].add(card)
        report["subcategory_senders"][category].add(sender)
    return report


def percent(part, whole):
    return f"{(100.0 * part / whole):5.1f}%" if whole else "    -"


def plural(count, noun):
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


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

    add("=" * width)
    add("SOCIAL SENDS - CATEGORY REPORT".center(width))
    add("=" * width)
    add(f"Date            {span}")
    add(f"Cards sent      {total:,} sends of {len(report['cards']):,} different cards")
    add(f"Senders         {len(report['senders']):,} distinct IPs "
        f"in {len(report['countries'])} countries")
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
        add(f"{label:<26}{tally.sends:>7,}{percent(tally.sends, matched):>8}"
            f"{len(tally.cards):>7}{len(tally.senders):>9}"
            f"  {channel} {percent(count, tally.sends).strip()}")
    add(f"{'TOTAL':<26}{matched:>7,}{'100.0%':>8}"
        f"{len(set().union(*(t.cards for _, t in order)) if order else set()):>7}"
        f"{len(report['senders']):>9}")
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
        add("-" * width)
        add(f"{label.upper():<34}{prefix + '_*':>18}"
            f"{tally.sends:>10,} sends{percent(tally.sends, matched):>10}")
        add("-" * width)
        add(f"  {plural(len(tally.cards), 'card')}, "
            f"{plural(len(tally.senders), 'sender')}")
        wrap(add, "  Channels   ",
             [f"{name} {count} ({percent(count, tally.sends).strip()})"
              for name, count in tally.channels.most_common()], width)
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
            subcategory = split_q1(q1)[1] or "(no sub-category)"
            add(f"  {subcategory:<40}{count:>6,}"
                f"{percent(count, tally.sends):>8}{percent(count, matched):>8}"
                f"{len(report['subcategory_cards'][q1]):>6}"
                f"{len(report['subcategory_senders'][q1]):>8}")
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
    written.append(path)

    path = os.path.join(directory, "by_subcategory.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["q1_value", "category", "subcategory", "sends",
                         "share_of_sends"])
        for q1, count in report["subcategories"].most_common():
            writer.writerow([q1, label_of(q1), split_q1(q1)[1], count,
                             round(100.0 * count / matched, 2) if matched else ""])
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
                    PREFIX_LABEL.get(prefix, prefix), prefix, split_q1(q1)[1], q1,
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
            writer.writerow([card, q1, label_of(q1) if q1 else "",
                             split_q1(q1)[1] if q1 else "", count])
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
    written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Category-wise report of the cards sent through social sharing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Both files are looked for in data/ when not named.")
    parser.add_argument("sends", nargs="?",
                        help="the send log (.tsv, .csv, .xlsx, or a paste)")
    parser.add_argument("--cards", "--catalogue", dest="cards",
                        help="the card list with q1_value in it")
    parser.add_argument("--csv", dest="csv_dir", metavar="DIR",
                        help="also write the tables as CSV into DIR")
    parser.add_argument("--top", type=int, default=15,
                        help="how many rows in the top-N tables (default 15)")
    parser.add_argument("--detail", action="store_true",
                        help="add a block per category, with its sub-categories")
    parser.add_argument("--out", metavar="FILE", help="write the report to FILE too")
    args = parser.parse_args(argv)

    cards_path = find_catalogue(args.cards)
    sends_path = find_sends(args.sends)
    categories = load_categories(cards_path)
    sends = normalise(read_any(sends_path))
    if not sends:
        raise SystemExit(f"{sends_path} has no rows in it.")
    if "card_id" not in sends[0]:
        raise SystemExit(
            f"{sends_path} has no card id column, so nothing can be categorised.\n"
            f"  Columns found: {', '.join(sends[0]) or '(none recognised)'}")

    report = build(sends, categories)
    text = render(report, top=args.top)
    if args.detail:
        text += "\n" + render_detail(report)
    print(text)
    print(f"{len(sends):,} sends read from {os.path.relpath(sends_path)}, "
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
