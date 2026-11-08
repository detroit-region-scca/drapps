"""Export the public records database for drapps (drapps.drscca.org/records): results, events, standings and people.
No worker points, no full text, no raw lines. Usage: export_records.py <scca.db> <records.db> [records-edits.json]
The optional third file is the corrections log from the server (data/records-edits.json, see records_edits.py): fetch
it right before exporting, so directors' corrections are applied to the new database too."""
import collections, difflib, os, sqlite3, sys, unicodedata
src, out = sys.argv[1], sys.argv[2]
tmp = out + '.tmp'
if os.path.exists(tmp): os.remove(tmp)
d = sqlite3.connect(tmp)
d.execute(f"ATTACH '{src}' AS s")
d.executescript('''
CREATE TABLE files AS SELECT id, event_id, program, year, date, kind, sorted_name AS name, drive_url, ocr, sheet_label FROM s.files
  WHERE program IN ('Autocross', 'RallyCross', 'Rally', 'Road Racing', 'Sim Racing');
CREATE TABLE events AS SELECT * FROM s.events WHERE id IN (SELECT event_id FROM files WHERE event_id IS NOT NULL);
CREATE TABLE result_rows AS SELECT id, file_id, event_id, person_id, program, year, date, section, position, class, car_number,
  driver, vehicle, total, raw_time, pax_time, total_s, raw_s, pax_s, line_no, class_place, laps, best_lap, status FROM s.result_rows
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
    n = re.sub(r'(?<!\d)\d{6,8}(?!\d)|(?<!\d)\d{1,2}[-/]\d{1,2}[-/]\d{2,4}(?!\d)', ' ', (name or '').replace('_', ' '))
    n = re.sub(r'(?i)^(detroit region scca )?regional at waterford hills( \d{4})?$', 'SCCA Regional at Waterford Hills', n.strip())
    n = re.sub(r'(?i)^div champ$', 'Divisional Championship', n.strip())
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
for i, nm, prog in d.execute("select id, name, program from events where program != 'Sim Racing'").fetchall():   # sim names are built clean
    d.execute('update events set name = ? where id = ?', ((lambda c: c if c and not c.endswith(' event') and not c.startswith('Run group') else f"{prog} {c or 'event'}".replace(' Saturday event', ', Saturday').replace(' Sunday event', ', Sunday'))(clean(nm)), i))
RALLY_NAMES = [   # one name per recurring rally, whatever the file called it
    (r'sno\W*d\w*ft|snodrift|son of sno', 'Son of Sno*Drift'), (r'press on regardless|\b[fs]?por\b|^[fs]?por', 'Press On Regardless'),
    (r'moo\w*\s*monte', 'Moonlight Monte'), (r'th\w*bs up', 'Thumbs Up'), (r'twilight', 'Twilight Tour'), (r'michigan winter', 'Michigan Winter Rally'),
    (r'rali+gators', 'Ralligators Reunion'), (r'mid\s*summer', 'Midsummer'), (r'tri\s*-?county', 'Tri-County'),
    (r'clare color', 'Clare Color Tour'), (r'pavement ends', 'Pavement Ends'), (r'dawnbuster', 'Dawnbuster'),
    (r'cast in stone', 'Cast in Stone'), (r'are you territorial', 'Are You Territorial'), (r'rights of spring', 'Rights of Spring'),
    (r'rally school', 'Rally School'), (r'august madness', 'August Madness'), (r'great northern', 'Great Northern'),
    (r'santas? nightmare', "Santa's Nightmare"), (r'how the west', 'How the West Was Won'), (r'maca grand tour', 'MACA Grand Tour for Children'),
    (r'mini\W*maca', 'Mini-MACA'), (r'hell and back', 'Hell and Back')]
SMALL = {'a', 'an', 'and', 'at', 'by', 'for', 'in', 'of', 'on', 'or', 'the', 'to', 'with'}
def headline(n):
    """Title case for an event name: small words lower, Roman numerals upper, "Custer's" not "Custer'S"."""
    out = []
    for i, w in enumerate(n.split()):
        if re.fullmatch(r'(?i)[ivx]{1,5}', w) and i:
            out.append(w.upper())
        elif w.lower() in SMALL and i:
            out.append(w.lower())
        elif w.isupper() and 1 < len(w.strip('.,')) <= 4 or w.isupper() and '.' in w:
            out.append(w)                                   # acronyms as printed: TSD, MACA, T.G.I.S.
        else:
            out.append('-'.join(p[:1].upper() + p[1:].lower() for p in w.split('-')))
    return ' '.join(out)
def rally_name(n):
    for rx, name in RALLY_NAMES:
        if re.search(rx, n, re.I):
            return re.sub(r'(?i)\s+(i{1,3}|iv|v|vi{1,3}|ix|x)$', lambda m: ' ' + m[1].upper(), name if name != 'Ralligators Reunion' else name + (re.search(r'(?i)\s+([ivx]+)\s*$', n.strip()) and ' ' + re.search(r'(?i)\s+([ivx]+)\s*$', n.strip())[1].upper() or ''))
    n = re.sub(r'(?i)\b(rally|results?|summary|scoring|official|dtr|ncr|scca|\d+)\b', ' ', n)
    return headline(' '.join(n.split())) or 'Rally'
for i, nm in d.execute("select id, name from events where program = 'Rally'").fetchall():
    d.execute('update events set name = ? where id = ?', (rally_name(nm), i))
# the 2007 Grattan race groups were posted as "2007-5-12_results_grp4.pdf"; one was sorted as RallyCross
for (eid,) in d.execute("select distinct event_id from files where name glob '*20[0-9][0-9]-*_results_grp[0-9]*'").fetchall():
    d.execute("update events set program = 'Road Racing' where id = ?", (eid,))
    d.execute("update result_rows set program = 'Road Racing' where event_id = ?", (eid,))
    d.execute("update files set program = 'Road Racing' where event_id = ?", (eid,))
# RallyCross names come from file names and sheet titles ("Rallyx33107Results V2", "R X Adrian", "At South Lyon"):
# one shape for all, "<event> at <venue>", "RallyCross at <venue>", or "RallyCross, <Mon D>" when the venue is unknown
RX_VENUES = [(r'crystal', 'Crystal Motor Speedway'), (r'i-?96', 'I-96 Speedway'), (r'oak\s*shade', 'Oakshade Raceway'),
             (r'thistle\s*hill|south lyon', 'Thistle Hill'), (r'bundy', 'Bundy Hill'), (r'calhoun', 'Calhoun County Fairgrounds'),
             (r'marshall', 'Marshall'), (r'adrian', 'Adrian'), (r'fowlerville', 'Fowlerville'), (r'clarksville', 'Clarksville'),
             (r'milan', 'Milan'), (r'\bthe hill\b', 'The Hill')]
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
def rx_name(n, dt):
    low = n.lower()
    venue = next((v for rx, v in RX_VENUES if re.search(rx, low)), None)
    title = re.split(r'(?i)\s+at\s+', n)[0].strip(' ,') if re.search(r'(?i)\s+at\s+', n) else ''
    if not title or re.fullmatch(r'(?i)rally\s*cross|rallyx|r\s*x', title):
        m = re.search(r'(?i)(divisional championship|gld championship|rallycross championship|national|championship|divisional)', n)
        title = {'national': 'National RallyCross', 'championship': 'RallyCross Championship', 'divisional': 'Divisional Championship'}.get(
            m[1].lower(), m[1].title().replace('Gld', 'GLD').replace('Rallycross', 'RallyCross')) if m else 'RallyCross'
    title = re.sub(r'(?i)rallycross', 'RallyCross', title)
    if re.search(r'(?i)(,\s*(saturday|sunday))$', n):
        return 'RallyCross' + re.search(r'(?i)(,\s*(saturday|sunday))$', n)[1].title()
    if venue:
        return f'{title} at {venue}'
    if title != 'RallyCross':
        return title
    if dt:
        return f'RallyCross, {MON[int(dt[5:7]) - 1]} {int(dt[8:10])}'
    ev = re.search(r'(?i)event\s*(\d+)', n)
    return f'RallyCross event {ev[1]}' if ev else re.sub(r'(?i)rally\s*cross', 'RallyCross', n.strip()) or 'RallyCross'
for i, nm, dt in d.execute("select id, name, date from events where program = 'RallyCross'").fetchall():
    d.execute('update events set name = ? where id = ?', (rx_name(nm, dt), i))
# Road races the old website's results files don't name (its season pages did): track and event by date
ROAD_RACES = {'2018-05-19': 'Spring Sprints at GingerMan'}     # older road races are named by their transcriptions
for dt, nm in ROAD_RACES.items():
    d.execute("update events set name = ? where program = 'Road Racing' and date = ?", (nm, dt))
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
    if len(c) < 2 or not re.search(r'[A-Za-z]{2}', c) or re.search(r'\d{4}|:', c) \
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
# A car is never shown with its colour ("2003 Kia Rio/White", "Kart Blue/Red"): RallyCross sheets print "Year Make Model / Color".
COLOUR = r'(?:light |dark |bright |metallic |pearl )?(?:red|blue|black|white|silver|green|gr[ae]y|yellow|orange|purple|gold|tan|brown|maroon|burgundy|beige|pink|teal|bronze|champagne|navy|lime|copper|charcoal|cream)'
COLOUR_TAIL = re.compile(r'(?i)(?:\s+' + COLOUR + r')?\s*/\s*' + COLOUR + r'(?:\s*(?:/|&|and|w/)\s*' + COLOUR + r')*\s*$')
uncoloured = 0
for rid, veh in d.execute('select id, vehicle from result_rows where vehicle like ?', ('%/%',)).fetchall():
    new = COLOUR_TAIL.sub('', veh).strip(' /,-')
    if new != veh:
        d.execute('update result_rows set vehicle = ? where id = ?', (new or None, rid)); uncoloured += 1
print('car colours removed', uncoloured)
d.execute("delete from result_rows where position is null and total is null and raw_time is null and pax_time is null and status is null and file_id not in (select id from files where ocr = 'transcribed')")
d.execute("delete from result_rows where file_id not in (select id from files where ocr = 'transcribed') and id not in (select min(id) from result_rows group by file_id, line_no, coalesce(person_id, -id))")   # a name repeated on one line; never two unlinked crew members
d.execute("update result_rows set section = null where section like '%page % of %' or section glob '*[0-9][0-9][0-9][0-9]*'")
REGION = r'(?:det|wmr|now|atl|ovr|svr|cen|cin|gld|lol|stl|non|ind|ohv|fin|sfr)'
def tidy(n):
    """A person's name: no class/region/flag prefixes from the sheets, no stray numbers, normal capitals."""
    w = n.split()
    while len(w) > 2 and not (len(w[0]) == 1 and len(w[1].strip('.')) == 1) and (re.fullmatch(r'(?i)[a-z]{1,3}\d*', w[0]) and (w[0].isupper() or re.fullmatch(r'(?i)' + REGION, w[0]) or len(w[0]) == 1
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
together = collections.defaultdict(set)       # people who appear on the same sheet are never the same person
for f, p in d.execute('select file_id, person_id from result_rows where person_id is not null'):
    together[p].add(f)
def spelled(n):
    """How well a spelling keeps its capitals: "McExample" over "Mcexample", mixed case over ALL CAPS."""
    return (not n.isupper() and not n.islower(), sum(1 for w in n.split() for ch in w[1:] if ch.isupper()))
def counts():
    return {i: (n, k) for i, n, k in d.execute("""select p.id, p.name, (select count(*) from result_rows r where r.person_id = p.id)
        + (select count(*) from season_standings s where s.person_id = p.id) from people p""")}
# same name apart from case, punctuation or a middle initial: one person (the most used spelling wins)
for kind, fn in (('same name', key), ('middle initial', lambda n: nomid(key(n)))):
    groups = collections.defaultdict(list)
    for i, (n, k) in counts().items():
        if len(n.split()) >= 2: groups[fn(n)].append((k, i))
    names = {i: n for i, (n, k) in counts().items()}
    for g in groups.values():
        if len(g) > 1:
            g.sort(key=lambda x: (spelled(names[x[1]]), x[0]), reverse=True)
            keep = g[0][1]
            for _, i in g[1:]:
                if kind == 'middle initial' and together[i] & together[keep]:
                    stats['not merged: on the same sheet'] += 1; continue
                merge(i, keep); together[keep] |= together[i]; stats['merged: ' + kind] += 1
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
            if difflib.SequenceMatcher(None, ka, kb).ratio() < 0.88 or ka[0] != kb[0] or together[a] & together[b]: continue   # Rich vs Eric
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
# never a hometown or member number in the car column (some entry-list style sheets put them there)
for i, v in d.execute('select id, vehicle from result_rows where vehicle is not null').fetchall():
    if re.search(r'(?<![\w/])\d{5,7}(?![\w/])|[a-z],\s*[A-Z]{2}\b(?!\w)', v):
        d.execute('update result_rows set vehicle = null where id = ?', (i,))
# a hometown read as a name ("Lake Orion MI"): never a person, never a crew partner
STATE = r'(?:MI|OH|IN|ON|IL|WI|PA|NY|KY|TN|FL|TX|CA|GA|MN|IA|MO|NC|SC|VA|WV|MD|NJ|CT|MA|CO|AZ|QC)'
for i, drv, veh in d.execute('select id, driver, vehicle from result_rows').fetchall():
    if drv and re.search(rf'[a-z.]\s+{STATE}\.?$', drv):
        d.execute('delete from result_rows where id = ?', (i,)); stats['hometowns dropped (read as names)'] += 1
    elif veh and re.fullmatch(rf'with .*[a-z.]\s+{STATE}\.?', veh):
        d.execute('update result_rows set vehicle = null where id = ?', (i,)); stats['hometowns dropped (read as names)'] += 1
# hometowns some 2007 race sheets print after the class: 'GT1 "FENTON, MI"' -> 'GT1'
d.execute("""update result_rows set class = trim(substr(class, 1, instr(class, '"') - 1)) where class like '%"%, __"%'""")
d.execute("""update result_rows set class = substr(class, 1, instr(class, ' ') - 1) where class glob '[A-Z0-9]* [A-Z]*, [A-Z][A-Z]'""")
# class codes in one case
d.execute("update result_rows set class = upper(class) where class not like '% %' and length(class) <= 8 and class = lower(class)")   # never B-Spec, SpR
d.execute("update result_rows set section = upper(section) where section not like '% %' and length(section) <= 8 and section = lower(section)")
# the same event twice (same program and date) outside Rally and Sim Racing (two series can race the same night), where two names on one day mean a date conflict
for prog, dt in d.execute("select program, date from events where date is not null and program not in ('Rally', 'Sim Racing') group by 1, 2 having count(*) > 1").fetchall():
    ids = [r[0] for r in d.execute('select id from events where program = ? and date = ? order by length(name), id', (prog, dt))]
    for i in ids[1:]:
        d.execute('update files set event_id = ? where event_id = ?', (ids[0], i)); d.execute('update result_rows set event_id = ? where event_id = ?', (ids[0], i))
        d.execute('delete from events where id = ?', (i,)); stats['events merged (same day)'] += 1
# a road race weekend that has Speedhive's timing files and an old-site results file (2018 Spring Sprints): keep
# Speedhive's rows, which have real columns, so the race is not listed twice; the old file stays linked
for (eid,) in d.execute("""select event_id from files where program = 'Road Racing' group by event_id
                            having sum(name like '%.csv' and name like '% - %') > 0 and sum(not (name like '%.csv' and name like '% - %')) > 0""").fetchall():
    gone = d.execute("""delete from result_rows where event_id = ? and file_id in
                         (select id from files where event_id = ? and not (name like '%.csv' and name like '% - %'))""", (eid, eid)).rowcount
    stats['old-site rows dropped (same race on Speedhive)'] += gone
# a rally's overall sheet posted a few days later ("POR-2026-Scoring_20260914-overall.pdf", a Monday): part of the
# rally it scores, the latest same-name event up to 3 days before, as its Overall tab
import datetime as _dt
for i, nm, dt, yr in d.execute("select id, name, date, year from events where program = 'Rally' and date is not null").fetchall():
    day = _dt.date.fromisoformat(dt)
    if day.weekday() not in (0, 1, 2, 3):
        continue
    prev = d.execute("select id from events where program = 'Rally' and name = ? and year = ? and date < ? and date >= ? order by date desc limit 1",
                     (nm, yr, dt, (day - _dt.timedelta(days=3)).isoformat())).fetchone()
    if prev:
        d.execute('update files set event_id = ? where event_id = ?', (prev[0], i))
        d.execute('update result_rows set event_id = ?, date = (select date from events where id = ?) where event_id = ?', (prev[0], prev[0], i))
        d.execute('delete from events where id = ?', (i,)); stats['rally sheets posted later joined to their rally'] += 1
# place labels some standings sheets print as rows ("First PA", "Second MA")
d.execute("delete from season_standings where name glob '[FST][a-z]* [A-Z][A-Z]' and lower(substr(name, 1, instr(name, ' ') - 1)) in ('first','second','third','fourth','fifth')")
d.execute("update result_rows set vehicle = null where vehicle glob 'with [A-Z][A-Z]'")
# an event none of whose files could be read (a scanned narrative, a photo of a sheet) lists nothing but looks like a
# rally or race that has no results: leave it out; the file is still in Drive under Results by program
for (eid,) in d.execute('''select id from events where id not in (select distinct event_id from result_rows where event_id is not null)
                            and id not in (select event_id from files where (ocr like 'scan not read%' or ocr like 'needs transcription%') and event_id is not null)''').fetchall():
    # (an event whose results are only in a scan we could not read cleanly stays, with a link to the scan)
    d.execute('delete from files where event_id = ?', (eid,)); d.execute('delete from events where id = ?', (eid,))
    stats['events with no readable results left out'] += 1
# Tabs: what each sheet is (Class results, PAX, Raw, Overall, Leg scores) and which part of the day it covers, from
# its file name ("..._am_course_results", "grfn_sat_pm_pax"), so two sheets never share a name unless they are the same
d.execute('alter table files add column label text')
d.execute('alter table files add column sort integer')
def sheet_label(nm):
    low = (nm or '').lower().replace('_', ' ').replace('-', ' ')
    if re.search(r'leg\s*by\s*leg', low):
        base, srt = 'Leg scores', 5
    elif re.search(r'\bpax\b|\bindex\b', low):
        base, srt = 'PAX', 2
    elif re.search(r'\braw\b', low):
        base, srt = 'Raw', 3
    elif re.search(r'\boverall\b', low) and not re.search(r'\bclass', low):
        base, srt = 'Overall', 2
    elif re.search(r'class|final|results?|scores?', low):
        base, srt = 'Class results', 1
    else:
        base, srt = 'Results', 4
    q = []
    for rx, word in ((r'\bsat(urday)?\b', 'Saturday'), (r'\bsun(day)?\b', 'Sunday'), (r'\b(am|morning)\b', 'AM'),
                     (r'\b(pm|afternoon)\b', 'PM'), (r'\bday\s*([12])\b', 'Day {}'), (r'\bheat\s*(\d)\b', 'Heat {}'),
                     (r'\bcourse\s*([ab12])\b', 'Course {}')):
        m = re.search(rx, low)
        if m:
            q.append(word.format(m[1].upper()) if '{}' in word else word)
    if base != 'Overall' and re.search(r'\boverall\b', low) and q == []:
        q.append('Overall')
    order = {'Saturday': 1, 'Day 1': 1, 'Sunday': 3, 'Day 2': 3, 'AM': 1, 'PM': 2, 'Overall': 0}
    return base + (' \u00b7 ' + ', '.join(q) if q else ''), sum(order.get(x, 3) for x in q) * 10 + srt
for i, nm, given in d.execute('select id, name, sheet_label from files').fetchall():
    lab, srt = sheet_label(nm)
    if given and re.search(r'(?i)class results', given):
        given = 'Class results'                 # "FPOR class results": the event's date already says which day
    if given:                                   # a transcription names its sheet ("Overall", "Class results")
        lab = given + (' \u00b7 ' + lab.split(' \u00b7 ', 1)[1] if ' \u00b7 ' in lab and given == 'Class results' else '')
    if (nm or '').lower().endswith('.csv') and ' - ' in nm:          # Speedhive race sessions: the label made at import
        lab = nm.rsplit(' - ', 1)[1][:-4]
        g = re.search(r'group\s*(\d+)', lab, re.I)
        srt = (int(g[1]) if g else 50) * 10 + next((k for k, w in enumerate(('saturday', 'race 1', 'qualifying', 'sunday', 'race 2', 'feature'), 1) if w in lab.lower()), 7)
    d.execute('update files set label = ?, sort = ? where id = ?', (lab, srt, i))
# the same results posted twice (a PDF and a web page, or a revision): keep one sheet, the latest revision or the
# fuller one; the other file stays linked under Original files
def rev(nm):
    m = re.search(r'rev\s*(\d+)|v(\d+)\b', (nm or '').lower())
    return int(m[1] or m[2]) if m else (1 if 'final' in (nm or '').lower() else 0)
for (eid,) in d.execute('select distinct event_id from files where event_id is not null').fetchall():
    fs = d.execute('select f.id, f.name, f.label from files f where f.event_id = ? and exists (select 1 from result_rows r where r.file_id = f.id)', (eid,)).fetchall()
    sig = {fid: {(dr.lower(), t) for dr, t in d.execute('select driver, coalesce(total, raw_time, pax_time) from result_rows where file_id = ?', (fid,)) if dr}
           for fid, _, _ in fs}
    gone = set()
    for x in fs:
        for y in fs:
            if x[0] >= y[0] or x[0] in gone or y[0] in gone or x[2] != y[2] or not sig[x[0]] or not sig[y[0]]:
                continue
            if len(sig[x[0]] & sig[y[0]]) / min(len(sig[x[0]]), len(sig[y[0]])) >= 0.9:
                keep = max((x, y), key=lambda f: (rev(f[1]), len(sig[f[0]]), f[0]))
                drop = y if keep is x else x
                d.execute('delete from result_rows where file_id = ?', (drop[0],)); gone.add(drop[0])
                stats['duplicate sheets hidden (same results posted twice)'] += 1
# people left with no results or standings (their rows were dropped as junk, e.g. a town read as a name)
d.execute('delete from people where id not in (select person_id from result_rows where person_id is not null union select person_id from season_standings where person_id is not null)')
print(dict(stats))
open(os.path.join(os.path.dirname(out), 'records-review.txt'), 'w').write(
    'Possible same person, not merged (name, rows, name, rows):\n' + '\n'.join(f'{a} ({x})  |  {b} ({y})' for a, x, b, y in sorted(set(review))) + '\n')
if len(sys.argv) > 3:         # corrections made in drapps by program directors and officers
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import records_edits
    log = records_edits.load(sys.argv[3])
    print('corrections applied:', records_edits.apply_all(d, log), 'of', len(log))
d.commit(); d.execute('DETACH s'); d.execute('VACUUM'); d.close()
os.replace(tmp, out)
c = sqlite3.connect(out)
print({t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('events', 'files', 'result_rows', 'season_standings', 'people')},
      round(os.path.getsize(out) / 1e6, 1), 'MB')
