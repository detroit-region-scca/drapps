"""Adding worker points goes through a review page; nothing is saved until Save there. Made-up names only."""
import io
import re
from datetime import date

import openpyxl
import pytest
from werkzeug.datastructures import MultiDict

import members_logic as L

MEMBERS = [{'member': '100', 'first': 'Pat', 'last': 'Example'}, {'member': '200', 'first': 'Lee', 'last': 'Sample'},
           {'member': '201', 'first': 'Lynn', 'last': 'Sample'}, {'member': '300', 'first': 'Rob', 'last': 'Tester III'},
           {'member': '400', 'first': 'Kim', 'last': 'Doe'}, {'member': '401', 'first': 'Kim', 'last': 'Doe'}]
BY_ID = {m['member']: m for m in MEMBERS}


def form_rows():
    """The worker point form as volunteer leads send it: titles, two Name / Points pairs, a Total, then a footer."""
    return [('DETROIT REGION SCCA', None, None, None, None), ('Formula Test Event', None, None, None, None),
            ('May 14-16, 2026', None, None, None, None), (None,) * 5, ('SPECIALTY:   All', None, None, None, None),
            ("Worker's Name", 'Points', None, 'Workers Name', 'Points'),
            ('Pat Example', 9, None, 'Lee Sample', 6), ('Robert Tester', 12, None, 'Kim Doe', 3),
            ('Nobody Known', 3, None, None, None), (None,) * 5,
            (None, None, None, 'Total', 33), (None,) * 5,
            ('Submitted by: Pat Example', None, None, '1 Main Street', None), ('Race day worker - 3 points', None, None, None, None)]


def test_sheet_reads_both_columns_stops_before_the_footer_and_finds_the_total():
    r = L.read_points_sheet([('Point Submission', form_rows())])
    assert [(x['name'], x['points']) for x in r['lines']] == [('Pat Example', 9), ('Lee Sample', 6), ('Robert Tester', 12),
                                                                 ('Kim Doe', 3), ('Nobody Known', 3)]
    t = r['tabs'][0]
    assert t['total'] == 33 and t['tab'] == 'Point Submission' and 'Formula Test Event' in t['title'] and t['below'] == 0


def test_sheet_keeps_reading_past_one_blank_row_and_reads_every_tab():
    rows = [('Name', 'Points'), ('Pat Example', 9), (None, None), ('Lee Sample', 6), ('Kim Doe', 3), (None, None), (None, None), ('Total', 18)]
    t = L.read_points_sheet([('S', rows)])['tabs'][0]
    assert [x['name'] for x in t['lines']] == ['Pat Example', 'Lee Sample', 'Kim Doe'] and t['total'] == 18
    rows = [('Name', 'Points'), ('Pat Example', 9), (None, None), (None, None), ('Lee Sample', 6)]
    t = L.read_points_sheet([('S', rows)])['tabs'][0]
    assert [x['name'] for x in t['lines']] == ['Pat Example'] and t['below'] == 1          # said on the page, not silent
    two = L.read_points_sheet([('Blank form', [('Name', 'Points')]), ('Day 1', [('Name', 'Points'), ('Pat Example', 3), ('Total Points', 3)]),
                               ('Day 2', [('Workers Name', 'Points'), ('Lee Sample', 3), ('TOTAL:', 3)])])
    assert [t['tab'] for t in two['tabs']] == ['Day 1', 'Day 2'] and [t['total'] for t in two['tabs']] == [3, 3]
    assert [x['name'] for x in two['lines']] == ['Pat Example', 'Lee Sample']
    side = L.read_points_sheet([('S', [('Name', 'Points', None, 'Name', 'Points'), ('Pat Example', 9, None, 'Lee Sample', 6),
                                       ('Total', 15, None, 'Kim Doe', 3), (None,) * 5, (None,) * 5, ('Mail to: x', None, None, 'y', None)])])
    assert [x['name'] for x in side['lines']] == ['Pat Example', 'Lee Sample', 'Kim Doe'] and side['tabs'][0]['total'] == 15


def test_sheet_without_the_heading_is_refused_and_blank_points_are_kept_for_review():
    with pytest.raises(ValueError):
        L.read_points_sheet([('Sheet1', [('Name only',), ('Pat Example',)])])
    r = L.read_points_sheet([('S', [('Name', 'Points'), ('Pat Example', None), ('Lee Sample', 'two')])])
    assert [(x['name'], x['points']) for x in r['lines']] == [('Pat Example', None), ('Lee Sample', None)]


