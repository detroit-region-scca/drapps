"""Invoices: numbering, the one-time New invoice page, who may do what, and the PDF for each status. Made-up names only."""
from datetime import date

import pytest
from werkzeug.datastructures import MultiDict

import inv_store as I


class FakeWS:
    def __init__(self, rows):
        self.rows = rows

    def col_values(self, n):
        return [r[n - 1] for r in self.rows]

    def append_row(self, row, **k):
        self.rows.append(row)

    def append_rows(self, rows, **k):
        self.rows += rows


@pytest.fixture
def sheet(monkeypatch, tmp_path):
    tabs = {'Invoices': FakeWS([I.INV_LABELS]), 'Lines': FakeWS([I.LINE_LABELS])}
    monkeypatch.setattr(I, '_ws', lambda t: tabs[t])
    monkeypatch.setattr(I, 'LOCK', str(tmp_path / 'inv.lock'))
    monkeypatch.setattr(I, 'ISSUED_FILE', str(tmp_path / 'issued.json'))
    monkeypatch.setattr(I, 'refresh', lambda: None)

    def get(iid):
        r = next((r for r in tabs['Invoices'].rows[1:] if r[0] == iid), None)
        return dict(zip(I.INV_COLS, r), lines=[]) if r else None
    monkeypatch.setattr(I, 'get', get)
    return tabs


LINE = [{'description': 'Sponsorship', 'quantity': 2, 'unit_price': 50}]


def test_numbers_count_up_and_a_used_page_makes_no_second_invoice(sheet):
    y = date.today().year
    a, again = I.create({'bill_name': 'Example Co'}, LINE, 'page-1')
    assert a['id'] == f'INV-{y}-001' and a['total'] == 100 and not again
    b, again = I.create({'bill_name': 'Example Co'}, LINE, 'page-1')
    assert b['id'] == a['id'] and again and len(sheet['Invoices'].rows) == 2
    c, _ = I.create({'bill_name': 'Other Co'}, LINE)
    assert c['id'] == f'INV-{y}-002'


def test_a_deleted_newest_number_is_never_given_out_again(sheet):
    y = date.today().year
    I.create({'bill_name': 'Example Co'}, LINE)
    I.create({'bill_name': 'Example Co'}, LINE)
    sheet['Invoices'].rows.pop()            # INV-...-002 deleted
    d, _ = I.create({'bill_name': 'Example Co'}, LINE)
    assert d['id'] == f'INV-{y}-003'


def _inv(**k):
    return dict({'id': 'INV-2026-001', 'created': '2026-11-08', 'due': '2026-12-08', 'bill_name': 'Example Co', 'total': 100.0,
                 'status': 'Open', 'lines': [{'description': 'A line ' * 30, 'quantity': 1, 'unit_price': 100, 'amount': 100}]}, **k)


@pytest.mark.parametrize('status', ['Open', 'Paid', 'Void'])
def test_pdf_for_each_status(status):
    data = I.pdf_bytes(_inv(status=status, paid_date='2026-11-20', paid_ref='1234', paid_amount=100.0))
    assert data[:5] == b'%PDF-'


def test_long_invoices_run_to_more_pages():
    many = _inv(lines=[{'description': 'Item with a long description ' * 3, 'quantity': 1, 'unit_price': 1, 'amount': 1}] * 60)
    import re
    pages = int(re.search(rb'/Count (\d+)', I.pdf_bytes(many)).group(1))
    assert pages >= 2


def test_long_date():
    assert I._long_date('2026-11-08') == 'November 8, 2026' and I._long_date('soon') == 'soon'


# ---------- the pages ----------

@pytest.fixture
def site(monkeypatch, sheet):
    import app as A
    import auth
    import inv
    store = {'mail': [], 'user': {'name': 'Pat Director', 'level': 'official', 'roles': [], 'perms': {'invoices', 'invoices_all'}}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(store['user'], email=e))
    monkeypatch.setattr(auth, 'group_address', lambda e: 'director@example.org')
    monkeypatch.setattr(I, 'enabled', lambda: True)
    monkeypatch.setattr(I, 'save_pdf', lambda inv, data: 'https://drive.example/d/x/view')
    monkeypatch.setattr(I, 'update', lambda iid, **f: f)
    monkeypatch.setattr(inv, 'load_events', lambda: [{'unique_code': '#E1', 'event_name': 'Test Event', 'date': '2026-05-14', 'program_code': 'AX'}])
    monkeypatch.setattr(inv, 'load_programs', lambda: {'AX': 'Autocross', 'RX': 'RallyCross'})
    monkeypatch.setattr(inv, 'send_mail', lambda subject, body, to=None, **k: store['mail'].append((subject, to)))
    monkeypatch.setattr(inv, 'log_audit_event', lambda *a: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = 'pat@example.org'
    return c, store


def _form(**k):
    return MultiDict(dict({'program': 'AX', 'bill_name': 'Example Co', 'token': 'page-9', 'event_code': ''}, **k)) | MultiDict(
        [('desc', 'Sponsorship'), ('qty', '1'), ('price', '250')])


def test_new_page_has_a_token_and_says_where_an_officials_copy_goes(site):
    c, _ = site
    html = c.get('/invoices/new').get_data(as_text=True)
    assert 'name="token"' in html and 'director@example.org' in html and 'name="my_email"' not in html


def test_double_submit_makes_one_invoice_and_one_email(site, sheet):
    c, store = site
    r1 = c.post('/invoices/new', data=_form())
    r2 = c.post('/invoices/new', data=_form())
    assert r1.status_code == r2.status_code == 302 and r1.location == r2.location
    assert len(sheet['Invoices'].rows) == 2 and len(store['mail']) == 1 and store['mail'][0][1] == 'director@example.org'


def test_event_from_another_program_is_refused(site, sheet):
    c, _ = site
    r = c.post('/invoices/new', data=_form(program='RX', event_code='#E1'))
    assert r.status_code == 400 and len(sheet['Invoices'].rows) == 1


def test_volunteer_may_ask_to_delete_their_own_invoice_only(site, sheet):
    c, store = site
    c.post('/invoices/new', data=_form())
    iid = sheet['Invoices'].rows[1][0]
    store['user'] = {'name': 'Val Volunteer', 'level': 'volunteer', 'roles': [], 'perms': {'invoices'}}
    assert c.post(f'/invoices/{iid}/delete', data={'note': 'test'}).status_code == 403
    sheet['Invoices'].rows[1][I.INV_COLS.index('created_by')] = 'Val Volunteer'
    r = c.post(f'/invoices/{iid}/delete', data={'note': 'test'})
    assert r.status_code == 302 and r.location.endswith(f'/invoices/{iid}')
