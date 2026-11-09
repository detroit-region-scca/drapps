"""MotorsportReg link checks and cancelled events."""
import bud
import bud_store as B
import msr

PAGE = """<html><head><title>Summer Daze RallyCross-CANCELLED - MotorsportReg.com</title></head>
<body><h1>Summer Daze</h1><div>Sun, Aug 2, 2026</div><script>var x='Mon, Jan 5, 2026';</script></body></html>"""


def test_clean_url():
    assert msr.clean_url(' https://www.motorsportreg.com/events/summer-ends-rallycross-123?utm=x ') == \
        'https://www.motorsportreg.com/events/summer-ends-rallycross-123'
    assert msr.clean_url('https://evil.example/events/x') == ''
    assert msr.clean_url('https://www.motorsportreg.com/orgs/scca/detroit') == ''


def test_parse_and_differences():
    info = msr.parse(PAGE)
    assert info['date'] == '2026-08-02' and info['cancelled'] and 'MotorsportReg' not in info['title']
    e = {'date': '2026-08-12', 'event_name': 'Summer Daze'}
    assert msr.differences(e, info) == ['MotorsportReg says this event is cancelled', 'MotorsportReg has it on Aug 2, 2026']
    assert msr.differences(dict(e, cancelled='2026-11-09', date='2026-08-02'), info) == []
    assert msr.differences(e, None) == []


def test_check_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(msr, 'CACHE', str(tmp_path / 'c.json'))
    monkeypatch.setattr(msr, 'LOCK', str(tmp_path / 'c.lock'))
    calls = []
    u = 'https://www.motorsportreg.com/events/x-1'
    assert msr.check(u, fetch=lambda url: calls.append(url) or PAGE)['date'] == '2026-08-02'
    assert msr.check(u, fetch=lambda url: calls.append(url) or PAGE)['cancelled']
    assert len(calls) == 1 and msr.cached(u)['date'] == '2026-08-02'
    # a failed read keeps the old copy
    monkeypatch.setattr(msr, 'TTL', -1)
    assert msr.check(u, fetch=lambda url: 1 / 0)['date'] == '2026-08-02'


def test_cancelled_event_has_no_warnings(monkeypatch):
    from datetime import date as D
    monkeypatch.setattr(B, 'enabled', lambda: True)
    ev = {'date': '2026-08-12', 'event_name': 'X', 'cancelled': '2026-11-09'}
    assert bud._warnings(ev, None, D(2026, 11, 9)) == []
    info = msr.parse(PAGE)
    w = bud._warnings(dict(ev, cancelled=''), None, D(2026, 11, 9), info=info)
    assert [a for _, a in w] == ['msr_cancel', 'msr_date', 'budget']


ORG = """<a href="/events/solo-6-360990">DRSCCA Solo #6: Fall into Autumn #DRR7O</a>
<a class="x" href="/events/por-270878"><span>Press on Regardless (#DR9H6)</span></a>
<a href="/events/ehc-vol-1">Empire Hill Climb - Volunteers #DRABC</a><a href="/events/ehc-comp-2">Empire Hill Climb - Competitors #DRABC</a>
<a href="/events/turkey-096197">Turkey Trot Day/Night RallyCross</a>"""


def test_find_by_code_in_name(tmp_path, monkeypatch):
    monkeypatch.setattr(msr, 'CACHE', str(tmp_path / 'c.json'))
    monkeypatch.setattr(msr, 'LOCK', str(tmp_path / 'c.lock'))
    idx = msr.org_index(fetch=lambda u: ORG)
    assert msr.find('#DRR7O', idx).endswith('/events/solo-6-360990')
    assert msr.find('#DR9H6', idx).endswith('/events/por-270878')
    assert msr.find('#DRABC', idx).endswith('/events/ehc-comp-2')     # competitors, not the volunteer entry
    assert msr.find('#DRR7', idx) == '' and msr.find('#DRI13', idx) == ''
    assert msr.link_for({'msr_url': 'https://www.motorsportreg.com/events/turkey-096197'}, '#DRI13') == \
        ('https://www.motorsportreg.com/events/turkey-096197', 'pasted')
    assert msr.link_for({'msr_url': 'x'}, '#DRR7O')[1] == 'name'


def test_missing_on_msr_only_within_a_month(tmp_path, monkeypatch):
    from datetime import date as D
    monkeypatch.setattr(msr, 'CACHE', str(tmp_path / 'c.json'))
    monkeypatch.setattr(msr, 'LOCK', str(tmp_path / 'c.lock'))
    monkeypatch.setattr(B, 'enabled', lambda: False)
    msr.org_index(fetch=lambda u: ORG)
    today = D(2026, 11, 9)
    near = {'date': '2026-11-20', 'unique_code': '#DRZZZ', 'custom_tag': ''}
    assert bud._warnings(near, None, today)[0][1] == 'msr_missing'
    assert bud._warnings(dict(near, date='2027-03-01'), None, today) == []
    assert bud._warnings(dict(near, unique_code='#DRR7O'), None, today) == []
