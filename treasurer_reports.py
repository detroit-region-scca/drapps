"""Monthly treasurer's reports to the board. Each month is one Google Doc in the DRSCCA Apps shared drive, under
Treasurer > Treasurer's reports: "YYYY-MM Treasurer's report", the page shown to the board, current to the day it
was started, with the profit and loss statement for the year to date. It is edited in Google Docs; the app lists the reports, shows
them, and starts a month with a draft from the apps. Bank balances are never in it (the region does not state its
balances in the report)."""
import json
import re
from datetime import date
from html import escape
from html.parser import HTMLParser

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup

from utils import log_audit_event, login_required

bp = Blueprint('treports', __name__, url_prefix='/treasurer/reports')
FOLDER = "Treasurer's reports"
NAME = re.compile(r"^(\d{4}-\d{2}) Treasurer's report(?: \(board\))?$")
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
    """{'YYYY-MM': Drive file}, newest first."""
    out = {}
    for f in _q(_session(), f"'{folder()}' in parents and trashed = false"):
        m = NAME.match(f['name'])
        if m:
            out[m[1]] = f
    return dict(sorted(out.items(), reverse=True))


def create(month, html):
    """A Google Doc made from html in the reports folder. Returns the Drive file."""
    from storage import UPLOAD
    meta = {'name': f"{month} Treasurer's report", 'parents': [folder()], 'mimeType': 'application/vnd.google-apps.document'}
    files = {'metadata': ('metadata', json.dumps(meta), 'application/json'), 'file': ('report.html', html.encode(), 'text/html')}
    r = _session().post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'},
                        files=files, timeout=110)
    r.raise_for_status()
    return r.json()


def replace(file_id, html):
    """Overwrite a report Doc with new html."""
    from storage import UPLOAD
    r = _session().patch(f'{UPLOAD}/{file_id}', params={'uploadType': 'media', 'supportsAllDrives': 'true'},
                         data=html.encode(), headers={'Content-Type': 'text/html'}, timeout=110)
    r.raise_for_status()


class _Clean(HTMLParser):
    """Google's HTML export cut down to plain structure: headings, paragraphs, lists, tables, bold, links, text and
    cell colors, and the chart (a PNG kept inside the Doc)."""
    KEEP = {'h1': 'h2', 'h2': 'h3', 'h3': 'h4', 'h4': 'h5', 'p': 'p', 'ul': 'ul', 'ol': 'ol', 'li': 'li', 'table': 'table',
            'tr': 'tr', 'td': 'td', 'th': 'th', 'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i', 'br': 'br'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('style', 'script', 'head', 'title'):
            self.skip += 1
            return
        if tag == 'span':
            st = a.get('style') or ''
            bold = re.search(r'font-weight:\s*(700|bold)', st)
            color = re.search(r'(?<![-\w])color:\s*(#[0-9a-fA-F]{6})', st)
            color = color[1] if color and color[1].lower() != '#000000' else ''
            self.stack.append(('<b>' if bold else '') + (f'<span style="color:{color}">' if color else ''))
            self.out.append(self.stack[-1])
        elif tag == 'a' and (a.get('href') or '').startswith(('http://', 'https://')):
            href = re.sub(r'^https://www\.google\.com/url\?q=([^&]+).*$', lambda m: __import__('urllib.parse').parse.unquote(m[1]), a['href'])
            self.out.append(f'<a href="{escape(href)}" target="_blank" rel="noopener">')
        elif tag == 'img' and (a.get('src') or '').startswith('data:image/png;base64,'):
            self.out.append(f'<img src="{a["src"]}" alt="{escape(a.get("alt") or "Chart")}" style="max-width: 100%; height: auto;">')
        elif tag in self.KEEP:
            t = self.KEEP[tag]
            bg = re.search(r'background-color:\s*(#[0-9a-fA-F]{6})', a.get('style') or '') if t in ('td', 'th') else None
            bg = bg[1] if bg and bg[1].lower() != '#ffffff' else ''
            self.out.append(f'<{t} class="table table-sm">' if t == 'table' else f'<{t} style="background-color:{bg}">' if bg else f'<{t}>')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'head', 'title'):
            self.skip -= 1
        elif tag == 'span':
            if self.stack:
                opened = self.stack.pop()
                self.out.append(('</span>' if '<span' in opened else '') + ('</b>' if '<b>' in opened else ''))
        elif tag == 'a':
            self.out.append('</a>')
        elif tag in self.KEEP and tag != 'br':
            self.out.append(f'</{self.KEEP[tag]}>')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data))


