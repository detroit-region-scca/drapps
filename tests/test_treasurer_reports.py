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

    def create(month, kind, html):
        docs.setdefault(month, {})[kind] = {'id': f'{month}-{kind}', 'webViewLink': f'https://docs.google.com/{month}-{kind}', 'html': html}
        return docs[month][kind]
    monkeypatch.setattr(T, 'reports', lambda: dict(sorted(docs.items(), reverse=True)))
    monkeypatch.setattr(T, 'create', create)
    monkeypatch.setattr(T, 'shown', lambda fid: T.Markup('<h2>shown ' + fid + '</h2>'))
    monkeypatch.setattr(T, 'draft', lambda m: ('<h1>board</h1>', '<h1>script</h1>'))
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
    assert r.status_code == 302 and set(docs['2026-11']) == {'board', 'script'}
    ada.post('/treasurer/reports/new', data={'month': '2026-11'})          # running it again makes nothing new
    assert len(docs['2026-11']) == 2
    page = ada.get('/treasurer/reports/2026-11?show=script').data
    assert b'shown 2026-11-script' in page and b'Show the board' in page
    assert b'shown 2026-11-board' in ada.get('/treasurer/reports/2026-11?present=1').data


def test_only_the_treasurer_role_starts_one(box):
    as_, docs = box
    pat = as_('pat@example.org')
    assert pat.get('/treasurer/reports/').status_code == 200            # looking is never gated for officials
    pat.post('/treasurer/reports/new', data={'month': '2026-11'})
    assert '2026-11' not in docs
