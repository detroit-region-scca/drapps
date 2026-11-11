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
