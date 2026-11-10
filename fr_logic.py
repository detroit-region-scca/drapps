"""Who has to approve a funds request before the treasurer pays it, from the region policies. No I/O here.

Policy (Detroit Region policies, Financial Policy II.A, and Bylaws Article VIII):
  A.3   no expenses are paid until the event budget is approved
  A.6   the chair needs board approval to exceed the overall expense budget by more than 10%
  A.13  the event chair approves each expense, with a bill or receipt
  A.18, A.20  other reimbursements need a board-approved budget first (for non-event spending, the program budget;
              Operations has no events and its annual plan is its budget)
  VIII  a check over the board's limit ($2,500) needs a second signer: the RE or ARE

Approvals are kept on the request as "role|name|date|note" entries joined by "; ".
A board approval (with the meeting date) covers both budget items.
A "ready|DRSCCA Apps|date|" entry records the day a request first had everything. After that the overrun check counts
as passed: once the treasurer enters the bill in QuickBooks, the sync adds it to the event's spending and the open
request would otherwise be counted twice (FR-2026-113: $9,600 in QuickBooks plus the same $9,600 still open).

Approvals come from an emailed link sent to the official's address in the read-only officials list (fr.py).
Nobody approves their own money: whoever filed a request, or is named on it as submitter or payee, cannot give its
director or second signer approval (on_request, who_approves). When that is the director, the RE or ARE approves in
their place; a director filing for their own program no longer approves it by filing.
"""
import re

SECOND_SIGNER_LIMIT = 2500.00
OVERRUN = 1.10
ROLES = {'chair': 'Program director', 'second': 'Second signer (RE or ARE)', 'board': 'Board (recorded by the RE, ARE, secretary or treasurer)'}


def parse(text):
    out = []
    for part in (text or '').split(';'):
        bits = [b.strip() for b in part.split('|')] + ['', '', '', '']
        if bits[0] in ROLES:
            out.append({'role': bits[0], 'name': bits[1], 'date': bits[2], 'note': bits[3]})
    return out


def ready_on(text):
    """The day the request first had every approval ('ready' entry), or ''."""
    for part in (text or '').split(';'):
        bits = [b.strip() for b in part.split('|')] + ['', '', '']
        if bits[0] == 'ready':
            return bits[2]
    return ''


def add(text, role, name, day, note=''):
    clean = lambda s: re.sub(r'[|;]', ',', str(s or '')).strip()  # noqa: E731
    return '; '.join(filter(None, [(text or '').strip(), '|'.join([role, clean(name), str(day), clean(note)])]))


def nice_date(d):
    """'2026-11-09' -> 'Nov 9, 2026' for text people read (as app.nice_date, kept here so this module needs no app);
    anything else is given back as it is."""
    from datetime import datetime
    try:
        d = datetime.strptime(str(d)[:10], '%Y-%m-%d')
        return f'{d.strftime("%b")} {d.day}, {d.year}'
    except ValueError:
        return d or ''


def _words(name):
    return re.findall(r'[a-z]+', (name or '').lower())


def same_person(a, b):
    """Same last name and the first names agree ("Matt Sample" and "Matthew Sample")."""
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2 or wa[-1] != wb[-1]:
        return False
    return wa[0].startswith(wb[0]) or wb[0].startswith(wa[0])


def on_request(r, name='', emails=()):
    """True when this person is the payee or the submitter of the request: one of their addresses filed it or is on it
    as submitter or payee, or their name is on it exactly (ignoring case and punctuation). Not a loose match: two
    relatives with like names are two people, and a loose match could leave a request nobody may approve."""
    mine = {(e or '').strip().lower() for e in emails} - {''}
    theirs = {(r.get(k) or '').strip().lower() for k in ('filed_email', 'submitter_email', 'payee_email')}
    if mine & theirs:
        return True
    names = [r.get(k) or '' for k in ('submitter_name', 'payee_name', 'entered_by')]
    me = _words(name)
    return bool(me) and any(_words(n) == me for n in names)


