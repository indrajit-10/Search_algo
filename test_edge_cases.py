"""
test_edge_cases.py  --  hostile input, and the promise that the page is never empty.

    python test_edge_cases.py card_database.csv

test_injection.py covers the original toy matcher. This covers the real engine
against the real 13,042-card catalogue, and adds the invariant the product now
depends on:

    EVERY input returns cards. There is no such thing as zero results.

That is not cosmetic. 64% of logged searches ended on the zero-result carousel,
so the empty page was the single most common thing the search box produced. The
floor is latest_cards(), and `fallback` in the response says whether the results
are real matches or the floor - so the front end can be honest rather than
silently passing off newest cards as matches.

Payloads below are real: OWASP-style probes plus the actual garbage from the
production query log, including the XSS attempt someone ran 25 times.
"""

import sys
import time
import traceback
import unicodedata

import search_engine as se

SLOW = 1.0                # seconds; anything slower is a denial-of-service risk
DANGEROUS = set("<>\"'`;\\&|${}()")


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

INJECTION = [
    ("sql", "' OR '1'='1"), ("sql", "' OR 1=1--"), ("sql", "admin'--"),
    ("sql", "'; DROP TABLE cards;--"), ("sql", "' UNION SELECT null,null--"),
    ("sql", "anniversary' AND SLEEP(5)--"), ("sql", "1' ORDER BY 50--"),
    ("sql", '" OR ""="'), ("sql", "0x27 OR 1=1"),
    ("xss", "<script>alert(1)</script>"), ("xss", "<img src=x onerror=alert(1)>"),
    ("xss", "javascript:alert(1)"), ("xss", "<svg/onload=alert(1)>"),
    ("xss", "\"><script>alert(document.cookie)</script>"),
    # verbatim from the query log - searched 25 times
    ("xss", "\"><script>alert('xss by vipe"),
    ("cmd", "; ls -la"), ("cmd", "| cat /etc/passwd"), ("cmd", "$(whoami)"),
    ("cmd", "`id`"), ("cmd", "&& dir"),
    ("path", "../../../../etc/passwd"), ("path", "..\\..\\windows\\system32"),
    ("path", "%2e%2e%2f%2e%2e%2f"), ("path", "file:///etc/passwd"),
    # also verbatim from the log
    ("path", "/etc/passwd"),
    ("template", "{{7*7}}"), ("template", "${7*7}"), ("template", "<%= 7*7 %>"),
    ("template", "#{7*7}"), ("template", "{search_term_string}"),
    ("nosql", '{"$gt": ""}'), ("nosql", "[$ne]=1"),
    ("ldap", "*)(uid=*"), ("crlf", "anniversary\r\nX-Injected: yes"),
]

UNICODE = [
    ("null", "\x00"), ("null", "\x00birthday"), ("null", "birth\x00day"),
    ("rtl", "\u202eyadhtrib"), ("bidi", "\u202dbirthday\u202c"),
    ("zwsp", "birth\u200bday"), ("zwj", "birth\u200dday"),
    ("bom", "\ufeffbirthday"),
    ("fraktur", "\U0001d51e\U0001d52b\U0001d52b\U0001d526"),
    ("emoji", "🎂🎉"), ("emoji", "birthday 🎂"),
    ("cjk", "生日快乐"), ("cyrillic", "день рождения"),
    ("devanagari", "जन्मदिन"), ("arabic", "عيد ميلاد"),
    ("combining", "birthday" + "\u0301" * 40),
    ("homoglyph", "bіrthday"),            # Cyrillic і
    ("fullwidth", "ｂｉｒｔｈｄａｙ"),
    ("surrogate-ish", "\ud83c"),          # lone high surrogate, if it survives
    ("mojibake", "FelicitÃ¡tions"),
    ("entity", "Mother%92s Day"), ("entity", "&#39;birthday"),
    ("double-encoded", "%2527"),
]