def shown(file_id):
    """A report Doc as clean HTML for the page."""
    from storage import DRIVE
    r = _session().get(f'{DRIVE}/{file_id}/export', params={'mimeType': 'text/html'}, timeout=60)
    r.raise_for_status()
    c = _Clean()
    c.feed(r.text)
    return Markup(re.sub(r'<p>\s*</p>', '', ''.join(c.out)))


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


def chart_html(year, through):
    """The chart for the report, from the plan and the QuickBooks figures in the apps."""
    import plan_store as P
    if not P.enabled():
        return ''
    plan_m = [0.0] * 12
    for p in P.plan(str(year)).values():
        for ln in p['lines']:
            if ln['month']:
                plan_m[ln['month'] - 1] += ln['net'] or 0
            else:
                for i in range(12):
                    plan_m[i] += (ln['net'] or 0) / 12
    nets = P.net_by_month()
    png = chart_png(year, through, plan_m, nets.get(int(year), [0.0] * 12),
                    {y: v for y, v in nets.items() if int(year) - 5 <= y < int(year)})
    if not png:
        return ''
    return (f'<h2>Net income through the year</h2><img src="data:image/png;base64,{png}" width="640" height="320" '
            'alt="Net income added up month by month: actual, annual plan and five-year average">')


SECTION_BG = {'income': '#e6f4ea', 'cost': '#fdecea', 'net': '#eceff1'}
GOOD, BAD = '#1b7f3b', '#c62828'
NETS = ('Gross Profit', 'Net Operating Income', 'Net Other Income', 'Net Income')


def _nice(d):
    return date.fromisoformat(d).strftime('%b %-d, %Y')


def pnl_html(rows, period):
    """QuickBooks' profit and loss as a table, current to the day `period` (YYYY-MM-DD): from the first of last month to that day, the year
    to date, the average of the same dates over the five years before, and the difference. Colored: income green,
    costs red, net lines gray; a difference is green when it is good for the region (more income, less cost)."""
    if not rows:
        return '<p>The profit and loss statement is not in the apps yet (the nightly QuickBooks sync brings it).</p>'
    d = date.fromisoformat(period)
    y = d.year
    frm = (rows[0].get('from') or '')[:10]
    since = f'Since {date.fromisoformat(frm).strftime("%b %-d")}' if frm else 'Past month'
    out = [f'<table><tr><th>Account</th><th>{since}</th><th>Year to date</th><th>Average {y - 5} to {y - 1}</th>'
           '<th>Difference</th></tr>']
    short, section = False, 'income'
    for r in rows:
        if r['depth'] == 0 and r['kind'] == 'head':
            section = 'income' if 'Income' in r['label'] else 'cost'
        kind = 'net' if r['kind'] == 'total' and r['label'] in NETS else section
        bg = f' style="background-color:{SECTION_BG[kind]}"' if r['depth'] == 0 else ''
        label = '\u00a0' * 4 * r['depth'] + escape(r['label'])
        if r['kind'] == 'head':
            out.append(f'<tr><td{bg}><b>{label}</b></td>' + f'<td{bg}></td>' * 4 + '</tr>')
            continue
        avg = r.get('avg5')
        n = r.get('avg_years') or 0
        mark = '*' if avg is not None and n < 5 else ''
        short = short or bool(mark)
        diff = None if avg is None else round(r['ytd'] - avg, 2)
        dtxt = _amt(diff)
        if diff:
            good = diff > 0 if kind in ('income', 'net') else diff < 0
            dtxt = f'<span style="color:{GOOD if good else BAD}">{dtxt}</span>'
        cells = [_amt(r['month']), _amt(r['ytd']), (_amt(avg) + mark) if avg is not None else '', dtxt]
        if r['kind'] == 'total':
            out.append(f'<tr><td{bg}><b>{label}</b></td>' + ''.join(f'<td{bg}><b>{c}</b></td>' for c in cells) + '</tr>')
        else:
            out.append(f'<tr><td>{label}</td>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>')
    note = (f'<p>Through {_nice(period)}. Average: January 1 to {d.strftime("%B %-d")} of each of the five years before. '
            f'Difference: year to date less the average, <span style="color:{GOOD}">green</span> when it is better for the '
            f'region, <span style="color:{BAD}">red</span> when worse.'
            + (' * Fewer than five years: the account did not exist in all of them (the accounts were reorganized in 2022).'
               if short else '') + '</p>')
    return ''.join(out) + '</table>' + note


