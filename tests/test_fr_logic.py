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
    assert L.same_person('Matt Wolfe', 'Matthew Wolfe')


def test_no_budget_or_changed_budget_holds_for_board():
    assert 'budget' in keys(L.review(req(approvals=CHAIR), None), False)
    assert 'budget' in keys(L.review(req(approvals=CHAIR), 'changed', 1000), False)


def test_over_110_percent_needs_board_and_board_approval_clears_it():
    r = req(net=300, approvals=CHAIR)
    assert 'overrun' in keys(L.review(r, 'approved', 1000, 801), False)
    assert L.ready(L.review(r, 'approved', 1000, 800))
    r['approvals'] = L.add(CHAIR, 'board', 'Board', '2026-11-12', 'minutes')
    assert L.ready(L.review(r, 'approved', 1000, 5000))


def test_non_event_uses_the_annual_plan():
    r = req(code='', approvals=CHAIR)
    assert 'budget' in keys(L.review(r), False)
    assert L.ready(L.review(r, plan_ok=True))


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
