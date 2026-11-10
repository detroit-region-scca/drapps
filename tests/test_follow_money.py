"""Nobody approves their own money, a request or invoice belongs to the address that filed it, an open request whose
bill is already in QuickBooks is not counted twice, and a sign-in code survives a Sheets outage. Made-up names only."""
import pytest

import fr
import fr_logic as FL
import fr_store as F
import inv_store as I
import plan_store as P
from test_money_stores import FakeWS, _book

RAE = {'name': 'Rae Director', 'mail': 'rx-group@example.org', 'emails': ['rx-group@example.org', 'rae@example.org']}
REMY = {'name': 'Remy Exec', 'mail': 're@example.org', 'emails': ['re@example.org']}
ALEX = {'name': 'Alex Assistant', 'mail': 'are@example.org', 'emails': ['are@example.org']}


def req(**kw):
    return dict({'id': 'FR-2026-001', 'status': 'Submitted', 'submitted': '2026-11-01', 'program': 'RX', 'event_code': '',
                 'event_name': '', 'net': 3000.0, 'total': 3000.0, 'approvals': '', 'payee_name': 'Example Rentals',
                 'payee_email': '', 'submitter_name': 'Sam Sample', 'submitter_email': 'sam@example.org',
                 'entered_by': 'Sam Sample', 'filed_email': 'sam@example.org', 'lines': []}, **kw)


# ---------- A: who may approve ----------

def test_on_request_goes_by_address_or_by_name():
    r = req(payee_name='Matthew Sample', payee_email='matt@example.org')
    assert FL.on_request(r, 'Someone Else', ['SAM@example.org'])            # filed it
    assert FL.on_request(r, 'Someone Else', ['matt@example.org'])           # is paid by it
    assert FL.on_request(r, 'matthew  sample', [])                          # the payee, by exact name
    assert not FL.on_request(r, 'Matt Sample', [])                          # a like name is another person (a relative)
    assert FL.on_request(req(entered_by='Rae Director'), 'Rae Director')    # entered it
    assert not FL.on_request(r, 'Remy Exec', ['re@example.org', ''])
    assert not FL.on_request(req(payee_email='', filed_email=''), 'Remy Exec', [''])    # empty never matches empty


def test_the_usual_approvers_when_nobody_is_on_the_request():
    plan = FL.who_approves(req(), RAE, [REMY, ALEX])
    assert plan['chair'] == [RAE] and plan['second'] == [REMY, ALEX] and not plan['stand_in'] and not plan['note']


def test_a_director_on_the_request_is_replaced_by_the_re_or_are():
    for r in (req(filed_email='rae@example.org'), req(payee_name='Rae Director'), req(submitter_email='rx-group@example.org')):
        plan = FL.who_approves(r, RAE, [REMY, ALEX])
        assert plan['stand_in'] and plan['chair'] == [REMY, ALEX] and 'RE or ARE approves instead' in plan['note']['chair']


def test_the_re_as_payee_leaves_the_are_and_the_reverse():
    plan = FL.who_approves(req(payee_name='Remy Exec'), RAE, [REMY, ALEX])
    assert plan['second'] == [ALEX] and plan['chair'] == [RAE] and 'Alex Assistant only' in plan['note']['second']
    plan = FL.who_approves(req(filed_email='are@example.org'), RAE, [REMY, ALEX])
    assert plan['second'] == [REMY]


def test_both_signers_on_the_request_is_said_plainly():
    r = req(payee_name='Remy Exec', filed_email='are@example.org')
    plan = FL.who_approves(r, RAE, [REMY, ALEX])
    assert plan['second'] == [] and 'neither can approve it' in plan['note']['second']
    items = FL.explain([{'key': 'chair', 'label': 'Approved by the program director', 'done': False, 'by': ''},
                        {'key': 'second', 'label': 'Second signer', 'done': False, 'by': ''}], plan)
    assert items[0]['label'] == 'Approved by the program director' and 'neither can approve it' in items[1]['label']
    # the director too: nobody is left for the director approval either
    plan = FL.who_approves(dict(r, submitter_name='Rae Director'), RAE, [REMY, ALEX])
    assert plan['chair'] == [] and 'neither can approve it' in plan['note']['chair']


