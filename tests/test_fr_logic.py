import fr_logic as L


def req(net=100.0, code='#DRAAA', sub='Pat Smith', receipts='https://drive/x', approvals=''):
    return {'net': net, 'event_code': code, 'event_name': 'X', 'submitter_name': sub, 'receipts_url': receipts, 'approvals': approvals}


def keys(items, done=True):
    return [i['key'] for i in items if i['done'] == done]


CHAIR = L.add('', 'chair', 'Pat Smith', '2026-10-01', 'by email link')


def test_director_approval_by_email_with_approved_budget_is_ready():
    items = L.review(req(approvals=CHAIR), 'approved', 1000, 200)
    assert L.ready(items) and keys(items) == ['receipt', 'chair', 'budget', 'overrun']


def test_a_typed_name_never_approves():
    # The login is a shared password and a typed name: submitting as the director is not an approval.
    items = L.review(req(sub='Pat Smith'), 'approved', 1000, 0)
    assert keys(items, False) == ['chair']


def test_someone_else_needs_chair_and_receipt():
    items = L.review(req(sub='Lee Brown', receipts=''), 'approved', 1000, 0)
    assert keys(items, False) == ['receipt', 'chair']
    assert L.waiting_on(items) == ['receipt', 'director approval']


def test_same_last_name_is_not_the_chair():
    assert not L.same_person('Ana Smith', 'Pat Smith')
    assert L.same_person('Matt Sample', 'Matthew Sample')


def test_no_budget_or_changed_budget_holds_for_board():
    assert 'budget' in keys(L.review(req(approvals=CHAIR), None), False)
    assert 'budget' in keys(L.review(req(approvals=CHAIR), 'changed', 1000), False)


def test_over_110_percent_needs_board_and_board_approval_clears_it():
    r = req(net=300, approvals=CHAIR)
    assert 'overrun' in keys(L.review(r, 'approved', 1000, 801), False)
    assert L.ready(L.review(r, 'approved', 1000, 800))
    r['approvals'] = L.add(CHAIR, 'board', 'Board', '2026-11-12', 'minutes')
    assert L.ready(L.review(r, 'approved', 1000, 5000))


def test_once_ready_the_bill_in_quickbooks_does_not_reopen_the_overrun_check():
    # Ready with $900 of other spending; then the bill is entered in QuickBooks, so the same $300 shows up in spending
    # while the request is still open. It stays ready, and the approvals list is unchanged.
    r = req(net=300, approvals=CHAIR)
    assert L.ready(L.review(r, 'approved', 1000, 800))
    r['approvals'] = L.add(r['approvals'], 'ready', 'DRSCCA Apps', '2026-11-06')
    items = L.review(r, 'approved', 1000, 1100)
    assert L.ready(items) and 'when every approval was in, 2026-11-06' in [i for i in items if i['key'] == 'overrun'][0]['by']
    assert [a['role'] for a in L.parse(r['approvals'])] == ['chair'] and L.ready_on(r['approvals']) == '2026-11-06'
    # a budget changed after approval still holds it
    assert 'budget' in keys(L.review(r, 'changed', 1000, 1100), False)


def test_non_event_uses_the_program_budget():
    r = req(code='', approvals=CHAIR)
    items = L.review(r, None, scope='program')
    assert 'budget' in keys(items, False) and any(i['key'] == 'budget' and 'Program budget' in i['label'] for i in items)
    assert 'budget' in keys(L.review(r, 'budget', 1000, scope='program'), False)
    assert L.ready(L.review(r, 'approved', 1000, 0, scope='program'))
    assert 'overrun' in keys(L.review(r, 'approved', 50, 0, scope='program'), False)


def test_operations_plan_is_its_budget():
    r = req(code='', approvals=CHAIR)
    assert 'budget' in keys(L.review(r, None, scope='ops'), False)
    assert L.ready(L.review(r, 'approved', None, 99999, scope='ops'))


def test_over_limit_needs_second_signer():
    r = req(net=2600, approvals=CHAIR)
    items = L.review(r, 'approved', 5000, 0)
    assert keys(items, False) == ['second']
    r['approvals'] = L.add(r['approvals'], 'second', 'Alex Example', '2026-10-29')
    assert L.ready(L.review(r, 'approved', 5000, 0))


