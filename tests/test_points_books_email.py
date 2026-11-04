from datetime import date


def test_send_books_emails_and_audits(monkeypatch):
    import members
    import utils
    sent, audit = [], []
    entries = [{'date': date(2026, 10, 5), 'points': 2.0, 'member': '1', 'event': '#TEST1', 'status': 'approved'}]
    monkeypatch.setattr(members.L, 'unreconciled', lambda es: entries)
    monkeypatch.setattr(members.S, 'entries', lambda: entries)
    monkeypatch.setattr(members.S, 'members', lambda: [{'points': 2.0}])
    monkeypatch.setattr(members.L, 'journal_entries', lambda es, ev: ([{'memo': 'x'}], []))
    monkeypatch.setattr(members.L, 'books_email', lambda bk, span, reserve, value: ('Subject', 'Body'))
    monkeypatch.setattr(members, '_events_by_code', lambda: {})
    monkeypatch.setattr(members, '_reserve', lambda: None)
    monkeypatch.setattr(utils, 'send_mail', lambda *a, **k: sent.append(a))
    monkeypatch.setattr(members, 'log_audit_event', lambda *a: audit.append(a))
    assert members.send_books('Tester') == 1
    assert sent and audit[0][2] == {'entries': 1, 'journal_entries': 1}
