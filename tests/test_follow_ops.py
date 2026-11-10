"""Follow-ups: a family number only takes the primary member's dates when it is the same person, "Checked against
QuickBooks" is a choice plus a note kept in the same cell, the Members list starts short, and dates read as words.
Made-up names only."""
from datetime import date

import pytest

import ballots_logic as BL
import equip_store as E
import members_logic as L

LIST = date(2026, 11, 2)


def _report(*rows):
    return L.roster([('Member ID', 'First Name', 'Last Name', 'SCCA Member Expire', 'Region Join Date')] + list(rows))


def test_a_spouse_who_left_does_not_take_the_primary_members_dates():
    report = _report(('500200_0', 'Robin', 'Madeup', '05/31/2027', '11/02/1991'))
    members = [{'member': '500200', 'first': 'Robin', 'last': 'Madeup', 'life': False, 'points': 3.0, 'expires': date(2026, 5, 31), 'listed': date(2026, 10, 1)},
               {'member': '500200-1', 'first': 'Jordan', 'last': 'Madeup', 'life': False, 'points': 12.0, 'expires': date(2026, 5, 31), 'listed': date(2026, 10, 1)}]
    out = {m['member']: m for m in L.merge_roster(members, report, LIST)}
    assert len(out) == 2
    assert out['500200']['listed'] == LIST and out['500200']['expires'] == date(2027, 5, 31) and not out['500200']['lapsed']
    spouse = out['500200-1']
    assert spouse['listed'] == date(2026, 10, 1) and spouse['expires'] == date(2026, 5, 31) and spouse['lapsed'] == date(2026, 5, 31)
    assert [m['member'] for m in L.lapsed(list(out.values()), LIST)] == ['500200-1']


def test_the_primary_still_gets_a_row_when_only_the_spouse_was_in_the_ledger():
    report = _report(('500200_0', 'Robin', 'Madeup', '05/31/2027', '11/02/1991'))
    members = [{'member': '500200-1', 'first': 'Jordan', 'last': 'Madeup', 'life': False, 'points': 12.0}]
    out = {m['member']: m for m in L.merge_roster(members, report, LIST)}
    assert out['500200']['first'] == 'Robin' and out['500200']['listed'] == LIST and out['500200']['points'] == 0
    assert not out['500200-1'].get('listed') and out['500200-1']['lapsed'] == LIST


def test_one_person_under_both_number_forms_still_matches_loosely():
    report = _report(('500200', 'Robert', 'Madeup', '05/31/2027', '11/02/1991'))
    for first, last in [('Bob', 'Madeup'), ('R.', 'MADEUP'), ('Rob', 'Madeup Jr'), ('Robert', 'Madeup')]:
        out = L.merge_roster([{'member': '500200-1', 'first': first, 'last': last, 'life': False, 'points': 1.0}], report, LIST)
        assert len(out) == 1 and out[0]['listed'] == LIST and out[0]['first'] == first, first      # dates taken, name kept
    for first, last in [('Jordan', 'Madeup'), ('Robert', 'Other'), ('', 'Madeup')]:
        out = L.merge_roster([{'member': '500200-1', 'first': first, 'last': last, 'life': False, 'points': 1.0}], report, LIST)
        assert len(out) == 2 and not out[0].get('listed'), first
    # a family number that is on the list itself needs no name check
    out = L.merge_roster([{'member': '500200-1', 'first': 'J', 'last': 'Madeup', 'life': False, 'points': 1.0}],
                         _report(('500200_1', 'Jordan', 'Madeup', '05/31/2027', '11/02/1991')), LIST)
    assert len(out) == 1 and out[0]['first'] == 'Jordan' and out[0]['listed'] == LIST


def test_quickbooks_check_reads_old_text_and_writes_word_and_note():
    assert E.qbo_split('Matches') == ('Matches', '') and E.qbo_split('Differs: $20 more in QuickBooks') == ('Differs', '$20 more in QuickBooks')
    assert E.qbo_split('Corrected 2026-10-01, was $95') == ('Corrected', '2026-10-01, was $95')
    assert E.qbo_split('Estimated from last year') == ('', 'Estimated from last year') and E.qbo_split('ask the treasurer') == ('', 'ask the treasurer')
    assert E.qbo_split(None) == ('', '')
    assert E.qbo_join('Matches', '') == 'Matches' and E.qbo_join('Differs', ' $20 more ') == 'Differs: $20 more'
    assert E.qbo_join('', 'ask the treasurer') == 'ask the treasurer' and E.qbo_join('Nonsense', 'x') == 'x' and E.qbo_join('', '') == ''
    for text in ('Matches', 'Estimate: about $40', 'ask the treasurer'):
        assert E.qbo_join(*E.qbo_split(text)) == text


def _task(**kw):
    return dict({'id': 'TK-001', 'task': 'Storage unit insurance', 'about': '', 'due': '2027-03-10', 'repeat_months': 12.0, 'cost': 120.0, 'owner': '',
                 'notes': '', 'last_done': '', 'done_by': '', 'category': 'Insurance', 'paid_how': '', 'auto': '', 'qbo_check': '', 'account': '', 'ends': ''}, **kw)


