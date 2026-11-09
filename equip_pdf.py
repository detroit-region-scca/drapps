"""The Operations report: a printable PDF laid out like the Operations pages, one section each.

1. Summary: the four numbers, what needs attention, the next 30 days
2. To do, by when it is due
3. Bills, by category, with the yearly total
4. Vehicles and trailers
5. Documents: what is in Drive and what is missing
6. Storage places
7. Replacement plan
8. Gear, by category

Built with fpdf2 from the same records as the pages. Letter size, normal reading sizes, as many pages as it needs, with
the section names and page numbers in the header and footer. No I/O beyond the logo file.
"""
import os
from datetime import date, timedelta

from fpdf import FPDF
from fpdf.fonts import FontFace

import equip_store as E

NAVY, GREY, RED, AMBER, GREEN = (20, 40, 80), (100, 100, 100), (180, 30, 30), (138, 90, 0), (25, 120, 60)
LIGHT = (243, 245, 249)
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'drscca-logo.png')
SOON_DAYS, NEXT_DAYS = 90, 30
_SWAP = {'–': '-', '—': '-', '‘': "'", '’': "'", '“': '"', '”': '"', '…': '...',
         '×': 'x', '•': '-', '→': '->', '✓': '', '✗': '', ' ': ' '}


def clean(v):
    """Text the PDF's built-in font can draw (Latin-1): curly quotes and dashes made plain, anything else dropped."""
    s = '' if v is None else str(v)
    for a, b in _SWAP.items():
        s = s.replace(a, b)
    return s.encode('latin-1', 'replace').decode('latin-1')


def money(v, places=0):
    if v is None or v == '':
        return ''
    return ('-' if v < 0 else '') + f"${abs(v):,.{places}f}"


