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
