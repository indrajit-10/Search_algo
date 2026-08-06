"""
test_injection.py  --  injection and robustness suite for search_logic.py

Put this file in the SAME FOLDER as search_logic.py, then run:
    python test_injection.py

What this proves and what it does NOT prove
-------------------------------------------
This tests the Python matching logic in isolation. The logic has no database
and renders no HTML, so a payload cannot "execute" here. What we are checking
is that malicious input:

    1. does not raise an exception (a 500 error is a defect)
    2. does not hang the process (denial of service)
    3. does not leak the whole catalogue (auth/filter bypass pattern)
    4. does not survive normalisation into the matching layer

The layers that CAN actually be exploited are Perl (SQL) and the front end
(HTML rendering). Test 5 below shows where this file hands them a live grenade.
"""

import time
import traceback

from search_logic import search, normalise

# ---------------------------------------------------------------------------
# Payloads. These are standard OWASP-style test vectors - the same strings a
# scanner like ZAP or Burp would fire at your search box. Nothing exotic.
# ---------------------------------------------------------------------------

SQL_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    "admin'--",
    "'; DROP TABLE cards;--",
    "' UNION SELECT null,null,null--",
    "anniversary' AND SLEEP(5)--",
    "1' ORDER BY 50--",
    '" OR ""="',
]

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg/onload=alert(1)>",
    "\"><script>alert(document.cookie)</script>",
    "<iframe src='evil'></iframe>",
]

COMMAND_PAYLOADS = [
    "; ls -la",
    "| cat /etc/passwd",
    "$(whoami)",
    "`id`",
    "&& dir",
]

PATH_PAYLOADS = [
    "../../../../etc/passwd",
    "..\\..\\..\\windows\\system32\\config\\sam",
    "%2e%2e%2f%2e%2e%2f",
    "file:///etc/passwd",
]

TEMPLATE_PAYLOADS = [
    "{{7*7}}",
    "${7*7}",
    "<%= 7*7 %>",
    "#{7*7}",
]

MALFORMED_PAYLOADS = [
    "\x00",                      # null byte
    "\x00anniversary",           # null byte prefix
    "anniversary\r\nX-Injected: yes",   # header injection / CRLF
    "\u202eyradinna",            # right-to-left override
    "𝔞𝔫𝔫𝔦𝔳𝔢𝔯𝔰𝔞𝔯𝔶",             # unicode lookalikes
    "ANNIVERSARY" * 500,         # very long input
    "a " * 5000,                 # many tokens
    "%%%%%%",
    "\\",
    "()" * 2000,                 # regex stress
]

ALL_PAYLOADS = (
    [("SQL", p) for p in SQL_PAYLOADS]
    + [("XSS", p) for p in XSS_PAYLOADS]
    + [("CMD", p) for p in COMMAND_PAYLOADS]
    + [("PATH", p) for p in PATH_PAYLOADS]
    + [("TEMPLATE", p) for p in TEMPLATE_PAYLOADS]
    + [("MALFORMED", p) for p in MALFORMED_PAYLOADS]
)

TOTAL_CARDS = 15          # keep in sync with len(CARDS) in search_logic.py
SLOW_THRESHOLD = 1.0      # seconds; anything slower is a DoS risk
DANGEROUS_CHARS = set("<>\"'`;\\&|${}()")


def short(s, n=42):
    s = s.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "\\x00")
    return s if len(s) <= n else s[:n] + "..."


passed = 0
failed = 0
findings = []


