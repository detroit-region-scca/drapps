"""Operations as four tabs (Overview, To do, Gear, Money): old addresses still land, Gear is one list with filters, an
item's page holds its documents, to-dos and replacement fields, each replacement rule is changed where it is shown,
the committee's note is kept beside the rules without touching them, and people who cannot edit see no controls.
Made-up names only."""
import re
from datetime import date, timedelta

import pytest

import equip_store as E

T = date.today()
O = '/operations'


def _day(n):
    return (T + timedelta(days=n)).isoformat()


def _item(id_, name, cat='Other', status='In use', place='North barn', **kw):
    return dict({'id': id_, 'name': name, 'program': '', 'category': cat, 'place': place, 'year': None, 'quantity': None, 'cost': None,
                 'life_years': None, 'vin': '', 'plate': '', 'condition': '', 'status': status, 'notes': '', 'docs_url': '', 'updated': '',
                 'updated_by': '', 'replace_cost': None, 'replace_year': None, 'nickname': '', 'seen': '', 'account': ''}, **kw)


def _task(id_, what, **kw):
    return dict({'id': id_, 'task': what, 'about': '', 'due': '', 'repeat_months': None, 'cost': None, 'owner': '', 'notes': '', 'last_done': '',
                 'done_by': '', 'category': '', 'paid_how': '', 'auto': '', 'qbo_check': '', 'account': '', 'ends': ''}, **kw)


def _place(id_, name, **kw):
    return dict({'id': id_, 'name': name, 'address': '', 'cost_month': None, 'cost_year': None, 'account_holder': '', 'access': '', 'notes': '',
                 'updated': '', 'updated_by': '', 'qbo_check': '', 'status': ''}, **kw)


