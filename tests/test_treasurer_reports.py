"""Monthly treasurer's reports: one Google Slides deck a month in Drive (here an in-memory stand-in), three slides."""
import base64
import io

import pytest

import treasurer_reports as T

ROWS = [{'kind': 'head', 'depth': 0, 'label': 'Income', 'month': 0, 'ytd': 0, 'avg5': None, 'avg_years': 0, 'from': '2026-10-01'},
        {'kind': 'line', 'depth': 1, 'label': 'RX Rallycross Revenue', 'month': 1200.0, 'ytd': 27265.0, 'avg5': 25000.0, 'avg_years': 4},
        {'kind': 'total', 'depth': 0, 'label': 'Total Income', 'month': 1200.0, 'ytd': 27265.0, 'avg5': 25000.0, 'avg_years': 5},
        {'kind': 'head', 'depth': 0, 'label': 'Cost of Goods Sold', 'month': 0, 'ytd': 0, 'avg5': None, 'avg_years': 0},
        {'kind': 'line', 'depth': 1, 'label': 'RX Rallycross Expense', 'month': 300.0, 'ytd': 10.0, 'avg5': 4.0, 'avg_years': 5},
        {'kind': 'total', 'depth': 0, 'label': 'Net Income', 'month': -50.5, 'ytd': 46514.55, 'avg5': 59076.23, 'avg_years': 5}]


def _data(**kw):
    d = {'title': "Treasurer's report, November 2026", 'period': '2026-11-11', 'since': 'Since Oct 1', 'net': ROWS[-1],
         'programs': [('RallyCross', -855.0)], 'waiting': [], 'board': ['Michigan Winter Rally IV (#DRA9S)'],
         'housekeeping': ['Old checks'], 'chart': base64.b64decode(T.chart_png(2026, 11, [100.0] * 12, [50.0] * 11 + [0], {2025: [10.0] * 12})),
         'pnl': ROWS}
    d.update(kw)
    return d


def test_the_deck_has_three_slides_with_the_summary_chart_and_statement():
    from pptx import Presentation
    prs = Presentation(io.BytesIO(T.deck(_data())))
    assert len(prs.slides) == 3
    texts = ['\n'.join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in prs.slides]
    assert 'Since Oct 1: down $50. Year to date: up $46,515.' in texts[0] and 'RallyCross: down $855' in texts[0]
    assert 'Nothing waiting' in texts[0] and 'Michigan Winter Rally IV (#DRA9S)' in texts[0] and 'Old checks' in texts[0]
    assert 'Net income through the year' in texts[1] and any(sh.shape_type == 13 for sh in prs.slides[1].shapes)   # a picture
    tbl = next(sh for sh in prs.slides[2].shapes if sh.has_table).table
    assert [c.text for c in tbl.rows[0].cells] == ['Account', 'Since Oct 1', 'Year to date', 'Average 2021 to 2025', 'Difference']
    assert [c.text for c in tbl.rows[2].cells] == ['    RX Rallycross Revenue', '1,200.00', '27,265.00', '25,000.00*', '2,265.00']
    color = lambda r, c: str(tbl.cell(r, c).text_frame.paragraphs[0].runs[0].font.color.rgb)      # noqa: E731
    assert color(2, 4) == T.GOOD and color(5, 4) == T.BAD and color(6, 4) == T.BAD          # more income good, more cost bad
    assert str(tbl.cell(1, 0).fill.fore_color.rgb) == T.SECTION_BG['income']
    assert 'Fewer than five years' in texts[2]


def test_an_empty_month_still_makes_a_deck():
    from pptx import Presentation
    prs = Presentation(io.BytesIO(T.deck(_data(net=None, programs=[], board=[], housekeeping=[], chart=b'', pnl=[]))))
    assert len(prs.slides) == 3 and 'No results in QuickBooks yet.' in prs.slides[0].shapes[0].text_frame.text + ''.join(
        sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)


def test_sub_accounts_fold_into_their_group():
    rows = [{'kind': 'head', 'depth': 0, 'label': 'Income'},
            {'kind': 'line', 'depth': 1, 'label': 'Club AX Autocross Revenue', 'ytd': 5},
            {'kind': 'total', 'depth': 0, 'label': 'Total Income', 'ytd': 5},
            {'kind': 'head', 'depth': 0, 'label': 'Cost of Goods Sold'},
            {'kind': 'head', 'depth': 1, 'label': 'Club AX Autocross Expense'},
            {'kind': 'line', 'depth': 2, 'label': 'Club AX Autocross Site Rental', 'ytd': 3},
            {'kind': 'total', 'depth': 1, 'label': 'Total Club AX Autocross Expense', 'ytd': 3},
            {'kind': 'total', 'depth': 0, 'label': 'Total Other Income', 'ytd': 7},
            {'kind': 'total', 'depth': 0, 'label': 'Net Other Income', 'ytd': 7},
            {'kind': 'total', 'depth': 0, 'label': 'Net Income', 'ytd': 9}]
    assert [(r['kind'], r['label']) for r in T.collapse(rows)] == [
        ('head', 'Income'), ('line', 'AX Autocross Revenue'), ('total', 'Total Income'), ('head', 'Cost of Goods Sold'),
        ('line', 'AX Autocross Expense'), ('total', 'Total Other Income'), ('total', 'Net Income')]