class Report(FPDF):
    def __init__(self, region, today):
        super().__init__(unit='pt', format='Letter')
        self.region, self.today, self.section = region, today, ''
        self.set_margins(40, 54, 40)
        self.set_auto_page_break(True, 46)
        self.alias_nb_pages()

    # Page furniture -------------------------------------------------------------------------------------------
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('Helvetica', '', 8)
        self.set_text_color(*GREY)
        self.set_xy(self.l_margin, 26)
        self.cell(0, 10, clean(f'{self.region} Operations report'), align='L')
        self.set_xy(self.l_margin, 26)
        self.cell(0, 10, clean(self.section), align='R')
        self.set_draw_color(210, 210, 210)
        self.set_line_width(0.5)
        self.line(self.l_margin, 39, self.w - self.r_margin, 39)
        self.set_y(54)

    def footer(self):
        self.set_y(-32)
        self.set_font('Helvetica', '', 8)
        self.set_text_color(*GREY)
        self.cell(0, 10, clean(f'As of {self.today:%B %-d, %Y}'), align='L')
        self.set_x(self.l_margin)
        self.cell(0, 10, f'Page {self.page_no()} of {{nb}}', align='R')

    # Building blocks ------------------------------------------------------------------------------------------
    @property
    def width(self):
        return self.w - self.l_margin - self.r_margin

    def title_block(self):
        top = 40
        if os.path.exists(LOGO):
            self.image(LOGO, x=self.l_margin, y=top, h=40)
        x = self.l_margin + 40 * 700 / 471 + 12
        self.set_xy(x, top + 2)
        self.set_font('Helvetica', 'B', 20)
        self.set_text_color(*NAVY)
        self.cell(0, 22, clean(f'{self.region}: Operations report'))
        self.set_xy(x, top + 25)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*GREY)
        self.cell(0, 12, clean(f'As of {self.today:%B %-d, %Y}. What the region pays for and looks after between events.'))
        self.set_draw_color(*NAVY)
        self.set_line_width(1.2)
        self.line(self.l_margin, top + 50, self.w - self.r_margin, top + 50)
        self.set_y(top + 62)

    def contents(self, names):
        self.set_font('Helvetica', '', 9)
        self.set_text_color(*GREY)
        self.multi_cell(0, 12, clean('In this report: ' + ' - '.join(f'{n}. {s}' for n, s in enumerate(names, 1))))
        self.ln(8)

    def h1(self, n, text, note=''):
        """A numbered section. Starts a new page unless there is plenty of room left on this one."""
        self.section = f'{n}. {text}'
        if self.get_y() > self.h - 220:
            self.add_page()
        else:
            self.ln(10)
        self.set_font('Helvetica', 'B', 15)
        self.set_text_color(*NAVY)
        self.cell(0, 20, clean(f'{n}. {text}'), new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*NAVY)
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(5)
        if note:
            self.note(note)

    def h2(self, text, color=NAVY):
        if self.get_y() > self.h - 120:
            self.add_page()
        self.ln(4)
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(*color)
        self.cell(0, 15, clean(text), new_x='LMARGIN', new_y='NEXT')
        self.set_text_color(0, 0, 0)

    def note(self, text):
        self.set_font('Helvetica', '', 9)
        self.set_text_color(*GREY)
        self.multi_cell(0, 12, clean(text), new_x='LMARGIN', new_y='NEXT')
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def grid(self, heads, rows, widths, align=None, colors=None, size=9):
        """A table with a header row (repeated after a page break), light stripes and lines between rows.
        rows: lists of cell text; colors: optional per-row text color."""
        if not rows:
            self.note('None.')
            return
        align = align or ['LEFT'] * len(heads)
        self.set_font('Helvetica', '', size)
        self.set_text_color(0, 0, 0)
        self.set_draw_color(215, 215, 215)
        self.set_line_width(0.4)
        with self.table(col_widths=widths, width=self.width, text_align=align, line_height=size * 1.35,
                        borders_layout='HORIZONTAL_LINES', cell_fill_color=LIGHT, cell_fill_mode='ROWS',
                        headings_style=FontFace(emphasis='BOLD', color=NAVY, fill_color=(228, 233, 242), size_pt=size),
                        padding=(3, 4)) as t:
            hr = t.row()
            for h in heads:
                hr.cell(clean(h))
            for i, r in enumerate(rows):
                style = FontFace(color=colors[i]) if colors and colors[i] else None
                tr = t.row(style=style)
                for v in r:
                    tr.cell(clean(v))
        self.ln(6)

    def stats(self, boxes):
        """The summary numbers side by side: (label, big value, its unit, small line under it, color of that line)."""
        gap = 10
        w = (self.width - gap * (len(boxes) - 1)) / len(boxes)
        y = self.get_y()
        for i, (label, value, unit, sub, color) in enumerate(boxes):
            x = self.l_margin + i * (w + gap)
            self.set_fill_color(*LIGHT)
            self.set_draw_color(215, 215, 215)
            self.rect(x, y, w, 70, style='DF')
            self.set_xy(x + 9, y + 8)
            self.set_font('Helvetica', '', 8.5)
            self.set_text_color(*GREY)
            self.cell(w - 18, 10, clean(label.upper()))
            self.set_xy(x + 9, y + 21)
            self.set_font('Helvetica', 'B', 17)
            self.set_text_color(0, 0, 0)
            vw = self.get_string_width(clean(value)) + 4
            self.cell(vw, 20, clean(value))
            self.set_font('Helvetica', '', 9)
            self.set_text_color(*GREY)
            self.cell(w - 18 - vw, 22, clean(unit))
            self.set_xy(x + 9, y + 44)
            self.set_font('Helvetica', '', 8.5)
            self.set_text_color(*(color or GREY))
            self.multi_cell(w - 18, 10, clean(sub))
        self.set_text_color(0, 0, 0)
        self.set_y(y + 82)


def _roll(b, today):
    """An automatic charge's next date on or after today."""
    d = b['next']
    if not d or d >= today:
        return d or ''
    return E.next_due({'due': d, 'repeat_months': b['every']}, after=(date.fromisoformat(today) - timedelta(days=1)).isoformat()) or ''