def test_an_approval_already_given_keeps_its_label():
    plan = FL.who_approves(req(payee_name='Remy Exec'), RAE, [REMY, ALEX])
    items = FL.explain([{'key': 'second', 'label': 'Second signer', 'done': True, 'by': 'Alex Assistant'}], plan)
    assert items[0]['label'] == 'Second signer'


@pytest.fixture
def officials(monkeypatch):
    people = [{'role': 'Director', 'name': 'Rae Director', 'email': 'rae@example.org', 'program': 'RX'},
              {'role': 'RE', 'name': 'Remy Exec', 'email': 're@example.org'},
              {'role': 'ARE', 'name': 'Alex Assistant', 'email': 'are@example.org'}]
    monkeypatch.setattr(fr.O, 'all', lambda: [dict(o) for o in people])
    monkeypatch.setattr(fr.O, 'mailto', lambda o: o['email'])
    return people


@pytest.fixture
def asking(monkeypatch, officials):
    """ask_approvals with the director and second signer approvals both missing; mail is collected."""
    from app import app
    state = {'r': req(), 'mail': []}
    monkeypatch.setattr(fr.F, 'get', lambda rid: dict(state['r']))
    missing = [{'key': 'chair', 'done': False}, {'key': 'second', 'done': False}]
    monkeypatch.setattr(fr, 'Approvals', lambda: type('A', (), {'items': lambda self, r: missing})())
    monkeypatch.setattr(fr, 'send_mail', lambda subject, body, to=None, **k: state['mail'].append((to, body)))

    def ask():
        with app.test_request_context('/', base_url='https://apps.example.org'):
            return fr.ask_approvals('FR-2026-001')
    state['ask'] = ask
    return state


def test_links_go_to_the_director_and_both_signers_when_none_is_on_the_request(asking):
    assert asking['ask']() == ['rae@example.org', 're@example.org', 'are@example.org']


def test_the_re_who_is_the_payee_gets_no_second_signer_link(asking):
    asking['r'] = req(payee_name='Remy Exec')
    assert asking['ask']() == ['rae@example.org', 'are@example.org']


def test_a_director_who_filed_it_is_not_asked_and_the_re_and_are_are(asking):
    asking['r'] = req(filed_email='rae@example.org', submitter_name='Rae Director', net=300.0)
    sent = asking['ask']()
    assert 'rae@example.org' not in sent and sent[:2] == ['re@example.org', 'are@example.org']
    assert 'asked of the RE or ARE instead' in asking['mail'][0][1]


def test_the_page_names_who_approves_and_drops_the_no_director_line_when_the_label_says_why(officials):
    assert fr.approvers(req())['chair'] == ['Rae Director']
    assert fr.approvers(req(filed_email='rae@example.org'))['chair'] == ['Remy Exec', 'Alex Assistant']
    assert fr.approvers(req(payee_name='Remy Exec'))['second'] == ['Alex Assistant']
    assert 'second' not in fr.approvers(req(payee_name='Remy Exec', filed_email='are@example.org'))


@pytest.fixture
def link(monkeypatch, officials):
    """The emailed approval link, with the store replaced by a dict."""
    from app import app
    state = {'r': req(), 'approved': []}
    monkeypatch.setattr(fr, '_guard', lambda: None)
    monkeypatch.setattr(fr.F, 'refresh', lambda: None)
    monkeypatch.setattr(fr.F, 'get', lambda rid: dict(state['r']))
    monkeypatch.setattr(fr, '_approved', lambda rid, role, who, note='': state['approved'].append((role, who, note)))
    app.config['TESTING'] = True

    def post(role, name, email):
        with app.test_request_context('/'):
            token = fr._serializer().dumps({'id': 'FR-2026-001', 'role': role, 'name': name, 'email': email})
        return app.test_client().post(f'/funds/approve/{token}', data={'action': 'approve'}).get_data(as_text=True)
    state['post'] = post
    return state


