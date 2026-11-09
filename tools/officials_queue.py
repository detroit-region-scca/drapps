#!/usr/bin/env python3
"""Apply officials-list changes the admin made in the apps (data/officials-queue.json): each one is a handover
(tools/handover.py hand ... --apply), which edits officials.json. The Google Groups are not touched.
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
sys.path.insert(1, os.path.dirname(HERE))
import access  # noqa: E402
import handover  # noqa: E402
import officials  # noqa: E402
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


def apply_person(rows, c):
    """One person's roles, as ticked on the Users page: keep (and rename) the entries for titles still ticked, drop the
    rest, add entries for newly ticked titles (copying the role's kind, program and email group from its other
    holders), and a brand new role when one was given."""
    frm, email, name = (c.get('from') or '').lower(), c['email'].lower(), c['name']
    want = list(c.get('titles') or [])
    titles = {o.get('title', '') for o in rows}
    out, have = [], set()
    for o in rows:
        if officials.follows(o.get('title', ''), titles):
            out.append(o)          # follows its job; set below
            continue
        if frm and (o.get('email') or '').lower() == frm:
            if o.get('title') in want:
                o = dict(o, name=name, email=email)
                have.add(o.get('title'))
                print(f"officials.json: {o['title']}: {name} <{email}>")
            else:
                print(f"officials.json: removed {o.get('name')} <{o.get('email')}> from {o.get('title')}")
                continue
        out.append(o)
    for t in want:
        if t in have:
            continue
        tpl = next((o for o in rows if o.get('title') == t), None)
        role_of = c.get('role_of') or c.get('kinds') or {}      # 'kinds' in changes queued before the rename
        if not tpl and t in role_of:
            tpl = {'role': role_of[t]}       # a role made on Admin > Roles that nobody held yet
        if not tpl:
            raise SystemExit(f'No role titled "{t}" to copy.')
        out.append({**{k: tpl[k] for k in ('role', 'program', 'notify') if tpl.get(k)}, 'title': t, 'name': name, 'email': email})
        print(f"officials.json: added {name} <{email}> as {t}")
    return follow_jobs(out)


def follow_jobs(rows):
    """Entries named after a job in brackets go with whoever holds that job (when exactly one person does)."""
    titles = {o.get('title', '') for o in rows}
    for o in rows:
        job = officials.follows(o.get('title', ''), titles)
        if not job:
            continue
        holders = [p for p in rows if p.get('title') == job]
        if len(holders) == 1 and (holders[0].get('email'), holders[0].get('name')) != (o.get('email'), o.get('name')):
            o['name'], o['email'] = holders[0].get('name'), holders[0].get('email')
            print(f"officials.json: {o['title']} follows {job}: {o['name']} <{o['email']}>")
    return rows


def check_needed(before, after):
    """Refuse a result that leaves a needed role (one that had a holder) with nobody."""
    def holders(rows):
        out = {}
        for o in rows:
            if o.get('role') in access.NEEDED and (o.get('email') or '').strip():
                out.setdefault(o.get('title', ''), set()).add(o['email'].lower())
        return out
    lost = sorted(set(holders(before)) - set(holders(after)))
    if lost:
        raise SystemExit(f"That would leave nobody as {', '.join(lost)}. Give it to someone else first.")


def _drop_volunteer(email):
    """Take a new official off the Volunteers tab, now that their role is in officials.json."""
    try:
        os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', '')
        sys.path.insert(0, os.path.dirname(HERE))
        import auth
        if email in auth.volunteers():
            auth.set_volunteer(email, None)
            print(f'Volunteers tab: removed {email} (now an official)')
    except Exception as e:      # noqa: BLE001
        print(f'Volunteers tab: could not remove {email}: {e}')


def apply_one(c):
    """Apply one queued change to officials.json. Returns (ok, message)."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            op = c.get('op', 'change')
            if op == 'change':
                handover.cmd_hand(argparse.Namespace(title=c['title'], email=c['email'], name=c['name'], old=c.get('from') or None, apply=True))
                if c.get('drop_volunteer'):
                    _drop_volunteer(c['email'].lower())
                return True, out.getvalue()
            if op != 'person':
                raise SystemExit(f'Unknown change "{op}"; nothing was saved.')
            doc = handover.load()
            before = [dict(o) for o in doc['officials']]
            rows = apply_person(doc['officials'], c)
            check_needed(before, rows)
            doc['officials'] = rows
            handover.save(doc)
            if c.get('drop_volunteer'):
                _drop_volunteer(c['email'].lower())
        return True, out.getvalue()
    except SystemExit as e:
        return False, str(e)
    except Exception as e:      # noqa: BLE001
        return False, f'{type(e).__name__}: {e}'


def main():
    """Apply everything queued, and keep going until the queue stays empty: a save made while this runs would not
    start another run (the path unit merges it into this one)."""
    done = _read(DONE, {'results': []})
    mirrored = False
    while True:
        with FileLock(QUEUE + '.lock'):
            changes = _read(QUEUE, {}).get('changes', [])
            if not changes:
                break
            _write(QUEUE, {'changes': []})
        for c in changes:
            ok, msg = apply_one(c)
            done['results'].append(dict(c, ok=ok, applied=datetime.now().astimezone().isoformat(timespec='seconds'),
                                        message=(msg or '').strip()[-1500:]))
            print(('OK ' if ok else 'FAILED ') + f"{c.get('title')}: {c.get('name')} <{c.get('email')}>" + ('' if ok else f' ({msg})'))
        done['results'] = done['results'][-50:]
        _write(DONE, done)
        mirrored = True
    if mirrored:
        # The readable copy in the shared drive. Written from inside the app's container: that is where the Drive
        # key is, and it reads the file just changed.
        import subprocess
        container = os.environ.get('APP_CONTAINER') or 'drapps'
        try:
            r = subprocess.run(['docker', 'exec', container, 'python', '-c',
                                'import officials; print(officials.mirror_to_sheet())'], capture_output=True, text=True, timeout=120)
            failed = '' if r.returncode == 0 else r.stderr.strip()[-300:] or f'exit {r.returncode}'
        except (OSError, subprocess.SubprocessError) as e:
            failed = f'{type(e).__name__}: {e}'
        if failed:
            # officials.json itself is saved. Exit non-zero so the job's alert email goes out (drscca-alert@).
            sys.exit(f'Drive copy failed (container "{container}"): {failed}')
        print(f'Drive copy: {r.stdout.strip()} officials')


if __name__ == '__main__':
    main()