def test_points_redemption_only_needs_second_signer_when_large():
    r = {'net': 50, 'event_code': '', 'event_name': 'Worker points redemption', 'receipts_url': '', 'approvals': ''}
    assert L.review(r) == []


def test_add_and_parse_strip_separators():
    t = L.add(L.add('', 'chair', 'Pat; Smith', '2026-10-01'), 'board', 'Board', '2026-11-12', 'a|b')
    assert [a['role'] for a in L.parse(t)] == ['chair', 'board']
    assert L.parse(t)[0]['name'] == 'Pat, Smith' and L.parse(t)[1]['note'] == 'a,b'


def _closed(**kw):
    return dict({'id': 'FR-2026-001', 'status': 'Paid', 'notify': 'yes', 'submitter_email': 'filer@example.org',
                 'payee_name': 'Example Hall', 'net': 1200.0, 'event_name': 'Solo #1', 'program': 'AX',
                 'method': 'Check to the payee', 'paid_ref': 'Check 6100', 'paid_amount': 1200.0, 'paid_date': '2026-11-04',
                 'closed_note': ''}, **kw)


def test_submitter_told_when_paid_only_if_they_asked():
    subject, body = L.closed_message(_closed(), 'https://example.org/funds/FR-2026-001')
    assert subject == 'Funds request FR-2026-001 paid'
    assert 'Check 6100 for $1,200.00 on 2026-11-04' in body and 'goes to Example Hall' in body
    assert body.endswith('https://example.org/funds/FR-2026-001')
    assert L.closed_message(_closed(notify='')) is None
    assert L.closed_message(_closed(submitter_email='')) is None
    assert L.closed_message(_closed(status='Submitted')) is None


def test_reimbursement_and_rejection_wording():
    _, body = L.closed_message(_closed(method='Reimburse me'))
    assert 'on its way to you' in body
    subject, body = L.closed_message(_closed(status='Rejected', closed_note='No receipt'))
    assert subject.endswith('rejected') and 'Why: No receipt' in body


def test_funds_routes_registered():
    import app as A
    eps = {r.endpoint for r in A.app.url_map.iter_rules()}
    assert {'fr.close', 'fr.detail'} <= eps and 'fr.tell_submitter' not in eps


def test_only_a_passed_board_motion_stands_in_for_a_missing_budget():
    r = req(approvals='chair|Pat Smith|2026-11-03|filed it themselves')
    r['id'] = 'FR-2026-200'
    assert not L.ready(L.review(r, None))
    vote = {'id': 'M-2026-005', 'decided': '2026-11-10', 'how': 'email vote', 'vote': '7-0'}
    items = L.review(r, None, board_vote=vote)
    assert L.ready(items)
    assert next(i for i in items if i['key'] == 'budget')['by'] == 'Board, motion M-2026-005, 2026-11-10 (email vote, 7-0)'


def test_motion_for_a_request():
    ms = [{'id': 'M-1', 'link': '/funds/FR-2026-200', 'status': 'withdrawn'},
          {'id': 'M-2', 'link': '/funds/FR-2026-200', 'status': 'open'},
          {'id': 'M-3', 'link': '/funds/FR-2026-201', 'status': 'passed'}]
    assert L.motion_for('FR-2026-200', ms)['id'] == 'M-2'
    assert L.motion_for('FR-2026-201', ms)['id'] == 'M-3'
    assert L.motion_for('FR-2026-20', ms) is None
    assert L.motion_for('FR-2026-200', ms[:1])['status'] == 'withdrawn'


def test_board_link_no_longer_approves(monkeypatch):
    import app as A
    import fr
    r = {'id': 'FR-2026-200', 'status': 'Submitted', 'approvals': '', 'payee_name': 'X', 'net': 10.0, 'lines': []}
    monkeypatch.setattr(fr.F, 'refresh', lambda: None)
    monkeypatch.setattr(fr.F, 'get', lambda rid: r)
    monkeypatch.setattr(fr, '_guard', lambda: None)
    called = []
    monkeypatch.setattr(fr, '_approved', lambda *a, **k: called.append(a))
    with A.app.test_request_context('/'):
        token = fr._serializer().dumps({'id': 'FR-2026-200', 'role': 'board', 'name': 'Someone', 'email': 's@example.org'})
    c = A.app.test_client()
    page = c.post(f'/funds/approve/{token}', data={'meeting': '2026-11-03'}).get_data(as_text=True)
    assert not called and 'no longer confirmed from an email' in page