def who_approves(r, director, signers):
    """Who may give each approval, leaving out anyone who is the payee or submitter (on_request).
    director: {'name', 'mail', 'emails'} or None; signers: the RE and ARE, the same shape (mail: where their link goes,
    emails: every address that is theirs). Returns {'chair': [...], 'second': [...], 'stand_in': bool, 'note': {key: text}}.
    stand_in: the director is on the request, so the RE or ARE gives the director approval. note: a plain line for an
    approval that works differently here, or that nobody can give."""
    free = [s for s in signers if not on_request(r, s['name'], s.get('emails', ()))]
    out = {'chair': [director] if director and director.get('mail') else [], 'second': free, 'stand_in': False, 'note': {}}
    both = 'the RE and the ARE are both the payee or submitter of this request, so neither can approve it. Someone else has to file it'
    if director and on_request(r, director['name'], director.get('emails', ())):
        out['chair'], out['stand_in'] = free, True
        out['note']['chair'] = ('the director is the payee or submitter, so the RE or ARE approves instead' if free else
                                both if signers else 'the director is the payee or submitter and no RE or ARE is in the officials list')
    if signers and not free:
        out['note']['second'] = both
    elif len(free) < len(signers):
        out['note']['second'] = f"{' or '.join(s['name'] for s in free)} only: the other is the payee or submitter"
    return out


def explain(items, plan):
    """Add who_approves' notes to the labels of the approvals still missing."""
    for i in items:
        if not i['done'] and i['key'] in plan['note']:
            i['label'] += f" ({plan['note'][i['key']]})"
    return items


_COMPANY = {'llc', 'inc', 'co', 'corp', 'ltd', 'the', 'company', 'and'}


def same_payee(a, b):
    """A loose match of two payee names, a person or a business: same_person, or one name's words are all in the other
    ("Example Rentals" and "Example Rentals LLC", "Sample, Sam" and "Sam Sample")."""
    if same_person(a, b):
        return True
    wa, wb = set(_words(a)) - _COMPANY, set(_words(b)) - _COMPANY
    return bool(wa and wb and (wa <= wb or wb <= wa))


def billed(others, bills, code=''):
    """The open requests among `others` whose bill is already in QuickBooks, as a set of ids: an unpaid bill of the same
    amount to the cent whose vendor matches the payee (same_payee) and which is booked to the same place: for an
    event (code), a line on that event's QuickBooks project; for a program's own spending (no code), no line on any
    event project. Each bill is used once. Such a request is already in the QuickBooks spending being checked, so the
    overrun check must not add it again. Anything less certain is not matched, and the request counts in full."""
    def same_place(b):
        lines = str(b.get('lines') or '')
        return code in lines if code else '#DR' not in lines
    left, out = list(bills or []), set()
    for x in sorted(others, key=lambda x: x.get('id') or ''):
        cents = round((x.get('net') or 0) * 100)
        hit = next((b for b in left if round((b.get('total') or 0) * 100) == cents
                    and (b.get('vendor') or '').strip() and same_payee(b['vendor'], x.get('payee_name')) and same_place(b)), None)
        if hit:
            left.remove(hit)
            out.add(x['id'])
    return out


def is_points(r):
    """A worker points redemption: already approved in the app when the points were taken off."""
    return r.get('event_name') == 'Worker points redemption'