@pytest.fixture()
def ops(monkeypatch):
    import app as A
    import auth
    import equip
    import equip_docs as D
    monkeypatch.setenv('OPERATIONS_DOCS_FOLDER_URL', 'https://drive.google.com/drive/folders/rootfolder')
    state = {
        'items': [_item('EQ-001', 'Blue tow truck', 'Vehicle', year=2015.0, cost=30000.0, condition='Good', replace_year=T.year + 1.0, replace_cost=45000.0),
                  _item('EQ-002', 'Long trailer', 'Trailer', nickname='Race Trailer', cost=23000.0, replace_year=T.year + 3.0, replace_cost=40000.0),
                  _item('EQ-003', 'Timing laptop', 'Timing', place='With Pat Example', cost=3000.0),
                  _item('EQ-004', 'Cones', 'Course', place='Race Trailer'),
                  _item('EQ-005', 'Old canopy', 'Hospitality', status='Gone'),
                  _item('EQ-006', 'New radios', 'Safety', status='Planned', place='', replace_year=T.year + 1.0, replace_cost=6000.0),
                  _item('EQ-007', 'Pop-up tent', 'Hospitality', year=float(T.year - 2), cost=4000.0, life_years=12.0)],
        'places': [_place('PL-001', 'North barn', cost_month=250.0), _place('PL-002', 'Shut lot', status='Closed')],
        'tasks': [_task('TK-001', 'Renew trailer plate', about='Long trailer', due=_day(-10), repeat_months=12.0, cost=120.0),
                  _task('TK-002', 'Sweep the floor', about='North barn', due=_day(12)),
                  _task('TK-003', 'Call about <keys>', about='Somebody & co'),
                  _task('TK-004', 'Web hosting', due=_day(20), repeat_months=12.0, cost=336.0, auto='yes', category='Software and web')],
        'docs': [{'id': 'DC-001', 'for_id': 'EQ-001', 'for_name': 'Blue tow truck', 'folder': 'https://drive.google.com/drive/folders/f1',
                  'needed': 'Title, Registration', 'notes': ''},
                 {'id': 'DC-002', 'for_id': 'PL-001', 'for_name': 'North barn', 'folder': 'https://drive.google.com/drive/folders/f2', 'needed': 'Agreement', 'notes': ''},
                 {'id': 'DC-003', 'for_id': '', 'for_name': 'Region insurance', 'folder': 'https://drive.google.com/drive/folders/f3', 'needed': 'Policy', 'notes': ''}],
        'files': {'f1': [{'name': 'Title - Blue tow truck.pdf', 'webViewLink': 'https://example.org/1'}]},
        'cfg': dict(E.PLAN_DEFAULTS), 'note': '', 'saves': [], 'audit': [], 'uploads': [], 'board': None, 'sheet': []}

    # Three people: Kim is an officer (records board decisions, edits Operations), Ed edits Operations only, Lou only looks.
    people = {'kim@example.org': ('Kim Officer', 'Treasurer', {'board', 'ops'}), 'ed@example.org': ('Ed Gear', 'Equipment', {'ops'}),
              'lou@example.org': ('Lou Looker', 'Steward', set())}

    def lookup(e):
        return {'email': e, 'name': people[e][0], 'level': 'official', 'roles': [people[e][1]], 'perms': set(people[e][2])} if e in people else None
    monkeypatch.setattr(auth, 'lookup', lookup)
    monkeypatch.setattr(equip, 'editors', lambda: {e: v[0] for e, v in people.items() if 'ops' in v[2]})
    monkeypatch.setattr(equip.E, 'enabled', lambda: True)
    monkeypatch.setattr(equip.E, 'items', lambda: [dict(i) for i in state['items']])
    monkeypatch.setattr(equip.E, 'places', lambda closed=False: [dict(p) for p in state['places'] if closed or p['status'] != 'Closed'])
    monkeypatch.setattr(equip.E, 'tasks', lambda: sorted([dict(t) for t in state['tasks']], key=lambda t: t['due'] or '9999'))
    monkeypatch.setattr(equip.E, 'documents', lambda: [dict(d) for d in state['docs']])
    monkeypatch.setattr(equip.E, 'plan_settings', lambda: dict(state['cfg']))
    monkeypatch.setattr(equip.E, 'plan_note', lambda: state['note'])
    monkeypatch.setattr(equip.E, 'board_amount', lambda: dict(state['board']) if state['board'] else None)
    monkeypatch.setattr(equip.E, 'reserve_now', lambda: E.reserve_balance(state['sheet']))
    monkeypatch.setattr(equip.E, 'save_board_amount', lambda monthly, decided, who, when: state.update(board={'monthly': monthly, 'date': decided, 'by': who}))

    def save(tab, row, changed=None, expect=None):
        state['saves'].append((tab, dict(row), changed))
        return row.get('id') or 'EQ-099'
    monkeypatch.setattr(equip.E, 'save', save)
    monkeypatch.setattr(equip.E, 'save_plan_settings', lambda cfg, who, when: state.update(cfg=dict(cfg)))
    monkeypatch.setattr(equip.E, 'save_plan_note', lambda text, who, when: state.update(note=text))
    monkeypatch.setattr(equip, 'log_audit_event', lambda action, who, details: state['audit'].append((action, details)))
    monkeypatch.setattr(D, '_files', lambda ids: {i: state['files'].get(i, []) for i in ids})
    monkeypatch.setattr(D, 'refresh', lambda: None)
    monkeypatch.setattr(D, 'upload', lambda folder, name, f, who: state['uploads'].append((folder, name)))

    def client(email):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, email=email, who=people[email][0])
            if 'ops' in people[email][2]:
                s['equip_editor'] = {'email': email, 'name': people[email][0]}
        return c
    state['client'] = client
    return client('ed@example.org'), client('lou@example.org'), state


def _page(c, url):
    r = c.get(url)
    assert r.status_code == 200, (url, r.status_code, r.headers.get('Location'))
    return r.get_data(as_text=True)


def _main(html):
    """The page below the top bar, without scripts and without the flash message's close button."""
    return re.sub(r'<button[^>]*btn-close[^>]*>', '', html.split('<main', 1)[1].split('</main>')[0])