def build(items, places, tasks, docs=None, today=None, region='Detroit Region SCCA'):
    """docs: the Documents rows with what Drive holds (equip_docs.status), or None to leave that section out."""
    today = today or date.today()
    t_iso = today.isoformat()
    soon, month = (today + timedelta(days=SOON_DAYS)).isoformat(), (today + timedelta(days=NEXT_DAYS)).isoformat()
    live = [i for i in items if i['status'] != 'Gone']
    owned = [i for i in live if i['status'] != 'Planned']
    big = sorted([i for i in owned if i['category'] in ('Vehicle', 'Trailer')], key=lambda i: (i['category'], i['name']))
    lcs = {i['id']: E.lifecycle(i, today) for i in big}
    todo = [t for t in tasks if not E.is_auto(t)]
    overdue = sorted([t for t in todo if t['due'] and t['due'] < t_iso], key=lambda t: t['due'])
    undated = [t for t in todo if not t['due'] and not t['last_done']]
    bills = E.recurring_costs(places, tasks)
    auto = {t['id'] for t in tasks if E.is_auto(t)}
    rp = E.reserve_plan(live, today, E.plan_settings() if E.enabled() else None)
    replace_soon = [i for i in big if lcs[i['id']] and lcs[i['id']].get('replace') and lcs[i['id']]['replace'] <= today.year + 1]
    docs_missing = sum(len(r['missing']) for r in docs or [])
    docs_total = sum(len(r['have']) + len(r['missing']) for r in docs or [])

    sections = ['Summary', 'To do', 'Bills', 'Vehicles and trailers'] + (['Documents'] if docs is not None else []) + \
        ['Storage places', 'Replacement plan', 'Gear']
    p = Report(region, today)
    p.add_page()
    p.title_block()
    p.contents(sections)
    n = iter(range(1, 20))

    # 1. Summary ---------------------------------------------------------------------------------------------------
    p.h1(next(n), 'Summary')
    n_due = sum(1 for t in todo if t['due'] and t['due'] <= soon)
    p.stats([
        ('To do', str(n_due), 'in the next 90 days', (f'{len(overdue)} overdue' if overdue else 'nothing overdue') + (f', {len(undated)} with no date' if undated else ''), RED if overdue else None),
        ('Bills', money(sum(b['per_year'] for b in bills)), 'a year', f"about {money(sum(b['per_year'] for b in bills) / 12)} a month, {len(bills)} bills", None),
        ('Vehicles', str(sum(1 for i in big if i['category'] == 'Vehicle')), f"and {sum(1 for i in big if i['category'] == 'Trailer')} trailers",
         f'{len(replace_soon)} due for replacement by {today.year + 1}' if replace_soon else 'none due for replacement soon', AMBER if replace_soon else None),
        ('Save for replacements', money(rp['monthly']), 'a month', f"from {rp['start_label']}", None)])
    attention = [[t['due'], t['task'], 'Overdue to-do' + (f": {t['about']}" if t['about'] else '')] for t in overdue]
    attention += [[str(lcs[i['id']]['replace']), i['name'], 'Due for replacement'] for i in replace_soon]
    if docs_missing:
        attention.append(['', f'{docs_missing} documents missing', f'{docs_total - docs_missing} of {docs_total} are in Drive; see Documents'])
    if attention:
        p.h2('Needs attention', RED)
        p.grid(['When', 'What', 'Why'], attention, (14, 46, 40))
    coming = [[t['due'], t['task'], 'To do' + (f": {t['about']}" if t['about'] else ''), ''] for t in todo if t['due'] and t_iso <= t['due'] <= month]
    coming += [[_roll(b, t_iso), b['what'], 'Charged automatically' + (f": {b['paid_how']}" if b['paid_how'] else ''), money(b['each'])]
               for b in bills if b['id'] in auto and (b['every'] or 0) > 1 and t_iso <= _roll(b, t_iso) <= month
               and not (b.get('ends') and _roll(b, t_iso) > b['ends'])]
    p.h2(f'Next {NEXT_DAYS} days')
    p.grid(['Date', 'What', 'Kind', 'Amount'], sorted(coming), (14, 44, 30, 12), ['LEFT', 'LEFT', 'LEFT', 'RIGHT'])

    # 2. To do -----------------------------------------------------------------------------------------------------
    p.h1(next(n), 'To do', 'Things someone has to do. Bills that charge the card on their own are under Bills.')
    groups = [('Overdue', overdue, RED), ('Next 90 days', [t for t in todo if t['due'] and t_iso <= t['due'] <= soon], None),
              ('Later', [t for t in todo if t['due'] and t['due'] > soon], None), ('No date yet', [t for t in todo if not t['due'] and not (t['last_done'] and not t['repeat_months'])], None)]      # not the finished one-time ones
    for name, rows, color in groups:
        if not rows:
            continue
        p.h2(f'{name} ({len(rows)})', color or NAVY)
        rows = sorted(rows, key=lambda t: (t['due'] or '9999', t['task']))
        p.grid(['Due', 'To do', 'For', 'Who', 'How often', 'Cost'],
               [[t['due'] or '', t['task'], t['about'], t['owner'], E.every_label(t['repeat_months']) if t['repeat_months'] else 'Once',
                 money(t['cost']) if t['cost'] else ''] for t in rows],
               (12, 38, 19, 12, 10, 9), ['LEFT'] * 5 + ['RIGHT'])

    # 3. Bills -----------------------------------------------------------------------------------------------------
    total = sum(b['per_year'] for b in bills)
    p.h1(next(n), 'Bills', f'Everything paid on a schedule: {money(total)} a year, about {money(total / 12)} a month.')
    cats = {}
    for b in bills:
        cats.setdefault(b['category'], []).append(b)
    p.grid(['Category', 'Bills', 'A year'], [[c, str(len(bs)), money(sum(b['per_year'] for b in bs))]
                                              for c, bs in sorted(cats.items(), key=lambda kv: -sum(b['per_year'] for b in kv[1]))]
           + [['Total', str(len(bills)), money(total)]], (60, 15, 25), ['LEFT', 'RIGHT', 'RIGHT'])
    for c, bs in sorted(cats.items(), key=lambda kv: -sum(b['per_year'] for b in kv[1])):
        p.h2(f"{c}: {money(sum(b['per_year'] for b in bs))} a year")
        p.grid(['What', 'How often', 'Next', 'Paid by', 'Each', 'A year'],
               [[b['what'], E.every_label(b['every']), _roll(b, t_iso) if b['id'] in auto else (b['next'] or ''), b['paid_how'] or '',
                 money(b['each'], 2 if b['each'] and b['each'] % 1 else 0), money(b['per_year'])] for b in bs],
               (38, 12, 13, 17, 10, 10), ['LEFT'] * 4 + ['RIGHT', 'RIGHT'])

    # 4. Vehicles and trailers ---------------------------------------------------------------------------------------
    p.h1(next(n), 'Vehicles and trailers', 'Value left is straight-line over the useful life, for planning only (not for tax).')
    for cat in ('Vehicle', 'Trailer'):
        rows = [i for i in big if i['category'] == cat]
        p.h2(f'{cat}s ({len(rows)})')
        p.grid(['Name', 'Kept at', 'Condition', 'VIN or serial', 'Bought', 'Value left', 'Replace'],
               [[i['name'] + (f" ({i['program']})" if i['program'] else ''), i['place'], i['condition'] or i['status'], i['vin'],
                 ' for '.join(v for v in [str(int(i['year'])) if i['year'] else '', money(i['cost']) if i['cost'] else ''] if v),
                 money(lcs[i['id']]['left']) if lcs[i['id']] and lcs[i['id']].get('left') is not None else '',
                 str(lcs[i['id']]['replace']) if lcs[i['id']] else ''] for i in rows],
               (23, 13, 19, 17, 11, 9, 8), ['LEFT'] * 5 + ['RIGHT', 'RIGHT'], size=8.5)

    # 5. Documents -------------------------------------------------------------------------------------------------
    if docs is not None:
        p.h1(next(n), 'Documents', f'{docs_total - docs_missing} of {docs_total} titles, registrations, insurance cards and agreements are in Drive '
                                   '(Operations > Documents). Upload a missing one from the item\'s page in the app.')
        p.grid(['For', 'Have', 'Missing'],
               [[r['for_name'], ', '.join(d for d, _ in r['have']) or '-', ', '.join(r['missing'])] for r in docs],
               (40, 30, 30))

    # 6. Storage places --------------------------------------------------------------------------------------------
    p.h1(next(n), 'Storage places')
    p.grid(['Place', 'Address', 'Cost', 'Account holder', 'Items', 'Notes'],
           [[pl['name'], pl['address'], f"{money(pl['cost_month'])}/mo" if pl['cost_month'] else f"{money(pl['cost_year'])}/yr" if pl['cost_year'] else 'Free',
             pl['account_holder'], str(sum(1 for i in owned if i['place'] == pl['name'])), pl['notes']] for pl in places],
           (15, 20, 9, 14, 6, 36), ['LEFT', 'LEFT', 'RIGHT', 'LEFT', 'RIGHT', 'LEFT'], size=8.5)

    # 7. Replacement plan ------------------------------------------------------------------------------------------
    p.h1(next(n), 'Replacement plan', f"Due by {rp['near_until']}: budgeted outright in that year. Later: money set aside each month, "
                                      'item by item, so each is paid for by July of its year.')
    near = {}
    for r in rp['near']:
        near.setdefault(r['year'], []).append(r)
    for yr, rs in sorted(near.items()):
        p.h2(f"Buy in {yr}: {money(sum(r['cost'] for r in rs))}")
        p.grid(['Item', 'Program', 'Cost'], [[r['it']['name'], r['it']['program'], money(r['cost'])] for r in rs], (65, 20, 15), ['LEFT', 'LEFT', 'RIGHT'])
    p.h2(f"Save for later: {money(rp['monthly'])} a month from {rp['start_label']}")
    if rp['big_total']:
        p.note(f"Or pay {money(rp['big_total'])} from savings for the big items (marked *: {money(rp['cfg']['big_cost'])} or more, due within "
               f"{rp['cfg']['big_within_years']} years) and set aside {money(rp['monthly_without_big'])} a month for the rest.")
    p.grid(['Year', 'Item', 'Cost', 'A month'],
           [[str(r['year']), r['it']['name'] + (' *' if r['big'] else ''), money(r['cost']), money(r['monthly'])] for r in rp['later']],
           (10, 60, 15, 15), ['LEFT', 'LEFT', 'RIGHT', 'RIGHT'])

    # 8. Gear ------------------------------------------------------------------------------------------------------
    rest = [i for i in owned if i['category'] not in ('Vehicle', 'Trailer')]
    p.h1(next(n), 'Gear', f'Everything else the region owns ({len(rest)} entries), by category.')
    by_cat = {}
    for i in rest:
        by_cat.setdefault(i['category'] or 'Other', []).append(i)
    for cat in E.CATEGORIES + sorted(c for c in by_cat if c not in E.CATEGORIES):
        group = sorted(by_cat.pop(cat, []), key=lambda i: i['name'].lower())
        if group:
            p.h2(f'{cat} ({len(group)})')
            p.grid(['Item', 'Qty', 'Kept at', 'Program', 'Status'],
                   [[i['name'], format(i['quantity'], 'g') if i['quantity'] else '', i['place'], i['program'], i['condition'] or i['status']] for i in group],
                   (46, 6, 18, 14, 16), ['LEFT', 'RIGHT', 'LEFT', 'LEFT', 'LEFT'], size=8.5)
    planned = [i for i in live if i['status'] == 'Planned']
    if planned:
        p.h2(f'Planned purchases ({len(planned)})')
        p.grid(['Item', 'Year', 'Estimated cost'], [[i['name'], str(int(i['replace_year'])) if i['replace_year'] else '', money(i['replace_cost'])] for i in planned],
               (66, 14, 20), ['LEFT', 'LEFT', 'RIGHT'])
    return bytes(p.output())
