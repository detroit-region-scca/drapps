"""Plain-language lines for the shared audit log, which every app writes through utils.log_audit_event.

Everything shown comes from what people typed (names, notes), so it is escaped here and the template
only marks the result safe.
"""
import re

from flask import url_for
from markupsafe import Markup, escape

# action prefix -> (app label, badge colour)
APPS = {'EVENT': ('Events', 'primary'), 'WP': ('Worker Points', 'warning'), 'FUNDS': ('Funds Requests', 'success'),
        'BUDGET': ('Events', 'primary'), 'PLAN': ('Annual Plan', 'light'), 'EQUIP': ('Operations', 'warning'), 'LOGIN': ('Login', 'secondary')}


def app_of(action):
    return APPS.get(action.split('_')[0], ('Other', 'secondary'))


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
    """One line, already escaped: who did what to which record."""
    a, d = log.get('action', ''), log.get('details') or {}
    who = Markup('<b>{}</b>').format(log.get('user') or 'Unknown')
    if not isinstance(d, dict):
        d = {}
    g = d.get
    if a == 'EVENT_CREATED':
        return Markup('{} registered {} {} ({})').format(who, g('event_name', ''), g('custom_tag') or g('unique_code', ''), g('program_code', ''))
    if a == 'EVENT_DELETED':
        return Markup('{} deleted {} ({})').format(who, g('event_name', ''), g('program_code', ''))
    if a == 'EVENT_EDITED':
        o, u = g('original') or {}, g('updated') or {}
        diffs = [f"{k.replace('_', ' ')}: '{o.get(k)}' to '{u.get(k)}'"
                 for k in ('program_code', 'date', 'event_name', 'creator_name', 'custom_tag') if o.get(k) != u.get(k)]
        return Markup('{} changed {}: {}').format(who, u.get('event_name') or o.get('event_name', ''), '; '.join(diffs) or 'no changes')
    if a == 'WP_EVENT_POSTED':
        return Markup('{} posted {} for {} workers at {}').format(who, _pts(g('points')), g('workers', ''), g('event_code', ''))
    if a == 'WP_ALLOCATIONS_POSTED':
        return Markup('{} posted the {} monthly allocations ({})').format(who, g('month', ''), _pts(g('points')))
    if a == 'WP_REDEMPTION_REQUESTED':
        return Markup('{} requested {} for member {} ({})').format(who, _pts(g('points')), g('member', ''), _link('wp.redeem_detail', g('id', ''), rid=g('id', '')))
    if a == 'WP_REDEEMED':
        extra = Markup(', {}').format(_link('fr.detail', g('funds_request'), req_id=g('funds_request'))) if g('funds_request') else ''
        rid = _link('wp.redeem_detail', g('id'), rid=g('id')) if g('id') else g('note', '')
        return Markup('{} approved {} off member {} ({}{})').format(who, _pts(g('points')), g('member', ''), rid, extra)
    if a == 'WP_REDEMPTION_DECLINED':
        return Markup('{} declined {}: {}').format(who, _link('wp.redeem_detail', g('id', ''), rid=g('id', '')), g('note', ''))
    if a == 'WP_FORFEIT':
        return Markup('{} forfeited {} for member {}').format(who, _pts(g('points')), g('member', ''))
    if a == 'WP_MEMBER_ADDED':
        return Markup('{} added member {} {}').format(who, g('member', ''), g('name', ''))
    if a == 'WP_QBO_BALANCE':
        return Markup('{} set the QuickBooks reserve balance to {} as of {}').format(who, g('qbo', ''), g('as_of', ''))
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
    if a == 'EQUIP_ITEM_SAVED':
        return Markup('{} {} {}').format(who, 'added' if g('new') else 'updated', _link('equip.item', g('name', ''), item_id=g('id', '')))
    if a == 'EQUIP_PLACE_SAVED':
        return Markup('{} updated storage place {}').format(who, _link('equip.place', g('name', ''), place_id=g('id', '')))
    if a == 'EQUIP_TASK_SAVED':
        return Markup('{} saved the Operations to-do "{}" (due {})').format(who, g('task', ''), g('due', '') or 'no date')
    if a == 'EQUIP_TASK_DONE':
        return Markup('{} marked done: {}{}').format(who, g('task', ''), f" (next due {g('next')})" if g('next') else '')
    if a == 'EQUIP_SIGNIN':
        return Markup('{} signed in to make changes in Operations').format(who)
    if a == 'FUNDS_REOPENED':
        return Markup('{} reopened {}').format(who, _link('fr.detail', g('id', ''), req_id=g('id', '')))
    if a == 'BUDGET_SAVED':
        return Markup('{} saved the budget for {} ({} lines)').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('lines', ''))
    if a == 'BUDGET_APPROVED':
        return Markup('{} recorded board approval of the budget for {} on {}').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('approved', ''))
    if a == 'BUDGET_UNAPPROVED':
        return Markup('{} cleared the board approval of the budget for {}').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')))
    if a == 'PLAN_LINKED':
        return Markup('{} tied {} to the {} {} plan line "{}"').format(who, _link('bud.detail', g('event_code', ''), code=g('event_code', '')), g('year', ''),
                                                                      _link('plan.detail', g('program', ''), year=g('year', ''), program=g('program', '')), g('item', ''))
    if a == 'PLAN_SAVED':
        return Markup('{} saved the {} {} plan ({} lines, {})').format(who, g('year', ''), _link('plan.detail', g('program', ''), year=g('year', ''), program=g('program', '')),
                                                                     g('lines', ''), _money(g('net')) if (g('net') or 0) >= 0 else '-' + _money(g('net')))
    if a == 'PLAN_REVIEWED':
        return Markup('{} marked a books check item as looked at: {}{}').format(who, g('detail', ''), Markup(' ({})').format(g('note')) if g('note') else '')
    if a.endswith('_IMPORTED'):
        return Markup('{} imported {}').format(who, g('summary', ''))
    if a in ('LOGIN_SUCCESS', 'LOGIN_FAILURE'):
        return Markup('{} from {}').format('Login' if a == 'LOGIN_SUCCESS' else 'Failed login', g('remote_addr', 'unknown address'))
    return Markup('{} {}').format(who, a.replace('_', ' ').lower())