def _text(html):
    """What the page says, tags and line breaks taken out."""
    return re.sub(r'\s+([.,:)])', r'\1', ' '.join(re.sub(r'<[^>]+>', '', html).split()))


def _flashes(c):
    with c.session_transaction() as s:
        return ' | '.join(m for _, m in s.pop('_flashes', []))


def test_four_tabs_and_old_addresses_still_land(ops):
    ed, lou, state = ops
    nav = _page(ed, f'{O}/').split('<main')[0]
    tabs = re.findall(r'<a class="nav-link[^"]*" href="(/operations/[^"]*)">([^<]+)</a>', nav)
    assert tabs == [(f'{O}/', 'Overview'), (f'{O}/tasks', 'To do'), (f'{O}/items', 'Gear'), (f'{O}/money', 'Money')]
    moved = {f'{O}/vehicles': f'{O}/items?by=vehicles', f'{O}/documents': f'{O}/#documents', f'{O}/documents?fresh=1': f'{O}/?fresh=1#documents',
             f'{O}/bills': f'{O}/money', f'{O}/costs': f'{O}/money', f'{O}/plan': f'{O}/money/replacement',
             f'{O}/plan?year=2027': f'{O}/money/replacement?year=2027', f'{O}/places': f'{O}/items?by=place'}
    for c in (ed, lou):
        for old, new in moved.items():
            r = c.get(old)
            assert r.status_code == 302 and r.headers['Location'] == new, (old, r.status_code, r.headers.get('Location'))
            assert c.get(new).status_code == 200, new
    assert ed.get(f'{O}/items?by=category').status_code == 200            # an old Gear link, still the category list
    assert ed.get('/equipment/plan').status_code == 301                   # the older name still hops on to /operations/plan
    for url, tab in ((f'{O}/money/replacement', 'Money'), (f'{O}/items/EQ-001', 'Gear'), (f'{O}/places/PL-001', 'Gear'), (f'{O}/tasks/new?bill=1', 'Money'),
                     (f'{O}/tasks/new', 'To do')):
        assert re.search(r'nav-link active" href="[^"]*">%s<' % tab, _page(ed, url)), url


def test_overview_lists_missing_documents_with_the_page_that_takes_them(ops):
    ed, lou, state = ops
    page = _main(_page(ed, f'{O}/'))
    assert 'Committee files' in page and 'folders/rootfolder' in page
    gaps = page.split('Missing documents')[1]
    assert f'href="{O}/items/EQ-001#add-document">Upload' in gaps and 'Missing registration' in gaps       # has a row, one of two there
    assert f'href="{O}/items/EQ-002#add-document">Upload' in gaps and 'Missing title, registration' in gaps  # no row yet: what a trailer needs
    assert f'href="{O}/places/PL-001#add-document">Upload' in gaps and f'href="{O}/money#region-documents">Upload' in gaps
    assert 'Renew trailer plate' in page and '>Done</button>' in page and f'href="{O}/items/EQ-002">Long trailer</a>' in page
    assert f'href="{O}/money"' in page and f'href="{O}/money/replacement"' in page and f'href="{O}/items?by=vehicles"' in page   # the four cards
    assert '>Upload<' not in _main(_page(lou, f'{O}/'))
    state['docs'], state['items'] = [], []
    assert 'Every title, registration, card and agreement is on file.' in _page(ed, f'{O}/')


