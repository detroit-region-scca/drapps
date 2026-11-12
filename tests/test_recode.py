import recode
import utils


def test_generated_codes_never_spell_words():
    events = []
    for _ in range(300):
        c = utils.generate_unique_code('RY', events)
        assert c.startswith('#') and not set(c[-3:]) & set('AEIOUYL01')
        events.append({'unique_code': c})


class FakeWS:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def get_all_values(self):
        return self.rows

    def batch_update(self, upd, value_input_option=None):
        self.updates += upd


def test_move_changes_only_cells_that_are_the_code(monkeypatch):
    tabs = {'Budgets': FakeWS([['event_code', 'notes', 'last_year_event'], ['#DRQER', 'from #DRQER.xlsx', ''], ['#DRNEXT', '', '#DRQER']]),
            'Ballots': FakeWS([['id', 'item'], ['1', 'budget:2027:#DRQER'], ['2', 'motion:7']])}
    monkeypatch.setenv('BUDGETS_SHEET_ID', 'b')
    monkeypatch.setenv('PLAN_SHEET_ID', 'p')
    monkeypatch.delenv('FUNDS_SHEET_ID', raising=False)
    monkeypatch.delenv('INVOICES_SHEET_ID', raising=False)

    def ws(sid, tab):
        if tab not in tabs:
            raise KeyError(tab)
        return tabs[tab]
    monkeypatch.setattr(recode, 'worksheet', ws)
    monkeypatch.setattr(recode.C, 'invalidate', lambda sid: None)
    done = recode.move('#DRQER', '#DRK7M')
    assert done == {'Budgets': 2, 'Ballots': 1}
    assert {u['range']: u['values'][0][0] for u in tabs['Budgets'].updates} == {'A2': '#DRK7M', 'C3': '#DRK7M'}
    assert tabs['Ballots'].updates[0] == {'range': 'B2', 'values': [['budget:2027:#DRK7M']]}


def test_a_quickbooks_project_with_an_old_code_still_matches():
    import qbo_logic as Q
    projects = [{'code': '#DRQER', 'project': '2027 RY Son of Sno*Drift XXIX #DRQER'}, {'code': '#DRABC', 'project': 'x #DRABC'}]
    events = [{'unique_code': '#DR4F5', 'custom_tag': '', 'former_codes': '#DRQER'}]
    assert Q.current_codes(projects, events) == [('2027 RY Son of Sno*Drift XXIX #DRQER', '#DRQER', '#DR4F5')]
    assert [p['code'] for p in projects] == ['#DR4F5', '#DRABC']


def test_a_code_change_remembers_the_old_code():
    import app
    ev = {'unique_code': '#DR4F5', 'custom_tag': '', 'former_codes': '#DRAAA'}
    app._remember_code(ev, '#DRQER')
    app._remember_code(ev, '#DRQER')
    assert ev['former_codes'] == '#DRAAA #DRQER'


def test_the_treasurer_is_emailed_once_about_an_old_code(monkeypatch, tmp_path):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('qn', pathlib.Path(__file__).parent.parent / 'tools' / 'qbo_nightly.py')
    qn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qn)
    sent = []
    monkeypatch.setattr(qn, 'send_mail', lambda subject, body, *a, **k: sent.append(body))
    monkeypatch.setattr(qn, 'OLD_CODES', str(tmp_path / 'told.json'))
    renamed = [('2027 RY Son of Sno*Drift XXIX #DRQER', '#DRQER', '#DR4F5')]
    qn.tell_old_codes(renamed, [])
    qn.tell_old_codes(renamed, [])
    assert len(sent) == 1 and 'rename to: 2027 RY Son of Sno*Drift XXIX #DR4F5' in sent[0]


def test_records_follow_their_events_program(monkeypatch):
    tabs = {'Budgets': FakeWS([['event_code', 'event_name', 'program'], ['#DR99L', 'Street Survival #1', 'SS']]),
            'Requests': FakeWS([['id', 'submitted', 'status', 'event_code', 'event_name', 'program'],
                                ['FR-2026-087', '2026-09-13', 'Paid', '#DR99L', 'Street Survival #1', 'DR'],
                                ['FR-2026-088', '2026-09-14', 'Paid', '#DRABC', 'Other', 'DR']])}
    for env in ('BUDGETS_SHEET_ID', 'FUNDS_SHEET_ID'):
        monkeypatch.setenv(env, env)
    monkeypatch.delenv('INVOICES_SHEET_ID', raising=False)

    def ws(sid, tab):
        if tab not in tabs:
            raise KeyError(tab)
        return tabs[tab]
    monkeypatch.setattr(recode, 'worksheet', ws)
    monkeypatch.setattr(recode.C, 'invalidate', lambda sid: None)
    assert recode.follow_program('#DR99L', 'SS') == {'Requests': 1}
    assert tabs['Requests'].updates == [{'range': 'F2', 'values': [['SS']]}] and tabs['Budgets'].updates == []


