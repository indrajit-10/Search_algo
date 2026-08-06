"""
scrape_catalogue.py -- pull real card titles from 123greetings.com
Standard library only. Writes cards.json.

BEFORE RUNNING:
1. robots.txt is checked automatically. Do not remove that check.
2. 2 second delay between requests. Do not lower it.
3. Tell whoever owns the servers before a large run.
4. The RIGHT source is a DB query:
      SELECT card_title, category_title, tags FROM CardMaster
   Ask for a dump first. Scraping only sees public pages and will miss
   unpublished/expired cards that still sit in the search index.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

BASE = "https://www.123greetings.com"
DELAY_SECONDS = 2.0
TIMEOUT = 20
USER_AGENT = "123g-search-qc-testing/1.0 (internal QC; catalogue sampling)"
OUTPUT_FILE = "cards.json"

CATEGORIES = [
    "/birthday/birthday_fun/",
    "/birthday/happy_birthday/",
    "/birthday/birthday_wishes/",
    "/birthday/belated_birthday/",
    "/birthday/husband_and_wife/",
    "/birthday/mom_n_dad/",
    "/birthday/milestone/",
    "/anniversary/anniversary_etc/",
    "/anniversary/our_anniversary/for_her/",
    "/anniversary/wedding_anniversary/couple/",
    "/love/",
    "/friendship/",
    "/family/",
    "/congratulations/",
    "/thank_you/",
    "/general/hi/",
    "/general/thinking_of_you/",
    "/cute_cards/",
    "/encouragement_and_inspiration/",
    "/events/friendship_day/",
]

NOT_CARDS = {
    "sitemap.html", "terms_of_use.html", "privacy_policy.html",
    "cookie_policy.html", "copyright_policy.html", "contact_us.html",
    "free-ecards-faq.html",
}


class CardLinkParser(HTMLParser):
    """
    Card links look like:
        <a href="/birthday/birthday_fun/a_vintage_birthday_wish.html"
           title="A Vintage Birthday Wish">
    The VISIBLE text is truncated with an ellipsis. The title attribute has
    the full name. We read the attribute -- truncated titles would poison
    the dictionary with fragments.
    """

    def __init__(self):
        super().__init__()
        self.found = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        href, title = a.get("href"), a.get("title")
        if not href or not title:
            return
        if not href.endswith(".html"):
            return
        if href.rsplit("/", 1)[-1] in NOT_CARDS:
            return
        if href.count("/") < 2:
            return
        title = title.strip()
        if not title or title.endswith("...") or len(title) < 3:
            return
        self.found[urllib.parse.urljoin(BASE, href)] = title


def make_robots_checker():
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    try:
        rp.read()
        print(f"robots.txt loaded from {BASE}/robots.txt")
    except Exception as exc:
        print(f"Could not read robots.txt ({exc}).")
        print("Refusing to continue without it.")
        raise SystemExit(1)
    return rp


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def category_from_url(url):
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    parts = parts[:-1]
    if not parts:
        return "Uncategorised"
    return ": ".join(p.replace("_", " ").title() for p in parts[:2])


def scrape():
    robots = make_robots_checker()
    cards, skipped, failed = {}, [], []

    for index, path in enumerate(CATEGORIES, start=1):
        url = urllib.parse.urljoin(BASE, path)

        if not robots.can_fetch(USER_AGENT, url):
            print(f"[{index}/{len(CATEGORIES)}] BLOCKED by robots.txt: {path}")
            skipped.append(path)
            continue

        try:
            html = fetch(url)
        except urllib.error.HTTPError as exc:
            print(f"[{index}/{len(CATEGORIES)}] HTTP {exc.code}: {path}")
            failed.append(path)
            time.sleep(DELAY_SECONDS)
            continue
        except Exception as exc:
            print(f"[{index}/{len(CATEGORIES)}] FAILED {type(exc).__name__}: {path}")
            failed.append(path)
            time.sleep(DELAY_SECONDS)
            continue

        parser = CardLinkParser()
        parser.feed(html)
        new = 0
        for card_url, title in parser.found.items():
            if card_url not in cards:
                cards[card_url] = {
                    "title": title,
                    "category": category_from_url(card_url),
                    "url": card_url,
                }
                new += 1

        print(f"[{index}/{len(CATEGORIES)}] {path:<48} {new:>3} new  "
              f"({len(cards)} total)")
        time.sleep(DELAY_SECONDS)

    return cards, skipped, failed


def save(cards):
    records = []
    for i, card in enumerate(sorted(cards.values(), key=lambda c: c["title"]), 1):
        records.append({
            "id": i,
            "title": card["title"],
            "category": card["category"],
            "tags": [],
            "url": card["url"],
        })
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
    return records


def report(records):
    print()
    print("=" * 70)
    print("WHAT YOU GOT")
    print("=" * 70)
    print(f"  {len(records)} unique cards -> {OUTPUT_FILE}")

    words = set()
    for r in records:
        for w in re.findall(r"[a-z0-9']+", r["title"].lower()):
            if len(w) >= 3:
                words.add(w)
    print(f"  {len(words)} unique words of vocabulary")
    print()

    print("  Sample titles:")
    for r in records[:8]:
        print(f"    {r['title'][:58]}")
    print()

    for probe in ("anniversary", "valentine", "birthday", "funny", "fun"):
        hits = [r for r in records if probe in r["title"].lower()]
        print(f"  titles containing '{probe}': {len(hits)}")
    print()
    print("  NOTE: tags are empty. Category pages do not expose them.")
    print("  Get tags from CardMaster, or the tag-match rule cannot be tested.")
    print("=" * 70)


if __name__ == "__main__":
    print("Scraping 123greetings category pages")
    print(f"Delay {DELAY_SECONDS}s between requests. "
          f"Roughly {len(CATEGORIES) * DELAY_SECONDS:.0f}s minimum.\n")

    cards, skipped, failed = scrape()

    if not cards:
        print("\nNothing collected. Check connectivity, proxy, or robots.txt rules.")
        raise SystemExit(1)

    records = save(cards)
    report(records)

    if skipped:
        print(f"\nBlocked by robots.txt ({len(skipped)}): {', '.join(skipped)}")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")

    print("\nNext: run load_real_data.py")
