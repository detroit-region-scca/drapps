"""Export the public records database for drapps (drapps.drscca.org/records): results, events, standings and people.
No worker points, no full text, no raw lines. Usage: export_records.py <scca.db> <records.db>"""
import collections, difflib, os, sqlite3, sys, unicodedata
src, out = sys.argv[1], sys.argv[2]
tmp = out + '.tmp'
if os.path.exists(tmp): os.remove(tmp)
d = sqlite3.connect(tmp)
d.execute(f"ATTACH '{src}' AS s")
d.executescript('''
CREATE TABLE files AS SELECT id, event_id, program, year, date, kind, sorted_name AS name, drive_url FROM s.files
  WHERE program IN ('Autocross', 'RallyCross', 'Rally', 'Road Racing');
CREATE TABLE events AS SELECT * FROM s.events WHERE id IN (SELECT event_id FROM files WHERE event_id IS NOT NULL);
CREATE TABLE result_rows AS SELECT id, file_id, event_id, person_id, program, year, date, section, position, class, car_number,
  driver, vehicle, total, raw_time, pax_time, total_s, raw_s, pax_s, line_no FROM s.result_rows
  WHERE event_id IN (SELECT id FROM events);
CREATE TABLE season_standings AS SELECT id, program, year, class, place, person_id, name, score, file_id FROM s.season_standings;
CREATE TABLE people AS SELECT id, name FROM s.people
  WHERE id IN (SELECT person_id FROM result_rows UNION SELECT person_id FROM season_standings);
CREATE INDEX rr_person ON result_rows(person_id); CREATE INDEX rr_event ON result_rows(event_id);
CREATE INDEX ss_person ON season_standings(person_id); CREATE INDEX ss_season ON season_standings(program, year);
CREATE INDEX ev_season ON events(program, year); CREATE INDEX f_event ON files(event_id);
CREATE INDEX p_name ON people(name COLLATE NOCASE);
''')
import re
def clean(name):
    """"04142019 Season Opener" -> "Season Opener": drop date codes, event numbers and region prefixes the timing laptop adds."""
    n = re.sub(r'\b\d{6,8}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b', ' ', name or '')
    n = re.sub(r'^\s*(detroit (region )?scca|drscca)\s*-?\s*', '', n, flags=re.I)
    n = re.sub(r'#\s*\d+\s*-?', ' ', n)
    n = re.sub(r'\s*-\s*-\s*', ' - ', n)
    n = ' '.join(n.split()).strip(' -,')
    n = re.sub(r'\.(pdf|htm|html|xlsx?|txt)$', '', n, flags=re.I)
    words = {'sosd': 'Son of Sno*Drift', 'mm': 'Moonlight Monte', 'por': 'Press On Regardless', 'sos': 'Son of Sno*Drift', 'tt': 'Twilight Tour', 'cct': 'Clare Color Tour',
             'pe': 'Pavement Ends', 'rx': 'RallyCross', 'ts': '', 'detsccarally x': 'RallyCross', 'i96': 'I-96', 'scca': 'SCCA', 'dr': 'DR'}
    n = ' '.join(words.get(w.lower(), w) for w in re.split(r'\s+', re.sub(r'(?i)detsccarally x', 'detsccarally_x', n)) if w).replace('detsccarally_x', 'RallyCross')
    n = re.sub(r'(?i)\brun group \d+\b', '', n)
    n = ' '.join(n.split()).strip(' -,')
    n = re.sub(r'(?i)^mm ?(\d{4})$', r'Moonlight Monte \1', n)
    n = re.sub(r'(?i)son of sno\*? ?drift drift', 'Son of Sno*Drift', n)
    n = re.sub(r'\b(?i:(?=[ivxlc]{2,}\b))([IiVvXxLlCc]+)\b', lambda m: m[1].upper(), n)   # roman numerals: "Xix" -> "XIX"
    n = re.sub(r'(?i)resultspostbannerphoto|resultspost\w*', '', n).strip()
    if re.fullmatch(r'(?i)(sat|saturday|sun|sunday)', n or ''):
        return {'sat': 'Saturday', 'sun': 'Sunday'}[n[:3].lower()] + ' event'
    if re.fullmatch(r'(?i)grp ?(\d+)', n or ''):
        return 'Run group ' + re.sub(r'\D', '', n)
    if not n or len(n) < 4 or n.lower() in ('results', 'results1', 'final', 'final results', 'live', 'esc'):
        return ''
    return n[0].upper() + n[1:]
