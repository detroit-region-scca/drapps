"""Money received: totals, the single-count flag, matching QuickBooks deposits, and the report form's checks."""
import pytest

import received_store as R

KEY = 'k' * 40


def rep(**kw):
    return dict({'id': 'CR-2026-001', 'status': 'Received', 'received': '2026-10-17', 'total': 1240.0, 'cash': 1040.0,
                 'second_counter': ''}, **kw)


def dep(i, d, amount, desc=''):
    return {'id': f'Deposit {i}', 'date': d, 'amount': amount, 'description': desc, 'payer': ''}


def test_totals_split_cash_and_checks():
    lines = [{'form': 'Cash', 'amount': 1040.0}, {'form': 'Check', 'amount': 200.0}]
    assert R.totals(lines) == (1040.0, 200.0, 1240.0)


def test_single_count_only_for_cash():
    assert R.single_count(rep())
    assert not R.single_count(rep(second_counter='Pat'))
    assert not R.single_count(rep(cash=0.0))


def test_matches_a_line_or_a_whole_deposit():
    one = R.match_deposits([rep()], [dep(1, '2026-10-20', 1240.0)])
    assert one[0][1] == {'deposit_date': '2026-10-20', 'deposit_ref': 'QuickBooks deposit 2026-10-20', 'deposit_amount': 1240.0}
    split = R.match_deposits([rep()], [dep(2, '2026-10-20', 1040.0), dep(2, '2026-10-20', 200.0)])
    assert split and split[0][1]['deposit_amount'] == 1240.0


def test_no_match_when_too_early_too_late_ambiguous_or_used():
    assert not R.match_deposits([rep()], [dep(1, '2026-10-16', 1240.0)])            # before it was received
    assert not R.match_deposits([rep()], [dep(1, '2026-12-15', 1240.0)])            # more than 45 days later
    assert not R.match_deposits([rep()], [dep(1, '2026-10-20', 1240.0), dep(2, '2026-10-21', 1240.0)])
    assert not R.match_deposits([rep()], [dep(1, '2026-10-20', 1240.0)], used={'Deposit 1'})
    assert not R.match_deposits([rep(status='Deposited')], [dep(1, '2026-10-20', 1240.0)])


def test_each_deposit_used_once():
    two = [rep(), rep(id='CR-2026-002')]
    assert len(R.match_deposits(two, [dep(1, '2026-10-20', 1240.0)])) == 1


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv('API_KEYS', f'treasurer@example.org:{KEY}')
    import fr
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'}])
    monkeypatch.setattr(fr.F, 'enabled', lambda: True)
    monkeypatch.setattr(fr, 'load_events', lambda: [{'unique_code': '#DRTEST', 'event_name': 'Test RallyCross', 'program_code': 'RX', 'date': '2026-10-17'}])
    made = []

    def create(r, lines, files):
        made.append((r, lines))
        return 'CR-2026-009'
    monkeypatch.setattr(R, 'create', create)
    monkeypatch.setattr(R, 'get', lambda rid: dict(rep(id=rid), event_name='Test RallyCross', event_code='#DRTEST', program='RX',
                                                   reported_by='Pat Treasurer', counted_by='Pat', held_by='Pat', notes='', checks=200.0,
                                                   lines=[]))
    monkeypatch.setattr(fr, 'send_mail', lambda *a, **k: None)
    monkeypatch.setattr(fr, 'log_audit_event', lambda *a, **k: None)
    from app import app
    app.config['TESTING'] = True
    c = app.test_client()
    c.made = made
    return c


def body(**kw):
    return dict({'program': 'RX', 'event_code': '#DRTEST', 'received': '2026-10-17', 'counted_by': 'Pat', 'second_counter': 'Sam',
                 'held_by': 'Pat', 'lines': [{'description': 'Gate', 'category': 'Entry fees', 'form': 'Cash', 'check_number': '77', 'amount': 1040},
                                             {'description': 'Sponsor', 'category': 'Sponsorship', 'form': 'Check', 'check_number': '1001', 'amount': 200}]}, **kw)


def test_report_goes_through_the_form(client):
    r = client.post('/api/v1/received', json=body(), headers={'Authorization': f'Bearer {KEY}'})
    assert r.status_code == 201 and r.json['id'] == 'CR-2026-009'
    rec, lines = client.made[0]
    assert rec['event_name'] == 'Test RallyCross' and rec['program'] == 'RX' and rec['second_counter'] == 'Sam'
    assert [(ln['form'], ln['amount'], ln['check_number']) for ln in lines] == [('Cash', 1040.0, ''), ('Check', 200.0, '1001')]


@pytest.mark.parametrize('bad', [{'received': '2999-01-01'}, {'lines': []}, {'second_counter': 'pat'}, {'lines': [{'description': 'Gate', 'form': 'Cash', 'amount': -5}]},
                                 {'event_code': '', 'program': ''}])
def test_form_refuses_bad_reports(client, bad):
    r = client.post('/api/v1/received', json=body(**bad), headers={'Authorization': f'Bearer {KEY}'})
    assert r.status_code == 400 and not client.made


def test_delete_only_before_deposit_and_by_owner_or_officer(monkeypatch):
    import fr
    from flask import Flask
    monkeypatch.setattr(fr, 'officer_current', lambda perm='board': None)
    app = Flask(__name__)
    app.secret_key = 'test'
    with app.test_request_context():
        from flask import session
        session.update(email='val@example.org', who='Val')
        mine = rep(reported_email='val@example.org', reported_by='Val')
        assert fr.can_delete_report(mine)
        assert not fr.can_delete_report(dict(mine, status='Deposited'))
        assert not fr.can_delete_report(rep(reported_email='someone@example.org', reported_by='Someone'))
        monkeypatch.setattr(fr, 'officer_current', lambda perm='board': {'name': 'Pat'})
        assert fr.can_delete_report(rep(reported_email='someone@example.org', reported_by='Someone'))


def test_deleted_rows_drop_out_but_keep_their_number(monkeypatch):
    import fr_store as F
    rows = [{'id': 'FR-2026-001', 'status': 'Submitted'}, {'id': 'FR-2026-002', 'status': 'Deleted'}]
    monkeypatch.setattr(F, '_snapshot', lambda: (rows, []))
    assert [r['id'] for r in F.requests()] == ['FR-2026-001'] and F.get('FR-2026-002') is None
    monkeypatch.setattr(R, '_snapshot', lambda: ([rep(), rep(id='CR-2026-002', status='Deleted')], []))
    assert [r['id'] for r in R.reports()] == ['CR-2026-001'] and R.get('CR-2026-002') is None


def test_funds_request_delete_rules(monkeypatch):
    import auth
    import fr
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = 'test'
    monkeypatch.setattr(fr, 'officer_current', lambda perm='board': None)
    monkeypatch.setattr(auth, 'owns_request', lambda r: r.get('submitter_email') == 'val@example.org')
    with app.test_request_context():
        mine = {'status': 'Submitted', 'submitter_email': 'val@example.org'}
        assert fr.can_delete_request(mine)
        assert not fr.can_delete_request(dict(mine, status='Paid'))
        assert not fr.can_delete_request({'status': 'Submitted', 'submitter_email': 'x@example.org'})
        monkeypatch.setattr(fr, 'officer_current', lambda perm='board': {'name': 'Pat'})
        assert fr.can_delete_request({'status': 'Submitted', 'submitter_email': 'x@example.org'})