def check(condition, label, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        findings.append(f"{label} -- {detail}")
    return condition


# ---------------------------------------------------------------------------
# TEST 1 - No payload may raise an exception
# ---------------------------------------------------------------------------
print("=" * 72)
print("TEST 1  --  CRASH RESISTANCE (no payload may throw)")
print("=" * 72)

for family, payload in ALL_PAYLOADS:
    try:
        search(payload)
        ok = True
        detail = ""
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {family:<10} {short(payload)}")
    check(ok, f"CRASH on {family} payload {short(payload)}", detail)
print()


# ---------------------------------------------------------------------------
# TEST 2 - No payload may return the entire catalogue
#          (the classic ' OR '1'='1 outcome - a filter bypass)
# ---------------------------------------------------------------------------
print("=" * 72)
print("TEST 2  --  NO FILTER BYPASS (payload must not dump the catalogue)")
print("=" * 72)

for family, payload in ALL_PAYLOADS:
    try:
        out = search(payload, limit=TOTAL_CARDS)
        n = len(out["results"])
    except Exception:
        n = -1
    ok = 0 <= n < TOTAL_CARDS
    print(f"[{'PASS' if ok else 'FAIL'}] {family:<10} {short(payload):<46} -> {n} results")
    check(ok, f"BYPASS on {family} {short(payload)}", f"returned {n} of {TOTAL_CARDS}")
print()


# ---------------------------------------------------------------------------
# TEST 3 - Normalisation must strip every dangerous character
#          If a quote or angle bracket survives into matching, the payload is
#          still alive further down the stack.
# ---------------------------------------------------------------------------
print("=" * 72)
print("TEST 3  --  NORMALISATION STRIPS DANGEROUS CHARACTERS")
print("=" * 72)

for family, payload in ALL_PAYLOADS:
    cleaned = normalise(payload)
    leaked = sorted(DANGEROUS_CHARS.intersection(cleaned))
    ok = not leaked
    print(f"[{'PASS' if ok else 'FAIL'}] {family:<10} {short(payload):<46} -> {short(cleaned, 24)!r}")
    check(ok, f"CHAR LEAK on {family} {short(payload)}", f"survived: {leaked}")
print()


# ---------------------------------------------------------------------------
# TEST 4 - Timing. A payload that makes search slow is a DoS.
#          Long inputs hit the fuzzy matcher hardest - it is O(n*m) per card.
# ---------------------------------------------------------------------------
print("=" * 72)
print("TEST 4  --  TIMING (no payload may take over 1.0s)")
print("=" * 72)

for family, payload in ALL_PAYLOADS:
    start = time.perf_counter()
    try:
        search(payload)
    except Exception:
        pass
    elapsed = time.perf_counter() - start
    ok = elapsed < SLOW_THRESHOLD
    flag = "" if ok else "   <-- SLOW"
    print(f"[{'PASS' if ok else 'FAIL'}] {family:<10} {short(payload):<46} {elapsed*1000:7.1f} ms{flag}")
    check(ok, f"SLOW on {family} {short(payload)}", f"{elapsed:.2f}s")
print()


# ---------------------------------------------------------------------------
# TEST 5 - THE REAL FINDING
#          search() returns the RAW query back in its response, unsanitised.
#          The matching layer is safe. The response payload is not.
# ---------------------------------------------------------------------------
print("=" * 72)
print("TEST 5  --  RAW QUERY ECHO (reflected XSS vector)")
print("=" * 72)

echo_vulnerable = []
for payload in XSS_PAYLOADS:
    out = search(payload)
    echoed = out["query"]
    intact = echoed == payload
    if intact:
        echo_vulnerable.append(payload)
    print(f"[{'ECHOED RAW' if intact else 'sanitised '}] {short(payload):<46} -> {short(str(echoed), 46)!r}")

print()
if echo_vulnerable:
    print(f"  FINDING: {len(echo_vulnerable)} of {len(XSS_PAYLOADS)} XSS payloads are returned")
    print("  verbatim in the 'query' field of the response.")
    print()
    print("  This is NOT exploitable inside Python. It becomes exploitable the")
    print("  moment the front end renders it, e.g.")
    print('      No results for "<script>alert(1)</script>"')
    print()
    print("  Fix belongs in the template layer, not here:")
    print("    Perl / Template Toolkit :  [% query | html %]")
    print("    HTML::Entities          :  encode_entities($query)")
    print("    JavaScript              :  use .textContent, never .innerHTML")
print()


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
print("=" * 72)
print("SUMMARY")
print("=" * 72)
print(f"  {passed} passed, {failed} failed")
if findings:
    print("\n  Defects to log:")
    for f in findings:
        print(f"    - {f}")
else:
    print("  No crashes, no bypasses, no character leaks, no slow paths.")
print(f"\n  Separately: raw query echo affects {len(echo_vulnerable)} payloads (see TEST 5).")
print("=" * 72)
