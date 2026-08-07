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

import csv
import html
import json
import posixpath
import sys
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


def do_search(query, limit, boost, compare=True):
    started = time.perf_counter()
    out = se.search(INDEX, query, limit=limit, recency_boost=boost)
    elapsed = (time.perf_counter() - started) * 1000

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
            self._send(json.dumps(do_search(query, limit, boost, compare)),
                       "application/json")
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
.panes{display:grid;gap:26px}
.panes.split{grid-template-columns:1fr 1fr}
@media(max-width:1000px){.panes.split{grid-template-columns:1fr}}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
  margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  overflow:hidden;display:flex;flex-direction:column;text-decoration:none;color:inherit}
.card:hover{border-color:var(--accent)}
.thumbwrap{aspect-ratio:4/3;background:var(--chip);display:flex;align-items:center;
  justify-content:center;overflow:hidden;position:relative}
.thumbwrap img{width:100%;height:100%;object-fit:cover;display:block}
.ph{color:var(--muted);font:11px ui-monospace,monospace;text-align:center;padding:10px;
  word-break:break-all;line-height:1.35}
.meta{padding:9px 10px;display:flex;flex-direction:column;gap:5px;flex:1}
.t{font-weight:600;font-size:13.5px;line-height:1.3}
.c{font:11px ui-monospace,monospace;color:var(--muted);word-break:break-all}
.why{font-size:11px;color:var(--good);line-height:1.35}
.tags{display:flex;gap:4px;flex-wrap:wrap;margin-top:auto;padding-top:4px}
.tag{font-size:10px;background:var(--chip);border-radius:4px;padding:2px 6px;color:var(--muted)}
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
  <div class="status" id="status"></div>
  <div class="panes" id="panes"></div>
</main>
<script>
const $ = s => document.querySelector(s);
let mode = "new", defaults = {}, last = null;
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
  const facets = Object.entries(c.facets || {})
    .flatMap(([k, v]) => v.map(x => `<span class="tag">${k}:${x}</span>`)).join("");

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
  const bits = [];
  if(n.corrections && Object.keys(n.corrections).length)
    bits.push(`<span class="pill good">corrected: ${
      Object.entries(n.corrections).map(([a,b])=>`${a} &rarr; ${b}`).join(", ")}</span>`);
  if(n.fallback)
    bits.push(`<span class="pill bad">no matches &mdash; showing newest cards</span>`);
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
    + "&limit=24&mode=" + mode
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
    global INDEX, LIVE_ROWS
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

    rows = se.load_rows(se.find_export(path))

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
    print("\n  Thumbnails load directly from your live site. If they do not appear,")
    print("  click 'Image URLs' and paste the real pattern - the failed URL is")
    print("  printed in each empty tile so the template is easy to correct.\n")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