def test_a_link_held_by_the_payee_or_submitter_approves_nothing(link):
    link['r'] = req(payee_name='Remy Exec')
    assert 'you cannot approve it' in link['post']('second', 'Remy Exec', 're@example.org') and not link['approved']
    link['r'] = req(filed_email='rae@example.org')            # an old link, sent before the director was known to be on it
    assert 'you cannot approve it' in link['post']('chair', 'Rae Director', 'rx-group@example.org') and not link['approved']


def test_a_link_held_by_someone_else_approves(link):
    link['r'] = req(payee_name='Remy Exec')
    assert 'Approved.' in link['post']('second', 'Alex Assistant', 'are@example.org')
    link['r'] = req(filed_email='rae@example.org')
    link['post']('chair', 'Remy Exec', 're@example.org')
    assert [a[:2] for a in link['approved']] == [('second', 'Alex Assistant'), ('chair', 'Remy Exec')]
    assert 'in place of the director' in link['approved'][1][2] and 'in place of' not in link['approved'][0][2]


def test_a_director_filing_for_their_own_program_approves_nothing_by_filing(monkeypatch, officials):
    import auth
    from app import app
    from werkzeug.datastructures import MultiDict
    made = []
    monkeypatch.setattr(fr, '_guard', lambda: None)
    monkeypatch.setattr(fr, 'load_events', lambda: [])
    monkeypatch.setattr(fr, 'load_programs', lambda: {'RX': 'RallyCross'})
    monkeypatch.setattr(fr.F, 'create_once', lambda r, lines, receipts, token='': (made.append(r), ('FR-2026-007', False))[1])
    monkeypatch.setattr(fr, 'notify', lambda *a: None)
    monkeypatch.setattr(fr, 'mark_ready', lambda rid: False)
    monkeypatch.setattr(fr, 'log_audit_event', lambda *a, **k: None)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Rae Director', 'level': 'official', 'roles': ['Director'],
                                                   'perms': {'funds'}} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='rae@example.org', who='Rae Director')
    form = MultiDict({'method': 'check', 'program': 'RX', 'submitter_name': 'Rae Director', 'submitter_email': 'someone@example.org',
                      'payee_name': 'Example Rentals', 'payee_address': '1 Main St', 'line_date': '2026-11-01', 'line_desc': 'Cones',
                      'line_cat': 'Supplies', 'line_amount': '80', 'advances': '0', 'token': 'page-1'})
    r = c.post('/funds/new', data=form)
    assert r.status_code == 302 and r.location.endswith('/funds/FR-2026-007'), (r.location, _in(c))
    assert not made[0].get('approvals') and made[0]['filed_email'] == 'rae@example.org'     # the sign-in, not what was typed


# ---------- B: whose request, whose invoice ----------

def test_a_requests_tab_without_the_new_column_reads_and_is_widened_on_the_first_write(monkeypatch, tmp_path):
    old = F.REQ_COLS[:-1]
    row = [{'id': 'FR-2026-001', 'submitted': '2026-11-01', 'status': 'Submitted', 'net': 50}.get(c, '') for c in old]
    tabs = _book(monkeypatch, F, {'Requests': FakeWS([old, row], cols=len(old)), 'Lines': FakeWS([F.LINE_COLS])}, tmp_path, 'ID_LOCK')
    monkeypatch.setattr(F, 'ISSUED_FILE', str(tmp_path / 'funds-issued.json'))
    assert F.get('FR-2026-001')['filed_email'] == ''
    assert F.update('FR-2026-001', closed_note='looked at')            # the fake refuses a write outside the grid
    ws = tabs['Requests']
    assert ws.col_count == len(F.REQ_COLS) and ws.rows[0][-1] == 'filed_email'
    monkeypatch.setattr(F, 'drive_folder', lambda *a: None)
    rid, _ = F.create_once({'payee_name': 'Example Rentals', 'filed_email': 'sam@example.org'}, [], [])
    assert F.get(rid)['filed_email'] == 'sam@example.org'