for i, nm, prog in d.execute('select id, name, program from events').fetchall():
    d.execute('update events set name = ? where id = ?', ((lambda c: c if c and not c.endswith(' event') and not c.startswith('Run group') else f"{prog} {c or 'event'}".replace(' Saturday event', ', Saturday').replace(' Sunday event', ', Sunday'))(clean(nm)), i))
RALLY_NAMES = [   # one name per recurring rally, whatever the file called it
    (r'sno\W*d\w*ft|snodrift|son of sno', 'Son of Sno*Drift'), (r'press on regardless|\bpor\b', 'Press On Regardless'),
    (r'moo\w*\s*monte', 'Moonlight Monte'), (r'th\w*bs up', 'Thumbs Up'), (r'twilight\s*tour', 'Twilight Tour'),
    (r'rali+gators', 'Ralligators Reunion'), (r'mid\s*summer', 'Midsummer'), (r'tri\s*-?county', 'Tri-County'),
    (r'clare color', 'Clare Color Tour'), (r'pavement ends', 'Pavement Ends'), (r'dawnbuster', 'Dawnbuster'),
    (r'cast in stone', 'Cast in Stone'), (r'are you territorial', 'Are You Territorial'), (r'rights of spring', 'Rights of Spring'),
    (r'rally school', 'Rally School'), (r'august madness', 'August Madness'), (r'great northern', 'Great Northern'),
    (r'santas? nightmare', "Santa's Nightmare"), (r'how the west', 'How the West Was Won')]
def rally_name(n):
    for rx, name in RALLY_NAMES:
        if re.search(rx, n, re.I):
            return name
    n = re.sub(r'(?i)\b(rally|results?|summary|scoring|official|dtr|ncr|scca|\d+)\b', ' ', n)
    return ' '.join(n.split()).title() or 'Rally'
for i, nm in d.execute("select id, name from events where program = 'Rally'").fetchall():
    d.execute('update events set name = ? where id = ?', (rally_name(nm), i))
# The same event split over several files: one event per program, year and name (when the dates agree or one is missing).
merged = 0
groups = {}
for i, prog, yr, dt, nm in d.execute('select id, program, year, date, name from events order by date is null, date, id').fetchall():
    keep = next((k for k, kd in groups.get((prog, yr, nm), []) if kd == dt or kd is None or dt is None), None)
    if keep is None:
        groups.setdefault((prog, yr, nm), []).append((i, dt))
        continue
    d.execute('update files set event_id = ? where event_id = ?', (keep, i))
    d.execute('update result_rows set event_id = ? where event_id = ?', (keep, i))
    if dt: d.execute('update events set date = coalesce(date, ?) where id = ?', (dt, keep))
    d.execute('delete from events where id = ?', (i,)); merged += 1
print('merged', merged, 'duplicate events')
def person_name(n):
    """Strip notes the sheets add to names: "12 Pat Example*Not DR member*" -> "Pat Example"."""
    n = re.sub(r'\*[^*]*\*?|\((?:not|non)[^)]*\)', ' ', n or '', flags=re.I)
    n = re.sub(r'^\s*\d+\s+', '', n)
    return ' '.join(n.split()).strip(' ,*')
