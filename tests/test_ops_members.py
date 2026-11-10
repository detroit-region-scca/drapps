"""Members: points are used and deleted once however often the button is pressed, the Members tab is never left
empty by a failed write, reconciled marks follow the entry (not the row number), and typed forms survive an error.
Made-up names only."""
from datetime import date

import pytest
from werkzeug.datastructures import MultiDict

import members_logic as L

LIST = date(2026, 11, 1)
MEMBERS = [{'member': '100', 'first': 'Pat', 'last': 'Example', 'life': False, 'listed': LIST, 'expires': date(2027, 6, 30), 'joined': None},
           {'member': '200', 'first': 'Lee', 'last': 'Sample', 'life': False, 'listed': date(2025, 11, 1), 'expires': date(2026, 3, 31), 'joined': None}]


def _entry(member, name, points, kind='Opening', what='Opening balance'):
    return {'row': 0, 'date': date(2026, 1, 1), 'member': member, 'name': name, 'points': points, 'what': what, 'event': '', 'kind': kind,
            'status': 'Approved', 'by': 'Keeper', 'reconciled': ''}


@pytest.fixture()
def site(monkeypatch, tmp_path):
    import app as A
    import auth
    import members
    store = {'entries': [_entry('100', 'Pat Example', 10.0), _entry('200', 'Lee Sample', 25.0)], 'audit': [], 'shown': None}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Kay Keeper', 'level': 'official', 'roles': ['Points Keeper'],
                                                   'perms': {'points', 'points_enter'}})
    monkeypatch.setattr(members.S, 'enabled', lambda: True)

    def mem():       # the Points column, as the sheet's formula would show it (or a stale page's idea of it)
        bal = L.balances(store['entries'])
        return [dict(m, points=store['shown'] if store['shown'] is not None else bal.get(m['member'], 0.0)) for m in MEMBERS]
    monkeypatch.setattr(members.S, 'members', mem)
    monkeypatch.setattr(members.S, 'entries', lambda: [dict(e, row=i) for i, e in enumerate(store['entries'], start=2)])

    def add(rows, status, who):
        store['entries'] += [dict(r, status=status, by=who, reconciled='') for r in rows]
        return len(rows)
    monkeypatch.setattr(members.S, 'add_entries', add)
    monkeypatch.setattr(members.S, 'refresh', lambda: None)
    monkeypatch.setattr(members.S, 'SAVES_FILE', str(tmp_path / 'points-saves.json'))
    monkeypatch.setattr(members.S, 'auto_rows', lambda fresh=False: [])
    monkeypatch.setattr(members, 'log_audit_event', lambda *a: store['audit'].append(a))
    monkeypatch.setattr(members, '_reserve', lambda: None)
    monkeypatch.setattr(members, 'load_events', lambda: [])
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='keeper@example.org', who='Kay Keeper')
    return c, store


def _flashes(c):
    with c.session_transaction() as s:
        return ' | '.join(m for _, m in s.pop('_flashes', []))


def _used(store):
    return [e['points'] for e in store['entries'] if e['kind'] == 'Used']


def test_use_points_twice_from_one_page_uses_them_once(site):
    c, store = site
    form = {'member': 'Pat Example (100)', 'points': '8', 'for': 'Event entry', 'token': 'page-1'}
    assert c.post('/members/use', data=form).status_code == 302
    c.post('/members/use', data=form)                       # the double click
    assert _used(store) == [-8.0] and 'already used' in _flashes(c)


def test_use_points_checks_the_balance_again_when_saving(site):
    c, store = site
    store['shown'] = 10.0                                     # what this worker's copy of the Members tab still says
    store['entries'].append(_entry('100', 'Pat Example', -8.0, 'Used', 'Event entry'))   # used a moment ago elsewhere
    r = c.post('/members/use', data={'member': 'Pat Example (100)', 'points': '8', 'for': 'Event entry'})   # an old page: no token
    assert _used(store) == [-8.0]                             # not -16: the balance never goes below zero
    assert r.status_code == 200 and 'has only 2 points' in r.get_data(as_text=True)


def test_delete_their_points_twice_deletes_once(site):
    c, store = site
    c.post('/members/forfeit', data={'token': 'home-1'})
    c.post('/members/forfeit', data={'token': 'home-1'})       # the double click
    c.post('/members/forfeit')                                # and again from an old page with no token
    forfeits = [(e['member'], e['points']) for e in store['entries'] if e['kind'] == 'Forfeit']
    assert forfeits == [('200', -25.0)]                       # only the lapsed member, once
    assert L.balances(store['entries']) == {'100': 10.0, '200': 0.0}