def test_gear_is_one_list_with_filters(ops):
    ed, lou, state = ops
    page = _main(_page(ed, f'{O}/items'))
    for label, href in (('All', f'{O}/items'), ('Vehicles and trailers', f'{O}/items?by=vehicles'), ('By place', f'{O}/items?by=place'),
                        ('By category', f'{O}/items?by=category')):
        assert re.search(r'href="%s">%s</a>' % (re.escape(href), label), page), label
    assert 'btn-outline-light active" aria-current="true" href="/operations/items">All<' in page
    assert all(n in page for n in ('Blue tow truck', 'Cones', 'New radios', 'Timing laptop')) and 'Old canopy' not in page
    assert page.index('Blue tow truck') < page.index('Cones') < page.index('Long trailer')            # by name
    assert 'Old canopy' in _page(ed, f'{O}/items?gone=1')
    veh = _main(_page(ed, f'{O}/items?by=vehicles'))
    assert all(h in veh for h in ('Kept at', 'Condition', 'Value left', 'Replace in')) and 'Blue tow truck' in veh and 'Long trailer' in veh
    assert 'Cones' not in veh and 'New radios' not in veh and 'Missing registration' in veh
    where = _main(_page(ed, f'{O}/items?by=place'))
    assert f'href="{O}/places/PL-001">North barn</a>' in where and 'With people' in where and 'Pat Example' in where and 'Shut lot' not in where
    assert where.index('Long trailer') < where.index('Cones')                                           # the cones are in the trailer
    cat = _main(_page(ed, f'{O}/items?by=category'))
    assert 'Hospitality (1)' in cat and 'Old canopy' not in cat and 'Hospitality (2)' in _page(ed, f'{O}/items?by=category&gone=1')
    assert 'aria-current="true" href="/operations/items">All<' in _page(ed, f'{O}/items?by=sideways')    # nonsense: the whole list


def test_an_item_page_has_its_documents_to_dos_and_replacement_fields(ops):
    ed, lou, state = ops
    page = _main(_page(ed, f'{O}/items/EQ-001'))
    assert 'Title - Blue tow truck.pdf' in page and 'Registration: <span class="text-warning">missing' in page
    assert f'action="{O}/documents/add"' in page and 'name="for_id" value="EQ-001"' in page and 'name="back" value="/operations/items/EQ-001"' in page
    assert 'value="located"' in page and 'Confirm or move' in page
    assert re.search(r'id="repl-year" name="replace_year"[^>]*value="%d"' % (T.year + 1), page) and 'name="replace_cost"' in page and 'value="$45,000"' in page
    assert 'value="off_plan"' in page and 'Take off the plan' in page
    trailer = _main(_page(ed, f'{O}/items/EQ-002'))
    assert 'Renew trailer plate' in trailer and 'name="was_due"' in trailer and 'Sweep the floor' not in trailer
    assert 'href="/operations/tasks/new?about=Long+trailer&amp;back=/operations/items/EQ-002"' in trailer
    assert 'Title (missing)' in trailer                                              # no Documents row yet: the upload form is still there
    barn = _main(_page(ed, f'{O}/places/PL-001'))
    assert 'Sweep the floor' in barn and 'Blue tow truck' in barn and 'Agreement: <span class="text-warning">missing' in barn
    assert 'name="for_id" value="PL-001"' in barn and '$3,000' in barn               # its bill, a year
    seen = _main(_page(lou, f'{O}/items/EQ-001'))
    assert 'Replace in %d, estimated' % (T.year + 1) in seen and 'Registration' in seen
    assert not re.search(r'<(form|input|button|select|textarea)\b', seen)


def test_upload_goes_back_to_where_it_came_from_and_only_to_this_site(ops):
    import io
    ed, lou, state = ops

    def send(back, **kw):
        data = dict({'doc': 'Registration', 'row_id': 'DC-001', 'for_id': 'EQ-001', 'back': back, 'files': [(io.BytesIO(b'x'), 'scan.pdf')]}, **kw)
        return ed.post(f'{O}/documents/add', data=data, content_type='multipart/form-data').headers['Location']
    assert send(f'{O}/items/EQ-001') == f'{O}/items/EQ-001'
    assert send('https://elsewhere.example/x') == f'{O}/items/EQ-001#documents' and send('//elsewhere.example') == f'{O}/items/EQ-001#documents'
    assert send('', row_id='DC-002', for_id='PL-001') == f'{O}/places/PL-001#documents'
    assert send('', row_id='DC-003', for_id='') == f'{O}/money#region-documents'
    assert len(state['uploads']) == 5 and state['uploads'][0] == ('f1', 'Registration - Blue tow truck.pdf')
    assert lou.post(f'{O}/documents/add', data={'doc': 'Title', 'for_id': 'EQ-001'}).status_code == 302 and len(state['uploads']) == 5