junk = []
for i, nm in d.execute('select id, name from people').fetchall():
    c = person_name(nm)
    if len(c) < 3 or not re.search(r'[A-Za-z]{2}', c) or re.fullmatch(r'(?i)\w{1,3}', c) or re.search(r'\d{4}|:', c) \
            or re.search(r'(?i)\b(street|prepared|touring|members?|classic|modified|stock|ladies|novice|class)\b', c):
        junk.append(i); continue
    if c != nm:
        d.execute('update people set name = ? where id = ?', (c, i))
        d.execute('update result_rows set driver = ? where person_id = ?', (c, i))
        d.execute('update season_standings set name = ? where person_id = ?', (c, i))
for i in junk:
    d.execute('delete from result_rows where person_id = ?', (i,)); d.execute('delete from season_standings where person_id = ?', (i,))
    d.execute('delete from people where id = ?', (i,))
print('cleaned names;', len(junk), 'junk names dropped')
# ---- Data hygiene: rows, names, people, classes, events ----
d.execute("delete from result_rows where position is null and total is null and raw_time is null and pax_time is null")
d.execute("delete from result_rows where id not in (select min(id) from result_rows group by file_id, person_id, line_no)")
d.execute("update result_rows set section = null where section like '%page % of %' or section glob '*[0-9][0-9][0-9][0-9]*'")
REGION = r'(?:det|wmr|now|atl|ovr|svr|cen|cin|gld|lol|stl|non|ind|ohv|fin|sfr)'
def tidy(n):
    """A person's name: no class/region/flag prefixes from the sheets, no stray numbers, normal capitals."""
    w = n.split()
    while len(w) > 2 and (re.fullmatch(r'(?i)[a-z]{1,3}\d*', w[0]) and (w[0].isupper() or re.fullmatch(r'(?i)' + REGION, w[0]) or len(w[0]) == 1
                          or re.fullmatch(r'(?i)(mr|mf|ma|sr|sf|sa|pr|pf|pa|m\d|x|e|l|s|n|u|t)', w[0]))):
        w = w[1:]
    w = [x for x in w if not re.fullmatch(r'\d+', x)]
    n = ' '.join(w)
    if n.isupper() or n.islower():
        n = ' '.join(re.sub(r"(?i)^(mc|o')(\w)", lambda m: m[1].capitalize() + m[2].upper(), x.capitalize()) if len(x) > 1 else x.upper()
                     for x in n.split())
        n = re.sub(r'(?i)\b(jr|sr)\b', lambda m: m[1].capitalize(), n)
        n = re.sub(r'\b(Ii|Iii|Iv)\b', lambda m: m[1].upper(), n)
    return n
def key(n):
    n = unicodedata.normalize('NFKD', n).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z ]', ' ', n).split())
def nomid(k):
    w = k.split()
    return ' '.join([w[0]] + [x for x in w[1:-1] if len(x) > 1] + [w[-1]]) if len(w) > 2 else k
def merge(src, dst):
    d.execute('update result_rows set person_id = ? where person_id = ?', (dst, src))
    d.execute('update season_standings set person_id = ? where person_id = ?', (dst, src))
    d.execute('delete from people where id = ?', (src,))
stats = collections.Counter()
for i, nm in d.execute('select id, name from people').fetchall():
    t = tidy(nm)
    if re.search(r'\d', t) or len(t) < 3 or re.search(r'(?i)\b(season|opener|ender|series|points|event|results?|region|scca|class|total|solo|standings)\b', t):
        d.execute('update result_rows set person_id = null where person_id = ?', (i,)); d.execute('delete from season_standings where person_id = ?', (i,))
        d.execute('delete from people where id = ?', (i,)); stats['junk names dropped'] += 1; continue
    if t != nm:
        d.execute('update people set name = ? where id = ?', (t, i)); stats['names tidied'] += 1
def counts():
    return {i: (n, k) for i, n, k in d.execute("""select p.id, p.name, (select count(*) from result_rows r where r.person_id = p.id)
        + (select count(*) from season_standings s where s.person_id = p.id) from people p""")}