def test_the_chart_draws():
    import base64
    png = T.chart_png(2026, 10, [100.0] * 12, [50.0] * 10 + [0, 0], {2024: [10.0] * 12, 2025: [-10.0] * 12})
    assert base64.b64decode(png)[:8] == b'\x89PNG\r\n\x1a\n'
    assert T.chart_png(2026, 0, [0.0] * 12, [], {}) == ''


@pytest.fixture()
def box(monkeypatch):
    import auth
    people = {'ada@example.org': {'name': 'Ada', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'pay'}},
              'pat@example.org': {'name': 'Pat', 'level': 'official', 'roles': ['Director'], 'perms': {'events'}}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(people[e], email=e) if e in people else None)
    reps, made = {}, []

    def create(month, pptx):
        reps[month] = {'deck': {'id': f'id{month}', 'webViewLink': f'https://docs.google.com/presentation/d/id{month}/edit', 'pptx': pptx},
                       'pdf': None}
        return reps[month]['deck']

    def save_pdf(month):
        reps[month]['pdf'] = {'id': f'pdf{month}', 'webViewLink': f'https://drive.google.com/file/d/pdf{month}/view',
                              'from': reps[month]['deck']['pptx']}
    monkeypatch.setattr(T, 'reports', lambda: dict(sorted(reps.items(), reverse=True)))
    monkeypatch.setattr(T, 'create', create)
    monkeypatch.setattr(T, 'save_pdf', save_pdf)
    monkeypatch.setattr(T, 'notes_from', lambda deck_id: [f'note from {deck_id}'])
    monkeypatch.setattr(T, 'data', lambda m, notes=(): made.append((m, list(notes))) or {'month': m})
    monkeypatch.setattr(T, 'deck', lambda d: b'PPTX ' + d['month'].encode())
    monkeypatch.setattr(T, 'log_audit_event', lambda *a, **k: None)
    from app import app
    app.config['TESTING'] = True

    def as_(email):
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, email=email, who=people[email]['name'])
        return c
    return as_, reps, made


def test_start_a_month_from_the_last_one_with_its_pdf(box):
    as_, reps, made = box
    ada = as_('ada@example.org')
    assert b'No reports yet' in ada.get('/treasurer/reports/').data
    assert ada.post('/treasurer/reports/new', data={'month': '2026-11'}).status_code == 302
    assert reps['2026-11']['pdf']['from'] == b'PPTX 2026-11' and made == [('2026-11', [])]     # the first has no notes to carry
    ada.post('/treasurer/reports/new', data={'month': '2026-12'})
    assert made[-1] == ('2026-12', ['note from id2026-11'])                                   # the last report is the template
    reps['2026-12']['deck']['pptx'] = b'edited'
    ada.post('/treasurer/reports/new', data={'month': '2026-12'})          # running it again never overwrites it
    assert reps['2026-12']['deck']['pptx'] == b'edited' and len(made) == 2
    ada.post('/treasurer/reports/2026-12/pdf')
    assert reps['2026-12']['pdf']['from'] == b'edited'                     # the PDF follows the edited deck
    page = ada.get('/treasurer/reports/').data
    for want in (b'https://docs.google.com/presentation/d/id2026-12/present', b'https://docs.google.com/presentation/d/id2026-12/edit',
                 b'https://drive.google.com/file/d/pdf2026-12/view', b'Update PDF'):
        assert want in page
    assert ada.get('/treasurer/reports/2026-12').status_code == 404        # no page per report


def test_only_the_treasurer_role_starts_one_or_updates_the_pdf(box):
    as_, reps, made = box
    as_('ada@example.org').post('/treasurer/reports/new', data={'month': '2026-11'})
    reps['2026-11']['deck']['pptx'] = b'edited'
    pat = as_('pat@example.org')
    assert pat.get('/treasurer/reports/').status_code == 200            # looking is never gated for officials
    pat.post('/treasurer/reports/new', data={'month': '2026-12'})
    pat.post('/treasurer/reports/2026-11/pdf')
    assert '2026-12' not in reps and reps['2026-11']['pdf']['from'] == b'PPTX 2026-11'


def test_housekeeping_notes_are_read_back_from_the_deck(monkeypatch):
    text = ("Treasurer's report, November 2026\nFunds requests\nNothing waiting.\nHousekeeping\n"
            "•  Reserve set up.\n•  Old checks.\n\nTreasurer's report, November 2026\nNet income through the year\n")
    monkeypatch.setattr(T, '_export', lambda fid, mime: text.encode())
    assert T.notes_from('x') == ['Reserve set up.', 'Old checks.']
    monkeypatch.setattr(T, '_export', lambda fid, mime: b'no notes here')
    assert T.notes_from('x') == []