def test_auto_add_keeps_what_was_typed_when_a_name_is_wrong(site, monkeypatch):
    import members
    c, store = site
    saved = []
    monkeypatch.setattr(members.S, 'save_auto_rows', saved.append)
    form = MultiDict([('member', 'Pat Example (100)'), ('role', 'Timing chief'), ('season', '2'), ('offseason', '1'),
                      ('member', 'Nobody Known'), ('role', 'Trailer wrangler'), ('season', '1'), ('offseason', '0.5')])
    r = c.post('/members/auto', data=form)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and saved == []
    assert 'Trailer wrangler' in page and 'Nobody Known' in page and 'Timing chief' in page and 'pick the member from the list' in page
    form.setlist('member', ['Pat Example (100)', 'Nobody Known ()'])      # sent back as the page showed it: still refused, not mangled
    assert c.post('/members/auto', data=form).status_code == 200 and saved == []
    form.setlist('member', ['Pat Example (100)', 'Lee Sample (200)'])
    assert c.post('/members/auto', data=form).status_code == 302
    assert [(r['member'], r['role']) for r in saved[0]] == [('100', 'Timing chief'), ('200', 'Trailer wrangler')]


def test_a_month_that_is_not_a_month_is_this_month(site, monkeypatch):
    import members
    this = f'{date.today():%Y-%m}'
    assert members._month('2026-13') == this and members._month('abcd-ef') == this and members._month(None) == this
    assert members._month('2026-00') == this and members._month('2026-02') == '2026-02'
    c, _ = site
    monkeypatch.setattr(members.S, 'milestone_rows', lambda fresh=False: [])
    assert c.get('/members/milestones?month=2026-13').status_code == 200


def test_add_member_knows_500100_0_is_500100(site, monkeypatch):
    import members
    c, store = site
    added = []
    monkeypatch.setattr(members.S, 'add_member', lambda *a: added.append(a))
    r = c.post('/members/add-member', data={'member': '100_0', 'first': 'Pat', 'last': 'Example'})
    assert added == [] and r.headers['Location'].endswith('/members/member/100') and 'already on the list' in _flashes(c)


# ---------------------------------------------------------------------------------------------- the store's writes

class Sheet:
    """A worksheet that remembers what was done to it, in order."""
    def __init__(self, col=(), rows=200, fail_on=None):
        self.calls, self.col, self.row_count, self.col_count, self.fail_on = [], list(col), rows, 10, fail_on

    def _did(self, what, *a):
        if self.fail_on == what:
            raise RuntimeError('Google did not answer')
        self.calls.append((what,) + a)

    def update(self, values, where, **k):
        self._did('update', where, values)

    def batch_clear(self, ranges):
        self._did('clear', ranges)

    def batch_update(self, data, **k):
        self._did('batch_update', data)

    def add_rows(self, n):
        self.row_count += n

    def add_cols(self, n):
        self.col_count += n

    def col_values(self, n):
        return list(self.col)


@pytest.fixture()
def S(monkeypatch, tmp_path):
    import members_store
    monkeypatch.setenv('MEMBERS_SHEET_ID', 'sheet')
    monkeypatch.setenv('PUBLIC_SHEET_ID', 'public')
    monkeypatch.setattr(members_store, 'SAVES_FILE', str(tmp_path / 'points-saves.json'))
    monkeypatch.setattr(members_store, 'refresh', lambda: None)
    monkeypatch.setattr(members_store.C, 'invalidate', lambda sid: None)
    return members_store


def test_members_tab_is_written_before_anything_is_cleared(S, monkeypatch):
    ws = Sheet()
    monkeypatch.setattr(S, 'worksheet', lambda sid, tab: ws)
    S.write_members([dict(m, points=0.0) for m in MEMBERS])
    assert [c[0] for c in ws.calls] == ['update', 'clear']
    assert ws.calls[0][1] == 'A1' and len(ws.calls[0][2]) == 3            # the heading and two members
    assert ws.calls[1][1] == ['A4:J']                                     # only what is left below them
    failing = Sheet(fail_on='update')
    monkeypatch.setattr(S, 'worksheet', lambda sid, tab: failing)
    with pytest.raises(RuntimeError):
        S.write_members([dict(m, points=0.0) for m in MEMBERS])
    assert failing.calls == []                                            # the write failed: nothing was cleared