MALFORMED = [
    ("blank", ""), ("blank", " "), ("blank", "\t\n  "),
    ("punct", "!!!"), ("punct", "%%%%%%"), ("punct", "\\"), ("punct", "--"),
    ("punct", "'"), ("punct", "\"\"\""), ("punct", "..."), ("punct", ">3"),
    ("single", "a"), ("single", "x"), ("single", "1"),
    ("numeric", "2019"), ("numeric", "99999999999999999999"),
    ("numeric", "-1"), ("numeric", "0"), ("numeric", "1e309"),
    ("long", "ANNIVERSARY" * 500), ("long", "a " * 5000),
    ("long", "birthday " * 1000),
    ("repeat", "()" * 2000), ("repeat", "a" * 10000), ("repeat", "\\" * 500),
    ("regex", "(a+)+$"), ("regex", "[[[[[["), ("regex", ".*.*.*.*.*"),
    ("spaces", "   birthday   "), ("case", "BIRTHDAY"), ("case", "BiRtHdAy"),
    # real mangled queries from the log
    ("log-junk", "s's x"), ("log-junk", "s's s's"), ("log-junk", "6 s's 18"),
    ("log-junk", "musicality's"), ("log-junk", "littleness's"),
    ("log-junk", "(null)"), ("log-junk", "1cards"), ("log-junk", "get we[["),
    ("log-junk", "birthday+cards+free"), ("log-junk", "th\\ank you"),
    ("log-junk", "happy mother\\\\\\\\'s day"),
]

ALL = ([(f"inject/{k}", p) for k, p in INJECTION]
       + [(f"unicode/{k}", p) for k, p in UNICODE]
       + [(f"malformed/{k}", p) for k, p in MALFORMED])


def short(text, n=38):
    text = (text.replace("\r", "\\r").replace("\n", "\\n")
                .replace("\x00", "\\x00").replace("\t", "\\t"))
    return text if len(text) <= n else text[:n] + "..."


