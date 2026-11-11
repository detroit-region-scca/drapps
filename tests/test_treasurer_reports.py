"""Monthly treasurer's reports: two Google Docs a month in Drive (here an in-memory stand-in), shown in the app."""
import pytest

import treasurer_reports as T


def test_google_export_is_cut_to_plain_structure():
    c = T._Clean()
    c.feed('<html><head><style>.c1{color:red}</style></head><body><h1 class="c3"><span>Report</span></h1>'
           '<p><span style="font-weight:700">Year to date.</span><span> Up $5.</span></p><ul><li><span>RX</span></li></ul>'
           '<p><a href="https://www.google.com/url?q=https://drscca.org/&amp;sa=D">site</a></p><script>x()</script></body></html>')
    assert ''.join(c.out) == ('<h2>Report</h2><p><b>Year to date.</b> Up $5.</p><ul><li>RX</li></ul>'
                              '<p><a href="https://drscca.org/" target="_blank" rel="noopener">site</a></p>')


@pytest.fixture()
def box(monkeypatch):
    import auth
    people = {'ada@example.org': {'name': 'Ada', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'pay'}},
              'pat@example.org': {'name': 'Pat', 'level': 'official', 'roles': ['Director'], 'perms': {'events'}}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(people[e], email=e) if e in people else None)
    docs = {}

    def create(month, html):
        docs[month] = {'id': month, 'webViewLink': f'https://docs.google.com/{month}', 'html': html}
        return docs[month]
    monkeypatch.setattr(T, 'reports', lambda: dict(sorted(docs.items(), reverse=True)))
    monkeypatch.setattr(T, 'create', create)
    monkeypatch.setattr(T, 'shown', lambda fid: T.Markup('<h2>shown ' + fid + '</h2>'))
    monkeypatch.setattr(T, 'draft', lambda m: '<h1>board</h1>')
    monkeypatch.setattr(T, 'log_audit_event', lambda *a, **k: None)
    from app import app
    app.config['TESTING'] = True

    def as_(email):
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, email=email, who=people[email]['name'])
        return c
    return as_, docs


def test_start_show_and_present(box):
    as_, docs = box
    ada = as_('ada@example.org')
    assert b'No reports yet' in ada.get('/treasurer/reports/').data
    r = ada.post('/treasurer/reports/new', data={'month': '2026-11'})
    assert r.status_code == 302 and docs['2026-11']['html'] == '<h1>board</h1>'
    docs['2026-11']['html'] = 'edited'
    ada.post('/treasurer/reports/new', data={'month': '2026-11'})          # running it again never overwrites it
    assert docs['2026-11']['html'] == 'edited'
    page = ada.get('/treasurer/reports/2026-11').data
    assert b'shown 2026-11' in page and b'Show the board' in page
    assert b'shown 2026-11' in ada.get('/treasurer/reports/2026-11?present=1').data


def test_only_the_treasurer_role_starts_one(box):
    as_, docs = box
    pat = as_('pat@example.org')
    assert pat.get('/treasurer/reports/').status_code == 200            # looking is never gated for officials
    pat.post('/treasurer/reports/new', data={'month': '2026-11'})
    assert '2026-11' not in docs


def test_the_statement_table():
    rows = [{'kind': 'head', 'depth': 0, 'label': 'Income', 'month': 0, 'ytd': 0, 'avg5': None, 'avg_years': 0},
            {'kind': 'line', 'depth': 1, 'label': 'Club RX Rallycross Revenue', 'month': 1200.0, 'ytd': 27265.0, 'avg5': 25000.0, 'avg_years': 4},
            {'kind': 'line', 'depth': 1, 'label': 'New account', 'month': 5.0, 'ytd': 5.0, 'avg5': None, 'avg_years': 0},
            {'kind': 'total', 'depth': 0, 'label': 'Net Income', 'month': -50.5, 'ytd': 46514.55, 'avg5': 59076.23, 'avg_years': 5}]
    html = T.pnl_html(rows, '2026-10')
    assert '<th>October</th><th>Year to date</th><th>Average 2021 to 2025</th><th>Difference</th>' in html
    assert ('\u00a0' * 4 + 'Club RX Rallycross Revenue</td><td>1,200.00</td><td>27,265.00</td><td>25,000.00*</td>'
            '<td>2,265.00</td>') in html
    assert 'New account</td><td>5.00</td><td>5.00</td><td></td><td></td>' in html
    assert '<td><b>59,076.23</b></td><td><b>(12,561.68)</b></td>' in html and 'Fewer than five years' in html
    assert T.prior('2027-01') == '2026-12'