def _as(monkeypatch, email, name):
    import auth
    monkeypatch.setattr(auth, 'user', lambda: {'email': email, 'name': name, 'perms': []})
    return auth


def test_a_request_belongs_to_the_address_that_filed_it(monkeypatch):
    auth = _as(monkeypatch, 'sam@example.org', 'Sam Sample')
    assert auth.owns_request({'filed_email': 'Sam@example.org', 'submitter_email': 'x@example.org', 'entered_by': 'Other'})
    # a row that names its filer: a same-named person is not enough, but the person it was filed for still owns it
    assert not auth.owns_request({'filed_email': 'lee@example.org', 'submitter_email': 'x@example.org', 'entered_by': 'Sam Sample'})
    assert auth.owns_request({'filed_email': 'lee@example.org', 'submitter_email': 'sam@example.org', 'entered_by': 'Lee Other'})
    # rows from before the column: the old match
    assert auth.owns_request({'submitter_email': 'sam@example.org', 'entered_by': 'Other'})
    assert auth.owns_request({'filed_email': '', 'entered_by': 'sam sample'})
    assert not auth.owns_request({'submitter_email': 'lee@example.org', 'entered_by': 'Lee Other'}) and not auth.owns_request(None)


def test_an_invoice_belongs_to_the_address_that_created_it(monkeypatch):
    auth = _as(monkeypatch, 'sam@example.org', 'Sam Sample')
    assert auth.owns_invoice({'filed_email': 'sam@example.org', 'created_by': 'Other'})
    assert not auth.owns_invoice({'filed_email': 'lee@example.org', 'created_by': 'Sam Sample'})
    assert auth.owns_invoice({'created_by': 'Sam Sample'}) and not auth.owns_invoice({'created_by': 'Lee Other'})


def test_a_money_received_report_goes_by_its_address_first():
    from app import app
    with app.test_request_context('/'):
        from flask import session
        session.update(email='sam@example.org', who='Sam Sample')
        assert fr._owns_report({'reported_email': 'sam@example.org', 'reported_by': 'Other'})
        assert not fr._owns_report({'reported_email': 'lee@example.org', 'reported_by': 'Sam Sample'})
        assert fr._owns_report({'reported_email': '', 'reported_by': 'Sam Sample'}) and not fr._owns_report(None)
        session.update(email='', who='')
        assert not fr._owns_report({'reported_email': '', 'reported_by': ''})


def test_an_invoices_tab_without_the_new_column_reads(monkeypatch):
    old = I.INV_COLS[:-1]
    row = [{'id': 'INV-2026-001', 'created': '2026-11-01', 'status': 'Open', 'total': 100, 'created_by': 'Sam Sample'}.get(c, '') for c in old]
    monkeypatch.setenv('INVOICES_SHEET_ID', 'sheet')
    monkeypatch.setattr(I, '_cache', {})
    monkeypatch.setattr(I.C, 'version', lambda sid: 1)
    monkeypatch.setattr(I.C, 'batch_get', lambda sid, ranges, params=None: {'valueRanges': [{'values': [I.INV_LABELS[:-1], row]}, {}]})
    inv = I.get('INV-2026-001')
    assert inv['filed_email'] == '' and inv['created_by'] == 'Sam Sample' and inv['total'] == 100.0


# ---------- C: a request whose bill is already in QuickBooks ----------

def test_same_payee_is_a_loose_name_match():
    assert FL.same_payee('Example Rentals', 'Example Rentals LLC') and FL.same_payee('Sample, Sam', 'Sam Sample')
    assert FL.same_payee('Matt Sample', 'Matthew Sample')
    assert not FL.same_payee('Example Rentals', 'Other Rentals') and not FL.same_payee('', 'Example Rentals') and not FL.same_payee('LLC', 'Inc')


def _open(rid, net, payee):
    return {'id': rid, 'net': net, 'payee_name': payee, 'status': 'Submitted'}


