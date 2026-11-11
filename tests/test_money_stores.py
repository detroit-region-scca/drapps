"""The money sheets' writes, against a pretend worksheet: status checked under the lock, approvals added not replaced,
a row deleted by hand, amounts read as stored, the one-time New request page, and the approval fingerprint that decides
"changed since approved". Made-up names only."""
import re

import pytest

import bud_store as B
import fr_logic as FL
import fr_store as F
import plan_store as P
import received_store as R


def _at(rng):
    m = re.match(r'([A-Z]+)(\d+)', rng)
    col = 0
    for ch in m.group(1):       # A=0 ... Z=25, AA=26
        col = col * 26 + ord(ch) - 64
    return col - 1, int(m.group(2)) - 1


class FakeWS:
    """Rows of stored values; `shown` maps a stored number to the text Sheets would display (Currency format)."""

    def __init__(self, rows, cols=26, shown=None):
        self.rows, self.col_count, self.shown = [list(r) for r in rows], cols, shown or {}

    @property
    def row_count(self):
        return len(self.rows) + 50

    def col_values(self, n):
        return [str(r[n - 1]) if len(r) >= n else '' for r in self.rows]

    def row_values(self, n, value_render_option=None):
        row = list(self.rows[n - 1])
        return row if value_render_option == 'UNFORMATTED_VALUE' else [self.shown.get(v, str(v)) if not isinstance(v, str) else v for v in row]

    def _put(self, values, rng):
        c, r = _at(rng)
        for i, vals in enumerate(values):
            assert c + len(vals) <= self.col_count, 'write outside the grid'
            while len(self.rows) <= r + i:
                self.rows.append([])
            row = self.rows[r + i]
            row += [''] * (c + len(vals) - len(row))
            row[c:c + len(vals)] = vals

    def update(self, values, rng, **k):
        self._put(values, rng)

    def batch_update(self, data, **k):
        for d in data:
            self._put(d['values'], d['range'])

    def append_row(self, row, **k):
        self.rows.append(list(row))

    def append_rows(self, rows, **k):
        self.rows += [list(r) for r in rows]

    def add_cols(self, n):
        self.col_count += n

    def add_rows(self, n):
        pass

    def batch_clear(self, ranges):
        for rng in ranges:
            del self.rows[_at(rng)[1]:]

    def delete_rows(self, n):
        del self.rows[n - 1]


def _book(monkeypatch, mod, tabs, tmp_path, lock):
    """Point a store at pretend tabs, for its direct writes and for its cached reads."""
    monkeypatch.setattr(mod, '_ws', lambda t: tabs[t])
    monkeypatch.setattr(mod, lock, str(tmp_path / f'{mod.__name__}.lock'))
    monkeypatch.setattr(mod, '_cache', {})
    monkeypatch.setenv({B: 'BUDGETS_SHEET_ID', P: 'PLAN_SHEET_ID'}.get(mod, 'FUNDS_SHEET_ID'), 'sheet')
    monkeypatch.setattr(mod.C, 'invalidate', lambda sid: None)
    monkeypatch.setattr(mod.C, 'version', lambda sid: 1)

    def batch_get(sid, ranges, params=None):
        names = [r.split('!')[0].strip("'") for r in ranges]
        return {'valueRanges': [{'values': [list(r) for r in tabs[n].rows]} if n in tabs else {} for n in names]}
    monkeypatch.setattr(mod.C, 'batch_get', batch_get)
    return tabs


def _req_row(**kw):
    row = dict({'id': 'FR-2026-001', 'submitted': '2026-11-01', 'status': 'Submitted', 'payee_name': 'Sam Sample', 'total': 1234.5,
                'advances': 0, 'net': 1234.5}, **kw)
    return [row.get(c, '') for c in F.REQ_COLS]


@pytest.fixture
def funds(monkeypatch, tmp_path):
    tabs = {'Requests': FakeWS([F.REQ_COLS, _req_row()], cols=len(F.REQ_COLS), shown={1234.5: '$1,234.50'}),
            'Lines': FakeWS([F.LINE_COLS])}
    monkeypatch.setattr(F, 'ISSUED_FILE', str(tmp_path / 'funds-issued.json'))
    return _book(monkeypatch, F, tabs, tmp_path, 'ID_LOCK')


def test_a_request_is_closed_once(funds):
    paid = dict(paid_ref='Check 6080', paid_amount=1234.5, closed_by='Pat Treasurer')
    assert F.close('FR-2026-001', 'Paid', expect='Submitted', **paid)['status'] == 'Paid'
    assert F.close('FR-2026-001', 'Paid', expect='Submitted', **paid) is None            # the second click
    assert F.close('FR-2026-001', 'Rejected', expect='Submitted', closed_note='no') is None
    assert F.get('FR-2026-001')['status'] == 'Paid'
    assert F.close('FR-2026-001', 'Submitted', expect='Paid', paid_ref='', paid_amount='')['status'] == 'Submitted'


