#!/usr/bin/env sh
#
# run.sh  --  one entry point, in its own environment.
#
#   ./run.sh              start the browser interface  (http://localhost:8000)
#   ./run.sh test         every test suite, in order
#   ./run.sh search       interactive search prompt
#   ./run.sh compare      old Sphinx pipeline vs new, side by side
#   ./run.sh evaluate     replay the query log through both engines
#   ./run.sh audit        measure the old system's failures from your export
#   ./run.sh doctor       check the environment without running anything
#
# THERE ARE NO DEPENDENCIES TO INSTALL.
#
# Verified by walking the AST of every file: zero third-party imports. Only
# bisect, collections, csv, gzip, html, http, io, json, math, os, posixpath, re,
# sys, time, traceback, unicodedata, urllib and zipfile, all standard library.
#
# So what is the virtualenv for? Not packages - isolation from a broken host.
# The failures this actually prevents are `python` pointing at Python 2, a
# PYTHONPATH from another project shadowing a stdlib module (a stray csv.py two
# directories up will break this and the traceback will not say why), and
# user site-packages overriding something.
#
# If venv creation fails - Debian and Ubuntu split it into a separate
# python3-venv package - this falls back to running against the interpreter
# directly and says so. With no dependencies that is perfectly safe, which is
# the point: the environment is a convenience here, never a prerequisite.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE"
VENV="$HERE/.venv"
MIN_MAJOR=3
MIN_MINOR=7

say()  { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# Find an interpreter new enough to run this.
# --------------------------------------------------------------------------
find_python() {
    for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 \
                     python3.8 python3.7 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MIN_MAJOR,$MIN_MINOR) else 1)" 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON=$(find_python) || fail "No Python $MIN_MAJOR.$MIN_MINOR or newer found on PATH.

  macOS:          brew install python3
  Debian/Ubuntu:  sudo apt install python3
  Windows:        use run.bat, or install from python.org

  Everything here is standard library, so any Python $MIN_MAJOR.$MIN_MINOR+ will do."

# --------------------------------------------------------------------------
# Build the environment on first run. Never rebuild it on later runs.
# --------------------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    say "First run: creating an isolated environment in .venv/"
    if "$PYTHON" -m venv "$VENV" >/dev/null 2>&1; then
        say "  done ($("$VENV/bin/python" -V 2>&1))"
    else
        say "  could not create one - python3-venv is probably not installed."
        say "  Falling back to $PYTHON directly. Nothing needs installing, so"
        say "  this works exactly the same; you just lose the isolation."
        say ""
    fi
fi

if [ -x "$VENV/bin/python" ]; then
    PY="$VENV/bin/python"
else
    PY="$PYTHON"
fi

# -E ignores PYTHON* environment variables, -s ignores user site-packages.
# Between them, another project's PYTHONPATH cannot shadow a stdlib module.
RUN="$PY -E -s"

# --------------------------------------------------------------------------
# Is there an export to work with?
# --------------------------------------------------------------------------
require_data() {
    for f in data/card_database.csv data/card_database.csv.gz data/card_database.zip; do
        [ -f "$f" ] && return 0
    done
    # any single csv-ish file will do
    [ -n "$(ls -1 data/ 2>/dev/null | grep -Ei '\.(csv|csv\.gz|zip)$' || true)" ] && return 0
    fail "No card export found.

  Copy your export in:

      cp /path/to/card_database.csv data/

  Then run this again. CSV please, not xlsx - Excel reformats the dates and
  mangles the apostrophes this depends on."
}

# --------------------------------------------------------------------------
case "${1:-serve}" in
  doctor)
    say "python      $PY"
    say "version     $($PY -V 2>&1)"
    say "environment $([ -x "$VENV/bin/python" ] && echo '.venv (isolated)' || echo 'system interpreter (no venv)')"
    say "third-party $($RUN - <<'PY'
import ast, sys, pathlib
std = set(getattr(sys, "stdlib_module_names", ()))
local = {p.stem for p in pathlib.Path(".").glob("*.py")}
bad = set()
for f in pathlib.Path(".").glob("*.py"):
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.Import): names = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module: names = [n.module.split(".")[0]]
        else: continue
        bad |= {m for m in names if std and m not in std and m not in local}
print(", ".join(sorted(bad)) if bad else "none - pure standard library")
PY
)"
    if ls data/ 2>/dev/null | grep -qEi '\.(csv|csv\.gz|zip)$'; then
      say "export      $(ls -1 data/ | grep -Ei '\.(csv|csv\.gz|zip)$' | head -1)"
    else
      say "export      MISSING - copy it to data/card_database.csv"
    fi
    ;;

  test)
    require_data
    say "=========== 1/3  engine tests ==========="
    $RUN search_engine.py </dev/null
    say ""
    say "=========== 2/3  edge cases ============="
    $RUN test_edge_cases.py
    say ""
    say "=========== 3/3  query log replay ======="
    $RUN evaluate.py
    ;;

  search)   require_data; $RUN search_engine.py ;;
  compare)  require_data; $RUN search_engine.py --compare ;;
  evaluate) require_data; $RUN evaluate.py ;;
  edge)     require_data; $RUN test_edge_cases.py ;;
  audit)    require_data; $RUN audit_catalogue.py ;;
  serve)    require_data; $RUN serve.py ;;

  -h|--help|help)
    sed -n '2,20p' "$0" | sed 's/^#//' | sed 's/^ //'
    ;;

  *)
    fail "Unknown command: $1
Try: ./run.sh          (interface)
     ./run.sh test     (all suites)
     ./run.sh --help"
    ;;
esac
