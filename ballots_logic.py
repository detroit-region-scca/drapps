"""Board email votes: who votes, how votes count, and when a vote is decided. Pure functions, so they are tested alone.

The rules come from the bylaws and the board's online-voting policy:
- Bylaws Art. IV s6.3: the board may vote by email; s6.5: every Director has one vote. The board has 13 seats: the four
  officers (RE, ARE, Secretary, Treasurer), four elected Directors at Large, and five appointed Directors (Road Racing,
  RallyCross, Road Rally, Solo/Autocross, Financial). A person holding two seats still votes once.
- Policy (2018), online votes: the answers are Yes, No and Abstain; 7 votes decide it (7 Yes passes, 7 No fails); a vote
  not decided within 30 days closes; a vote taken at a board meeting replaces the online vote; online results are noted
  in the next meeting's minutes.
"""
import os
from datetime import date, timedelta

CHOICES = ('yes', 'no', 'abstain')
LABEL = {'yes': 'Yes', 'no': 'No', 'abstain': 'Abstain'}
# Board seats in officials.json: these roles, plus the program Directors of these programs.
SEAT_ROLES = ('RE', 'ARE', 'Secretary', 'Treasurer', 'Financial Director', 'Director at Large')
SEAT_PROGRAMS = ('RR', 'AX', 'RY', 'RX')
OPEN_DAYS = 30


def needed():
    """Votes that decide it (the policy's 7; BOARD_VOTES_NEEDED to change it)."""
    try:
        return max(1, int(os.environ.get('BOARD_VOTES_NEEDED', '7')))
    except ValueError:
        return 7


def is_seat(o):
    return o.get('role') in SEAT_ROLES or (o.get('role') == 'Director' and o.get('program') in SEAT_PROGRAMS)


def roster(officials):
    """The people who vote, in list order: [{'email', 'name', 'seats': [titles]}]. One entry per person (by email),
    whatever number of seats they hold; a seat with no email address is skipped (nobody to send a ballot to)."""
    out = {}
    for o in officials:
        mail = (o.get('email') or '').strip().lower()
        if not is_seat(o) or not mail:
            continue
        p = out.setdefault(mail, {'email': mail, 'name': o.get('name', ''), 'seats': []})
        p['seats'].append(o.get('title') or o.get('role', ''))
    return list(out.values())


def latest(votes):
    """{email: vote} keeping each person's last vote (a director may change their vote while it is open)."""
    out = {}
    for v in sorted(votes, key=lambda v: v.get('at', '')):
        out[(v.get('email') or '').lower()] = v
    return out


def tally(votes, people):
    """Count the votes of the people on the roster: {'yes', 'no', 'abstain', 'voted', 'of', 'rows'} where rows is the
    roll call in roster order: [{'name', 'email', 'seats', 'choice' ('' = not yet), 'at'}]."""
    last = latest(votes)
    rows, n = [], {c: 0 for c in CHOICES}
    for p in people:
        v = last.get(p['email'])
        choice = v['choice'] if v and v.get('choice') in CHOICES else ''
        if choice:
            n[choice] += 1
        rows.append(dict(p, choice=choice, at=(v or {}).get('at', '') if choice else ''))
    return dict(n, voted=sum(n.values()), of=len(people), rows=rows)


def outcome(t):
    """'passed', 'failed', or None while undecided."""
    if t['yes'] >= needed():
        return 'passed'
    if t['no'] >= needed():
        return 'failed'
    return None


def count_text(t):
    """'7-1' (yes-no), with abstentions when there are any: '7-1, 2 abstained'."""
    s = f"{t['yes']}-{t['no']}"
    return s + (f", {t['abstain']} abstained" if t['abstain'] else '')


def closes(opened, days=OPEN_DAYS):
    return (date.fromisoformat(opened[:10]) + timedelta(days=days)).isoformat()


def nice_date(d):
    """'2026-12-05' -> 'Dec 5, 2026' for text people read (the pages' nice_date style); anything else as it is."""
    try:
        d = date.fromisoformat(str(d)[:10])
        return f'{d:%b} {d.day}, {d.year}'
    except ValueError:
        return d or ''


def state(ballot, t, today=None):
    """What a ballot is now: 'open', 'passed', 'failed' (decided, waiting for an officer to record it), 'recorded',
    'expired' (30 days without a result) or 'cancelled' (the item was decided another way or withdrawn)."""
    s = ballot.get('status') or 'open'
    if s != 'open':
        return s
    if outcome(t):
        return outcome(t)
    if (today or date.today().isoformat()) > (ballot.get('closes') or '9999'):
        return 'expired'
    return 'open'


def summary(ballot, t, today=None):
    """One line for the Board page: '5 of 12 voted: 4 yes, 1 no. 7 decide it. Closes Dec 5, 2026.'"""
    st = state(ballot, t, today)
    voted = f"{t['voted']} of {t['of']} voted: {t['yes']} yes, {t['no']} no" + (f", {t['abstain']} abstain" if t['abstain'] else '')
    if st == 'open':
        return f"{voted}. {needed()} decide it. Closes {nice_date(ballot.get('closes', ''))}."
    if st in ('passed', 'failed', 'recorded'):
        res = ballot.get('result') or st
        return f"{'Passed' if res == 'passed' else 'Failed'} {count_text(t)} by email vote."
    if st == 'expired':
        return f"Closed after {OPEN_DAYS} days without a result ({voted}). Request a new vote or take it to a meeting."
    return f"Email vote cancelled ({voted})."