def test_a_currency_formatted_amount_survives_an_update(funds):
    F.update('FR-2026-001', notes='thanks')
    r = F.get('FR-2026-001')
    assert r['total'] == 1234.5 and r['net'] == 1234.5 and r['notes'] == 'thanks'


def test_two_approvals_at_once_both_stay(funds):
    stale = F.get('FR-2026-001')['approvals']            # what both clicks read before either wrote
    assert stale == ''
    F.add_approval('FR-2026-001', 'chair', 'Dana Director', '2026-11-09')
    F.add_approval('FR-2026-001', 'second', 'Robin Exec', '2026-11-09')
    assert [a['role'] for a in FL.parse(F.get('FR-2026-001')['approvals'])] == ['chair', 'second']
    assert F.add_approval('FR-2026-001', 'ready', 'DRSCCA Apps', '2026-11-09', once=True)
    assert F.add_approval('FR-2026-001', 'ready', 'DRSCCA Apps', '2026-11-10', once=True) is None
    assert FL.ready_on(F.get('FR-2026-001')['approvals']) == '2026-11-09'


def test_a_row_deleted_by_hand_is_not_an_error(funds, monkeypatch, tmp_path):
    assert F.update('FR-2026-999', notes='x') is None and F.add_approval('FR-2026-999', 'chair', 'Dana', '2026-11-09') is None
    rows = [R.COLS, ['CR-2026-001', '2026-10-17', '2026-10-17', 'Received']]
    _book(monkeypatch, R, {R.TAB: FakeWS(rows, cols=len(R.COLS)), R.LINES_TAB: FakeWS([R.LINE_COLS])}, tmp_path, 'ID_LOCK')
    assert R.update('CR-2026-999', status='Deposited') is None
    assert R.update('CR-2026-001', expect='Received', status='Deposited', deposit_amount=200.0)['status'] == 'Deposited'
    assert R.update('CR-2026-001', expect='Received', status='Deposited', deposit_amount=200.0) is None


LINES = [{'date': '2026-11-01', 'description': 'Cones', 'category': 'Supplies', 'amount': 80.0}]


def test_a_used_page_or_a_retry_files_one_request(funds):
    rid, again = F.create_once({'payee_name': 'Sam Sample'}, LINES, [], 'page-1')
    assert not again and len(funds['Requests'].rows) == 3 and len(funds['Lines'].rows) == 2
    assert F.create_once({'payee_name': 'Sam Sample'}, LINES, [], 'page-1') == (rid, True)
    assert len(funds['Requests'].rows) == 3 and len(funds['Lines'].rows) == 2
    other, again = F.create_once({'payee_name': 'Sam Sample'}, LINES, [])      # a page with no token: always new
    assert not again
    assert other != rid and len(funds['Requests'].rows) == 4


def test_a_retry_after_the_lines_failed_finishes_the_first_request(funds, monkeypatch):
    real, fail = funds['Lines'].append_rows, [True]

    def flaky(rows, **k):
        if fail.pop() if fail else False:
            raise RuntimeError('Sheets did not answer')
        real(rows, **k)
    monkeypatch.setattr(funds['Lines'], 'append_rows', flaky)
    with pytest.raises(RuntimeError):
        F.create_once({'payee_name': 'Sam Sample'}, LINES, [], 'page-2')
    assert len(funds['Requests'].rows) == 3 and len(funds['Lines'].rows) == 1      # the request is there, its lines are not
    rid, again = F.create_once({'payee_name': 'Sam Sample'}, LINES, [], 'page-2')    # the person presses Submit again
    assert not again and len(funds['Requests'].rows) == 3      # no new request, and it counts as new so the emails go out
    assert [ln['description'] for ln in F.get(rid)['lines']] == ['Cones']


def _budget_tabs(approved='2026-11-05', updated='2026-11-01', digest=''):
    head = dict(event_code='#DRAAA', event_name='Test Solo', program='AX', date='2027-05-01', updated=updated, approved=approved,
                approved_hash=digest)
    return {'Budgets': FakeWS([B.BUD_COLS[:14], [head.get(c, '') for c in B.BUD_COLS[:14]] + ([digest] if digest else [])], cols=14),
            'Lines': FakeWS([B.LINE_COLS, ['#DRAAA', 'Income', 'Entries', '', 1000.0, '', 0], ['#DRAAA', 'Expense', 'Site', '', 400.0, '', 1]])}


def _lines(site=400.0):
    return [{'section': 'Income', 'description': 'Entries', 'last_year': None, 'budget': 1000.0, 'actual': None},
            {'section': 'Expense', 'description': 'Site', 'last_year': None, 'budget': site, 'actual': None}]


HEADER = {'event_code': '#DRAAA', 'event_name': 'Test Solo', 'program': 'AX', 'date': '2027-05-01', 'chair': '', 'location': '',
          'notes': '', 'updated_by': 'Pat', 'last_year_event': ''}


