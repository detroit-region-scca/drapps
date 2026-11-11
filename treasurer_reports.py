"""Monthly treasurer's reports to the board. Each month has two Google Docs in the DRSCCA Apps shared drive, under
Treasurer > Treasurer's reports: "YYYY-MM Treasurer's report (board)", the page shown to the board, and
"YYYY-MM Treasurer's report (script)", what the treasurer says. They are edited in Google Docs; the app lists them,
shows them, and starts next month's pair with a draft from the apps. Bank balances are never in them (the region
does not state its balances in the report)."""
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
KINDS = {'board': 'For the board', 'script': 'Script'}
NAME = re.compile(r"^(\d{4}-\d{2}) Treasurer's report \((board|script)\)$")
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
    """{'YYYY-MM': {'board': file, 'script': file}}, newest first."""
    out = {}
    for f in _q(_session(), f"'{folder()}' in parents and trashed = false"):
        m = NAME.match(f['name'])
        if m:
            out.setdefault(m[1], {})[m[2]] = f
    return dict(sorted(out.items(), reverse=True))


def create(month, kind, html):
    """A Google Doc made from html in the reports folder. Returns the Drive file."""
    from storage import UPLOAD
    meta = {'name': f"{month} Treasurer's report ({kind})", 'parents': [folder()], 'mimeType': 'application/vnd.google-apps.document'}
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
    """Google's HTML export cut down to plain structure: headings, paragraphs, lists, tables, bold, links."""
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
            bold = re.search(r'font-weight:\s*(700|bold)', a.get('style') or '')
            self.stack.append('b' if bold else None)
            if bold:
                self.out.append('<b>')
        elif tag == 'a' and (a.get('href') or '').startswith(('http://', 'https://')):
            href = re.sub(r'^https://www\.google\.com/url\?q=([^&]+).*$', lambda m: __import__('urllib.parse').parse.unquote(m[1]), a['href'])
            self.out.append(f'<a href="{escape(href)}" target="_blank" rel="noopener">')
        elif tag in self.KEEP:
            t = self.KEEP[tag]
            self.out.append(f'<{t} class="table table-sm">' if t == 'table' else f'<{t}>')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'head', 'title'):
            self.skip -= 1
        elif tag == 'span':
            if self.stack and self.stack.pop():
                self.out.append('</b>')
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


def draft(month):
    """(board html, script html) for a month, from the apps: profit by program this year, funds requests waiting, and
    what is waiting for the board. The treasurer adds the month's story in Google Docs."""
    import bud_store as B
    import fr_store as F
    import plan as plan_view
    import plan_store as P
    year = month[:4]
    label = date(int(year), int(month[5:]), 1).strftime('%B %Y')
    rows, tot, as_of = [], {}, ''
    if P.enabled():
        rows, tot = plan_view.summary(year)
        as_of = P.actuals(year)[1] or ''
    progs = [r for r in rows if r['actual'] not in (None, 0)]
    waiting = [r for r in (F.requests() if F.enabled() else []) if r['status'] == 'Submitted']
    board = []
    if B.enabled():
        for code, b in B.budgets().items():
            if B.status(b) in ('budget', 'changed'):
                board.append(f"{b.get('event_name') or code} ({code})")
    as_of_txt = f' through {as_of}' if as_of else ''

    def li(items):
        return '<ul>' + ''.join(f'<li>{escape(i)}</li>' for i in items) + '</ul>'

    prog_lines = [f"{r['name']}: {'up' if r['actual'] >= 0 else 'down'} {_money(r['actual'])}"
                  + (f" (plan {'+' if r['plan'] >= 0 else '-'}{_money(r['plan'])})" if r['plan'] is not None else '') for r in progs]
    board_html = (f"<h1>Treasurer's report, {label}</h1>"
                  f"<h2>Year to date{as_of_txt}</h2>"
                  + (li(prog_lines + [f"All programs: {'up' if tot.get('actual', 0) >= 0 else 'down'} {_money(tot.get('actual', 0))}"])
                     if progs else '<p>No results yet.</p>')
                  + '<h2>Funds requests</h2>'
                  + (li([f"{r['id']} {r.get('payee_name', '')}: {_money(r.get('net') or 0)}" for r in waiting]) if waiting
                     else '<p>Nothing waiting. Every request has been approved and paid.</p>')
                  + '<h2>Waiting on the board</h2>' + (li(board) if board else '<p>Nothing.</p>')
                  + '<h2>Housekeeping</h2><p></p>')
    script_html = (f"<h1>Treasurer's report script, {label}</h1>"
                   "<p>Good evening. Here's the money picture.</p>"
                   f"<p><b>Year to date{as_of_txt}.</b> "
                   + ' '.join(f"{r['name']} is {'up' if r['actual'] >= 0 else 'down'} {_money(r['actual'])}." for r in progs)
                   + f" Altogether we are {'up' if tot.get('actual', 0) >= 0 else 'down'} {_money(tot.get('actual', 0))}.</p>"
                   + '<p><b>Funds requests.</b> ' + (f"{len(waiting)} waiting." if waiting else 'Nothing is waiting.') + '</p>'
                   + '<p><b>Waiting on the board.</b> ' + ('; '.join(board) + '.' if board else 'Nothing.') + '</p>'
                   + "<p><b>Housekeeping.</b></p><p>That's it. Any questions?</p>")
    return board_html, script_html


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
                           have=have, next_month=nxt, kinds=KINDS,
                           folder_url=f"https://drive.google.com/drive/folders/{_folder_id['id']}" if 'id' in _folder_id else '')


@bp.route('/<month>')
@login_required
def month(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        abort(404)
    rep = reports().get(month)
    if not rep:
        abort(404)
    kind = request.args.get('show', 'board') if request.args.get('show') in KINDS else 'board'
    body = shown(rep[kind]['id']) if kind in rep else Markup('<p>Not made yet.</p>')
    label = date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')
    if request.args.get('present'):
        return render_template('treports_present.html', body=body, title=f"Treasurer's report {label}")
    return render_template('treports.html', title=f"Treasurer's report {label}", page_title="Treasurer's reports", view='month',
                           month=month, label=label, rep=rep, kind=kind, body=body, kinds=KINDS)


@bp.route('/new', methods=['POST'])
@login_required
def new():
    month = request.form.get('month', '')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        flash('Pick a month.', 'warning')
        return redirect(url_for('treports.home'))
    have = reports().get(month, {})
    board, script = draft(month)
    for kind, html in (('board', board), ('script', script)):
        if kind not in have:
            create(month, kind, html)
    import auth
    log_audit_event('TREASURER_REPORT_STARTED', (auth.user() or {}).get('name', ''), {'month': month})
    flash('Started. Edit both in Google Docs; this page shows what they say.', 'success')
    return redirect(url_for('treports.month', month=month))
