#!/usr/bin/env python3
"""Apply officials-list changes the admin made in the apps (data/officials-queue.json): each one is a handover
(tools/handover.py hand ... --apply), which edits officials.json and brings the drscca.org Google Groups in line.
Results go to data/officials-done.json for the Officials page. Run by drscca-officials.path when the queue changes.
"""
import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')
QUEUE, DONE = os.path.join(DATA, 'officials-queue.json'), os.path.join(DATA, 'officials-done.json')
sys.path.insert(0, HERE)
import handover  # noqa: E402
from filelock import FileLock  # noqa: E402


def _read(p, empty):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def _write(p, doc):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)


def main():
    with FileLock(QUEUE + '.lock'):
        changes = _read(QUEUE, {}).get('changes', [])
        if not changes:
            return
        _write(QUEUE, {'changes': []})
    done = _read(DONE, {'results': []})
    for c in changes:
        out = io.StringIO()
        ok, msg = True, ''
        try:
            with contextlib.redirect_stdout(out):
                op = c.get('op', 'change')
                if op == 'change':
                    handover.cmd_hand(argparse.Namespace(title=c['title'], email=c['email'], name=c['name'], old=c.get('from') or None, apply=True))
                else:
                    doc = handover.load()
                    rows = doc['officials']
                    if op == 'add':
                        rows.append(c['entry'])
                        print(f"officials.json: added {c['name']} <{c['email']}> as {c['title']}")
                    else:
                        keep = [o for o in rows if not (o.get('title') == c['title'] and (o.get('email') or '').lower() == c['email'].lower())]
                        if len(keep) == len(rows):
                            raise SystemExit(f"{c['email']} does not hold {c['title']}.")
                        doc['officials'] = rows = keep
                        print(f"officials.json: removed {c['name']} <{c['email']}> from {c['title']}")
                    handover.save(doc)
                    handover.run(handover.plan(handover.wanted(rows)), True)
        except SystemExit as e:
            ok, msg = False, str(e)
        except Exception as e:      # noqa: BLE001
            ok, msg = False, f'{type(e).__name__}: {e}'
        done['results'].append(dict(c, ok=ok, applied=datetime.now().astimezone().isoformat(timespec='seconds'),
                                    message=(msg or out.getvalue()).strip()[-1500:]))
        print(('OK ' if ok else 'FAILED ') + f"{c['title']}: {c['name']} <{c['email']}>" + ('' if ok else f' ({msg})'))
    done['results'] = done['results'][-50:]
    _write(DONE, done)


if __name__ == '__main__':
    main()
