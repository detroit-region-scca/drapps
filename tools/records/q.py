import sqlite3, sys
c = sqlite3.connect('data/scca.db')
for q in sys.argv[1:]:
    cur = c.execute(q); print(' | '.join(d[0] for d in cur.description))
    for r in cur.fetchall(): print(' | '.join('' if v is None else str(v)[:40] for v in r))
    print()