def test_money_has_bills_replacement_and_the_region_documents(ops):
    ed, lou, state = ops
    bills = _main(_page(ed, f'{O}/money'))
    assert re.search(r'href="/plan/\d{4}/OPS[^"]*">\d{4} Operations budget \(officers edit it\)</a>', bills)
    assert 'Web hosting' in bills and 'North barn' in bills and f'href="{O}/items/EQ-002">Long trailer</a>' in bills     # the bill is about the trailer
    assert 'Insurance and region documents' in bills and 'Region insurance' in bills and 'name="row_id" value="DC-003"' in bills
    assert 'aria-current="true" href="/operations/money">Bills<' in bills and f'href="{O}/money/replacement">Replacement</a>' in bills
    plan = _main(_page(ed, f'{O}/money/replacement'))
    assert 'aria-current="true" href="/operations/money/replacement">Replacement<' in plan and 'Plan settings' not in plan
    seen = _main(_page(lou, f'{O}/money'))
    assert 'Region insurance' in seen and not re.search(r'<(form|input|button|select|textarea)\b', seen)


def test_the_one_rule_sits_in_its_sentence_and_saves_alone(ops):
    ed, lou, state = ops
    plan = _main(_page(ed, f'{O}/money/replacement'))
    text = _text(plan)
    assert "so bought out of that year's Operations budget: $51,000 in %d." % (T.year + 1) in text and 'Due by %d,' % (T.year + 2) in text
    for gone in ('Big items', 'big item', 'from savings', 'come from savings', 'for the rest', 'motion', 'propose it'):
        assert gone not in text, gone
    forms = dict(re.findall(r'<form id="(rule-\w+)" method="post" action="/operations/plan/settings"[^>]*hidden>(.*?)</form>', plan, re.S))
    names = {k: set(re.findall(r'name="(\w+)"', v)) - {'_csrf'} for k, v in forms.items()}
    assert names == {'rule-soon': {'budget_within_years'}}
    assert plan.count('data-reveal="rule-soon" aria-expanded="false"') == 1 and 'rule-big' not in plan
    # the totals are worked out, never typed: the only fields are the rule, the note, and each row's year and cost
    # (Ed edits Operations but does not record board decisions, so the board's amount is text for him)
    assert set(re.findall(r'<(?:input|select|textarea)[^>]* name="(\w+)"', plan)) - {'_csrf', 'action'} == \
        {'budget_within_years', 'note', 'replace_year', 'replace_cost'}

    r = ed.post(f'{O}/plan/settings', data={'budget_within_years': '1'})
    assert r.headers['Location'] == f'{O}/money/replacement' and state['cfg'] == {'budget_within_years': 1}
    assert state['audit'][-1] == ('EQUIP_PLAN_SETTINGS', {'before': {'budget_within_years': 2}, 'after': {'budget_within_years': 1}})
    assert 'Due by %d,' % (T.year + 1) in _text(_main(_page(ed, f'{O}/money/replacement')))
    # an old form's big-item fields are ignored: nothing is saved for them
    n = len(state['audit'])
    _flashes(ed)
    ed.post(f'{O}/plan/settings', data={'big_cost': '$50,000', 'big_within_years': '4', 'big_from_savings': 'yes'})
    assert state['cfg'] == {'budget_within_years': 1} and len(state['audit']) == n and 'Nothing changed.' in _flashes(ed)
    for bad in ({'budget_within_years': '6'}, {'budget_within_years': '-1'}, {'budget_within_years': 'soon'}):
        ed.post(f'{O}/plan/settings', data=bad)
        assert state['cfg'] == {'budget_within_years': 1} and _flashes(ed), bad
    lou.post(f'{O}/plan/settings', data={'budget_within_years': '3'})
    assert state['cfg'] == {'budget_within_years': 1}


