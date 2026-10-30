"""Who has to approve a funds request before the treasurer pays it, from the region policies. No I/O here.

Policy (Detroit Region policies, Financial Policy II.A, and Bylaws Article VIII):
  A.3   no expenses are paid until the event budget is approved
  A.6   the chair needs board approval to exceed the overall expense budget by more than 10%
  A.13  the event chair approves each expense, with a bill or receipt
  A.18, A.20  other reimbursements need a board-approved budget first (for non-event spending, the annual plan)
  VIII  a check over the board's limit ($2,500) needs a second signer: the RE or ARE

Approvals are kept on the request as "role|name|date|note" entries joined by "; ".
A board approval (with the meeting date) covers both budget items.

Every approval comes from an emailed link sent to the official's address in the read-only officials list (fr.py). The
app login is one shared password and a typed name, so nothing typed into the app (a name, an approval) can stand in
for one: a director's own request is approved from the director's inbox like anyone else's.
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
    """Same last name and the first names agree ("Matt Wolfe" and "Matthew Wolfe")."""
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2 or wa[-1] != wb[-1]:
        return False
    return wa[0].startswith(wb[0]) or wb[0].startswith(wa[0])


def is_points(r):
    """A worker points redemption: already approved in the app when the points were taken off."""
    return r.get('event_name') == 'Worker points redemption'


def review(r, budget_status=None, budget_expense=None, spent=0.0, plan_ok=False):
    """What the request still needs: [{'key', 'label', 'done', 'by'}] in order.

    budget_status: 'approved', 'changed', 'budget' or None (the event's budget, see bud._status)
    budget_expense: the budgeted expense total; spent: QuickBooks expenses so far plus other open requests
    plan_ok: the program has an annual plan for the year (non-event requests)
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
        if r.get('event_code'):
            ok = budget_status == 'approved'
            label = {'changed': 'Event budget approved by the board (it was changed after approval)',
                     None: 'Event budget approved by the board (no budget yet)'}.get(budget_status, 'Event budget approved by the board')
            item('budget', label, ok or board, by(board) if board and not ok else '')
            if ok and budget_expense is not None:
                cap = round(budget_expense * OVERRUN, 2)
                within = spent + net <= cap + 0.005
                item('overrun', f'Within 110% of budgeted expenses (${spent + net:,.2f} of ${cap:,.2f})', within or board,
                     by(board) if board and not within else '')
        else:
            item('budget', 'Program has a board-approved annual plan', plan_ok or board, by(board) if board and not plan_ok else '')
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