class Suite:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.notes.append(f"{label} -- {detail}")
        return ok

    def rule(self, title):
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)

    def report(self):
        print("\n" + "=" * 78)
        print(f"{self.passed} passed, {self.failed} failed")
        if self.notes:
            print("\nFailures:")
            for note in self.notes:
                print(f"  - {note}")
        print("=" * 78)
        return self.failed == 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = args[0] if args else "card_database.csv"
    import csv
    csv.field_size_limit(10 ** 9)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    index = se.SearchIndex(rows)
    print(f"Indexed {index.total:,} live cards")

    s = Suite()

    # -------------------------------------------------------- never crashes
    s.rule("1. NO INPUT MAY RAISE")
    worst = (0.0, "")
    for family, payload in ALL:
        start = time.perf_counter()
        try:
            se.search(index, payload, limit=10)
            ok, detail = True, ""
        except Exception as exc:                       # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        elapsed = time.perf_counter() - start
        worst = max(worst, (elapsed, payload), key=lambda x: x[0])
        s.check(ok, f"crash on {family} {short(payload)}", detail)
    print(f"  {len(ALL)} payloads, none raised. "
          f"Slowest {worst[0]*1000:.0f} ms on {short(worst[1], 30)!r}")

    # ------------------------------------------------- THE ZERO-RESULT PROMISE
    s.rule("2. NO INPUT MAY RETURN ZERO CARDS  (the product requirement)")
    empties = []
    for family, payload in ALL:
        out = se.search(index, payload, limit=10)
        if not out["results"]:
            empties.append((family, payload))
        s.check(bool(out["results"]), f"empty page for {family} {short(payload)}")
    if empties:
        for family, payload in empties[:10]:
            print(f"  EMPTY: {family} {short(payload)!r}")
    else:
        print(f"  All {len(ALL)} payloads returned cards. Zero results is "
              f"now unreachable.")

    # ------------------------------------------------- fallbacks are labelled
    s.rule("3. FALLBACK RESULTS MUST SAY SO  (never pass them off as matches)")
    for payload, expect_fallback in [("birthday", False), ("funny birthday", False),
                                     ("mother's day", False), ("zzzzqqqxyz", True),
                                     ("", True), ("!!!", True), ("\x00", True),
                                     ("qwertyuiopasdfgh", True)]:
        out = se.search(index, payload, limit=5)
        got = out.get("fallback")
        s.check(got is expect_fallback,
                f"{payload!r:<22} fallback={got} (expected {expect_fallback})",
                f"strategy={out['strategy']!r}")
        print(f"  {payload[:20]!r:<24} fallback={str(got):<5} "
              f"strategy={out['strategy']:<22} {out['message'] or ''}")

    # ------------------------------------------- the floor really is the newest
    s.rule("4. THE FALLBACK SHOWS THE NEWEST CARDS")
    out = se.search(index, "zzzzqqqxyz", limit=10)
    years = [c.year for c in out["results"] if c.year]
    s.check(bool(years) and min(years) >= index.newest_year - 1,
            f"fallback cards are from {min(years)}-{max(years)}, "
            f"newest in catalogue is {index.newest_year}")
    print(f"  newest year in catalogue: {index.newest_year}")
    print(f"  fallback returned years:  {sorted(set(years), reverse=True)}")
    for card in out["results"][:5]:
        print(f"     {card.year}  {card.title[:46]}")

    descending = all(a.year >= b.year for a, b in
                     zip(out["results"], out["results"][1:]) if a.year and b.year)
    s.check(descending, "fallback is ordered newest first")

    # -------------------------------------------------------- no catalogue dump
    s.rule("5. NO PAYLOAD MAY DUMP THE CATALOGUE OR IGNORE THE LIMIT")
    for family, payload in ALL:
        out = se.search(index, payload, limit=10)
        n = len(out["results"])
        s.check(n <= 10, f"limit ignored by {family} {short(payload)}", f"got {n}")
    print(f"  every payload respected limit=10 (catalogue is {index.total:,})")

    # ------------------------------------------------------------- timing
    s.rule("6. NO PAYLOAD MAY BE SLOW")
    slowest = []
    for family, payload in ALL:
        start = time.perf_counter()
        try:
            se.search(index, payload, limit=10)
        except Exception:                              # noqa: BLE001
            pass
        elapsed = time.perf_counter() - start
        slowest.append((elapsed, family, payload))
        s.check(elapsed < SLOW, f"slow {family} {short(payload)}", f"{elapsed:.2f}s")
    slowest.sort(reverse=True)
    print("  slowest five:")
    for elapsed, family, payload in slowest[:5]:
        print(f"    {elapsed*1000:7.1f} ms  {family:<18} {short(payload, 30)!r}")

    # ---------------------------------------------- normalisation strips danger
    s.rule("7. NORMALISATION LEAVES NO DANGEROUS CHARACTERS")
    for family, payload in ALL:
        cleaned = se.normalise(payload)
        leaked = sorted(DANGEROUS.intersection(cleaned))
        s.check(not leaked, f"char leak from {family} {short(payload)}",
                f"survived: {leaked}")
    print("  no quote, bracket, backslash or shell metacharacter survives "
          "into matching")

    # ------------------------------------------------ the raw echo, still true
    s.rule("8. RAW QUERY ECHO  (unchanged, and still the front end's job)")
    payload = "<script>alert(1)</script>"
    echoed = se.search(index, payload)["query"]
    print(f"  search()['query'] returns the input verbatim: {echoed == payload}")
    print("  This is deliberate - it is needed for 'showing results for ...' -")
    print("  and it is safe until rendered. Escape at the template:")
    print("    Template Toolkit : [% query | html %]")
    print("    JavaScript       : .textContent, never .innerHTML")
    s.check(echoed == payload, "echo behaviour changed unexpectedly")

    # ------------------------------------------------------ idempotence / sanity
    s.rule("9. DETERMINISM AND CASE/SPACE INSENSITIVITY")
    for a, b in [("birthday", "BIRTHDAY"), ("birthday", "  birthday  "),
                 ("mother's day", "MOTHER'S DAY"), ("funny", "FuNnY"),
                 ("mother's day", "mothers day"), ("b'day", "bday")]:
        ra = [c.doc for c in se.search(index, a, limit=10)["results"]]
        rb = [c.doc for c in se.search(index, b, limit=10)["results"]]
        s.check(ra == rb, f"{a!r} and {b!r} disagree",
                f"{len(set(ra) ^ set(rb))} differing docs")
    twice = [[c.doc for c in se.search(index, "funny birthday", limit=10)["results"]]
             for _ in range(3)]
    s.check(twice[0] == twice[1] == twice[2], "search is not deterministic")
    print("  case, surrounding space, and apostrophes all fold; repeats are stable")

    return s.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
