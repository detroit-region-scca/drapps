"""Operations at a glance: everything in the Operations records on one page (every recurring cost, storage place,
open to-do, vehicle and trailer, the replacement plan, and all other property). Built from the same data as the
Operations pages with fpdf2; the text shrinks until it all fits on one Letter page. No other I/O."""
import os
from datetime import date

import equip_store as E

NAVY, GREY, RED = (20, 40, 80), (105, 105, 105), (180, 30, 30)
MARGIN, BOTTOM = 32, 792 - 30


def money(v, places=0):
    if v is None:
        return ''
    return ('-' if v < 0 else '') + f"${abs(v):,.{places}f}"


def every(months):
    return 'monthly' if months == 1 else 'yearly' if months == 12 else f'every {months:g} months'


def _layout(p, items, places, tasks, today, region, k):
    """Draw everything at text scale k. Returns the lowest y used (to check it fits)."""
    L, W, TOP = MARGIN, 612 - 2 * MARGIN, MARGIN
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'drscca-logo.png')
    if os.path.exists(logo):
        p.image(logo, x=L, y=TOP, h=34)
    tx = L + 34 * 700 / 471 + 9
    p.set_text_color(*NAVY)
    p.set_font('Helvetica', 'B', 15)
    p.set_xy(tx, TOP + 1)
    p.cell(380, 17, f'{region}: Operations at a glance')
    p.set_font('Helvetica', '', 8)
    p.set_text_color(*GREY)
    p.set_xy(tx, TOP + 19)
    p.cell(420, 10, f'As of {today:%B %-d, %Y}. Everything in the Operations records: costs, storage, to-dos, property and the replacement plan.')
    p.set_draw_color(*NAVY)
    p.set_line_width(1)
    p.line(L, TOP + 40, L + W, TOP + 40)

    fs, lh, ss, sh = 8 * k, 9.6 * k, 6.8 * k, 8 * k        # font and line sizes, main and note

    def head(x, y, text, w):
        y += 3 * k
        p.set_xy(x, y)
        p.set_font('Helvetica', 'B', 8.6 * k)
        p.set_text_color(*NAVY)
        p.cell(w, 11 * k, text.upper())
        p.set_draw_color(200, 200, 200)
        p.set_line_width(0.5)
        p.line(x, y + 11.5 * k, x + w, y + 11.5 * k)
        p.set_text_color(0, 0, 0)
        return y + 14 * k

    def row(x, y, w, left, right='', bold=False, sub='', indent=0, color=None):
        p.set_font('Helvetica', 'B' if bold else '', fs)
        if color:
            p.set_text_color(*color)
        rw = p.get_string_width(right) + 5 if right else 0
        if right:
            p.set_xy(x + w - rw, y)
            p.cell(rw, lh, right, align='R')
        p.set_xy(x + indent, y)
        p.multi_cell(w - rw - indent - 3, lh, str(left), align='L', new_x='LMARGIN', new_y='NEXT')
        y = p.get_y()
        p.set_text_color(0, 0, 0)
        if sub:
            p.set_font('Helvetica', '', ss)
            p.set_text_color(*GREY)
            p.set_xy(x + indent + 6, y)
            p.multi_cell(w - indent - 6, sh, str(sub), align='L', new_x='LMARGIN', new_y='NEXT')
            p.set_text_color(0, 0, 0)
            y = p.get_y()
        return y + 1.2 * k

    def para(x, y, w, label, text):
        p.set_xy(x, y)
        p.set_font('Helvetica', '', ss)
        p.multi_cell(w, sh, f'**{label}:** {text}', align='L', markdown=True, new_x='LMARGIN', new_y='NEXT')
        return p.get_y() + 1.5 * k

    live = [i for i in items if i['status'] != 'Gone']
    costs = E.recurring_costs(places, tasks)
    rp = E.reserve_plan(live, today)
    colw = (W - 16) / 2
    x1, x2 = L, L + colw + 16
    y1 = y2 = TOP + 46

    # Left: recurring costs (every one), storage, to-dos (every open one).
    y1 = head(x1, y1, f"Recurring costs: {money(sum(c['per_year'] for c in costs))} a year", colw)
    cats = {}
    for c in costs:
        cats.setdefault(c['category'], []).append(c)
    for cat, cs in sorted(cats.items(), key=lambda kv: -sum(c['per_year'] for c in kv[1])):
        y1 = row(x1, y1, colw, cat, money(sum(c['per_year'] for c in cs)), bold=True)
        for c in cs:
            how = every(c['every']) + (f", next {c['next']}" if c['next'] else '')
            y1 = row(x1, y1, colw, f"{c['what'].split('(')[0].strip()} ({how})", money(c['per_year']), indent=8)
    y1 = head(x1, y1, 'Storage', colw)
    for pl in places:
        cost = f"{money(pl['cost_month'])}/mo" if pl['cost_month'] else f"{money(pl['cost_year'])}/yr" if pl['cost_year'] else 'free'
        held = sum(1 for i in live if i['place'] == pl['name'])
        y1 = row(x1, y1, colw, f"{pl['name']}{', ' + pl['address'] if pl['address'] else ''}", cost,
                 sub=f"{held} items; {pl['account_holder'] or ''}{'; ' + pl['notes'] if pl['notes'] else ''}")
    y1 = head(x1, y1, 'To do (everything open)', colw)
    todo = sorted([t for t in tasks if not E.is_auto(t) and (t['due'] or not t['last_done'])], key=lambda t: t['due'] or '9999')
    for t in todo:
        late = t['due'] and t['due'] < today.isoformat()
        y1 = row(x1, y1, colw, t['task'], ('OVERDUE ' if late else '') + (t['due'] or 'no date'), color=RED if late else None,
                 sub='; '.join(v for v in [t['about'], t['owner'], money(t['cost']) if t['cost'] else '',
                                           every(t['repeat_months']) if t['repeat_months'] else 'once'] if v))

    # Right: replacement plan (all of it), vehicles and trailers, then every other item by category.
    y2 = head(x2, y2, 'Replacement plan', colw)
    near = {}
    for r in rp['near']:
        near.setdefault(r['year'], []).append(r)
    for yr, rs in sorted(near.items()):
        y2 = row(x2, y2, colw, f'Buy in {yr}', money(sum(r['cost'] for r in rs)), bold=True)
        for r in rs:
            y2 = row(x2, y2, colw, r['it']['name'], money(r['cost']), indent=8)
    y2 = row(x2, y2, colw, f"Save for later items, from {rp['start_label']}", f"{money(rp['monthly'])}/mo", bold=True)
    if rp['big_total']:
        y2 = row(x2, y2, colw, f"or {money(rp['big_total'])} from savings now for the big items, plus", f"{money(rp['monthly_without_big'])}/mo", indent=8)
    for r in rp['later']:
        y2 = row(x2, y2, colw, f"{r['year']}  {r['it']['name']}", f"{money(r['cost'])} ({money(r['monthly'])}/mo)", indent=8)
    y2 = head(x2, y2, 'Vehicles and trailers', colw)
    big = sorted([i for i in live if i['category'] in ('Vehicle', 'Trailer') and i['status'] != 'Planned'], key=lambda i: (i['category'], i['name']))
    for i in big:
        lc = E.lifecycle(i, today)
        y2 = row(x2, y2, colw, i['name'], f"replace {lc['replace']}" if lc else i['status'].lower(),
                 sub='; '.join(v for v in [i['place'], i['condition'] or i['status'], f"VIN {i['vin']}" if i['vin'] else '',
                                           f"bought {int(i['year'])} for {money(i['cost'])}" if i['year'] and i['cost'] else ''] if v))
    y2 = head(x2, y2, 'All other property', colw)
    rest = [i for i in live if i not in big and i['status'] != 'Planned']
    by_cat = {}
    for i in rest:
        by_cat.setdefault(i['category'] or 'Other', []).append(i)
    for cat in E.CATEGORIES + ['Other']:
        group = sorted(by_cat.pop(cat, []), key=lambda i: i['name'])
        if group:
            y2 = para(x2, y2, colw, cat, '; '.join(
                f"{i['name']}{' x' + format(i['quantity'], 'g') if i['quantity'] and i['quantity'] > 1 else ''}"
                f"{' (' + i['place'] + ')' if i['place'] else ''}{' [' + i['status'].lower() + ']' if i['status'] != 'In use' else ''}"
                for i in group))
    planned = [i for i in live if i['status'] == 'Planned']
    if planned:
        y2 = para(x2, y2, colw, 'Planned purchases', '; '.join(
            f"{i['name']} ({int(i['replace_year']) if i['replace_year'] else ''}, {money(i['replace_cost'])})" for i in planned))
    return max(y1, y2)


def build(items, places, tasks, today=None, region='Detroit Region SCCA'):
    from fpdf import FPDF
    today = today or date.today()
    k = 1.0
    while True:
        p = FPDF(unit='pt', format='Letter')
        p.set_auto_page_break(False)
        p.set_margins(MARGIN, MARGIN, MARGIN)
        p.add_page()
        bottom = _layout(p, items, places, tasks, today, region, k)
        if bottom <= BOTTOM - 6 or k <= 0.55:
            break
        k -= 0.04                                   # shrink everything a little and lay it out again
    p.set_xy(MARGIN, BOTTOM + 8)
    p.set_font('Helvetica', '', 6.5)
    p.set_text_color(*GREY)
    p.cell(612 - 2 * MARGIN, 9, f'{region} - Operations - generated {today.isoformat()} from the Operations records', align='C')
    return bytes(p.output())