def test_the_committee_note_is_saved_and_cleared_without_touching_the_rules(ops):
    ed, lou, state = ops
    state['cfg'] = dict(E.PLAN_DEFAULTS, budget_within_years=3)
    assert 'Note from the committee:' not in _page(lou, f'{O}/money/replacement')
    r = ed.post(f'{O}/plan/note', data={'note': '  Radios first.\nThe <b>trailer</b> can wait.  '})
    assert r.headers['Location'] == f'{O}/money/replacement' and state['note'] == 'Radios first. The <b>trailer</b> can wait.'
    assert state['cfg'] == dict(E.PLAN_DEFAULTS, budget_within_years=3)
    assert state['audit'][-1] == ('EQUIP_PLAN_NOTE', {'before': '', 'after': 'Radios first. The <b>trailer</b> can wait.'})
    seen = _main(_page(lou, f'{O}/money/replacement'))
    assert '<b>Note from the committee:</b> Radios first. The &lt;b&gt;trailer&lt;/b&gt; can wait.' in seen and 'data-reveal' not in seen
    assert 'data-reveal="note-form" aria-expanded="false" aria-controls="note-form">Edit<' in _page(ed, f'{O}/money/replacement')
    ed.post(f'{O}/plan/note', data={'note': 'x' * (E.NOTE_MAX + 1)})
    assert state['note'].startswith('Radios') and str(E.NOTE_MAX) in _flashes(ed)
    lou.post(f'{O}/plan/note', data={'note': 'mine'})
    assert state['note'].startswith('Radios')
    ed.post(f'{O}/plan/note', data={'note': ' '})
    assert state['note'] == '' and 'Note removed.' in _flashes(ed) and state['cfg'] == dict(E.PLAN_DEFAULTS, budget_within_years=3)
    assert 'Add a note from the committee' in _page(ed, f'{O}/money/replacement')


class Tab:
    """The 'Plan settings' worksheet, as much of it as the store uses."""
    title = E.PLAN_TAB

    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def get(self, rng, **k):
        return [list(r) for r in self.rows]

    def update(self, values, where, **k):
        assert where == 'A1'
        self.rows = [list(r) for r in values] + self.rows[len(values):]


def test_the_note_is_one_more_row_on_the_plan_settings_tab(monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'LOCK', str(tmp_path / 'equipment.lock'))
    monkeypatch.setattr(E, 'refresh', lambda: None)
    # the tab as it was left by the old rules: the big-item rows stay where they are, unread
    tab = Tab([E.PLAN_LABELS, ['big_cost', 25000, 'help', '2026-01-01', 'Pat Example'], ['big_within_years', 3], ['budget_within_years', 2],
               ['big_from_savings', 'no']])
    monkeypatch.setattr(E, '_plan_ws', lambda: tab)
    old = {r[0]: list(r) for r in tab.rows[1:]}
    kept = lambda: {r[0]: r[:len(old[r[0]])] for r in tab.rows if r and r[0] in old}  # noqa: E731
    assert E._note_text(tab.rows[1:]) == ''                                         # no note row yet: no note, no error
    E.save_plan_note('Radios first.', 'Ed Gear', '2026-11-09')
    assert kept() == old                                                            # every row exactly as it was
    note = next(r for r in tab.rows if r[0] == 'note')
    assert note[:2] == ['note', 'Radios first.'] and note[3:] == ['2026-11-09', 'Ed Gear']
    assert E._note_text(tab.rows[1:]) == 'Radios first.' and E._plan_cfg(tab.rows[1:]) == {'budget_within_years': 2}
    E.save_plan_settings({'budget_within_years': 3}, 'Ed Gear', '2026-11-10')
    assert [r[0] for r in tab.rows[1:]] == ['budget_within_years', 'note', 'big_cost', 'big_within_years', 'big_from_savings']
    assert E._plan_cfg(tab.rows[1:]) == {'budget_within_years': 3} and E._note_text(tab.rows[1:]) == 'Radios first.'
    assert {k: v for k, v in kept().items() if k.startswith('big_')} == {k: v for k, v in old.items() if k.startswith('big_')}
    E.save_plan_note('', 'Ed Gear', '2026-11-11')
    assert E._note_text(tab.rows[1:]) == '' and E._plan_cfg(tab.rows[1:]) == {'budget_within_years': 3}
    # a tab with only a note (no rule saved yet) keeps reading the default rules, and a note typed above the rules survives a rules save
    tab.rows = [E.PLAN_LABELS, ['note', 'Typed in the sheet']]
    assert E._plan_cfg(tab.rows[1:]) == E.PLAN_DEFAULTS
    E.save_plan_settings(dict(E.PLAN_DEFAULTS), 'Ed Gear', '2026-11-12')
    assert E._note_text(tab.rows[1:]) == 'Typed in the sheet' and len([r for r in tab.rows if r and r[0] == 'note']) == 1


