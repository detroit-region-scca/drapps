"""Monthly treasurer's reports to the board. Each month is one Google Slides deck in the DRSCCA Apps shared drive, under
Treasurer > Treasurer's reports: "YYYY-MM Treasurer's report", three slides shown at the meeting: the summary, a chart
of net income, and the profit and loss for the year to date. It is current to the day it was started. The app builds
the deck (as PowerPoint, which Drive turns into Google Slides), lists the reports and shows them; the treasurer edits
the deck in Google Slides. Bank balances are never in it (the region does not state its balances in the report)."""
import io
import json
import re
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from utils import log_audit_event, login_required

bp = Blueprint('treports', __name__, url_prefix='/treasurer/reports')
FOLDER = "Treasurer's reports"
NAME = re.compile(r"^(\d{4}-\d{2}) Treasurer's report$")
SLIDES = 'application/vnd.google-apps.presentation'
PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
_folder_id = {}


def _session():
    from storage import sheets_client
    return sheets_client().http_client.session


def _q(s, q):
    from storage import DRIVE
    r = s.get(DRIVE, params={'q': q, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true', 'corpora': 'allDrives',
                             'fields': 'files(id,name,mimeType,webViewLink,modifiedTime,driveId)', 'pageSize': 200})
    r.raise_for_status()
    return r.json().get('files', [])


def folder():
    """The Treasurer's reports folder, found under the shared drive's Treasurer folder, made the first time."""
    if 'id' in _folder_id:
        return _folder_id['id']
    from storage import drive_folder
    s = _session()
    tre = [f for f in _q(s, "name = 'Treasurer' and mimeType = 'application/vnd.google-apps.folder' and trashed = false")]
    tre = [f for f in tre if f.get('driveId')] or tre
    if not tre:
        raise RuntimeError('The Treasurer folder in the DRSCCA Apps shared drive was not found.')
    have = _q(s, f"name = \"{FOLDER}\" and '{tre[0]['id']}' in parents and trashed = false")
    _folder_id['id'] = have[0]['id'] if have else drive_folder(FOLDER, tre[0]['id'])['id']
    return _folder_id['id']


def reports():
    """{'YYYY-MM': the month's Google Slides deck}, newest first."""
    out = {}
    for f in _q(_session(), f"'{folder()}' in parents and mimeType = '{SLIDES}' and trashed = false"):
        m = NAME.match(f['name'])
        if m:
            out[m[1]] = f
    return dict(sorted(out.items(), reverse=True))


def create(month, pptx):
    """The month's deck in the reports folder, from PowerPoint bytes (Drive turns it into Google Slides)."""
    from storage import UPLOAD
    meta = {'name': f"{month} Treasurer's report", 'parents': [folder()], 'mimeType': SLIDES}
    files = {'metadata': ('metadata', json.dumps(meta), 'application/json'), 'file': ('report.pptx', pptx, PPTX)}
    r = _session().post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'},
                        files=files, timeout=110)
    r.raise_for_status()
    return r.json()


def pdf(file_id):
    """The deck as a PDF, for the preview on the app's page."""
    from storage import DRIVE
    r = _session().get(f'{DRIVE}/{file_id}/export', params={'mimeType': 'application/pdf'}, timeout=110)
    r.raise_for_status()
    return r.content


def present_url(file_id):
    return f'https://docs.google.com/presentation/d/{file_id}/present'


def _money(v):
    return f"${abs(v):,.0f}"


def _amt(v):
    if v is None:
        return ''
    return f"({abs(v):,.2f})" if v < 0 else f"{v:,.2f}"


def collapse(rows):
    """The statement with sub-accounts folded into their group: the top-level sections and totals, and one line per
    group or account under them (each program's expenses, Operations, interest)."""
    out, tot_other = [], None
    for r in rows:
        if r['depth'] == 0:
            if r['kind'] == 'total' and r['label'] == 'Total Other Income':
                tot_other = r['ytd']
            if r['kind'] == 'total' and r['label'] == 'Net Other Income' and r['ytd'] == tot_other:
                continue                # the same number again
            out.append(r)
        elif r['depth'] == 1 and r['kind'] == 'line':
            out.append(r)
        elif r['depth'] == 1 and r['kind'] == 'total':
            out.append(dict(r, kind='line', label=r['label'].removeprefix('Total ')))
    for r in out:
        r['label'] = r['label'].removeprefix('Club ')
    return out