# same name apart from case, punctuation or a middle initial: one person (the most used spelling wins)
for kind, fn in (('same name', key), ('middle initial', lambda n: nomid(key(n)))):
    groups = collections.defaultdict(list)
    for i, (n, k) in counts().items():
        if len(n.split()) >= 2: groups[fn(n)].append((k, i))
    for g in groups.values():
        if len(g) > 1:
            g.sort(reverse=True)
            for _, i in g[1:]: merge(i, g[0][1]); stats['merged: ' + kind] += 1
# typos: same last name, nearly the same spelling, one rare and the other common, in the same program
progs = collections.defaultdict(set)
for i, p in d.execute('select person_id, program from result_rows where person_id is not null union select person_id, program from season_standings'):
    progs[i].add(p)
c2 = counts(); bylast = collections.defaultdict(list)
for i, (n, k) in c2.items():
    w = key(n).split()
    if len(w) >= 2: bylast[w[-1]].append(i)
review = []
for ids in bylast.values():
    for a in ids:
        for b in ids:
            if a >= b or a not in c2 or b not in c2: continue
            ka, kb = key(c2[a][0]), key(c2[b][0])
            if difflib.SequenceMatcher(None, ka, kb).ratio() < 0.88: continue
            (rare, ra), (common, rc) = sorted([(a, c2[a][1]), (b, c2[b][1])], key=lambda x: x[1])
            if ra <= 2 and rc >= 3 * ra and progs[rare] & progs[common]:
                merge(rare, common); stats['merged: typo'] += 1; c2.pop(rare)
            else:
                review.append((c2[a][0], c2[a][1], c2[b][0], c2[b][1]))
# one-word names (a first name alone, or a rally crew's last name): keep the text on the result, no person page
for i, (n, k) in counts().items():
    if len(n.split()) < 2:
        d.execute('update result_rows set person_id = null where person_id = ?', (i,)); d.execute('delete from season_standings where person_id = ?', (i,))
        d.execute('delete from people where id = ?', (i,)); stats['one-word names unlinked'] += 1
d.execute('update result_rows set driver = (select name from people p where p.id = person_id) where person_id is not null')
d.execute('update season_standings set name = (select name from people p where p.id = person_id)')
# class codes in one case
d.execute("update result_rows set class = upper(class) where class not like '% %' and length(class) <= 8")
d.execute("update result_rows set section = upper(section) where section not like '% %' and length(section) <= 8")
# the same event twice (same program and date) outside Rally, where two names on one day mean a date conflict
for prog, dt in d.execute("select program, date from events where date is not null and program != 'Rally' group by 1, 2 having count(*) > 1").fetchall():
    ids = [r[0] for r in d.execute('select id from events where program = ? and date = ? order by length(name), id', (prog, dt))]
    for i in ids[1:]:
        d.execute('update files set event_id = ? where event_id = ?', (ids[0], i)); d.execute('update result_rows set event_id = ? where event_id = ?', (ids[0], i))
        d.execute('delete from events where id = ?', (i,)); stats['events merged (same day)'] += 1
print(dict(stats))
open(os.path.join(os.path.dirname(out), 'records-review.txt'), 'w').write(
    'Possible same person, not merged (name, rows, name, rows):\n' + '\n'.join(f'{a} ({x})  |  {b} ({y})' for a, x, b, y in sorted(set(review))) + '\n')
d.execute('alter table files add column label text')
d.execute('alter table files add column sort integer')
for i, nm in d.execute('select id, name from files').fetchall():
    low = (nm or '').lower()
    lab, srt = ('PAX', 2) if 'pax' in low or 'index' in low else ('Raw', 3) if 'raw' in low else ('Class results', 1) if re.search(r'class|final|results?', low) else ('Results', 4)
    d.execute('update files set label = ?, sort = ? where id = ?', (lab, srt, i))
d.commit(); d.execute('DETACH s'); d.execute('VACUUM'); d.close()
os.replace(tmp, out)
c = sqlite3.connect(out)
print({t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('events', 'files', 'result_rows', 'season_standings', 'people')},
      round(os.path.getsize(out) / 1e6, 1), 'MB')