def test_a_request_is_left_out_only_for_a_bill_of_its_amount_and_payee():
    others = [_open('FR-1', 300.0, 'Example Rentals'), _open('FR-2', 300.0, 'Sam Sample'), _open('FR-3', 120.5, 'Lee Other')]
    bills = [{'id': 'Bill 7', 'vendor': 'Example Rentals LLC', 'total': 300.0}, {'id': 'Bill 8', 'vendor': 'Lee Other', 'total': 120.51}]
    assert FL.billed(others, bills) == {'FR-1'}                    # FR-2: another vendor; FR-3: a cent off
    assert FL.billed(others, []) == set() and FL.billed(others, None) == set()


def test_each_bill_is_used_once_and_must_name_the_vendor_and_the_same_event():
    twins = [_open('FR-1', 300.0, 'Example Rentals'), _open('FR-2', 300.0, 'Example Rentals')]
    assert FL.billed(twins, [{'vendor': 'Example Rentals', 'total': 300.0}]) == {'FR-1'}
    assert FL.billed(twins, [{'vendor': 'Example Rentals', 'total': 300.0}, {'vendor': '', 'total': 300.0}]) == {'FR-1'}
    on_a = {'vendor': 'Example Rentals', 'total': 300.0, 'lines': 'Site Rental / Autocross / Test Day #DRAAA 300.00'}
    assert FL.billed(twins, [on_a], '#DRAAA') == {'FR-1'} and FL.billed(twins, [on_a], '#DRBBB') == set()
    assert FL.billed(twins, [on_a]) == set()      # a program's own spending is not excused by an event's bill


def test_the_overrun_check_does_not_count_a_billed_request_twice(monkeypatch, officials):
    r, other = req(event_code='#DRAAA', net=100.0), req(id='FR-2026-002', event_code='#DRAAA', net=400.0)
    budget = {'totals': {'budget': {'expense': 1000.0}}}
    monkeypatch.setattr(fr.F, 'requests', lambda: [r, other])
    monkeypatch.setattr(fr.B, 'enabled', lambda: True)
    monkeypatch.setattr(fr.B, 'budgets', lambda: {'#DRAAA': budget})
    monkeypatch.setattr(fr.B, 'status', lambda b: 'approved')
    monkeypatch.setattr(fr.B, 'qbo_actuals', lambda: {'#DRAAA': {'totals': {'expense': 700.0}}})     # the $400 bill is in here
    monkeypatch.setattr(fr.P, 'enabled', lambda: True)
    monkeypatch.setattr(fr.Approvals, 'board_vote', lambda self, r: None)
    over = lambda: next(i for i in fr.Approvals().items(r) if i['key'] == 'overrun')      # noqa: E731
    monkeypatch.setattr(fr.P, 'bills', lambda: [])
    assert not over()['done'] and '$1,200.00 of $1,100.00' in over()['label']              # 700 + 400 + 100: counted twice
    monkeypatch.setattr(fr.P, 'bills', lambda: [{'id': 'Bill 7', 'vendor': 'Example Rentals', 'total': 400.0, 'balance': 400.0,
                                                  'lines': 'Site Rental / Test Day #DRAAA 400.00'}])
    assert over()['done'] and '$800.00 of $1,100.00' in over()['label']


def test_bills_read_as_none_before_the_first_sync_writes_the_tab(monkeypatch):
    import gspread
    monkeypatch.setenv('PLAN_SHEET_ID', 'sheet')
    monkeypatch.setattr(P, '_cache', {})
    monkeypatch.setattr(P.C, 'version', lambda sid: 1)

    def no_tab(sid, ranges, params=None):
        raise gspread.exceptions.WorksheetNotFound('QBO Bills')
    monkeypatch.setattr(P.C, 'batch_get', no_tab)
    assert P.bills() == []