@pytest.fixture()
def ops(monkeypatch):
    import app as A
    import auth
    import equip
    state = {'tasks': [_task(qbo_check='Matches the March charge')], 'saves': []}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Ed Gear', 'level': 'official', 'roles': ['Equipment'], 'perms': {'ops'}} if e else None)
    monkeypatch.setattr(equip, 'editors', lambda: {'ed@example.org': 'Ed Gear'})
    monkeypatch.setattr(equip.E, 'enabled', lambda: True)
    monkeypatch.setattr(equip.E, 'tasks', lambda: [dict(t) for t in state['tasks']])
    monkeypatch.setattr(equip.E, 'items', lambda: [])
    monkeypatch.setattr(equip.E, 'places', lambda closed=False: [])

    def save(tab, row, changed=None, expect=None):
        state['saves'].append((dict(row), changed))
        state['tasks'] = [dict(row) if t['id'] == row['id'] else t for t in state['tasks']]
        return row['id']
    monkeypatch.setattr(equip.E, 'save', save)
    monkeypatch.setattr(equip, 'log_audit_event', lambda *a, **k: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='ed@example.org', who='Ed Gear', equip_editor={'email': 'ed@example.org', 'name': 'Ed Gear'})
    return c, state


def test_quickbooks_check_form_keeps_old_text_until_it_is_changed(ops):
    import json
    c, state = ops
    page = c.get('/operations/tasks/TK-001').get_data(as_text=True)
    assert '<option selected>Matches</option>' in page and 'value="the March charge"' in page and 'start with' not in page
    assert '<title>Storage unit insurance · ' in page
    base = json.dumps(state['tasks'][0])
    form = {'_base': base, 'task': 'Storage unit insurance', 'qbo_word': 'Matches', 'qbo_note': 'the March charge'}
    c.post('/operations/tasks/TK-001', data=form)
    row, changed = state['saves'][-1]
    assert row['qbo_check'] == 'Matches the March charge' and 'qbo_check' not in changed        # untouched: the cell is not rewritten
    c.post('/operations/tasks/TK-001', data=dict(form, qbo_word='Differs', qbo_note='$20 more in QuickBooks'))
    row, changed = state['saves'][-1]
    assert row['qbo_check'] == 'Differs: $20 more in QuickBooks' and 'qbo_check' in changed
    c.post('/operations/tasks/TK-001', data=dict(form, _base=json.dumps(row), qbo_word='', qbo_note=''))
    row, changed = state['saves'][-1]
    assert row['qbo_check'] == '' and 'qbo_check' in changed


@pytest.fixture()
def keeper(monkeypatch):
    import app as A
    import auth
    import members
    mem = [{'member': '100', 'first': 'Pat', 'last': 'Example', 'life': False, 'points': 0.0, 'listed': LIST, 'expires': date(2099, 1, 31), 'joined': date(2010, 5, 1)},
           {'member': '200', 'first': 'Lee', 'last': 'Sample', 'life': False, 'points': 4.0, 'listed': date(2020, 1, 1), 'expires': date(2020, 6, 30)},
           {'member': '300', 'first': 'Kim', 'last': 'Doe', 'life': False, 'points': 0.0, 'listed': date(2020, 1, 1), 'expires': date(2020, 6, 30)},
           {'member': '400', 'first': 'Robin', 'last': 'Madeup', 'life': True, 'points': 0.0}]
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Points Keeper', 'level': 'official', 'roles': [], 'perms': {'points'}})
    monkeypatch.setattr(members.S, 'enabled', lambda: True)
    monkeypatch.setattr(members.S, 'members', lambda: [dict(m) for m in mem])
    monkeypatch.setattr(members.S, 'entries', lambda: [])
    for name in ('members_sheet_url', 'list_folder_url'):
        monkeypatch.setattr(members.S, name, lambda: 'https://example.org/sheet')
    monkeypatch.setattr(members, '_reserve', lambda: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='keeper@example.org')
    return c


def test_members_list_starts_with_current_members_and_points(keeper):
    page = keeper.get('/members/').get_data(as_text=True)
    assert 'Pat Example' in page and 'Lee Sample' in page and 'Robin Madeup' in page      # current, has points, life member
    assert '/members/member/300' not in page and 'Show everyone (4)' in page and 'all=1' in page
    assert 'data-k=' not in page and 'data-href=' not in page
    everyone = keeper.get('/members/?all=1&q=kim').get_data(as_text=True)
    assert '/members/member/300' in everyone and 'Show everyone' not in everyone and 'value="kim"' in everyone
    assert '<title>Pat Example · ' in keeper.get('/members/member/100').get_data(as_text=True)


def test_dates_in_text_read_as_words():
    assert L.day_text(date(2026, 12, 5)) == 'Dec 5, 2026' and L.day_text(None) == ''
    assert E.nice_date('2026-12-05') == 'Dec 5, 2026' and E.nice_date('') == '' and E.nice_date('soon') == 'soon'
    assert BL.nice_date('2026-12-05') == 'Dec 5, 2026' and BL.nice_date('') == ''
    used = [{'date': date(2026, 12, 5), 'name': 'Pat Example', 'what': 'Renewal', 'points': -4.0}]
    body = L.books_email({'jes': [], 'used': used}, 'Dec 5, 2026')[1]
    assert 'Dec 5, 2026: Pat Example' in body and 'mark them as entered' in body