def test_status_is_judged_against_what_was_approved_not_the_day():
    b = {'approved': '2026-11-09', 'updated': '2026-11-09', 'lines': _lines()}
    assert B.status(b) == 'approved'                                   # an older row: compared by day, as before
    b['approved_hash'] = B.lines_hash(_lines())
    assert B.status(b) == 'approved'
    assert B.status(dict(b, lines=_lines(site=900.0))) == 'changed'    # raised the same afternoon
    assert B.status(dict(b, updated='2026-12-01')) == 'approved'       # saved again later with the same numbers
    results = _lines() + [{'section': 'Expense', 'description': 'Extra cones', 'budget': None, 'actual': 35.0}]
    assert B.status(dict(b, lines=results)) == 'approved'              # reported results are not a change
    assert B.status(dict(b, draft='2026-11-09 by Pat')) == 'draft' and B.status({'approved': ''}) == 'budget' and B.status(None) is None


def test_recording_approval_fingerprints_the_lines_and_widens_the_tab(monkeypatch, tmp_path):
    tabs = _book(monkeypatch, B, _budget_tabs(approved=''), tmp_path, 'SAVE_LOCK')
    assert B.status(B.get('#DRAAA')) == 'budget'          # a tab without the new column still reads
    B.set_approved('#DRAAA', '2026-11-09', 'Pat (board meeting)')
    assert tabs['Budgets'].col_count == 16 and tabs['Budgets'].rows[0][14:16] == ['approved_hash', 'no_vote']
    assert B.get('#DRAAA')['approved_hash'] == B.lines_hash(_lines()) and B.status(B.get('#DRAAA')) == 'approved'
    B.save(HEADER, _lines(site=900.0))                    # edited on the approval day
    assert B.status(B.get('#DRAAA')) == 'changed' and B.get('#DRAAA')['approved'] == '2026-11-09'
    B.set_draft('#DRAAA', '2026-11-09 by Pat')
    assert B.get('#DRAAA')['approved'] == '' and B.get('#DRAAA')['approved_hash'] == ''


def test_a_budget_approved_before_fingerprints_stays_approved_until_its_numbers_change(monkeypatch, tmp_path):
    _book(monkeypatch, B, _budget_tabs(), tmp_path, 'SAVE_LOCK')
    assert B.status(B.get('#DRAAA')) == 'approved'
    B.save(dict(HEADER, notes='see the flyer'), _lines())            # a note, on any later day: the numbers stand
    assert B.status(B.get('#DRAAA')) == 'approved' and B.get('#DRAAA')['approved_hash'] == B.lines_hash(_lines())
    B.save(HEADER, _lines(site=900.0))
    assert B.status(B.get('#DRAAA')) == 'changed'


def _plan_tabs(digest=None):
    prog = ['2027', 'OPS', 'Dana', 'ops@example.org', '', '2026-11-01', 'Dana', '2026-11-01', 'Dana', '2026-11-05', 'Pat'] + ([digest] if digest else [])
    tabs = {'Plan': FakeWS([P.PLAN_COLS, ['2027', 'OPS', 'Storage', '', -1200.0, '', '', 0, 'Storage Rent']]),
            'Programs': FakeWS([P.PROG_COLS[:11], prog], cols=11)}
    for t in ('QBO Programs', 'QBO Status', 'QBO Exceptions', 'Reviewed', 'QBO Monthly'):
        tabs[t] = FakeWS([['x']])
    return tabs


PLAN_LINES = [{'item': 'Storage', 'month': '', 'net': -1200.0, 'note': '', 'event_code': '', 'account': 'Storage Rent'}]


def test_saving_the_operations_plan_unchanged_keeps_it_approved(monkeypatch, tmp_path):
    tabs = _book(monkeypatch, P, _plan_tabs(), tmp_path, 'SAVE_LOCK')
    monkeypatch.setattr(B, 'enabled', lambda: False)
    info = lambda: P.plan('2027')['OPS']['info']  # noqa: E731
    assert P.plan_status(info()) == 'approved'
    P.save('2027', 'OPS', {'notes': '', 'updated_by': 'Sam'}, PLAN_LINES)
    assert P.plan_status(info()) == 'approved' and info()['updated'] == '2026-11-01' and info()['updated_by'] == 'Dana'
    assert tabs['Programs'].col_count == 12 and info()['approved_hash'] == P.lines_hash(PLAN_LINES)
    P.save('2027', 'OPS', {'notes': '', 'updated_by': 'Sam'}, [dict(PLAN_LINES[0], net=-1500.0)])
    assert P.plan_status(info()) == 'changed' and info()['updated_by'] == 'Sam'
    from datetime import date
    P.set_approved('2027', 'OPS', '2020-01-01', 'Pat')        # a meeting before today's edit: not what the board saw
    assert P.plan_status(info()) == 'changed'
    P.set_approved('2027', 'OPS', date.today().isoformat(), 'Pat')
    assert P.plan_status(info()) == 'approved' and info()['approved_hash'] == P.lines_hash([dict(PLAN_LINES[0], net=-1500.0)])
    P.set_draft('2027', 'OPS')
    assert info()['approved'] == '' and info()['approved_hash'] == '' and P.plan_status(info()) == 'draft'
