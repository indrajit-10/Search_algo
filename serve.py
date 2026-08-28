"""
serve.py  --  a browser interface for trying the search engine.

    python serve.py card_database.csv
    then open http://localhost:8000

Pure Python 3 standard library. No Flask, no pip installs.

WHY THERE IS NO SCRAPER HERE
----------------------------
You do not need one. Thumbnails are not data you have to collect - they are
already live on your own CDN, and the export carries everything needed to
address them: card_page_url, q1_value, card_number and card_thumb_extn. This
page emits <img src="https://your-cdn/..."> and the BROWSER fetches each image
straight from the live site as it renders. Nothing is crawled, nothing is
copied, and the pictures are always current.

What that costs you is one piece of configuration: the URL template. The export
does not spell out the CDN layout, though inhouse_music leaks the shape of it -
values like "/c/ejul_barnday/mp3/12440.mp3" say assets live under
/c/<category>/<type>/<id>.<ext>. Rather than guess, the template is editable in
the page itself. Paste your real pattern into the Image URL box, and the grid
re-renders immediately. Set IMAGE_TEMPLATE below once you know it and it becomes
the default for everyone.

Placeholders: {number} {q1} {occasion} {subcat} {page} {thumb_extn} {big_extn}
"""

import collections
import csv
import datetime
import html
import json
import os
import posixpath
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import search_engine as se

# Confirmed against card 123057 - q1_value birth_happybirthday, card_number
# 123057 - which really is served at
#     https://i.123g.us/c/birth_happybirthday/pc/123057_pc.jpg
# so the image path is q1_value and card_number with nothing in between.
IMAGE_TEMPLATE = "https://i.123g.us/c/{q1}/pc/{number}_pc.jpg"

# The _pc derivative appears to be jpg regardless of card_thumb_extn - 123057 is
# a jpg card, and the same category holds gif, png and empty ones. Rather than
# guess, the page retries with the card's own extension when the jpg 404s, so
# both conventions work without anyone having to know which is in force.
IMAGE_FALLBACK = "https://i.123g.us/c/{q1}/pc/{number}_pc.{thumb_extn}"

# NOT derivable from the export. The same card lives at
#     /birthday/happy_birthday/birthday191.html
# while its q1_value is birth_happybirthday: "birth" has to become "birthday"
# and "happybirthday" has to become "happy_birthday". Neither follows from
# splitting the slug, and the export carries no column holding the URL path.
# Left blank so tiles are not linked to a guessed 404 - set it once the
# slug-to-path mapping is available, or point it at a redirect that resolves a
# card by number.
PAGE_TEMPLATE = ""

PORT = 8000
INDEX = None
LIVE_ROWS = None
SUGGESTER = None

# ---------------------------------------------------------------------------
# RECORDING WHAT WAS SEARCHED AND WHAT CAME BACK
#
# The production log carries query, times and results-count. That is enough to
# find what is broken and nothing like enough to learn from, because it cannot
# tell one person refining a search from two people searching once. The four
# extra columns here are the difference, and none of them is expensive:
#
#   session   a random id the page keeps in localStorage. Turns two rows into a
#             sequence, which is what makes "deepavali, no click, diwali, click"
#             legible as one person failing and then succeeding.
#   ms        so a slow query is visible before anyone complains about it.
#   strategy  which rung answered - the difference between a real match and a
#             fallback dressed up as one.
#   fallback  whether the page was honest about that.
#
# Written as TSV under data/, which is gitignored, so this never leaves the
# machine it runs on.
# ---------------------------------------------------------------------------
QUERY_LOG = None
LOG_LOCK = threading.Lock()
LOG_COLUMNS = ("at", "session", "query", "corrected", "strategy",
               "results", "fallback", "ms")


def record(session, query, out, shown, elapsed):
    """Append one search to the log. Never allowed to break a search."""
    if not QUERY_LOG:
        return
    corrections = out.get("corrections") or {}
    row = [
        datetime.datetime.now().isoformat(timespec="seconds"),
        session or "-",
        (query or "").replace("\t", " ").replace("\n", " ")[:200],
        " ".join(f"{k}>{v}" for k, v in corrections.items()) or "-",
        out.get("strategy") or "-",
        str(shown),
        "1" if out.get("fallback") else "0",
        f"{elapsed:.1f}",
    ]
    try:
        with LOG_LOCK:
            new = not os.path.exists(QUERY_LOG)
            with open(QUERY_LOG, "a", encoding="utf-8", newline="") as fh:
                if new:
                    fh.write("\t".join(LOG_COLUMNS) + "\n")
                fh.write("\t".join(row) + "\n")
    except OSError:
        pass            # a full disk must not take the search down with it


