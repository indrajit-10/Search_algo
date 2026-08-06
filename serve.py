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

# Best guess from the inhouse_music paths. Almost certainly needs adjusting -
# edit it here, or override it live in the page.
IMAGE_TEMPLATE = "https://www.123greetings.com/c/{q1}/thumb/{number}.{thumb_extn}"
PAGE_TEMPLATE = "https://www.123greetings.com/{occasion}/{subcat}/{page}.html"

PORT = 8000
INDEX = None
LIVE_ROWS = None


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


def do_search(query, limit, boost):
    started = time.perf_counter()
    out = se.search(INDEX, query, limit=limit, recency_boost=boost)
    elapsed = (time.perf_counter() - started) * 1000

    new_results = [card_payload(c, out["explain"].get(c.doc, ""), INDEX)
                   for c in out["results"]]

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
        "defaults": {"image": IMAGE_TEMPLATE, "page": PAGE_TEMPLATE},
    }


class Handler(BaseHTTPRequestHandler):
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

        if route == "/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            query = (params.get("q") or [""])[0]
            limit = min(int((params.get("limit") or ["24"])[0]), 60)
            try:
                boost = max(0.0, min(float((params.get("boost")
                                            or [se.RECENCY_BOOST])[0]), 1.0))
            except ValueError:
                boost = se.RECENCY_BOOST
            self._send(json.dumps(do_search(query, limit, boost)),
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
    <input id="q" placeholder="Try: funny birthday for mom &nbsp;/&nbsp; birthdya &nbsp;/&nbsp; flash card &nbsp;/&nbsp; aniversary" autofocus>
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
  const img = fill(T.img || defaults.image || "", c);
  const href = fill(T.page || defaults.page || "", c);
  const facets = Object.entries(c.facets || {})
    .flatMap(([k, v]) => v.map(x => `<span class="tag">${k}:${x}</span>`)).join("");
  // If the image 404s the placeholder shows the URL that was tried, which makes
  // a wrong template obvious instead of silently blank.
  return `<a class="card" href="${href}" target="_blank" rel="noopener">
    <div class="thumbwrap">
      ${img ? `<img loading="lazy" src="${img}" alt=""
        onerror="this.parentNode.innerHTML='<div class=ph>${img.replace(/'/g,"")}</div>'">`
       : `<div class="ph">set an image URL template</div>`}
    </div>
    <div class="meta">
      <div class="t">${c.title}</div>
      <div class="c">${c.category} &middot; ${c.year || "?"} &middot; #${c.number}</div>
      ${c.why ? `<div class="why">${c.why}</div>` : ""}
      <div class="tags">${facets}</div>
    </div></a>`;
}

function paneHTML(title, list, note){
  const body = list.length
    ? `<div class="grid">${list.map(cardHTML).join("")}</div>`
    : `<div class="empty">${note || "No results."}</div>`;
  return `<section><h2>${title}</h2>${body}</section>`;
}

function render(){
  if(!last) return;
  const n = last.new, o = last.old;
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
  if(mode === "both"){
    bits.push(o.results.length
      ? `<span class="pill">old: ${o.total}${o.capped ? "+ (capped)" : ""} matches</span>`
      : `<span class="pill bad">old: ZERO &rarr; carousel</span>`);
  }
  $("#status").innerHTML = bits.join("");

  const panes = $("#panes");
  if(mode === "both"){
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

let timer = null;
async function run(){
  const q = $("#q").value.trim();
  if(!q){ $("#status").innerHTML=""; $("#panes").innerHTML=""; last=null; return; }
  const r = await fetch("/api/search?q=" + encodeURIComponent(q)
    + "&limit=24&boost=" + encodeURIComponent($("#boost").value));
  last = await r.json();
  defaults = last.defaults;
  if(!$("#tImg").value)  $("#tImg").value  = T.img  || defaults.image;
  if(!$("#tPage").value) $("#tPage").value = T.page || defaults.page;
  render();
}
$("#q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 160); });
$("#mNew").onclick  = () => { mode="new";  $("#mNew").classList.add("on");
                              $("#mBoth").classList.remove("on"); render(); };
$("#mBoth").onclick = () => { mode="both"; $("#mBoth").classList.add("on");
                              $("#mNew").classList.remove("on"); render(); };
$("#mCfg").onclick  = () => $("#cfg").classList.toggle("show");
$("#boost").onchange = run;
$("#tSave").onclick = () => {
  T.img = $("#tImg").value.trim(); T.page = $("#tPage").value.trim();
  localStorage.setItem("tImg", T.img); localStorage.setItem("tPage", T.page);
  render();
};
</script></body></html>
"""


def main():
    global INDEX, LIVE_ROWS
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else "card_database.csv"
    port = PORT
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])

    csv.field_size_limit(10 ** 9)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    started = time.perf_counter()
    INDEX = se.SearchIndex(rows)
    # The engine keeps only what it needs to rank; the UI also wants the file
    # extensions so it can build image URLs.
    INDEX.thumb_extn, INDEX.big_extn = {}, {}
    live = [r for r in rows
            if r["status_id"] == se.LIVE_STATUS and r["invalid_card"] == "0"]
    for doc, row in enumerate(live):
        INDEX.thumb_extn[doc] = row["card_thumb_extn"]
        INDEX.big_extn[doc] = row["card_bigimage_extn"]
    LIVE_ROWS = live

    print(f"Indexed {INDEX.total:,} live cards in "
          f"{(time.perf_counter()-started)*1000:.0f} ms")
    print(f"\n  http://localhost:{port}\n")
    print("  Thumbnails load directly from your live site. If they do not appear,")
    print("  click 'Image URLs' and paste the real pattern - the failed URL is")
    print("  printed in each empty tile so the template is easy to correct.\n")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