def test_bills_go_to_the_tab_and_come_back(monkeypatch):
    bill = {'id': 'Bill 7', 'date': '2026-11-02', 'vendor': 'Example Rentals', 'total': 400, 'balance': 400, 'number': 'A-1', 'memo': 'FR-2026-002',
            'lines': [{'description': 'Site', 'amount': 400, 'account': 'Site Rental', 'class': 'RallyCross', 'customer': '#DRAAA Fall'}]}
    row = P.bill_row(bill, '2026-11-09')
    assert len(row) == len(P.BILL_COLS) and row[P.BILL_COLS.index('lines')] == 'Site Rental / RallyCross / #DRAAA Fall 400.00'
    assert P.bill_row({'id': 'Bill 8'}, '2026-11-09')[3:5] == [0, 0]            # an export with fields missing still writes
    monkeypatch.setenv('PLAN_SHEET_ID', 'sheet')
    monkeypatch.setattr(P, '_cache', {})
    monkeypatch.setattr(P.C, 'version', lambda sid: 1)
    monkeypatch.setattr(P.C, 'batch_get', lambda sid, ranges, params=None: {'valueRanges': [{'values': [P.BILL_COLS, row]}]})
    assert P.bills()[0]['vendor'] == 'Example Rentals' and P.bills()[0]['total'] == 400.0


# ---------- D: a sign-in code and a Sheets outage ----------

@pytest.fixture
def signin(monkeypatch, tmp_path):
    import auth
    import officials as O
    monkeypatch.setattr(O, 'all', lambda: [{'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'}])
    state = {'down': False, 'mail': []}

    def volunteers():
        auth._vols['failed'] = state['down']
        return {} if state['down'] else {'vol@example.org': {'email': 'vol@example.org', 'name': 'Val Volunteer'}}
    monkeypatch.setattr(auth, 'volunteers', volunteers)
    monkeypatch.setattr(auth, '_vols', {'good': None, 'failed': False})
    monkeypatch.setattr(auth, 'CODES_FILE', str(tmp_path / 'codes.json'))
    monkeypatch.setattr(auth, 'send_mail', lambda subject, body, to=None, **kw: state['mail'].append(subject))
    monkeypatch.setattr(auth, 'log_audit_event', lambda *a, **kw: None)
    monkeypatch.setattr(auth, '_later', lambda fn: fn())
    from app import app
    app.config['TESTING'] = True
    state['c'] = app.test_client()
    return state


def _in(c):
    with c.session_transaction() as s:
        return 'email' in s, ' '.join(m for _, m in s.pop('_flashes', []))


def test_a_right_code_is_not_used_up_while_sheets_is_down(signin):
    c = signin['c']
    c.post('/signin', data={'email': 'vol@example.org'})
    code = signin['mail'][-1].split()[0]
    signin['down'] = True
    c.post('/signin/code', data={'email': 'vol@example.org', 'code': code})
    signed, said = _in(c)
    assert not signed and 'try again in a moment' in said and 'not right' not in said
    signin['down'] = False
    c.post('/signin/code', data={'email': 'vol@example.org', 'code': code})        # the same code, once Sheets is back
    assert _in(c)[0]


def test_a_lookup_that_raises_keeps_the_code_too(signin, monkeypatch):
    import auth
    c = signin['c']
    c.post('/signin', data={'email': 'vol@example.org'})
    code = signin['mail'][-1].split()[0]
    real = auth.lookup
    monkeypatch.setattr(auth, 'lookup', lambda e: (_ for _ in ()).throw(RuntimeError('Sheets is down')))
    c.post('/signin/code', data={'email': 'vol@example.org', 'code': code})
    assert 'try again in a moment' in _in(c)[1]
    monkeypatch.setattr(auth, 'lookup', real)
    c.post('/signin/code', data={'email': 'vol@example.org', 'code': code})
    assert _in(c)[0]


def test_an_official_signs_in_during_the_outage_and_a_wrong_code_still_counts(signin):
    import auth
    c = signin['c']
    c.post('/signin', data={'email': 'treasurer@example.org'})
    code = signin['mail'][-1].split()[0]
    signin['down'] = True
    c.post('/signin/code', data={'email': 'treasurer@example.org', 'code': '000000' if code != '000000' else '111111'})
    assert not _in(c)[0] and auth._codes(lambda codes, now: codes['treasurer@example.org']['tries']) == 1
    c.post('/signin/code', data={'email': 'treasurer@example.org', 'code': code})
    assert _in(c)[0]
