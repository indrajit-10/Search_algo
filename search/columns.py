#!/usr/bin/env python3
"""The column contract: what search needs from the card export.

The export is the private card table shared for this work, and search reads nothing else,
so no field here is invented. Six columns are required:

    card_title, card_description, card_tags   the text people search against
    q1_value                                  the category code, the only taxonomy in the export
    status_id, invalid_card                    which cards are live

Everything search ranks on -- occasion, recipient, tone, language, month, seasonality --
is derived from those six. A few more columns are used when the export carries them:

    an id column        so results can name a card rather than a row number
    a popularity column so good cards outrank weak ones before anyone clicks
    a format column     so "video" nudges video cards up
    a date column       so new cards are visible

Each is found by name, case-insensitively, from a list of spellings, and each has a neutral
default, so the same code runs on an export whose header differs and search degrades instead
of failing. `resolve` reports what it found; the index build prints it.
"""

# Required columns, canonical name -> the spellings accepted in a header.
REQUIRED = {
    "title": ("card_title", "title", "cardtitle"),
    "description": ("card_description", "description", "carddescription", "card_desc"),
    "tags": ("card_tags", "tags", "cardtags", "keywords", "card_keywords"),
    "code": ("q1_value", "q1value", "category_code", "category", "cat_code"),
    "status": ("status_id", "statusid", "status"),
    "invalid": ("invalid_card", "invalidcard", "invalid", "is_invalid"),
}

# Optional columns. Missing ones cost a signal, never a search.
OPTIONAL = {
    "id": ("card_id", "cardid", "id", "card_no", "cardno", "card_code"),
    "popularity": ("views", "view_count", "card_views", "hits", "sent", "sent_count",
                   "times_sent", "downloads", "popularity", "rating", "rank"),
    "format": ("card_type", "cardtype", "type", "format", "card_format", "template_type"),
    "created": ("created_date", "create_date", "date_added", "added_date", "launch_date",
                "created_at", "insert_date"),
}

# What a missing optional column costs, in the words the index build prints.
COST = {
    "id": "results are keyed by row number",
    "popularity": "every card starts at the same quality; wire click logs to replace it",
    "format": "format words such as video score as plain keywords only",
    "created": "new cards get no visibility boost",
}

LIVE_STATUS = "1"
INVALID_FLAG = "1"


class Columns:
    """The export's header, resolved to the names search uses."""

    def __init__(self, found, missing):
        self.found = found
        self.missing = missing
        for name in list(REQUIRED) + list(OPTIONAL):
            setattr(self, name, found.get(name))

    def get(self, row, name, default=""):
        column = self.found.get(name)
        return (row.get(column) or default) if column else default

    def is_live(self, row):
        """Live cards only: status_id 1 and not flagged invalid, as the intent build reads it."""
        return (self.get(row, "status").strip() == LIVE_STATUS
                and self.get(row, "invalid").strip() != INVALID_FLAG)

    def report(self):
        lines = [f"columns: " + ", ".join(f"{k}={v}" for k, v in sorted(self.found.items()))]
        for name in self.missing:
            lines.append(f"  no {name} column: {COST[name]}")
        return "\n".join(lines)


def resolve(header):
    """Maps an export header to the names search uses. Raises if a required column is absent."""
    lookup = {}
    for column in header or ():
        lookup.setdefault(column.strip().lower().lstrip("﻿"), column)

    found, absent, missing = {}, [], []
    for name, spellings in REQUIRED.items():
        for spelling in spellings:
            if spelling in lookup:
                found[name] = lookup[spelling]
                break
        else:
            absent.append(f"{name} (one of: {', '.join(spellings)})")
    if absent:
        raise ValueError("the export is missing required columns: " + "; ".join(absent))

    for name, spellings in OPTIONAL.items():
        for spelling in spellings:
            if spelling in lookup:
                found[name] = lookup[spelling]
                break
        else:
            missing.append(name)
    return Columns(found, missing)