def test_the_director_hears_when_the_board_approves_their_budgets(monkeypatch):
    import bud
    import officials
    buds = {'RY-2027': {'event_code': 'RY-2027', 'event_name': 'Road Rally program budget 2027', 'program': 'RY', 'date': '2027-01-01'},
            '#DRA9S': {'event_code': '#DRA9S', 'event_name': 'Winter Rally', 'program': 'RY', 'date': '2027-02-20'},
            '#AX1': {'event_code': '#AX1', 'event_name': 'Skool', 'program': 'AX', 'date': '2027-05-29'}}
    monkeypatch.setattr(bud.B, 'get', buds.get)
    monkeypatch.setattr(officials, 'director', lambda p: {'RY': ('Pat Rally', 'rally@example.org')}.get(p, ('', '')))
    sent = []
    monkeypatch.setattr(bud, 'send_mail', lambda s, b, to: sent.append((to, s, b)))
    from app import app
    with app.test_request_context('/'):
        bud.tell_approved(['RY-2027', '#DRA9S', '#AX1'], '2026-11-11', 'board meeting, 7-0')
    assert len(sent) == 1                                    # no group for AX: nobody to tell
    to, subject, body = sent[0]
    assert to == 'rally@example.org' and subject.startswith('2 budgets approved')
    assert 'Hi Pat,' in body and 'approved 2 budgets on Nov 11, 2026 (board meeting, 7-0)' in body
    assert '- Winter Rally (#DRA9S), Feb 20, 2027' in body and '- Road Rally program budget 2027 (RY-2027)\n' in body


def test_the_director_hears_every_change_to_their_budgets_but_not_their_own_doings(monkeypatch):
    import bud
    import officials
    monkeypatch.setattr(officials, 'director', lambda p: {'RY': ('Pat Rally', 'rally@example.org')}.get(p, ('', '')))
    b = {'event_code': '#DRA9S', 'event_name': 'Winter Rally', 'program': 'RY', 'date': '2027-02-20'}
    from app import app
    with app.test_request_context('/'):
        m = lambda kind, **kw: bud.director_emails(kind, [b], **kw)          # noqa: E731
        (to, subject, body), = m('failed', when='2026-11-11', how='board meeting, 3-4')
        assert subject == 'Budget not approved: Winter Rally' and 'did not approve the budget on Nov 11, 2026 (board meeting, 3-4)' in body
        (_, subject, body), = m('no_vote', who='Sam Treasurer', why='turned in after the season started')
        assert 'Sam Treasurer marked the budget as not needing a board vote (turned in after the season started)' in body and 'can go ahead' in body
        (_, subject, body), = m('deleted', who='Sam Treasurer')
        assert subject == 'Budget deleted: Winter Rally' and 'http' not in body
        assert m('draft', who='Pat Rally') == [] and m('unapproved', who='Pat Rally') == [] and m('deleted', who='Pat Rally') == []
        assert len(m('draft', who='Sam Treasurer')) == 1


def test_a_withdrawn_board_vote_is_announced_to_the_board(monkeypatch):
    import bud
    import ballots_store as BS
    sent, upd = [], []
    monkeypatch.setattr(BS, 'enabled', lambda: True)
    monkeypatch.setattr(BS, 'current', lambda item: {'id': 'B-1', 'status': 'open', 'title': 'Budget: Winter Rally'})
    monkeypatch.setattr(BS, 'update', lambda bid, **kw: upd.append(kw))
    monkeypatch.setattr(bud, 'send_mail', lambda s, b, to, reply_to=None: sent.append((s, b, to)))
    from app import app
    with app.test_request_context('/'):
        assert bud._cancel_ballots('budget:2027:#DRA9S', 'Sam', why='Sam put the budget back into draft')
    assert upd == [{'status': 'cancelled', 'recorded_by': 'Sam'}]
    (s, body, to), = sent
    assert s == 'Vote withdrawn: Budget: Winter Rally' and 'withdrawn: Sam put the budget back into draft. No need to vote' in body