def read_log():
    if not QUERY_LOG or not os.path.exists(QUERY_LOG):
        return []
    try:
        with open(QUERY_LOG, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t"))
    except OSError:
        return []


def card_payload(card, why, index):
    occasion, _, subcat = card.category.partition("_")
    return {
        "number": card.number,
        "title": card.title,
        "description": card.description,
        "category": card.category,
        "q1": card.category,          # the {q1} placeholder in the URL templates
        "occasion": occasion,
        "subcat": subcat or occasion,
        "page": card.url.replace(".html", ""),
        "thumb_extn": index.thumb_extn.get(card.doc) or "gif",
        "big_extn": index.big_extn.get(card.doc) or "gif",
        "year": card.year,
        "why": why,
        "facets": {k: sorted(v) for k, v in card.facets.items()},
    }


def _number(params, name, default, low, high, cast):
    """
    Read a query-string number, and never trust it.

    Both halves matter once this is reachable from another machine. Parsing
    without a guard meant "?limit=abc" threw ValueError out of the handler and
    dropped the connection mid-response. Clamping with min() alone let
    "?limit=-5" straight through, because -5 is under the ceiling - and a
    negative limit slices results the other way round, so a 4 KB response became
    383 KB of the whole result set. Anyone who can reach the page can send
    either one.
    """
    try:
        value = cast((params.get(name) or [default])[0])
    except (TypeError, ValueError):
        return default
    if value != value:                      # NaN survives every comparison
        return default
    return max(low, min(value, high))


def do_search(query, limit, boost, compare=True, session=None):
    started = time.perf_counter()
    out = se.search(INDEX, query, limit=limit, recency_boost=boost)
    elapsed = (time.perf_counter() - started) * 1000
    record(session, query, out, len(out["results"]), elapsed)

    new_results = [card_payload(c, out["explain"].get(c.doc, ""), INDEX)
                   for c in out["results"]]

    # The old pipeline rescans every live row, which costs ~30x the new engine
    # and is invisible unless the page is actually showing the comparison. On a
    # laptop that is a rounding error; with several people typing at once it is
    # the whole bottleneck, so it is only run when asked for.
    if not compare:
        return {
            "query": query,
            "new": {"results": new_results, "strategy": out["strategy"],
                    "message": out["message"], "corrections": out["corrections"],
                    "fallback": out.get("fallback", False),
                    "ms": round(elapsed, 1)},
            "old": None,
            "boost": boost,
            "defaults": {"image": IMAGE_TEMPLATE, "fallback": IMAGE_FALLBACK,
                         "page": PAGE_TEMPLATE},
        }

    started = time.perf_counter()
    old_rows, old_total = se.old_search(LIVE_ROWS, query, limit=limit)
    old_elapsed = (time.perf_counter() - started) * 1000
    old_results = []
    for row in old_rows:
        occasion, _, subcat = row["q1_value"].partition("_")
        old_results.append({
            "number": row["card_number"],
            "title": se.decode_entities(row["card_title"]),
            "description": se.decode_entities(row["card_description"]),
            "category": row["q1_value"],
            "q1": row["q1_value"],
            "occasion": occasion,
            "subcat": subcat or occasion,
            "page": row["card_page_url"].replace(".html", ""),
            "thumb_extn": row["card_thumb_extn"] or "gif",
            "big_extn": row["card_bigimage_extn"] or "gif",
            "year": int(row["card_created_date"][:4]) if row["card_created_date"] else 0,
            "why": "", "facets": {},
        })

    return {
        "query": query,
        "new": {
            "results": new_results,
            "strategy": out["strategy"],
            "message": out["message"],
            "corrections": out["corrections"],
            "fallback": out.get("fallback", False),
            "ms": round(elapsed, 1),
        },
        "old": {
            "results": old_results,
            "total": old_total,
            "ms": round(old_elapsed, 1),
            "capped": old_total >= se.OLD_CAP,
        },
        "boost": boost,
        "defaults": {"image": IMAGE_TEMPLATE, "fallback": IMAGE_FALLBACK,
                     "page": PAGE_TEMPLATE},
    }


# ---------------------------------------------------------------------------
# THE LIVE PAGE
#
# The same markup 123greetings.com/search emits - .cont, .body2-left,
# ul.sub-cat > li > .thumb-hold, p.msg-red - linked to the same two
# stylesheets, so this is what the new engine's results actually look like in
# the site's own clothes rather than in a test bench's.
#
# Three deliberate differences from the live page, all requested:
#   * the pc image instead of the th thumbnail
#   * one card a row rather than a grid
#   * no description under the title
#
# The overrides for those are inline and marked !important, because the site's
# own rules for ul.sub-cat li are not visible from here - c.123g.us is not
# reachable from the machine this was built on, so the cascade could not be
# checked. On a machine that can reach the CDN this loads the real stylesheets
# and the overrides sit on top of them.
# ---------------------------------------------------------------------------

LIVE_OVERRIDES = """
/* ---- the site's own tokens, taken from static_R1.css ---------------------
   headings  bold 14px/24px Georgia, serif with a 2px #999 rule under them
   heading   #ab1717      links #18397c      body Arial/Helvetica
   borders   #999 solid, #ccc dashed         muted #666
   Used below so the page still reads as 123Greetings when the CDN
   stylesheets are unreachable, which is how it was built. */

body{ margin:0; background:#fff; color:#222;
      font:12px/1.5 Arial,Helvetica,sans-serif; }
.cont{ max-width:1180px; margin:0 auto; padding:0 14px 50px; }
a{ color:#18397c; }
.breadcrumb ul{ list-style:none; padding:0; margin:12px 0; font-size:11px; color:#666; }
.breadcrumb li{ display:inline; }
.breadcrumb span{ margin:0 5px; color:#999; }

.bd{ border:1px solid #ccc; padding:10px; margin-bottom:12px; }
.bd-search input[type=text]{ padding:6px 8px; font-size:13px; width:min(420px,64%);
  border:1px solid #bdc7d8; color:#333; }
.bd-search .search_btn{ padding:6px 16px; font-size:12px; font-weight:bold;
  color:#fff; border:1px solid #036; cursor:pointer;
  background-image:linear-gradient(to bottom,#369,#036); }

/* The red liner. .msg-red is the site's own class; this only fills it in when
   the CDN copy is missing. */
p.msg-red{ color:#ab1717; font-size:13px; margin:14px 0 12px; line-height:1.6; }
p.msg-red a{ color:#18397c; }
p.msg-red b{ font-weight:bold; }

.search_heading h3, .heading_tag{
  font:bold 14px/24px Georgia,serif; color:#ab1717;
  border-bottom:2px solid #999; margin:16px 0 12px; padding:1px 0; }

/* ---- the three requested changes -----------------------------------------
   pc image instead of the th thumbnail, one card a row instead of a grid, and
   no description under the title.

   Marked !important because the rules these override - ul.sub-cat li - are in
   sub_categories_R1.css or styleopt_R1.css, neither of which was available
   here, so the real cascade could not be checked. See assets/README.md. */
ul.sub-cat{ display:block !important; list-style:none; padding:0; margin:0; }
ul.sub-cat > li{
  width:auto !important; float:none !important; clear:both !important;
  display:flex !important; gap:20px; align-items:flex-start;
  height:auto !important; min-height:0 !important;
  margin:0 0 14px !important; padding:12px !important;
  border:1px solid #ccc; background:#fff; text-align:left !important; }
ul.sub-cat > li .thumb-hold{
  flex:0 0 300px; width:300px !important; height:auto !important;
  margin:0 !important; overflow:hidden; background:#f4f4f4;
  min-height:110px; display:flex; align-items:center; justify-content:center; }
ul.sub-cat > li .thumb-hold img{
  width:100% !important; height:auto !important; max-width:none !important;
  display:block; border:0; }
ul.sub-cat > li .listbody{ flex:1; min-width:0; }
ul.sub-cat > li h2{
  border:0 !important; margin:0 0 5px !important; padding:0 !important;
  height:auto !important; overflow:visible !important;
  font:bold 15px/1.35 Georgia,serif !important; }
ul.sub-cat > li h2 a{ color:#18397c !important; text-decoration:none; }
ul.sub-cat > li h2 a:hover{ text-decoration:underline; }

.card-meta{ color:#666; font:11px/1.5 ui-monospace,Menlo,Consolas,monospace;
  word-break:break-all; }
.card-why{ color:#0a7c46; font-size:11.5px; margin-top:3px; }
.card-facets{ margin-top:5px; }
.card-facets span{ display:inline-block; background:#f1f1f1; color:#666;
  border:1px solid #e3e3e3; padding:1px 6px; font-size:10.5px; margin:0 3px 3px 0; }
.thumb-hold .missing{ color:#999; font:10.5px/1.4 ui-monospace,Menlo,monospace;
  padding:12px; text-align:center; word-break:break-all; }

.enginebar{ border:1px dashed #ccc; padding:6px 10px; margin:0 0 12px;
  font-size:11px; color:#666; }
.enginebar b{ color:#222; }
"""


def _live_card(c):
    """One <li>, in the site's own shape, minus the description."""
    esc = html.escape
    img = IMAGE_TEMPLATE.format(**c)
    alt = IMAGE_FALLBACK.format(**c)
    title = esc(c["title"])
    onerr = ("if(!this.dataset.r){this.dataset.r=1;this.src='%s';}"
             "else{this.parentNode.innerHTML="
             "'<div class=missing>%s</div>';}" % (alt, esc(img)))
    facets = "".join(
        f"<span>{esc(k)}:{esc(v)}</span>"
        for k, vs in sorted((c.get("facets") or {}).items()) for v in vs[:3])
    why = (f'<div class="card-why">{esc(c["why"])}</div>' if c.get("why") else "")
    return f"""  <li>
    <div class="thumb-hold">
      <a id="p_{c['number']}" class="q-view-scat" href="javascript:void(0);" title="Click to Preview"></a>
      <img id="img_{c['number']}" src="{img}" alt="{title}" title="{title}" onerror="{onerr}" />
    </div>
    <div class="listbody">
      <h2>{title}</h2>
      <div class="card-meta">{esc(c['category'])} &middot; {c['year'] or '?'} &middot; #{c['number']}</div>
      {why}
      <div class="card-facets">{facets}</div>
    </div>
  </li>"""


def live_page(query, limit, boost, session):
    esc = html.escape
    started = time.perf_counter()
    out = se.search(INDEX, query, limit=limit, recency_boost=boost) if query else None
    elapsed = (time.perf_counter() - started) * 1000
    cards = []
    if out:
        record(session, query, out, len(out["results"]), elapsed)
        cards = [card_payload(c, out["explain"].get(c.doc, ""), INDEX)
                 for c in out["results"]]

    # The red liner, in the site's own voice and its own class.
    red = ""
    if out:
        if out.get("fallback"):
            red = ('<p class="msg-red std gutter-bottom">Sorry! The search query '
                   'you entered did not find any matching results. '
                   'Here are the newest cards instead.</p>')
        elif out.get("corrections"):
            # The whole query as it was actually run, with the fixed words in
            # bold - the shape the live page uses for "Did you mean", except
            # this one found results, so it is not a question.
            fixed = " ".join(
                f"<b>{esc(out['corrections'][w])}</b>" if w in out["corrections"]
                else esc(w) for w in se.normalise(query).split())
            red = (f'<p class="msg-red std gutter-bottom">'
                   f'Showing results for {fixed}</p>')

    heading = ""
    if out and out.get("fallback"):
        heading = ('<div class="search_heading"><h3>Enjoy sending our newest '
                   'cards! All cards are FREE!</h3></div>')

    bar = ""
    if out:
        bar = (f'<p class="enginebar">answered by <b>{esc(out["strategy"])}</b>'
               f' &middot; <b>{len(cards)}</b> cards &middot; '
               f'<b>{elapsed:.1f} ms</b> &middot; '
               f'<a href="/">test bench</a> &middot; <a href="/report">report</a></p>')

    title = f"123Greetings: Search {esc(query)} ecards" if query else "123Greetings: Search"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{title}</title>
<link href="//c.123g.us/css/static_R1.css" rel="stylesheet" type="text/css" />
<link href="//c.123g.us/css/sub_categories_R1.css" rel="stylesheet" type="text/css" />
<link href="/assets/123g_static_R1.css" rel="stylesheet" type="text/css" />
<style>{LIVE_OVERRIDES}</style>
</head><body>
<div class="cont">

<div class="breadcrumb sm">
<ul>
<li><a href="//www.123greetings.com/">123Greetings</a><span>&raquo;</span></li>
<li><a class="active">Search</a></li>
</ul>
<div class="clear"></div>
</div>

<div class="body2-left">
<div class="bd bd-search">
<form action="" method="get">
<input type="text" id="search_query" name="query" value="{esc(query or '')}" autocomplete="off" />
<input type="submit" class="search_btn" value="Search" name="srch_button" />
</form>
</div>

{bar}
{red}
{heading}

<ul class="sub-cat">
{chr(10).join(_live_card(c) for c in cards)}
</ul>
</div>
</div>
<!-- New engine ended at : Total time: {elapsed / 1000:.3f} secs -->
<!-- Strategy: {esc(out["strategy"]) if out else "-"} -->
<!-- Results given: {len(cards)} -->
</body></html>"""


def report_page():
    """
    What was searched, and what came back.

    Deliberately answers the operational questions rather than showing a wall of
    rows: which searches came back thin, which had to be widened, which got a
    spelling fixed, and - the one production cannot answer today - which people
    had to retype before they got anywhere.
    """
    rows = read_log()
    if not rows:
        return ("<!doctype html><meta charset=utf-8><title>Search report</title>"
                "<style>body{font:15px/1.55 system-ui;margin:60px auto;max-width:52ch;"
                "color:#14151a}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}"
                "</style><h1>Nothing recorded yet</h1><p>Run some searches on "
                "<a href='/'>the search page</a> and reload this. Every search is "
                "appended to <code>data/query_log.tsv</code>.</p>")

    def n(r, key, default=0):
        try:
            return int(r.get(key) or default)
        except ValueError:
            return default

    total = len(rows)
    counts = collections.Counter(r["query"].strip().lower() for r in rows)
    zero = [r for r in rows if n(r, "results") == 0]
    thin = [r for r in rows if 0 < n(r, "results") < 5]
    fell = [r for r in rows if r.get("fallback") == "1"]
    fixed = [r for r in rows if (r.get("corrected") or "-") != "-"]
    widened = [r for r in rows if r.get("strategy") not in
               ("all terms", "card number", "-", None)]
    times = sorted(float(r.get("ms") or 0) for r in rows)

    # Reformulation: same session, a search that came back thin or fell back,
    # followed by a different search that did not. This is the pair a synonym
    # would have to have known - and the reason the session column exists.
    by_session = collections.defaultdict(list)
    for r in rows:
        by_session[r.get("session") or "-"].append(r)
    pairs = collections.Counter()
    for sid, rs in by_session.items():
        if sid == "-":
            continue
        for a, b in zip(rs, rs[1:]):
            aq, bq = a["query"].strip().lower(), b["query"].strip().lower()
            if aq == bq or not aq or not bq:
                continue
            gave_up = a.get("fallback") == "1" or n(a, "results") < 5
            worked = b.get("fallback") != "1" and n(b, "results") >= 5
            if gave_up and worked:
                pairs[(aq, bq)] += 1

    def pct(k):
        return f"{k / total * 100:.0f}%"

    def table(title, head, body, note=""):
        if not body:
            return (f"<section><h2>{title}</h2>"
                    f"<p class=none>Nothing here — good.</p></section>")
        th = "".join(f"<th>{h}</th>" for h in head)
        tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                     for row in body)
        note = f"<p class=note>{note}</p>" if note else ""
        return (f"<section><h2>{title}</h2>{note}"
                f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"
                f"</section>")

    esc = html.escape
    tiles = [
        ("searches recorded", f"{total:,}", ""),
        ("distinct queries", f"{len(counts):,}", ""),
        ("came back empty", f"{len(zero):,}", pct(len(zero))),
        ("fewer than 5 cards", f"{len(thin):,}", pct(len(thin))),
        ("showed newest instead", f"{len(fell):,}", pct(len(fell))),
        ("spelling corrected", f"{len(fixed):,}", pct(len(fixed))),
        ("needed widening", f"{len(widened):,}", pct(len(widened))),
        ("median / slowest", f"{times[len(times)//2]:.1f} / {times[-1]:.0f} ms", ""),
    ]
    tilehtml = "".join(
        f'<div class="tile{" bad" if lab in ("came back empty",) and v != "0" else ""}">'
        f'<b>{v}</b><span>{lab}</span>{f"<i>{p}</i>" if p else ""}</div>'
        for lab, v, p in tiles)

    return f"""<!doctype html><meta charset=utf-8><title>Search report</title>
<style>
:root{{--bg:#fbfbfd;--panel:#fff;--ink:#14151a;--muted:#6b7280;--line:#e5e7eb;
 --accent:#2563eb;--bad:#dc2626;--good:#059669;--chip:#f3f4f6}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0e0f13;--panel:#171920;--ink:#e8eaf0;
 --muted:#9aa1ae;--line:#272a33;--accent:#7aa2ff;--bad:#f87171;--good:#34d399;--chip:#22252e}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:34px 22px 70px}}
h1{{font-size:24px;margin:0 0 4px}}
.sub{{color:var(--muted);margin:0 0 26px;font-size:14px}}
.sub a{{color:var(--accent)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;
 margin-bottom:34px}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:13px 15px;display:flex;flex-direction:column;gap:2px}}
.tile b{{font-size:23px;font-variant-numeric:tabular-nums;line-height:1.15}}
.tile span{{color:var(--muted);font-size:12.5px}}
.tile i{{color:var(--muted);font-size:11.5px;font-style:normal}}
.tile.bad b{{color:var(--bad)}}
section{{margin-bottom:34px}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
 margin:0 0 10px;padding-bottom:7px;border-bottom:1px solid var(--line)}}
table{{width:100%;border-collapse:collapse;background:var(--panel);
 border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th{{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;
 color:var(--muted);padding:9px 12px;background:var(--chip);font-weight:600}}
td{{padding:8px 12px;border-top:1px solid var(--line);font-size:14px}}
td:not(:first-child){{font-variant-numeric:tabular-nums;white-space:nowrap}}
.none{{color:var(--good);font-size:14px;margin:0}}
.note{{color:var(--muted);font-size:13px;margin:0 0 10px}}
code{{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--chip);
 padding:2px 6px;border-radius:4px}}
</style>
<main>
<h1>Search report</h1>
<p class="sub">{total:,} searches recorded ·
 <a href="/">back to search</a> ·
 <a href="/report.tsv">download as TSV</a> ·
 raw file <code>data/query_log.tsv</code></p>
<div class="tiles">{tilehtml}</div>

{table("Most searched", ["query", "searches"],
       [[esc(q), f"{c:,}"] for q, c in counts.most_common(15)])}

{table("Came back empty", ["query", "when"],
       [[esc(r["query"]), r["at"][:16].replace("T", " ")] for r in zero[-15:]],
       "The engine guarantees this cannot happen, so anything here is a bug "
       "worth reporting.")}

{table("Thin results — fewer than 5 cards", ["query", "cards", "answered by"],
       [[esc(r["query"]), r["results"], esc(r["strategy"])] for r in thin[-15:]],
       "Not failures, but the first place to look for a content gap.")}

{table("Showed the newest cards instead", ["query", "when"],
       [[esc(r["query"]), r["at"][:16].replace("T", " ")] for r in fell[-15:]],
       "Nothing matched. Each of these is either a synonym to add or a card to "
       "commission.")}

{table("Spelling corrected", ["query", "corrected to", "cards"],
       [[esc(r["query"]),
         " · ".join(esc(x).replace("&gt;", " &rarr; ")
                    for x in (r["corrected"] or "").split()),
         r["results"]] for r in fixed[-15:]])}

{table("Retyped until it worked", ["they first searched", "then searched", "times"],
       [[esc(a), esc(b), t] for (a, b), t in pairs.most_common(15)],
       "Same person, one search that went nowhere followed by one that worked. "
       "This is the pair a synonym list would have had to know in advance — and "
       "it is why the session column is here. Production cannot produce this "
       "table today.")}
</main>"""


class Handler(BaseHTTPRequestHandler):
    # A connection that opens and then says nothing otherwise holds its thread
    # for ever. Harmless on a laptop; on anything reachable by other people it
    # is how you run out of threads without any traffic.
    timeout = 30

    def log_message(self, *args):          # keep the console readable
        pass

    def _send(self, body, content_type):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = posixpath.normpath(parsed.path)

        if route == "/api/suggest":
            params = urllib.parse.parse_qs(parsed.query)
            typed = (params.get("q") or [""])[0]
            started = time.perf_counter()
            hits = SUGGESTER.suggest(typed, limit=8) if SUGGESTER else []
            self._send(json.dumps({
                "q": typed, "suggestions": hits,
                "ms": round((time.perf_counter() - started) * 1000, 2),
            }), "application/json")
            return

        if route == "/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            query = (params.get("q") or [""])[0]
            limit = _number(params, "limit", 24, 1, 60, int)
            boost = _number(params, "boost", se.RECENCY_BOOST, 0.0, 1.0, float)
            compare = (params.get("mode") or ["new"])[0] == "both"
            session = (params.get("s") or ["-"])[0][:40]
            self._send(json.dumps(do_search(query, limit, boost, compare,
                                            session)),
                       "application/json")
            return

        if route == "/live":
            params = urllib.parse.parse_qs(parsed.query)
            self._send(live_page(
                (params.get("query") or params.get("q") or [""])[0][:200],
                _number(params, "limit", 24, 1, 60, int),
                _number(params, "boost", se.RECENCY_BOOST, 0.0, 1.0, float),
                (params.get("s") or ["live"])[0][:40]),
                "text/html; charset=utf-8")
            return

        if route == "/assets/123g_static_R1.css":
            # Only ever this one file, by exact name - never a path built from
            # the request, so there is nothing here to traverse out of.
            here = os.path.dirname(os.path.abspath(__file__))
            try:
                with open(os.path.join(here, "assets", "123g_static_R1.css"),
                          encoding="utf-8") as fh:
                    self._send(fh.read(), "text/css; charset=utf-8")
            except OSError:
                self.send_error(404)
            return

        if route == "/report":
            self._send(report_page(), "text/html; charset=utf-8")
            return

        if route == "/report.tsv":
            rows = read_log()
            body = "\t".join(LOG_COLUMNS) + "\n" + "\n".join(
                "\t".join(r.get(c, "") for c in LOG_COLUMNS) for r in rows)
            self._send(body, "text/tab-separated-values; charset=utf-8")
            return

        if route in ("/", "/index.html"):
            self._send(PAGE, "text/html; charset=utf-8")
            return

        self.send_error(404)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Card search - test bench</title>
<style>
:root{
  --bg:#fbfbfd; --panel:#fff; --ink:#14151a; --muted:#6b7280; --line:#e5e7eb;
  --accent:#2563eb; --good:#059669; --bad:#dc2626; --chip:#f3f4f6;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0e0f13; --panel:#171920; --ink:#e8eaf0; --muted:#9aa1ae; --line:#272a33;
  --accent:#7aa2ff; --good:#34d399; --bad:#f87171; --chip:#22252e;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:10;background:var(--panel);
  border-bottom:1px solid var(--line);padding:14px 20px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:1500px;margin:0 auto}
#q{flex:1;min-width:260px;padding:11px 14px;font-size:16px;border:1px solid var(--line);
  border-radius:9px;background:var(--bg);color:var(--ink)}
#q:focus{outline:2px solid var(--accent);outline-offset:-1px}
.qwrap{position:relative;flex:1;min-width:260px;display:flex}
.qwrap #q{flex:1}
#sug{position:absolute;top:calc(100% + 4px);left:0;right:0;z-index:40;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;
  box-shadow:0 8px 28px rgba(0,0,0,.14);overflow:hidden;display:none}
#sug.show{display:block}
.sug{padding:9px 14px;cursor:pointer;display:flex;align-items:center;gap:9px;
  font-size:14.5px}
.sug:hover,.sug.sel{background:var(--chip)}
.sug .ic{opacity:.4;flex:none}
.sug b{font-weight:600}
button,select{padding:9px 13px;border:1px solid var(--line);border-radius:8px;
  background:var(--bg);color:var(--ink);cursor:pointer;font-size:14px}
button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.cfg{max-width:1500px;margin:10px auto 0;display:none;gap:8px;flex-wrap:wrap}
.cfg.show{display:flex}
.cfg input{flex:1;min-width:320px;padding:8px 11px;font:13px ui-monospace,monospace;
  border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink)}
main{max-width:1500px;margin:0 auto;padding:18px 20px 60px}
.status{color:var(--muted);font-size:13.5px;margin:2px 0 16px;display:flex;
  gap:14px;flex-wrap:wrap;align-items:center}
.pill{background:var(--chip);border-radius:20px;padding:3px 11px;font-size:12.5px}
.pill.good{color:var(--good)} .pill.bad{color:var(--bad)}
.reportlink{display:inline-flex;align-items:center;padding:0 13px;height:34px;
  border:1px solid var(--line);border-radius:7px;background:var(--bg);
  color:var(--muted);text-decoration:none;font-size:14px}
.reportlink:hover{border-color:var(--accent);color:var(--accent)}
.panes{display:grid;gap:26px}
.panes.split{grid-template-columns:1fr 1fr}
@media(max-width:1000px){.panes.split{grid-template-columns:1fr}}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
  margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
/* One card a row, at pc size. The grid of thumbnails was a contact sheet;
   this is what someone browsing for a card to send actually looks at. */
.grid{display:flex;flex-direction:column;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  overflow:hidden;display:flex;gap:18px;align-items:flex-start;padding:14px;
  text-decoration:none;color:inherit}
.card:hover{border-color:var(--accent)}
.thumbwrap{flex:0 0 var(--pcw);background:var(--chip);border-radius:8px;
  display:flex;align-items:center;justify-content:center;overflow:hidden;
  min-height:90px}
.thumbwrap img{width:100%;height:auto;display:block}
.ph{color:var(--muted);font:11px ui-monospace,monospace;text-align:center;padding:14px;
  word-break:break-all;line-height:1.35}
.meta{display:flex;flex-direction:column;gap:6px;flex:1;min-width:0;padding-top:2px}
.t{font-weight:600;font-size:16px;line-height:1.3}
.c{font:11.5px ui-monospace,monospace;color:var(--muted);word-break:break-all}
.why{font-size:12px;color:var(--good);line-height:1.35}
:root{--pcw:260px}
@media(max-width:720px){.card{flex-direction:column}:root{--pcw:100%}}
/* The red liner, as the live site writes it. */
.redline{color:var(--bad);font-size:14.5px;margin:0 0 14px;line-height:1.5}
.redline a{color:var(--accent);font-weight:600}
.redline b{font-weight:700}
.tags{display:flex;gap:4px;flex-wrap:wrap;margin-top:auto;padding-top:4px}
.tag{font-size:11px;background:var(--chip);border-radius:4px;padding:2px 7px;color:var(--muted)}
.tag.more{background:transparent;border:1px dashed var(--line)}
.empty{padding:40px;text-align:center;color:var(--muted);border:1px dashed var(--line);
  border-radius:11px}
.hint{color:var(--muted);font-size:12.5px;margin-top:6px}
code{font:12px ui-monospace,monospace;background:var(--chip);padding:1px 5px;border-radius:4px}
</style></head><body>
<header>
  <div class="row">
    <div class="qwrap">
      <input id="q" autocomplete="off" spellcheck="false"
             placeholder="Try: funny birthday for mom &nbsp;/&nbsp; birthdya &nbsp;/&nbsp; flash card"
             autofocus>
      <div id="sug"></div>
    </div>
    <button id="mNew" class="on">New</button>
    <button id="mBoth">Compare</button>
    <button id="mCfg">Image URLs</button>
    <a class="reportlink" href="/live" target="_blank" rel="noopener">Live look</a>
    <a class="reportlink" href="/report" target="_blank" rel="noopener">Report</a>
    <select id="boost" title="How much newer cards are favoured">
      <option value="0">Freshness: off</option>
      <option value="0.15" selected>Freshness: subtle (15%)</option>
      <option value="0.30">Freshness: strong (30%)</option>
      <option value="0.60">Freshness: heavy (60%)</option>
    </select>
  </div>
  <div class="cfg" id="cfg">
    <input id="tImg" spellcheck="false">
    <input id="tPage" spellcheck="false">
    <button id="tSave">Apply</button>
    <div class="hint" style="flex-basis:100%">
      Placeholders: <code>{number}</code> <code>{q1}</code> <code>{occasion}</code>
      <code>{subcat}</code> <code>{page}</code> <code>{thumb_extn}</code>
      <code>{big_extn}</code> &mdash; saved in this browser. Images load straight from
      your live site; nothing is scraped.
    </div>
  </div>
</header>
<main>
  <p class="redline" id="redline" style="display:none"></p>
  <div class="status" id="status"></div>
  <div class="panes" id="panes"></div>
</main>
<script>
const $ = s => document.querySelector(s);
let mode = "new", defaults = {}, last = null;
// One id per browser, so the report can tell one person refining a search from
// two people searching once. Never leaves this machine.
const SID = (() => {
  try {
    let v = localStorage.getItem("sid");
    if (!v) { v = Math.random().toString(36).slice(2, 12); localStorage.setItem("sid", v); }
    return v;
  } catch (e) { return "-"; }
})();
const T = {
  img: localStorage.getItem("tImg") || "",
  page: localStorage.getItem("tPage") || ""
};

function fill(tpl, c){
  return tpl.replace(/\{(\w+)\}/g, (_, k) => (c[k] !== undefined ? c[k] : ""));
}

function cardHTML(c){
  const img  = fill(T.img || defaults.image || "", c);
  // The _pc derivative may be jpg for every card or may follow the card's own
  // extension. Rather than make anyone find out, try the primary and swap to
  // the fallback once on error; only if BOTH miss does the tile show the URL,
  // which keeps a genuinely wrong template obvious instead of silently blank.
  const alt  = fill(T.img ? "" : (defaults.fallback || ""), c);
  const href = fill(T.page || defaults.page || "", c);
  // A card tagged for eight recipients renders eight chips, which in a single
  // column is a wall. Show the first few and count the rest.
  const all = Object.entries(c.facets || {})
    .flatMap(([k, v]) => v.map(x => `${k}:${x}`));
  const facets = all.slice(0, 5).map(t => `<span class="tag">${t}</span>`).join("")
    + (all.length > 5 ? `<span class="tag more">+${all.length - 5}</span>` : "");

  let thumb;
  if(!img){
    thumb = `<div class="ph">set an image URL template</div>`;
  } else {
    const onerr = alt && alt !== img
      ? `if(!this.dataset.retried){this.dataset.retried=1;this.src='${alt}';}`
        + `else{this.parentNode.innerHTML='<div class=ph>${img.replace(/'/g,"")}</div>';}`
      : `this.parentNode.innerHTML='<div class=ph>${img.replace(/'/g,"")}</div>';`;
    thumb = `<img loading="lazy" src="${img}" alt="" onerror="${onerr}">`;
  }

  // No description here on purpose: card_description is the field with the worst
  // precision, and showing it next to a result invites judging the search by
  // prose it deliberately does not rank on.
  const body = `<div class="thumbwrap">${thumb}</div>
    <div class="meta">
      <div class="t">${c.title}</div>
      <div class="c">${c.category} &middot; ${c.year || "?"} &middot; #${c.number}</div>
      ${c.why ? `<div class="why">${c.why}</div>` : ""}
      <div class="tags">${facets}</div>
    </div>`;

  // No card-page template yet, so do not dress tiles as links to a guessed 404.
  return href
    ? `<a class="card" href="${href}" target="_blank" rel="noopener">${body}</a>`
    : `<div class="card">${body}</div>`;
}

function paneHTML(title, list, note){
  const body = list.length
    ? `<div class="grid">${list.map(cardHTML).join("")}</div>`
    : `<div class="empty">${note || "No results."}</div>`;
  return `<section><h2>${title}</h2>${body}</section>`;
}

function render(){
  if(!last) return;
  // o is null when the search was fetched in New-only mode, so every use of it
  // below has to be gated on the comparison actually having been run.
  const n = last.new, o = last.old, both = mode === "both" && o;
  // The red liner. The live site prints one line in .msg-red above the results -
  // "Sorry! ... Did you mean X?" - and that is the only prose a searcher reads,
  // so it says the same things here: what was corrected, or that nothing
  // matched. Everything else stays in the pills below it, which are for us.
  const red = [];
  if(n.corrections && Object.keys(n.corrections).length){
    const fixes = Object.entries(n.corrections)
      .map(([a,b]) => `<b>${b}</b>`).join(", ");
    red.push(`Showing results for ${fixes}.`);
  }
  if(n.fallback)
    red.push("Sorry! The search query you entered did not find any matching "
           + "results. Here are the newest cards instead.");
  $("#redline").innerHTML = red.join(" ");
  $("#redline").style.display = red.length ? "" : "none";

  const bits = [];
  bits.push(`<span class="pill">strategy: ${n.strategy}</span>`);
  bits.push(`<span class="pill">${n.results.length} shown &middot; ${n.ms} ms</span>`);
  const meanYear = n.results.length
    ? Math.round(n.results.reduce((a, c) => a + (c.year || 0), 0) / n.results.length) : 0;
  if (meanYear) bits.push(`<span class="pill">mean year ${meanYear}</span>`);
  if(both){
    bits.push(o.results.length
      ? `<span class="pill">old: ${o.total}${o.capped ? "+ (capped)" : ""} matches</span>`
      : `<span class="pill bad">old: ZERO &rarr; carousel</span>`);
  }
  $("#status").innerHTML = bits.join("");

  const panes = $("#panes");
  if(both){
    panes.className = "panes split";
    panes.innerHTML = paneHTML("New engine", n.results)
      + paneHTML("Old (Sphinx pipeline)", o.results,
          "Zero results &mdash; production falls through to the popular-cards carousel.");
  } else {
    panes.className = "panes";
    panes.innerHTML = paneHTML(
      n.fallback ? "Latest cards (nothing matched your search)" : "Results",
      n.results);
  }
}

// ------------------------------------------------------------- autocomplete
let sugItems = [], sugSel = -1, sugTimer = null, sugSeq = 0;

function closeSug(){ $("#sug").classList.remove("show"); sugSel = -1; }

function renderSug(){
  const box = $("#sug");
  if(!sugItems.length){ closeSug(); return; }
  const typed = $("#q").value.trim().toLowerCase();
  box.innerHTML = sugItems.map((sg, i) => {
    // Bold only the part the user has NOT typed, the way a search box does,
    // so the eye lands on what each suggestion adds.
    const rest = sg.toLowerCase().startsWith(typed)
      ? `${sg.slice(0, typed.length)}<b>${sg.slice(typed.length)}</b>` : `<b>${sg}</b>`;
    return `<div class="sug${i === sugSel ? " sel" : ""}" data-i="${i}">`
         + `<span class="ic">&#9906;</span><span>${rest}</span></div>`;
  }).join("");
  box.classList.add("show");
  box.querySelectorAll(".sug").forEach(el => {
    el.onmousedown = e => { e.preventDefault(); pick(+el.dataset.i); };
  });
}

function pick(i){
  if(i < 0 || i >= sugItems.length) return;
  $("#q").value = sugItems[i];
  closeSug();
  run();
}

async function fetchSug(){
  const q = $("#q").value.trim();
  if(q.length < 2){ sugItems = []; closeSug(); return; }
  const seq = ++sugSeq;
  const r = await fetch("/api/suggest?q=" + encodeURIComponent(q));
  const d = await r.json();
  if(seq !== sugSeq) return;          // a later keystroke already won
  sugItems = d.suggestions || [];
  sugSel = -1;
  renderSug();
}

$("#q").addEventListener("keydown", e => {
  const open = $("#sug").classList.contains("show");
  if(e.key === "ArrowDown" && open){
    e.preventDefault(); sugSel = (sugSel + 1) % sugItems.length; renderSug();
  } else if(e.key === "ArrowUp" && open){
    e.preventDefault(); sugSel = (sugSel - 1 + sugItems.length) % sugItems.length; renderSug();
  } else if(e.key === "Enter"){
    if(open && sugSel >= 0){ e.preventDefault(); pick(sugSel); }
    else { closeSug(); run(); }
  } else if(e.key === "Escape"){
    closeSug();
  }
});
$("#q").addEventListener("blur", () => setTimeout(closeSug, 120));

let timer = null;
async function run(){
  const q = $("#q").value.trim();
  if(!q){ $("#status").innerHTML=""; $("#panes").innerHTML=""; last=null; return; }
  const r = await fetch("/api/search?q=" + encodeURIComponent(q)
    + "&limit=24&mode=" + mode + "&s=" + SID
    + "&boost=" + encodeURIComponent($("#boost").value));
  last = await r.json();
  defaults = last.defaults;
  if(!$("#tImg").value)  $("#tImg").value  = T.img  || defaults.image;
  if(!$("#tPage").value) $("#tPage").value = T.page || defaults.page;
  render();
}
$("#q").addEventListener("input", () => {
  clearTimeout(timer);  timer = setTimeout(run, 160);
  clearTimeout(sugTimer); sugTimer = setTimeout(fetchSug, 90);
});
// Switching to Compare has to re-fetch: the old pipeline is only run when the
// page says it is going to show it.
$("#mNew").onclick  = () => { mode="new";  $("#mNew").classList.add("on");
                              $("#mBoth").classList.remove("on"); render(); };
$("#mBoth").onclick = () => { mode="both"; $("#mBoth").classList.add("on");
                              $("#mNew").classList.remove("on");
                              last && last.old ? render() : run(); };
$("#mCfg").onclick  = () => $("#cfg").classList.toggle("show");
$("#boost").onchange = run;
$("#tSave").onclick = () => {
  T.img = $("#tImg").value.trim(); T.page = $("#tPage").value.trim();
  localStorage.setItem("tImg", T.img); localStorage.setItem("tPage", T.page);
  render();
};
</script></body></html>
"""


def lan_address(port):
    """
    The address someone else on this network should type.

    Found by asking the OS which of our interfaces it would use to reach the
    outside world - no packet is actually sent, a connected UDP socket just
    resolves the route. Reading hostname resolution instead tends to answer
    127.0.1.1 on Linux, which is useless to hand to anyone.
    """
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))     # TEST-NET-1: reserved, never routed
        return f"http://{probe.getsockname()[0]}:{port}"
    except OSError:
        return None
    finally:
        probe.close()