def test_replacement_rows_save_and_come_off_the_plan(ops):
    ed, lou, state = ops
    plan = _main(_page(ed, f'{O}/money/replacement'))
    row = plan.split('<tr id="EQ-002">')[1].split('</tr>')[0]
    assert 'class="form-control form-control-sm"' in row and 'form="rp-EQ-002" name="replace_year"' in row and 'form="rp-EQ-002" name="replace_cost"' in row
    assert '<form id="rp-EQ-002" method="post" action="/operations/items/EQ-002"' in row and 'data-busy="Saving…">Save</button>' in row
    assert 'value="off_plan"' in row and 'Take off the plan' in row and 'return confirm(' in row
    assert 'visibility:hidden' not in plan                                           # Save is always there, not only after typing
    assert f'href="{O}/items/EQ-003#replacement">Timing laptop</a>' in plan         # over $2,500, no year: opens at the fields
    r = ed.post(f'{O}/items/EQ-002', data={'action': 'replacement', 'replace_year': '2032', 'replace_cost': '$41,500'})
    tab, saved, changed = state['saves'][-1]
    assert r.headers['Location'] == f'{O}/money/replacement#EQ-002' and saved['replace_year'] == '2032' and saved['replace_cost'] == '41500'
    assert {'replace_year', 'replace_cost'} <= changed
    n = len(state['saves'])
    ed.post(f'{O}/items/EQ-002', data={'action': 'replacement', 'replace_year': 'soon', 'replace_cost': '1'})
    assert len(state['saves']) == n and 'numbers' in _flashes(ed)
    r = ed.post(f'{O}/items/EQ-002', data={'action': 'off_plan'})
    tab, saved, changed = state['saves'][-1]
    assert tab == 'Property' and saved['replace_year'] == '' and saved['replace_cost'] == '' and saved['status'] == 'In use' and saved['name'] == 'Long trailer'
    assert {'replace_year', 'replace_cost'} <= changed and 'status' not in changed and r.headers['Location'] == f'{O}/money/replacement'
    assert 'Long trailer is off the replacement plan. It is still in Gear.' in _flashes(ed)
    assert state['audit'][-1] == ('EQUIP_ITEM_SAVED', {'id': 'EQ-002', 'name': 'Long trailer', 'replace_year': '', 'replace_cost': ''})
    state['items'][6].update(replace_year=float(T.year + 4), replace_cost=5000.0)    # the tent also has a year bought and a useful life
    ed.post(f'{O}/items/EQ-007', data={'action': 'off_plan', 'back': f'{O}/items/EQ-007#replacement'})
    assert 'still on the plan for %d' % (T.year + 10) in _flashes(ed)
    n = len(state['saves'])
    lou.post(f'{O}/items/EQ-001', data={'action': 'off_plan'})
    assert len(state['saves']) == n


