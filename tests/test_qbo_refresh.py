import json

import utils


def test_refresh_request_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, 'QBO_REQUEST', str(tmp_path / 'req.json'))
    monkeypatch.setattr(utils, 'QBO_LAST', str(tmp_path / 'last.json'))
    assert utils.qbo_state() == {'requested': '', 'last': '', 'pending': False}
    assert utils.request_qbo_refresh('Tester') is True
    s = utils.qbo_state()
    assert s['pending'] and json.loads((tmp_path / 'req.json').read_text())['by'] == 'Tester'
    assert utils.request_qbo_refresh('Tester') is False          # one already waiting
    (tmp_path / 'last.json').write_text(json.dumps({'at': '9999-01-01T00:00:00+00:00'}))
    assert utils.qbo_state()['pending'] is False                  # the sync finished after the request
    assert utils.request_qbo_refresh('Tester') is True