def chart_png(year, through, plan_months, actual_months, history):
    """A PNG of net income added up month by month: the annual plan, this year through the month `through` (the last
    one may be part of a month), and the
    average of the years in `history` ({year: [12 months]}). Returns base64 text, or '' if there is nothing to draw."""
    import base64
    import io
    from PIL import Image, ImageDraw, ImageFont

    def cum(xs):
        out, t = [], 0.0
        for x in xs:
            t += x
            out.append(t)
        return out
    past = [v for v in history.values() if len(v) == 12]
    series = []
    if past:
        series.append((f'Average {min(history)} to {max(history)}', cum([sum(v[i] for v in past) / len(past) for i in range(12)]), (150, 150, 150), 5))
    if any(plan_months):
        series.append(('Annual plan', cum(plan_months), (29, 63, 184), 5))
    if through:
        series.append((f'{year} actual', cum(actual_months[:through]), (208, 2, 27), 8))
    if not series:
        return ''
    W, H, L, R, T, B = 1600, 800, 170, 60, 110, 90
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    try:
        font, small = ImageFont.load_default(size=30), ImageFont.load_default(size=26)
    except TypeError:
        font = small = ImageFont.load_default()
    vals = [v for _, ys, _, _ in series for v in ys] + [0]
    lo, hi = min(vals), max(vals)
    step = next(s for s in (1000, 2500, 5000, 10000, 20000, 25000, 50000, 100000, 250000) if (hi - lo) / s <= 8)
    lo, hi = (lo // step) * step, -((-hi) // step) * step
    x = lambda i: L + (W - L - R) * i / 11            # noqa: E731
    y = lambda v: T + (H - T - B) * (hi - v) / ((hi - lo) or 1)      # noqa: E731
    v = lo
    while v <= hi:
        d.line([(L, y(v)), (W - R, y(v))], fill=(225, 225, 225) if v else (120, 120, 120), width=2)
        lab = f"{'-' if v < 0 else ''}${abs(v) / 1000:,.0f}k"
        d.text((L - 15, y(v)), lab, fill=(80, 80, 80), font=small, anchor='rm')
        v += step
    for i, mname in enumerate('JFMAMJJASOND'):
        d.text((x(i), H - B + 20), mname, fill=(80, 80, 80), font=small, anchor='mt')
    for name, ys, color, width in series:
        pts = [(x(i), y(v)) for i, v in enumerate(ys)]
        if len(pts) > 1:
            d.line(pts, fill=color, width=width, joint='curve')
        for p in pts[-1:]:
            d.ellipse([p[0] - width, p[1] - width, p[0] + width, p[1] + width], fill=color)
    lx = L
    for name, _, color, width in reversed(series):
        d.line([(lx, 50), (lx + 50, 50)], fill=color, width=width)
        d.text((lx + 62, 50), name, fill=(30, 30, 30), font=font, anchor='lm')
        lx += 90 + d.textlength(name, font=font)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _nice(d):
    return date.fromisoformat(d).strftime('%b %-d, %Y')


SECTION_BG = {'income': 'E6F4EA', 'cost': 'FDECEA', 'net': 'ECEFF1'}
GOOD, BAD = '1B7F3B', 'C62828'
NETS = ('Gross Profit', 'Net Operating Income', 'Net Other Income', 'Net Income')


def data(month, housekeeping=()):
    """What goes in a month's report, from the apps, current to the day of the last QuickBooks sync (normally today)."""
    import base64
    import bud_store as B
    import fr_store as F
    import plan as plan_view
    import plan_store as P
    full = P.pnl() if P.enabled() else []
    period = full[0]['period'][:10] if full else date.today().isoformat()
    year, mon = int(period[:4]), int(period[5:7])
    by_month = P.monthly(str(year)) if P.enabled() else {}
    progs = [(name, round(sum(by_month[code][:mon]), 2)) for code, name in plan_view._programs()
             if code in by_month and round(sum(by_month[code][:mon]), 2)]
    waiting = [r for r in (F.requests() if F.enabled() else []) if r['status'] == 'Submitted']
    board = [f"{b.get('event_name') or code} ({code})" for code, b in (B.budgets().items() if B.enabled() else [])
             if B.status(b) in ('budget', 'changed')]
    plan_m = [0.0] * 12
    for p in (P.plan(str(year)).values() if P.enabled() else []):
        for ln in p['lines']:
            if ln['month']:
                plan_m[ln['month'] - 1] += ln['net'] or 0
            else:
                for i in range(12):
                    plan_m[i] += (ln['net'] or 0) / 12
    nets = P.net_by_month() if P.enabled() else {}
    png = chart_png(year, mon, plan_m, nets.get(year, [0.0] * 12), {y: v for y, v in nets.items() if year - 5 <= y < year})
    frm = (full[0].get('from') or '')[:10] if full else ''
    return {'title': f"Treasurer's report, {date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')}",
            'period': period, 'since': f"Since {date.fromisoformat(frm).strftime('%b %-d')}" if frm else 'Past month',
            'net': next((r for r in full if r['kind'] == 'total' and r['label'] == 'Net Income'), None),
            'programs': progs, 'waiting': [f"{r['id']} {r.get('payee_name', '')}: {_money(r.get('net') or 0)}" for r in waiting],
            'board': board, 'housekeeping': list(housekeeping), 'chart': base64.b64decode(png) if png else b'',
            'pnl': collapse(full)}


def deck(d):
    """The report as a three-slide PowerPoint (16:9): the summary, the chart, the profit and loss."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    RED, GRAY, INK = RGBColor(0xD0, 0x02, 0x1B), RGBColor(0x66, 0x66, 0x66), RGBColor(0x11, 0x11, 0x11)

    def text(slide, x, y, w, h, paras):
        """paras: [(text, size, bold, color or None)]"""
        tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        for i, (t, size, bold, color) in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            r = p.add_run()
            r.text, r.font.size, r.font.bold = t, Pt(size), bold
            r.font.color.rgb = color or INK
            p.space_after = Pt(4 if size < 16 else 6)
        return tf

    def page(sub):
        sl = prs.slides.add_slide(blank)
        text(sl, 0.6, 0.35, 12.1, 0.7, [(d['title'], 30, True, None)])
        bar = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.08), Inches(12.1), Inches(0.05))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RED
        bar.line.fill.background()
        text(sl, 0.6, 1.2, 12.1, 0.5, [(sub, 20, True, None)])
        return sl

    def updown(v):
        return f"{'up' if v >= 0 else 'down'} {_money(v)}"

    # 1. Summary
    net = d['net']
    sl = page(f"{d['since']}: {updown(net['month'])}. Year to date: {updown(net['ytd'])}." if net else 'No results in QuickBooks yet.')
    text(sl, 0.6, 1.75, 12.1, 0.4, [(f"Current to {_nice(d['period'])}.", 14, False, GRAY)])
    left = [('By program, year to date', 18, True, None)] + [(f"•  {n}: {updown(v)}", 16, False, None) for n, v in d['programs']]
    text(sl, 0.6, 2.3, 5.6, 4.9, left)
    right = [('Funds requests', 18, True, None)]
    right += [(f'•  {w}', 15, False, None) for w in d['waiting']] or [('Nothing waiting. Every request has been approved and paid.', 15, False, None)]
    right += [('Waiting on the board', 18, True, None)]
    right += [(f'•  {b}', 15, False, None) for b in d['board']] or [('Nothing.', 15, False, None)]
    right += [('Housekeeping', 18, True, None)] + [(f'•  {h}', 15, False, None) for h in d['housekeeping']]
    text(sl, 6.6, 2.3, 6.1, 4.9, right)

    # 2. Chart
    sl = page('Net income through the year')
    if d['chart']:
        sl.shapes.add_picture(io.BytesIO(d['chart']), Inches(0.9), Inches(1.85), width=Inches(11.5))

    # 3. Profit and loss
    sl = page('Profit and loss, year to date')
    rows = d['pnl']
    y = int(d['period'][:4])
    heads = ['Account', d['since'], 'Year to date', f'Average {y - 5} to {y - 1}', 'Difference']
    widths = [3.9, 1.8, 1.9, 2.4, 1.8]
    tbl = sl.shapes.add_table(len(rows) + 1, 5, Inches(0.6), Inches(1.75), Inches(sum(widths)), Inches(0.2 * (len(rows) + 1))).table
    size = Pt(10 if len(rows) <= 26 else 8)
    for i, wd in enumerate(widths):
        tbl.columns[i].width = Inches(wd)

    def cell(r, c, t, bold=False, fill=None, color=None):
        ce = tbl.cell(r, c)
        ce.margin_top = ce.margin_bottom = Inches(0.01)
        ce.margin_left = ce.margin_right = Inches(0.06)
        ce.fill.solid()
        ce.fill.fore_color.rgb = RGBColor.from_string(fill or 'FFFFFF')
        p = ce.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT
        run = p.add_run()
        run.text, run.font.size, run.font.bold = t, size, bold
        run.font.color.rgb = RGBColor.from_string(color or '111111')
    for c, h in enumerate(heads):
        cell(0, c, h, bold=True, color='555555')
    short, section = False, 'income'
    for i, r in enumerate(rows, 1):
        tbl.rows[i].height = Inches(0.2)
        if r['depth'] == 0 and r['kind'] == 'head':
            section = 'income' if 'Income' in r['label'] else 'cost'
        kind = 'net' if r['kind'] == 'total' and r['label'] in NETS else section
        fill = SECTION_BG[kind] if r['depth'] == 0 else None
        bold = r['kind'] != 'line'
        cell(i, 0, '    ' * r['depth'] + r['label'], bold, fill)
        if r['kind'] == 'head':
            for c in range(1, 5):
                cell(i, c, '', fill=fill)
            continue
        avg, n = r.get('avg5'), r.get('avg_years') or 0
        mark = '*' if avg is not None and n < 5 else ''
        short = short or bool(mark)
        diff = None if avg is None else round(r['ytd'] - avg, 2)
        good = diff is not None and ((diff > 0) if kind in ('income', 'net') else (diff < 0))
        cell(i, 1, _amt(r['month']), bold, fill)
        cell(i, 2, _amt(r['ytd']), bold, fill)
        cell(i, 3, (_amt(avg) + mark) if avg is not None else '', bold, fill)
        cell(i, 4, _amt(diff), bold, fill, (GOOD if good else BAD) if diff else None)
    tbl.rows[0].height = Inches(0.22)
    note = (f"Through {_nice(d['period'])}. Average: January 1 to {date.fromisoformat(d['period']).strftime('%B %-d')} of each of "
            'the five years before. Difference: year to date less the average; green is better for the region, red worse.'
            + (' * Fewer than five years: the account did not exist in all of them (accounts reorganized in 2022).' if short else ''))
    text(sl, 0.6, 7.0, 12.1, 0.4, [(note, 9, False, GRAY)])
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@bp.route('/')
@login_required
def home():
    try:
        have = reports()
    except Exception as e:
        flash(f'Could not read the reports folder in Drive: {e}', 'warning')
        have = {}
    return render_template('treports.html', title="Treasurer's reports", page_title="Treasurer's reports", view='list',
                           have=have, next_month=date.today().strftime('%Y-%m'), present_url=present_url,
                           folder_url=f"https://drive.google.com/drive/folders/{_folder_id['id']}" if 'id' in _folder_id else '')


def _report(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        abort(404)
    rep = reports().get(month)
    if not rep:
        abort(404)
    return rep


@bp.route('/<month>')
@login_required
def month(month):
    rep = _report(month)
    label = date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')
    return render_template('treports.html', title=f"Treasurer's report {label}", page_title="Treasurer's reports", view='month',
                           month=month, label=label, rep=rep, present=present_url(rep['id']))


@bp.route('/<month>.pdf')
@login_required
def month_pdf(month):
    rep = _report(month)
    return Response(pdf(rep['id']), mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{month} Treasurer\'s report.pdf"'})


@bp.route('/new', methods=['POST'])
@login_required
def new():
    month = request.form.get('month', '')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        flash('Pick a month.', 'warning')
        return redirect(url_for('treports.home'))
    if month not in reports():
        create(month, deck(data(month)))
    import auth
    log_audit_event('TREASURER_REPORT_STARTED', (auth.user() or {}).get('name', ''), {'month': month})
    flash('Started. Edit it in Google Slides; this page shows what it says.', 'success')
    return redirect(url_for('treports.month', month=month))
