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

Approvals come from an emailed link sent to the official's address in the read-only officials list (fr.py). A
director who files a request for their own program, signed in as themselves, has approved it by filing it.
"""
import re

SECOND_SIGNER_LIMIT = 2500.00
OVERRUN = 1.10
ROLES = {'chair': 'Program director', 'second': 'Second signer (RE or ARE)', 'board': 'Board (confirmed by the RE or ARE)'}


def parse(text):
    out = []
    for part in (text or '').split(';'):
        bits = [b.strip() for b in part.split('|')] + ['', '', '', '']
        if bits[0] in ROLES:
            out.append({'role': bits[0], 'name': bits[1], 'date': bits[2], 'note': bits[3]})
    return out


def add(text, role, name, day, note=''):
    clean = lambda s: re.sub(r'[|;]', ',', str(s or '')).strip()  # noqa: E731
    return '; '.join(filter(None, [(text or '').strip(), '|'.join([role, clean(name), str(day), clean(note)])]))


def _words(name):
    return re.findall(r'[a-z]+', (name or '').lower())


def same_person(a, b):
    """Same last name and the first names agree ("Matt Sample" and "Matthew Sample")."""
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2 or wa[-1] != wb[-1]:
        return False
    return wa[0].startswith(wb[0]) or wb[0].startswith(wa[0])


def is_points(r):
    """A worker points redemption: already approved in the app when the points were taken off."""
    return r.get('event_name') == 'Worker points redemption'


def review(r, budget_status=None, budget_expense=None, spent=0.0, scope='event'):
    """What the request still needs: [{'key', 'label', 'done', 'by'}] in order.

    budget_status: 'approved', 'changed', 'budget' or None (the budget it is paid against, see bud._status)
    budget_expense: the budgeted expense total; spent: QuickBooks expenses so far plus other open requests
    scope: 'event' (the event's budget), 'program' (a request not tied to an event: its program's budget for the
    year) or 'ops' (Operations, whose annual plan is its budget, and 2026 and earlier, when a program's annual plan
    covered its spending outside events; no overrun check)
    """
    got = {}
    for a in parse(r.get('approvals')):
        got.setdefault(a['role'], a)
    by = lambda a: f"{a['name']}, {a['date']}" + (f" ({a['note']})" if a['note'] else '')  # noqa: E731
    items = []

    def item(key, label, done, who=''):
        items.append({'key': key, 'label': label, 'done': bool(done), 'by': who})

    net = r.get('net') or 0
    if not is_points(r):
        item('receipt', 'Receipt or invoice attached', r.get('receipts_url'))
        c = got.get('chair')
        item('chair', 'Approved by the program director', c, by(c) if c else '')
        board = got.get('board')
        what = {'event': 'Event budget', 'program': 'Program budget', 'ops': 'Operations budget' if r.get('program') == 'OPS' else 'Annual plan'}[scope]
        ok = budget_status == 'approved'
        label = {'changed': f'{what} approved by the board (it was changed after approval)',
                 None: f'{what} approved by the board (no budget yet)'}.get(budget_status, f'{what} approved by the board')
        item('budget', label, ok or board, by(board) if board and not ok else '')
        if ok and budget_expense is not None and scope != 'ops':
            cap = round(budget_expense * OVERRUN, 2)
            within = spent + net <= cap + 0.005
            item('overrun', f'Within 110% of budgeted expenses (${spent + net:,.2f} of ${cap:,.2f})', within or board,
                 by(board) if board and not within else '')
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
        how = (f"{ref} for ${amt:,.2f}" if ref else f"${amt:,.2f}") + (f" on {r['paid_date']}" if r.get('paid_date') else '')
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
