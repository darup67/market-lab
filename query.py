#!/usr/bin/env python3
"""
SQL over the recorded windows.

Loads every JSONL row into an in-memory SQLite table called `w` and runs your
query against it. Nothing is written back — the JSONL files stay the source of
truth, and a bad query costs you nothing.

    python3 query.py                          show the schema and a few rows
    python3 query.py "SELECT ..."             run a query
    python3 query.py -f myquery.sql
    echo "SELECT ..." | python3 query.py

Columns: series window ticker open_time close_time target settled_yes
         settlement trades volume_fp first last min max range q25 q50 q75
         path (JSON text) recorded_at

`min` and `max` are also SQL functions, so quote them when SQLite could read
either — `SELECT "max" FROM w` is unambiguous, `SELECT max FROM w` in an
aggregate context may not be. The rest parse fine bare.
"""
import json, sqlite3, sys, glob, os

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = ["series","window","ticker","open_time","close_time","target","settled_yes",
        "settlement","trades","volume_fp","first","last","min","max","range",
        "q25","q50","q75","path","recorded_at"]

def build():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    quoted = ", ".join('"%s"' % c for c in COLS)
    db.execute("CREATE TABLE w (%s)" % quoted)
    rows, bad = [], 0
    for f in sorted(glob.glob(os.path.join(HERE, "data", "*.jsonl"))):
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                bad += 1     # a torn append is one row, not a dead query
                continue
            r["path"] = json.dumps(r.get("path"))
            rows.append([r.get(c) for c in COLS])
    db.executemany(f"INSERT INTO w VALUES ({','.join('?' * len(COLS))})", rows)
    db.commit()
    return db, len(rows), bad

def show(cur):
    got = cur.fetchall()
    if not got:
        print("(no rows)")
        return
    names = got[0].keys()
    widths = [max(len(n), max(len(str(r[n])) for r in got)) for n in names]
    print("  " + "  ".join(n.ljust(widths[i]) for i, n in enumerate(names)))
    print("  " + "  ".join("-" * widths[i] for i in range(len(names))))
    for r in got[:200]:
        print("  " + "  ".join(str(r[n]).ljust(widths[i]) for i, n in enumerate(names)))
    if len(got) > 200:
        print(f"  … {len(got) - 200} more rows")
    print(f"\n  {len(got)} row(s)")

def main():
    db, n, bad = build()
    print(f"loaded {n} windows{f' ({bad} unparseable rows skipped)' if bad else ''}\n")

    if len(sys.argv) > 2 and sys.argv[1] == "-f":
        sql = open(sys.argv[2]).read()
    elif len(sys.argv) > 1:
        sql = " ".join(sys.argv[1:])
    else:
        # Only read stdin when data is actually waiting. isatty() alone is not
        # enough: under a scheduler or a captured shell, stdin is neither a tty
        # nor closed, and a bare read() blocks forever.
        sql = ""
        if not sys.stdin.isatty():
            import select
            if select.select([sys.stdin], [], [], 0.0)[0]:
                sql = sys.stdin.read()

    if not sql.strip():
        print(__doc__.strip())
        print("\nSample rows:\n")
        show(db.execute('SELECT series,window,"first","last","range",settled_yes '
                        'FROM w ORDER BY window DESC LIMIT 5'))
        return

    try:
        show(db.execute(sql))
    except sqlite3.Error as e:
        print(f"SQL error: {e}")
        print('hint: the table is `w`; quote "min"/"max" if SQLite reads them as functions')
        sys.exit(1)

if __name__ == "__main__":
    main()
