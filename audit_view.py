"""Plain-language lines for the shared audit log, which every app writes through utils.log_audit_event.

Everything shown comes from what people typed (names, notes), so it is escaped here and the template
only marks the result safe.
"""
import re
from datetime import datetime

from flask import url_for
from markupsafe import Markup, escape

# action prefix -> (app label, badge colour)
APPS = {'EVENT': ('Events', 'primary'), 'FUNDS': ('Money', 'success'), 'MONEY': ('Money', 'success'),
        'BUDGET': ('Events', 'primary'), 'PLAN': ('Annual Plan', 'light'), 'EQUIP': ('Operations', 'warning'), 'INVOICE': ('Invoices', 'info'),
        'SHEET': ('Sheets', 'danger'), 'BOARD': ('Board', 'primary'), 'MOTION': ('Board', 'primary'), 'BALLOT': ('Board', 'primary'),
        'MINUTES': ('Board', 'primary'),
        'MEMBER': ('Members', 'warning'), 'MILESTONES': ('Members', 'warning'), 'POINTS': ('Members', 'warning'), 'AUTO': ('Members', 'warning'),
        'WP': ('Members', 'warning'),          # entries from when Members was Worker Points
        'RECORDS': ('Records', 'info'), 'RESULTS': ('Records', 'info'),
        'LOGIN': ('Login', 'secondary'), 'SIGNIN': ('Login', 'secondary'),
        'ACCESS': ('Admin', 'secondary'), 'ROLE': ('Admin', 'secondary'), 'USER': ('Admin', 'secondary'), 'PROGRAM': ('Admin', 'secondary')}


def app_of(action):
    return APPS.get(str(action or '').split('_')[0], ('Other', 'secondary'))


def _n(v, one, many=None):
    """'1 file', '3 files' from a count or a list; the bare word when there is no number."""
    n = len(v) if isinstance(v, (list, tuple)) else v
    try:
        n = int(n)
    except (TypeError, ValueError):
        return many or one + 's'
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _vote_item(item):
    """'budget:2027:#DRABC' -> 'the 2027 budget for #DRABC'; 'motion:MO-2026-03' -> 'motion MO-2026-03'."""
    kind, _, rest = str(item or '').partition(':')
    if kind == 'budget':
        year, _, code = rest.partition(':')
        return f'the {year} budget for {code}'
    return f'motion {rest}' if kind == 'motion' else (str(item) if item else 'an item')


def _money(v):
    try:
        return f'${abs(float(v)):,.2f}'
    except (TypeError, ValueError):
        return str(v)


def _pts(v):
    try:
        return f'{abs(float(v)):g} points'
    except (TypeError, ValueError):
        return f'{v} points'


def _link(endpoint, label, **kw):
    try:
        return Markup('<a href="{}">{}</a>').format(url_for(endpoint, **kw), label)
    except Exception:
        return escape(label)


