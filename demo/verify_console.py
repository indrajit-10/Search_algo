import datetime
import json
import os
import sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "intent"), os.path.join(REPO, "search")]
from rank import Search
index_path, qfile, month = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = Search(path=index_path, today=datetime.date(2026, month, 15))
out = []
for q in json.load(open(qfile)):
    f = s.query(q, top=12, explain=True)
    out.append({
        "query": q, "understood": f["understood"],
        "corrections": [list(c) for c in f["corrections"]],
        "dated": not f["seasonal"], "candidates": f["candidates"], "kept": f["kept"],
        "fallback": f["fallback"],
        "phrases": [[ph, list(t)] for ph, t in f["phrases"]],
        "intent": [[c, sc] for c, sc in f["intent"]],
        "top": [[r["id"], r["code"], round(r["score"], 3)] for r in f["results"][:6]],
    })
print(json.dumps(out, indent=1))