def test_a_to_do_picks_its_item_or_place_from_a_list_and_links_to_it(ops):
    ed, lou, state = ops
    form = _page(ed, f'{O}/tasks/new')
    names = re.findall(r'<option value="([^"]*)">', form.split('<datalist id="aboutNames">')[1].split('</datalist>')[0])
    assert names == ['Blue tow truck', 'Cones', 'Long trailer', 'New radios', 'North barn', 'Pop-up tent', 'Timing laptop']   # not the gone canopy or the shut lot
    assert 'name="about" class="form-control" list="aboutNames"' in form
    assert 'name="about" class="form-control" list="aboutNames" autocomplete="off" value="North barn"' in _page(ed, f'{O}/tasks/new?about=North+barn')
    ed.post(f'{O}/tasks/new', data={'task': 'Check the tires', 'about': 'Anything at all', 'back': f'{O}/items/EQ-002'})
    assert state['saves'][-1][1]['about'] == 'Anything at all'                      # free text is still fine
    todo = _main(_page(ed, f'{O}/tasks'))
    assert f'<a href="{O}/items/EQ-002">Long trailer</a>' in todo and f'<a href="{O}/places/PL-001">North barn</a>' in todo
    assert 'Somebody &amp; co' in todo and 'Call about &lt;keys&gt;' in todo and '>Somebody' not in todo.replace('">Somebody', '')
    state['tasks'][1]['about'] = 'race trailer'                                      # what people call it works too
    assert f'<a href="{O}/items/EQ-002">race trailer</a>' in _page(lou, f'{O}/tasks')


def test_people_who_cannot_edit_see_no_controls(ops):
    ed, lou, state = ops
    state['note'] = 'Radios first.'
    for url in (f'{O}/', f'{O}/tasks', f'{O}/items', f'{O}/items?by=vehicles', f'{O}/items?by=place', f'{O}/items?by=category', f'{O}/items/EQ-001',
                f'{O}/items/EQ-002', f'{O}/items/EQ-005', f'{O}/places/PL-001', f'{O}/money', f'{O}/money/replacement'):
        seen = _main(_page(lou, url))
        assert not re.search(r'<(form|input|button|select|textarea)\b', seen), url
        assert not re.search(r'>\s*(Edit|Save|Change|Upload|Done|Remove|Take off the plan|Add an? [a-z -]+)\s*</(a|button)>', seen), url
        assert 'Changes are made by the Operations committee' in seen
        assert re.search(r'<form\b|btn-accent', _main(_page(ed, url))), url            # and the committee does
    for url in (f'{O}/items/EQ-001?edit=1', f'{O}/items/new', f'{O}/places/new', f'{O}/places/PL-001?edit=1', f'{O}/tasks/new', f'{O}/tasks/TK-001'):
        assert lou.get(url).status_code == 302 and ed.get(url).status_code == 200, url


def test_no_page_repeats_an_element_id_or_leaves_a_field_unnamed(ops):
    ed, lou, state = ops
    state['note'] = 'Radios first.'
    for url in (f'{O}/', f'{O}/tasks', f'{O}/items', f'{O}/items?by=vehicles', f'{O}/items?by=place', f'{O}/items?by=category&gone=1', f'{O}/items/EQ-001',
                f'{O}/items/EQ-002', f'{O}/places/PL-001', f'{O}/money', f'{O}/money/replacement', f'{O}/tasks/TK-001', f'{O}/items/EQ-001?edit=1'):
        html = _page(ed, url)
        ids = re.findall(r'\sid="([^"]+)"', html)
        assert len(ids) == len(set(ids)), (url, sorted(i for i in set(ids) if ids.count(i) > 1))
        assert '—' not in html, url
        labelled = set(re.findall(r'<label[^>]* for="([^"]+)"', html))
        for tag in re.findall(r'<(?:input|select|textarea)\b[^>]*>', _main(html)):
            if 'type="hidden"' in tag or 'aria-label=' in tag or (re.search(r'\sid="([^"]+)"', tag) or [None, ''])[1] in labelled:
                continue
            before = _main(html).split(tag)[0].rstrip()
            assert before.endswith('</label>'), (url, tag)                            # base.html ties a label to the field right after it
