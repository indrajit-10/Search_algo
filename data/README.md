# Put your card export here

Drop the plain CSV in this folder and every script finds it. No path argument,
no unpacking, nothing to configure:

    data/card_database.csv

It must be the `cards` table with its normal columns. That is the same export
you already have.

Everything in this folder is gitignored, so your production data is never
committed.

---

Only relevant if you need to move the file somewhere with an upload limit: a
full export is around 33 MB. `gzip -9 -k card_database.csv` takes it to 8 MB,
and the scripts read `.csv.gz` and `.zip` directly if you ever want them to.
Locally there is no reason to bother.
