"""Operations at a glance: a one-page PDF of the Operations records (recurring costs, vehicles and trailers, storage,
what is coming up, replacement plan). Built from the same data as the Operations pages; fpdf2, no other I/O."""
import os
from datetime import date, timedelta

import equip_store as E

NAVY, GREY, RED = (20, 40, 80), (105, 105, 105), (180, 30, 30)


def money(v, places=0):
    if v is None:
        return '-'
    return ('-' if v < 0 else '') + f"${abs(v):,.{places}f}"


def build(items, places, tasks, today=None, region='Detroit Region SCCA', drive_url=''):
    from fpdf import FPDF
    today = today or date.today()
    p = FPDF(unit='pt', format='Letter')
    p.set_auto_page_break(False)
    p.add_page()
    L, W, TOP = 36, 612 - 72, 36
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'drscca-logo.png')
    if os.path.exists(logo):
        p.image(logo, x=L, y=TOP, h=40)
    tx = L + 40 * 700 / 471 + 10
    p.set_text_color(*NAVY)
    p.set_font('Helvetica', 'B', 16)
    p.set_xy(tx, TOP + 2)
    p.cell(300, 18, f'{region}: Operations at a glance')
    p.set_font('Helvetica', '', 8.5)
    p.set_text_color(*GREY)
    p.set_xy(tx, TOP + 22)
    p.cell(400, 11, f'As of {today:%B %-d, %Y}. Recurring costs, property, storage, what is due, and the replacement plan.')
    p.set_draw_color(*NAVY)
    p.set_line_width(1)
    p.line(L, TOP + 48, L + W, TOP + 48)

    live = [i for i in items if i['status'] != 'Gone']
    costs = E.recurring_costs(places, tasks)
    total = sum(c['per_year'] for c in costs)
    rp = E.reserve_plan(live, today)

    def head(x, y, text, w):
        p.set_xy(x, y)
        p.set_font('Helvetica', 'B', 9.5)
        p.set_text_color(*NAVY)
        p.cell(w, 13, text.upper())
        p.set_draw_color(200, 200, 200)
        p.set_line_width(0.5)
        p.line(x, y + 14, x + w, y + 14)
        p.set_text_color(0, 0, 0)
        return y + 18

    def fit(text, width):
        """Trim text to the column width in the current font, so nothing runs into the next column."""
        text = str(text)
        if p.get_string_width(text) <= width:
            return text
        while text and p.get_string_width(text + '...') > width:
            text = text[:-1]
        return text.rstrip(' ,;-') + '...'

    def row(x, y, w, left, right='', bold=False, size=8.5, sub=''):
        p.set_font('Helvetica', 'B' if bold else '', size)
        rw = max(p.get_string_width(right) + 6, w * 0.2)
        p.set_xy(x + w - rw, y)
        p.cell(rw, 11, right, align='R')
        p.set_xy(x, y)
        p.cell(w - rw, 11, fit(left, w - rw - 4))
        y += 11
        if sub:
            p.set_font('Helvetica', '', 7.2)
            p.set_text_color(*GREY)
            p.set_xy(x + 6, y)
            p.cell(w - 6, 9, fit(sub, w - 8))
            p.set_text_color(0, 0, 0)
            y += 9.5
        return y + 1.5

    colw = (W - 18) / 2
    x1, x2 = L, L + colw + 18
    y1 = y2 = TOP + 58

    # Left column: recurring costs, storage, vehicles and trailers.
    y1 = head(x1, y1, f'Recurring costs: {money(total)} a year', colw)
    by_cat = {}
    for c in costs:
        by_cat.setdefault(c['category'], []).append(c)
    for cat, cs in sorted(by_cat.items(), key=lambda kv: -sum(c['per_year'] for c in kv[1])):
        y1 = row(x1, y1, colw, cat, money(sum(c['per_year'] for c in cs)), bold=True,
                 sub=', '.join(f"{c['what'].split('(')[0].strip()} {money(c['per_year'])}" for c in cs[:4]))
    y1 += 6
    y1 = head(x1, y1, 'Storage', colw)
    for pl in places:
        cost = f"{money(pl['cost_month'])}/mo" if pl['cost_month'] else f"{money(pl['cost_year'])}/yr" if pl['cost_year'] else 'free'
        held = sum(1 for i in live if i['place'] == pl['name'])
        y1 = row(x1, y1, colw, pl['name'], cost, sub=f"{held} item{'s' if held != 1 else ''} listed; {pl['account_holder'] or ''}")
    y1 += 6
    y1 = head(x1, y1, 'Vehicles and trailers', colw)
    big = sorted([i for i in live if i['category'] in ('Vehicle', 'Trailer') and i['status'] != 'Planned'], key=lambda i: (i['category'], i['name']))
    for i in big:
        lc = E.lifecycle(i, today)
        right = f"replace {lc['replace']}" if lc else i['status'].lower()
        y1 = row(x1, y1, colw, i['name'][:46], right, sub=f"{i['place'] or 'no place listed'}; {i['condition'] or i['status']}")

    # Right column: coming up, replacement plan, where it lives.
    y2 = head(x2, y2, 'Coming up (next 6 months) and open items', colw)
    soon = (today + timedelta(days=183)).isoformat()
    todo = [t for t in tasks if not E.is_auto(t) and ((t['due'] and t['due'] <= soon) or (not t['due'] and not t['last_done']))]
    todo.sort(key=lambda t: t['due'] or '9999')
    for t in todo[:12]:
        due = t['due'] or 'no date'
        late = t['due'] and t['due'] < today.isoformat()
        p.set_text_color(*(RED if late else (0, 0, 0)))
        y2 = row(x2, y2, colw, t['task'].split('(')[0].strip()[:58], ('OVERDUE ' if late else '') + due,
                 sub=f"{t['about']}{' - ' + t['owner'] if t['owner'] else ''}{' - ' + money(t['cost']) if t['cost'] else ''}")
        p.set_text_color(0, 0, 0)
    y2 += 6
    y2 = head(x2, y2, 'Replacement plan', colw)
    near = {}
    for r in rp['near']:
        near.setdefault(r['year'], []).append(r)
    for yr, rs in sorted(near.items()):
        y2 = row(x2, y2, colw, f'Buy in {yr}', money(sum(r['cost'] for r in rs)), bold=True,
                 sub=', '.join(f"{r['it']['name'].split('(')[0].strip()} {money(r['cost'])}" for r in rs))
    y2 = row(x2, y2, colw, f"Save for later items (from {rp['start_label']})", f"{money(rp['monthly'])}/mo", bold=True)
    if rp['big_total']:
        y2 = row(x2, y2, colw, f"  or {money(rp['big_total'])} from savings now, plus", f"{money(rp['monthly_without_big'])}/mo")
    for r in rp['later'][:7]:
        y2 = row(x2, y2, colw, f"  {r['year']}  {r['it']['name'].split('(')[0].strip()[:40]}", money(r['cost']), size=8)
    y2 += 6
    y2 = head(x2, y2, 'Where it lives', colw)
    p.set_font('Helvetica', '', 8)
    p.set_xy(x2, y2)
    p.multi_cell(colw, 10.5, 'The records are the DRSCCA Operations spreadsheet in the DRSCCA Apps shared drive (Operations folder): '
                             'Property, Storage, Recurring costs and to-dos, and Officials. The app at drevent.drscca.org/operations '
                             'is a convenience on top; the region can keep the sheet up by hand without it. Changes in the app: '
                             'equipment committee and treasurer, through an emailed sign-in link.', align='L')
    p.set_xy(L, 792 - 30)
    p.set_font('Helvetica', '', 7)
    p.set_text_color(*GREY)
    p.cell(W, 10, f'{region} - Operations - generated {today.isoformat()} from the Operations records', align='C')
    return bytes(p.output())
