"""Short links in emails: they redirect to the signed link behind them, need no sign-in, and stop when it expires."""
import time

import shortlinks
from app import app


def test_short_link_redirects_without_sign_in_and_expires():
    with app.test_request_context(base_url='https://apps.example.org'):
        short = shortlinks.make('https://apps.example.org/board/vote/abc.def', 30)
    assert short.startswith('https://apps.example.org/l/') and len(short.rsplit('/', 1)[1]) <= 12
    c = app.test_client()
    r = c.get('/l/' + short.rsplit('/', 1)[1])
    assert r.status_code == 302 and r.headers['Location'] == 'https://apps.example.org/board/vote/abc.def'
    assert c.get('/l/not-a-real-link').status_code == 404
    links = shortlinks._load()
    sid = short.rsplit('/', 1)[1]
    links[sid]['expires'] = time.time() - 1
    import json
    with open(shortlinks.PATH, 'w') as f:
        json.dump(links, f)
    assert c.get('/l/' + sid).status_code == 404


def test_expired_links_are_dropped_when_a_new_one_is_made():
    with app.test_request_context(base_url='https://apps.example.org'):
        old = shortlinks.make('https://apps.example.org/x', 0)
        new = shortlinks.make('https://apps.example.org/y', 1)
    links = shortlinks._load()
    assert old.rsplit('/', 1)[1] not in links and new.rsplit('/', 1)[1] in links