def draft(month):
    """The board page, current to the day of the last QuickBooks sync (normally today): net since the first of last month and this
    year, profit by program, funds requests waiting, what is waiting for the board, a chart of net income against the
    plan and the five years before, and the profit and loss (sub-accounts folded into their group). The treasurer adds
    the month's story in Google Docs."""
    import bud_store as B
    import fr_store as F
    import plan as plan_view
    import plan_store as P
    label = date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')
    full = P.pnl() if P.enabled() else []
    period = full[0]['period'][:10] if full else date.today().isoformat()
    year, mon = int(period[:4]), int(period[5:7])
    pnl = collapse(full)
    # Each program's profit from January through this month so far, so it agrees with the statement below.
    by_month = P.monthly(str(year)) if P.enabled() else {}
    progs = [{'name': name, 'actual': round(sum(by_month[code][:mon]), 2)}
             for code, name in plan_view._programs() if code in by_month and round(sum(by_month[code][:mon]), 2)]
    waiting = [r for r in (F.requests() if F.enabled() else []) if r['status'] == 'Submitted']
    board = []
    if B.enabled():
        for code, b in B.budgets().items():
            if B.status(b) in ('budget', 'changed'):
                board.append(f"{b.get('event_name') or code} ({code})")

    def li(items):
        return '<ul>' + ''.join(f'<li>{escape(i)}</li>' for i in items) + '</ul>'

    def updown(v):
        return f"{'up' if v >= 0 else 'down'} {_money(v)}"

    net = next((r for r in full if r['kind'] == 'total' and r['label'] == 'Net Income'), None)
    frm = (full[0].get('from') or '')[:10] if full else ''
    past = f"since {date.fromisoformat(frm).strftime('%b %-d')}" if frm else 'the past month'
    return (f"<h1>Treasurer's report, {label}</h1><p>Current to {_nice(period)}.</p>"
            + (f"<p><b>Net {past}: {updown(net['month'])}. Year to date: {updown(net['ytd'])}.</b></p>" if net else '')
            + f"<h2>By program, year to date</h2>"
            + (li([f"{r['name']}: {updown(r['actual'])}" for r in progs]) if progs else '<p>No results in QuickBooks yet.</p>')
            + '<h2>Funds requests</h2>'
            + (li([f"{r['id']} {r.get('payee_name', '')}: {_money(r.get('net') or 0)}" for r in waiting]) if waiting
               else '<p>Nothing waiting. Every request has been approved and paid.</p>')
            + '<h2>Waiting on the board</h2>' + (li(board) if board else '<p>Nothing.</p>')
            + '<h2>Housekeeping</h2><p></p>'
            + chart_html(year, mon)
            + f'<h2>Profit and loss, year to date</h2>' + pnl_html(pnl, period))


@bp.route('/')
@login_required
def home():
    try:
        have = reports()
    except Exception as e:
        flash(f'Could not read the reports folder in Drive: {e}', 'warning')
        have = {}
    today = date.today()
    nxt = today.strftime('%Y-%m')
    return render_template('treports.html', title="Treasurer's reports", page_title="Treasurer's reports", view='list',
                           have=have, next_month=nxt,
                           folder_url=f"https://drive.google.com/drive/folders/{_folder_id['id']}" if 'id' in _folder_id else '')


@bp.route('/<month>')
@login_required
def month(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        abort(404)
    rep = reports().get(month)
    if not rep:
        abort(404)
    body = shown(rep['id'])
    label = date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')
    if request.args.get('present'):
        return render_template('treports_present.html', body=body, title=f"Treasurer's report {label}")
    return render_template('treports.html', title=f"Treasurer's report {label}", page_title="Treasurer's reports", view='month',
                           month=month, label=label, rep=rep, body=body)


@bp.route('/new', methods=['POST'])
@login_required
def new():
    month = request.form.get('month', '')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        flash('Pick a month.', 'warning')
        return redirect(url_for('treports.home'))
    if month not in reports():
        create(month, draft(month))
    import auth
    log_audit_event('TREASURER_REPORT_STARTED', (auth.user() or {}).get('name', ''), {'month': month})
    flash('Started. Edit it in Google Docs; this page shows what it says.', 'success')
    return redirect(url_for('treports.month', month=month))