def test_review_matches_only_exact_names_and_offers_the_rest():
    entries = [{'member': '200', 'event': '#E1', 'points': 4.0, 'status': 'Approved', 'by': 'Flag Chief'}]
    lines = [{'name': 'Pat Example', 'points': 9}, {'name': 'Robert Tester', 'points': 12}, {'name': 'Sample, Lee', 'points': 6},
             {'name': 'Lee Sample', 'points': 6}, {'name': 'Kim Doe', 'points': 3}, {'name': 'Pat Example', 'points': 1},
             {'name': 'Nobody Known', 'points': None}]
    r = L.review_lines(lines, MEMBERS, entries, '#E1')
    assert r[0]['member'] == '100' and not r[0]['already'] and not r[0]['twice']
    assert r[1]['member'] == '' and [c['member'] for c in r[1]['candidates']] == ['300']          # suffix ignored, never auto-picked
    assert r[2]['member'] == '' and [c['member'] for c in r[2]['candidates']] == ['200', '201']   # "Last, First"; same initial first
    assert r[2]['candidates'][0]['already']['points'] == 4
    assert r[3]['member'] == '200' and r[3]['already']['by'] == ['Flag Chief']
    assert r[4]['member'] == '' and {c['member'] for c in r[4]['candidates']} == {'400', '401'}  # two members, same name: pick
    assert r[5]['twice'] and r[6]['problem'] == 'No points on the sheet' and r[6]['candidates'] == []


def test_save_check_blocks_repeats_new_duplicates_and_bad_points():
    e = [{'member': '100', 'event': '#E1', 'points': 9.0, 'status': 'Approved', 'by': 'Ed'}]
    ok = [{'member': '200', 'points': 6, 'seen': 0}]
    assert L.save_check(BY_ID, e, '#E1', ok) == []
    assert 'already has 9 points' in L.save_check(BY_ID, e, '#E1', [{'member': '100', 'points': 9, 'seen': 0}])[0]
    assert L.save_check(BY_ID, e, '#E1', [{'member': '100', 'points': 9, 'seen': 1}]) == []           # "Add anyway" on the review
    assert L.save_check(BY_ID, e, '#E1', [{'member': '100', 'points': 9, 'seen': 0}], allow_duplicates=True) == []
    assert 'listed twice' in L.save_check(BY_ID, [], '#E1', [{'member': '200', 'points': 1}, {'member': '200', 'points': 2}])[0]
    assert 'at most 50' in L.save_check(BY_ID, [], '#E1', [{'member': '200', 'points': 51}])[0]
    assert 'not on the member list' in L.save_check(BY_ID, [], '#E1', [{'member': '999', 'points': 1, 'name': 'X'}])[0]
    assert L.save_check(BY_ID, e, '', [{'member': '100', 'points': 1}]) == []                          # no event: no duplicate check
    assert 'already has' in L.save_check(BY_ID, e, {'#TAG', '#E1'}, [{'member': '100', 'points': 1}])[0]  # the event's other code


@pytest.fixture
def site(monkeypatch, tmp_path):
    import app as A
    import auth
    import members
    import utils
    store = {'entries': [], 'mail': [], 'audit': []}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Ed Chair', 'level': 'official', 'roles': [], 'perms': {'points_enter'}})
    monkeypatch.setattr(members.S, 'enabled', lambda: True)
    monkeypatch.setattr(members.S, 'members', lambda: [dict(m, points=0.0) for m in MEMBERS])
    monkeypatch.setattr(members.S, 'entries', lambda: [dict(e) for e in store['entries']])

    def add(rows, status, who):
        store['entries'] += [dict(r, status=status, by=who) for r in rows]
        return len(rows)
    monkeypatch.setattr(members.S, 'add_entries', add)
    monkeypatch.setattr(members.S, 'refresh', lambda: None)
    monkeypatch.setattr(members.S, 'SAVES_FILE', str(tmp_path / 'points-saves.json'))
    ev = {'unique_code': '#E1', 'custom_tag': '', 'event_name': 'Formula Test Event', 'date': '2026-05-14'}
    monkeypatch.setattr(members, 'load_events', lambda: [ev])
    monkeypatch.setattr(members, '_events_by_code', lambda: {'#E1': ev})
    monkeypatch.setattr(utils, 'send_mail', lambda subject, body, **k: store['mail'].append((subject, body)))
    monkeypatch.setattr(members, 'log_audit_event', lambda *a: store['audit'].append(a))
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = 'chair@example.org'
    return c, store


def xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Point Submission'
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_upload_shows_a_review_and_saves_nothing_until_save(site):
    c, store = site
    page = c.post('/members/enter', data={'event': '#E1', 'first': '', 'sheet': (io.BytesIO(xlsx(form_rows())), 'points.xlsx')},
                  content_type='multipart/form-data').get_data(as_text=True)
    assert store['entries'] == [] and 'Check and save' in page and 'Matches the sheet' in page
    assert 'Leave out: not on the member list' in page and 'Rob Tester III (300)' in page
    # Save what the page offers: Pat, Lee, Kim (picked: 401), Robert (picked: 300); Nobody left out
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    form = MultiDict([('step', 'save'), ('event', '#E1'), ('first', '2026-05-14'), ('what', ''), ('seen', '{}'), ('sheet_name', 'points.xlsx'),
                      ('token', token)])
    for name, pick, pts in [('Pat Example', '100', '9'), ('Lee Sample', '200', '6'), ('Robert Tester', '300', '12'),
                            ('Kim Doe', '401', '3'), ('Nobody Known', '', '3')]:
        form.add('name', name); form.add('member', pick); form.add('points', pts)
    r = c.post('/members/enter', data=form)
    assert r.status_code == 302
    assert sorted((e['member'], e['points']) for e in store['entries']) == [('100', 9), ('200', 6), ('300', 12), ('401', 3)]
    assert all(e['event'] == '#E1' and e['date'] == date(2026, 5, 14) and e['status'] == 'Approved' for e in store['entries'])
    assert 'Nobody Known' in store['mail'][0][1] and 'Left out' in store['mail'][0][1]
    # The same page saved again (a double click, the back button): refused by its token, nothing added
    r = c.post('/members/enter', data=form)
    assert len(store['entries']) == 4 and r.status_code == 302
    # Someone else's review of the same people (the Registrar): refused as duplicates; the picks go back to Leave out
    form.setlist('token', ['another-page'])
    page = c.post('/members/enter', data=form).get_data(as_text=True)
    assert len(store['entries']) == 4 and 'already has 9 points' in page and 'Check and save' in page
    assert 'selected>Add anyway' not in page.replace('\n', '')
    picks = re.findall(r'<option value="(\d*)" selected', page)
    assert picks == []                          # nothing pre-picked: one more Save adds nothing
    again = MultiDict([(k, v) for k, v in form.items(multi=True) if k not in ('member', 'token', 'seen')])
    for p in [''] * 5:
        again.add('member', p)
    again.add('token', re.search(r'name="token" value="([^"]+)"', page).group(1))
    c.post('/members/enter', data=again)
    assert len(store['entries']) == 4


def test_review_page_survives_a_line_without_points_and_a_wrong_total(site):
    c, store = site
    rows = [('Name', 'Points'), ('Pat Example', 9), ('Lee Sample', None), ('Total', 15)]
    page = c.post('/members/enter', data={'event': '#E1', 'first': '', 'sheet': (io.BytesIO(xlsx(rows)), 'p.xlsx')},
                  content_type='multipart/form-data')
    assert page.status_code == 200 and 'Total says 15' in page.get_data(as_text=True) and store['entries'] == []


def test_no_event_save_is_once_per_review_page(site, monkeypatch):
    import members
    c, store = site
    monkeypatch.setattr(members, '_keeper', lambda: True)
    page = c.post('/members/enter', data=MultiDict([('event', 'none'), ('what', 'Trailer work'), ('first', '2026-11-01'),
                                                    ('member', 'Pat Example (100)'), ('points', '2')])).get_data(as_text=True)
    form = MultiDict([('step', 'save'), ('event', 'none'), ('what', 'Trailer work'), ('first', '2026-11-01'), ('seen', '{}'),
                      ('name', 'Pat Example (100)'), ('member', '100'), ('points', '2'),
                      ('token', re.search(r'name="token" value="([^"]+)"', page).group(1))])
    c.post('/members/enter', data=form)
    c.post('/members/enter', data=form)
    assert len(store['entries']) == 1 and store['entries'][0]['kind'] == 'Role points'


def test_typed_names_also_go_through_the_review(site):
    c, store = site
    form = MultiDict([('event', '#E1'), ('first', ''), ('member', 'Pat Example (100)'), ('points', '2'), ('member', 'Lee Sample'), ('points', '')])
    page = c.post('/members/enter', data=form).get_data(as_text=True)
    assert store['entries'] == [] and 'Check and save' in page and 'value="2"' in page and 'value="1"' in page


def test_api_style_save_refuses_duplicates_unless_allowed(site):
    c, store = site
    form = MultiDict([('step', 'save'), ('event', '#E1'), ('first', ''), ('member', '100'), ('points', '2')])
    c.post('/members/enter', data=form)
    assert len(store['entries']) == 1
    c.post('/members/enter', data=form)
    assert len(store['entries']) == 1
    form.add('allow_duplicates', 'yes')
    c.post('/members/enter', data=form)
    assert len(store['entries']) == 2