def main():
    global INDEX, LIVE_ROWS, QUERY_LOG
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else None
    port = PORT
    # Listening on every interface is what lets someone else open the page, and
    # it is also the whole security model: there is no login, so anyone who can
    # reach the port can search. --host=127.0.0.1 keeps it to this machine.
    host = "0.0.0.0"
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]

    export = se.find_export(path)
    # Beside the export, which is gitignored - so recorded searches stay local.
    QUERY_LOG = os.path.join(os.path.dirname(os.path.abspath(export)),
                             "query_log.tsv")
    rows = se.load_rows(export)

    started = time.perf_counter()
    INDEX = se.SearchIndex(rows)
    # The engine keeps only what it needs to rank; the UI also wants the file
    # extensions so it can build image URLs.
    INDEX.thumb_extn, INDEX.big_extn = {}, {}
    live = [r for r in rows
            if r["status_id"] == se.LIVE_STATUS and r["invalid_card"] == "0"
            and r["card_label_type"] not in se.EXCLUDE_LABEL_TYPES]
    for doc, row in enumerate(live):
        INDEX.thumb_extn[doc] = row["card_thumb_extn"]
        INDEX.big_extn[doc] = row["card_bigimage_extn"]
    LIVE_ROWS = live

    global SUGGESTER
    SUGGESTER = se.Suggester(INDEX, se.load_query_log())
    print(f"Indexed {INDEX.total:,} live cards in "
          f"{(time.perf_counter()-started)*1000:.0f} ms")
    print(f"  autocomplete: {len(SUGGESTER.phrases):,} suggestion phrases")
    print(f"\n  this machine     http://localhost:{port}")
    lan = lan_address(port) if host == "0.0.0.0" else None
    if lan:
        print(f"  same network     {lan}")
        print("                   (anyone who can reach it can search - there is no login)")
    elif host != "0.0.0.0":
        print(f"  bound to {host} only")
    print(f"  live look        http://localhost:{port}/live")
    print(f"  search report    http://localhost:{port}/report")
    print(f"                   recording to {QUERY_LOG}")
    print("\n  Thumbnails load directly from your live site. If they do not appear,")
    print("  click 'Image URLs' and paste the real pattern - the failed URL is")
    print("  printed in each empty tile so the template is easy to correct.\n")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