def test_milestones_are_written_before_the_leftovers_are_cleared(S, monkeypatch):
    ws = Sheet()
    monkeypatch.setattr(S, 'worksheet', lambda sid, tab: ws)
    monkeypatch.setattr(S, 'milestone_rows', lambda fresh=False: [['2026-10', 'Pat Example', 5, ''], ['2026-11', 'Old Name', 0, '']])
    S.save_milestones('2026-11', [['2026-11', 'Lee Sample', 10, '']])
    assert [c[0] for c in ws.calls] == ['update', 'clear']
    assert ws.calls[0][2] == [['2026-11', 'Lee Sample', 10, ''], ['2026-10', 'Pat Example', 5, '']] and ws.calls[1][1] == ['A4:D']


def test_list_load_merges_a_fresh_read(S, monkeypatch):
    seen, written = [], []
    monkeypatch.setattr(S, 'refresh', lambda: seen.append('fresh'))
    monkeypatch.setattr(S, 'members', lambda: seen.append('read') or [dict(MEMBERS[0], points=0.0)])
    monkeypatch.setattr(S, 'write_members', written.append)
    before, after = S.update_members(lambda rows: rows + [dict(MEMBERS[1], points=0.0)])
    assert seen == ['fresh', 'read'] and len(before) == 1 and [m['member'] for m in written[0]] == ['100', '200']


def test_add_member_refuses_the_same_number_written_another_way(S, monkeypatch):
    ws = Sheet(col=['Member #', '500100', '500200-1'])
    monkeypatch.setattr(S, 'worksheet', lambda sid, tab: ws)
    assert S.add_member('500100_0', 'Pat', 'Example') == '500100' and S.add_member('500200_1', 'Lee', 'Sample') == '500200-1'
    assert ws.calls == []
    assert S.add_member('500300', 'Kim', 'Doe') is None and ws.calls[0][:2] == ('update', 'A4')


def test_reconciled_marks_follow_the_entry_not_the_row_number(S, monkeypatch):
    ws = Sheet()
    monkeypatch.setattr(S, 'worksheet', lambda sid, tab: ws)
    e = [dict(_entry('100', 'Pat Example', 4.0, 'Earned', 'Worked Solo 1'), row=2),
         dict(_entry('200', 'Lee Sample', 2.0, 'Earned', 'Worked Solo 1'), row=3),
         dict(_entry('100', 'Pat Example', 1.0, 'Role points', 'Timing chief'), row=4)]
    keys = {x['row']: L.entry_key(x) for x in e}
    monkeypatch.setattr(S, 'entries', lambda: [dict(x) for x in e])
    assert S.mark_reconciled([2, 3], 'JE 1', keys) == ([2, 3], 0)
    assert [d['range'] for d in ws.calls[-1][1]] == ['J2', 'J3']
    # someone deleted a row above since the page was made: every entry is one row higher than the page said
    moved = [dict(x, row=x['row'] - 1) for x in e]
    monkeypatch.setattr(S, 'entries', lambda: [dict(x) for x in moved])
    assert S.mark_reconciled([3, 4], 'JE 2', keys) == ([2, 3], 0)        # Lee's and Pat's role points, where they are now
    # an entry that is gone or already marked is left alone, with or without a key
    monkeypatch.setattr(S, 'entries', lambda: [dict(e[0], reconciled='2026-11-01 JE 1'), dict(e[1])])
    assert S.mark_reconciled([2, 3, 9], 'JE 3', keys) == ([3], 2)
    assert S.mark_reconciled([2, 9], 'JE 4') == ([], 2)
    # an old page (no keys): a row is still only marked if an entry waiting to be booked is on it
    assert S.mark_reconciled([3], 'JE 5') == ([3], 0)


def test_quickbooks_page_says_what_was_left_alone(site, monkeypatch):
    import members
    c, store = site
    got = []
    monkeypatch.setattr(members.S, 'mark_reconciled', lambda rows, label, keys=None: got.append((rows, keys)) or ([2], 1))
    c.post('/members/quickbooks', data=MultiDict([('row', '2'), ('row', '3'), ('key2', 'k-two'), ('ref', 'JE 9')]))
    assert got == [([2, 3], {2: 'k-two', 3: ''})]
    said = _flashes(c)
    assert 'Marked 1 entry as entered in QuickBooks' in said and '1 ticked entry was left alone' in said