def review(r, budget_status=None, budget_expense=None, spent=0.0, scope='event', board_vote=None):
    """What the request still needs: [{'key', 'label', 'done', 'by'}] in order.

    budget_status: 'approved', 'changed', 'budget' or None (the budget it is paid against, see bud_store.status)
    budget_expense: the budgeted expense total; spent: QuickBooks expenses so far plus other open requests
    scope: 'event' (the event's budget), 'program' (a request not tied to an event: its program's budget for the
    year) or 'ops' (Operations, whose annual plan is its budget, and 2026 and earlier, when a program's annual plan
    covered its spending outside events; no overrun check)
    board_vote: the passed board motion that approved this request ({'id', 'decided', 'how', 'vote'}), recorded on the
    Board page after the vote. It stands in for a missing budget or an overrun. (Older requests may carry a 'board'
    entry in their approvals; those still count, but nothing adds new ones.)
    """
    got = {}
    for a in parse(r.get('approvals')):
        got.setdefault(a['role'], a)
    by = lambda a: f"{a['name']}, {nice_date(a['date'])}" + (f" ({a['note']})" if a['note'] else '')  # noqa: E731
    items = []

    def item(key, label, done, who=''):
        items.append({'key': key, 'label': label, 'done': bool(done), 'by': who})

    net = r.get('net') or 0
    if not is_points(r):
        item('receipt', 'Receipt or invoice attached', r.get('receipts_url'))
        c = got.get('chair')
        item('chair', 'Approved by the program director', c, by(c) if c else '')
        board = got.get('board')
        if board_vote:
            board = {'name': f"Board, motion {board_vote['id']}", 'date': board_vote.get('decided', ''),
                     'note': ', '.join(x for x in (board_vote.get('how'), board_vote.get('vote')) if x)}
        what = {'event': 'Event budget', 'program': 'Program budget', 'ops': 'Operations budget' if r.get('program') == 'OPS' else 'Annual plan'}[scope]
        ok = budget_status == 'approved'
        label = {'changed': f'{what} approved by the board (it was changed after approval)',
                 None: f'{what} approved by the board (no budget yet)'}.get(budget_status, f'{what} approved by the board')
        item('budget', label, ok or board, by(board) if board and not ok else '')
        if ok and budget_expense is not None and scope != 'ops':
            cap = round(budget_expense * OVERRUN, 2)
            within = spent + net <= cap + 0.005
            frozen = ready_on(r.get('approvals'))
            item('overrun', f'Within 110% of budgeted expenses (${spent + net:,.2f} of ${cap:,.2f})', within or board or frozen,
                 by(board) if board and not within else f'within budget when every approval was in, {nice_date(frozen)}' if frozen and not within else '')
    if net > SECOND_SIGNER_LIMIT:
        s = got.get('second')
        item('second', f'Second signer, over ${SECOND_SIGNER_LIMIT:,.0f} (RE or ARE)', s, by(s) if s else '')
    return items


def ready(items):
    return all(i['done'] for i in items)


def waiting_on(items):
    """Short names for what is missing, for the list and emails."""
    names = {'receipt': 'receipt', 'chair': 'director approval', 'budget': 'board (budget)', 'overrun': 'board (over budget)',
             'second': 'second signer'}
    return [names[i['key']] for i in items if not i['done']]


def closed_message(r, link=''):
    """The email to the person who filed a request when it is paid or rejected: (subject, body), or None when they
    did not ask to be told (the 'Email me' box on the form) or left no address."""
    if (r.get('notify') or '').lower() != 'yes' or not (r.get('submitter_email') or '').strip():
        return None
    what = f"{r['id']}: {r.get('payee_name') or 'payee not given'} ${float(r.get('net') or 0):,.2f}"
    for_ = r.get('event_name') or f"program {r.get('program', '')}"
    if r['status'] == 'Paid':
        ref, amt = r.get('paid_ref') or '', float(r.get('paid_amount') or r.get('net') or 0)
        how = (f"{ref} for ${amt:,.2f}" if ref else f"${amt:,.2f}") + (f" on {nice_date(r['paid_date'])}" if r.get('paid_date') else '')
        to = ('it is on its way to you' if r.get('method') == 'Reimburse me' else
              'the region card was charged' if 'card' in ref.lower() else f"it goes to {r.get('payee_name') or 'the payee'}")
        body = [f"Your funds request {what} ({for_}) has been paid: {how}; {to}."]
        subject = f"Funds request {r['id']} paid"
    elif r['status'] == 'Rejected':
        body = [f"Your funds request {what} ({for_}) was rejected.", '', f"Why: {r.get('closed_note') or 'no reason given'}",
                '', 'Questions go to treasurer@drscca.org.']
        subject = f"Funds request {r['id']} rejected"
    else:
        return None
    if link:
        body += ['', link]
    return subject, '\n'.join(body)


def motion_for(rid, motions):
    """The board motion made for a funds request (its link points at the request), newest first: passed if any."""
    mine = [m for m in motions if (m.get('link') or '').rstrip('/').endswith(f'/funds/{rid}')]
    live = [m for m in mine if m.get('status') != 'withdrawn']
    return next((m for m in live if m['status'] == 'passed'), None) or next(reversed(live), None) or next(reversed(mine), None)