def plain(log):
    """describe() as plain text, for the "What happened" column of the Audit tab (the sheet must read on its own)."""
    try:
        return ' '.join(re.sub(r'<[^>]+>', '', str(describe(log))).replace('&amp;', '&').replace('&#39;', "'").replace('&#34;', '"')
                        .replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').split())
    except Exception:
        return ''


def describe(log):
    """One line, already escaped: who did what to which record. Never raises: an entry this can't make sense of
    (hand-edited in the sheet, or written by an older version) gets the plain "who, action" line."""
    try:
        return _describe(log)
    except Exception:
        log = log if isinstance(log, dict) else {}
        return Markup('<b>{}</b> {}').format(log.get('user') or 'Unknown', str(log.get('action') or 'entry').replace('_', ' ').lower())


def _describe(log):
    a, d = str(log.get('action') or ''), log.get('details') or {}
    who = Markup('<b>{}</b>').format(log.get('user') or 'Unknown')
    if not isinstance(d, dict):
        d = {}
    g = d.get
    if a == 'SHEET_EDITED':
        return Markup('{} edited the {} sheet directly (not through the apps). <a href="{}" target="_blank" rel="noopener">See what changed</a> (File &gt; Version history).').format(
            who, g('sheet', 'a'), g('link', '#'))
    if a == 'EVENT_CREATED':
        return Markup('{} registered {} {} ({})').format(who, g('event_name', ''), g('custom_tag') or g('unique_code', ''), g('program_code', ''))
    if a == 'EVENT_DELETED':
        return Markup('{} deleted {} ({})').format(who, g('event_name', ''), g('program_code', ''))
    if a == 'EVENT_EDITED' and isinstance(g('date'), list):     # the event page's "Use that date" (from MotorsportReg)
        old, new = (g('date') + ['', ''])[:2]
        return Markup('{} moved {} from {} to {}, the MotorsportReg date').format(who, g('event_code', ''), old, new)
    if a == 'EVENT_EDITED' and isinstance(g('after'), dict):     # links and dates set from MotorsportReg
        o, u = g('before') or {}, g('after') or {}
        diffs = []
        for k in u:
            if u.get(k) == o.get(k):
                continue
            if k == 'msr_url':
                diffs.append('linked its MotorsportReg page' if u.get(k) else 'unlinked its MotorsportReg page')
            elif k == 'cancelled':
                diffs.append('marked it cancelled' if u.get(k) else 'marked it not cancelled')
            else:
                diffs.append(f"{k.replace('_', ' ')} {o.get(k) or 'none'} to {u.get(k) or 'none'}")
        return Markup('{} updated {} from MotorsportReg: {}').format(who, g('event_name') or g('event_code', ''), '; '.join(diffs) or 'no change')
    if a == 'EVENT_EDITED':
        o, u = g('original') or {}, g('updated') or {}
        diffs = [f"{k.replace('_', ' ')}: '{o.get(k)}' to '{u.get(k)}'"
                 for k in ('program_code', 'date', 'event_name', 'creator_name', 'custom_tag') if o.get(k) != u.get(k)]
        return Markup('{} changed {}: {}').format(who, u.get('event_name') or o.get('event_name', ''), '; '.join(diffs) or 'no changes')
    if a == 'BALLOT_VOTE':
        return Markup('{} voted {} on {}{}').format(who, str(g('choice', '')).capitalize(), _vote_item(g('item')),
                                                    Markup(' (by {})').format(g('via')) if g('via') else '')
    if a == 'BALLOT_DECIDED':
        return Markup('The board\'s email vote on {} is decided: {} {}').format(_vote_item(g('item')), g('result', ''), g('count', ''))
    if a == 'BALLOT_REMINDED':
        return Markup('{} sent a reminder to {} who had not voted').format(who, _n(g('to'), 'director'))
    if a == 'MINUTES_UPLOADED':
        return Markup('{} uploaded minutes {}: {}').format(who, 'and posted them' if g('approved') else 'as a draft', g('file', ''))
    if a == 'MINUTES_PUBLISHED':
        return Markup('{} posted the approved minutes {}').format(who, g('file', ''))
    if a == 'MINUTES_DRAFT_DELETED':
        return Markup('{} deleted the draft minutes {}').format(who, g('file', ''))
    if a == 'MEMBER_LIST_LOADED':
        return Markup('{} loaded the SCCA member list {}: {} on it').format(who, g('file', ''), _n(g('on_list'), 'current member'))
    if a == 'MEMBER_ADDED':
        return Markup('{} added member {}').format(who, _link('members.member', g('member', ''), mid=g('member', '')))
    if a == 'MILESTONES_SAVED':
        return Markup('{} saved the member milestones for {} ({})').format(who, g('month', ''), _n(g('names'), 'name'))
    if a == 'POINTS_ENTERED':
        ev = g('event') if g('event') not in (None, '', 'none') else 'a volunteer job'
        return Markup('{} entered {} for {} at {}').format(who, _pts(g('points')), _n(g('rows'), 'member'), ev)
    if a == 'POINTS_USED':
        return Markup('{} used {} from member {} for {}').format(who, _pts(g('points')), _link('members.member', g('member', ''), mid=g('member', '')), g('for', ''))
    if a == 'POINTS_FORFEITED':
        return Markup('{} deleted {} from {} who are not current').format(who, _pts(g('points')), _n(g('members'), 'member'))
    if a == 'POINTS_BOOKS_EMAILED':
        return Markup('{} emailed the treasurer the worker points to enter in QuickBooks ({})').format(who, _n(g('entries'), 'entry', 'entries'))
    if a == 'POINTS_RECONCILED':
        return Markup('{} marked {} as entered in QuickBooks ({})').format(who, _n(g('entries'), 'points entry', 'points entries'), g('ref', ''))
    if a == 'AUTO_ADD_SAVED':
        return Markup('{} saved the list of people who get points added every month ({})').format(who, _n(g('people'), 'person', 'people'))
    if a == 'RECORDS_CORRECTED' and isinstance(g('diff'), list) and g('diff'):
        more = f" and {_n(g('more'), 'more change')}" if g('more') else ''
        return Markup('{} corrected {}: {}{}').format(who, g('label') or g('what') or 'the records', ', '.join(map(str, g('diff'))), more)
    if a == 'RECORDS_CORRECTED':
        return Markup('{} corrected the records: {} ({})').format(who, g('what', ''), _n(g('changes'), 'change'))
    if a == 'RECORDS_UNDONE':
        try:
            on = datetime.strptime(str(g('made_on'))[:10], '%Y-%m-%d')
            on = f'{on:%b} {on.day}, {on.year}'
        except ValueError:
            on = g('made_on') or ''
        made = ' '.join(x for x in (f"by {g('made_by')}" if g('made_by') else '', f'on {on}' if on else '') if x)
        return Markup('{} undid a correction to {}: {}{}{}').format(
            who, g('label') or 'the records', ', '.join(map(str, g('diff') or [])) or 'a change', f' (made {made})' if made else '',
            '; what it removed comes back with the next results rebuild' if g('restored') is False else '')
    if a == 'RESULTS_ADDED':
        return Markup('{} added results for {} ({} {}): {}').format(who, g('event', ''), g('program', ''), g('date', ''), _n(g('files'), 'file'))
    if a == 'FUNDS_REQUESTED':
        return Markup('{} filed {}: {} to {} {}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')), _money(g('net')), g('payee', ''), g('event_code', ''))
    if a == 'FUNDS_PAID':
        return Markup('{} marked {} paid: {} by {}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')), _money(g('amount')), g('ref', ''))
    if a == 'FUNDS_REJECTED':
        return Markup('{} rejected {}: {}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')), g('note', ''))
    if a == 'FUNDS_APPROVED':
        return Markup('{} approved {} as {}{}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')), g('role', ''),
                                                       f" ({g('note')})" if g('note') else '')
    if a == 'FUNDS_RECEIPTS':
        return Markup('{} added {} receipt file(s) to {}').format(who, g('files', ''), _link('fr.detail', g('id', ''), req_id=g('id', '')))
    if a == 'INVOICE_CREATED':
        return Markup('{} created invoice {} for {}: {}').format(who, _link('inv.detail', g('id', ''), inv_id=g('id', '')), g('bill_name', ''), _money(g('total')))
    if a == 'INVOICE_PAID':
        return Markup('{} marked invoice {} paid: {}').format(who, _link('inv.detail', g('id', ''), inv_id=g('id', '')), _money(g('amount')))
    if a == 'INVOICE_VOID':
        return Markup('{} voided invoice {}: {}').format(who, _link('inv.detail', g('id', ''), inv_id=g('id', '')), g('note', ''))
    if a == 'INVOICE_REOPENED':
        return Markup('{} reopened invoice {}').format(who, _link('inv.detail', g('id', ''), inv_id=g('id', '')))
    if a == 'EQUIP_ITEM_SAVED':
        target = g('name', '') if str(g('id', '')).startswith('(') else _link('equip.item', g('name', ''), item_id=g('id', ''))   # "(estimates)" is not an item
        return Markup('{} {} {}').format(who, 'added' if g('new') else 'updated', target)
    if a == 'EQUIP_PLACE_SAVED':
        return Markup('{} updated storage place {}').format(who, _link('equip.place', g('name', ''), place_id=g('id', '')))
    if a == 'EQUIP_TASK_SAVED':
        return Markup('{} saved the Operations to-do "{}" (due {})').format(who, g('task', ''), g('due', '') or 'no date')
    if a == 'EQUIP_TASK_DONE':
        return Markup('{} marked done: {}{}').format(who, g('task', ''), f" (next due {g('next')})" if g('next') else '')
    if a == 'EQUIP_PLAN_SETTINGS':
        return Markup('{} changed how soon an item has to be due to go in the Operations budget, on the {}').format(who, _link('equip.replacement', 'replacement plan'))
    if a == 'EQUIP_PLAN_BOARD':
        after = g('after', {}) or {}
        try:
            amount = f"${float(after.get('monthly')):,.0f} a month"
        except (TypeError, ValueError):
            amount = 'an amount'
        return Markup("{} recorded what the board puts in the reserve fund on the {}: {}").format(who, _link('equip.replacement', 'replacement plan'), amount)
    if a == 'EQUIP_PLAN_NOTE':
        return Markup('{} {} the committee note on the {}').format(who, 'changed' if g('after') else 'removed', _link('equip.replacement', 'replacement plan'))
    if a == 'EQUIP_DOCS_FOLDER_MADE':
        return Markup('{} made a Drive folder for the documents of {}').format(who, g('name', ''))
    if a == 'EQUIP_DOCS_UPLOADED':
        return Markup('{} saved the document "{}" for {} ({})').format(who, g('doc', ''), g('for', ''), _n(g('files'), 'file'))
    if a == 'FUNDS_REOPENED':
        return Markup('{} reopened {}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')))
    if a == 'BUDGET_SAVED':
        return Markup('{} saved the budget for {} ({} lines)').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('lines', ''))
    if a == 'BUDGET_APPROVED':
        return Markup('{} recorded board approval of the budget for {} on {}').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('approved', ''))
    if a == 'BUDGET_DRAFT':
        return Markup('{} put the budget for {} back in draft{}').format(
            who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')),
            Markup(' (it was approved on {})').format(g('was_approved')) if g('was_approved') else '')
    if a == 'BUDGET_UNAPPROVED':
        return Markup('{} cleared the board approval of the budget for {}').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')))
    if a == 'PLAN_LINKED':
        return Markup('{} tied {} to the {} {} plan line "{}"').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('year', ''),
                                                                      _link('plan.detail', g('program', ''), year=g('year', ''), program=g('program', '')), g('item', ''))
    if a == 'PLAN_SAVED':
        return Markup('{} saved the {} {} plan ({} lines, {})').format(who, g('year', ''), _link('plan.detail', g('program', ''), year=g('year', ''), program=g('program', '')),
                                                                     g('lines', ''), _money(g('net')) if (g('net') or 0) >= 0 else '-' + _money(g('net')))
    if a == 'PLAN_REVIEWED':
        return Markup('{} hid a books check item: {}{}').format(who, g('detail', ''), Markup(' ({})').format(g('note')) if g('note') else '')
    if a.endswith('_IMPORTED'):
        return Markup('{} imported {}').format(who, g('summary', ''))
    if a in ('LOGIN_SUCCESS', 'LOGIN_FAILURE'):
        return Markup('{} from {}').format('Login' if a == 'LOGIN_SUCCESS' else 'Failed login', g('remote_addr', 'unknown address'))
    return Markup('{} {}').format(who, a.replace('_', ' ').lower())
