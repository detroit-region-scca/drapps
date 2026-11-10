"""Reads and writes the region's invoices: the DRSCCA Invoices spreadsheet (INVOICES_SHEET_ID) in the DRSCCA Apps
shared drive, Invoices folder, with each invoice's PDF in the PDFs folder beside it (INVOICES_PDF_FOLDER_ID).

  Invoices   one row per invoice (INV-2026-001): who it bills, program and event, dates, total, status, payment
  Lines      one row per line item, joined by invoice id

Plain column headers, so the region can keep invoicing by hand from the sheet if the app is gone.
"""
import io
import json
import os
import threading
import time
from datetime import date, timedelta

from filelock import FileLock

from storage import DATA_DIR, worksheet
import sheet_cache as C

INV_COLS = ['id', 'created', 'status', 'program', 'event_code', 'event_name', 'bill_name', 'bill_contact', 'bill_email', 'bill_address',
            'total', 'due', 'notes', 'created_by', 'pdf_url', 'paid_date', 'paid_ref', 'paid_amount', 'closed_by', 'closed_note',
            'filed_email']
INV_LABELS = ['Invoice #', 'Date', 'Status', 'Program', 'Event code', 'Event', 'Bill to', 'Attention', 'Email', 'Address',
              'Total', 'Due', 'Notes', 'Created by', 'PDF', 'Paid on', 'Payment reference', 'Amount paid', 'Closed by', 'Closing note',
              'Created by (sign-in email)']
LINE_COLS = ['invoice_id', 'description', 'quantity', 'unit_price', 'amount']
LINE_LABELS = ['Invoice #', 'Description', 'Quantity', 'Unit price', 'Amount']
NUMERIC = {'total', 'paid_amount', 'quantity', 'unit_price', 'amount'}
STATUSES = ['Open', 'Paid', 'Void']
TERMS_DAYS = 30
REMIT = {'payee': 'Detroit Region SCCA', 'lines': ['PO Box 401018', 'Redford, MI 48240-9018'], 'email': 'treasurer@drscca.org', 'web': 'drscca.org'}
LOCK = os.path.join(DATA_DIR, 'invoices.lock')
# Numbers already issued and the New invoice pages already used: a number is never given out twice (not even after the
# newest invoice is deleted), and a double click or the back button never makes a second invoice.
ISSUED_FILE = os.path.join(DATA_DIR, 'invoices-issued.json')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('INVOICES_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['INVOICES_SHEET_ID'], title)


def refresh():
    _cache.clear()
    C.invalidate(os.environ['INVOICES_SHEET_ID'])


def _num(v):
    try:
        return float(v) if str(v).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _snapshot():
    hit, ver = _cache.get('snap'), C.version(os.environ['INVOICES_SHEET_ID'])      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    # The whole Invoices tab, not A:U: a tab that does not have the newest column yet still reads.
    res = C.batch_get(os.environ['INVOICES_SHEET_ID'], ["'Invoices'", 'Lines!A:E'],
                                                                     params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], []]
    invs = [dict(zip(INV_COLS, list(r) + [''] * len(INV_COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
    lines = [dict(zip(LINE_COLS, list(r) + [''] * len(LINE_COLS))) for r in vr[1][1:] if r and str(r[0]).strip()]
    for row in invs + lines:
        for k in list(row):
            row[k] = _num(row[k]) if k in NUMERIC else str(row[k])
    _cache['snap'] = (ver, (invs, lines))
    return invs, lines


def invoices():
    return sorted(_snapshot()[0], key=lambda r: r['id'], reverse=True)


def get(inv_id):
    invs, lines = _snapshot()
    r = next((r for r in invs if r['id'] == inv_id), None)
    return dict(r, lines=[ln for ln in lines if ln['invoice_id'] == inv_id]) if r else None


def _cell(v):
    return '' if v is None else v if isinstance(v, (int, float)) else str(v)


def _heading(ws):
    """Columns added later (the sign-in email) get their heading the first time an invoice is written; the tab is
    widened first, or the write is refused."""
    if not _cache.get('heading_ok'):
        if ws.col_count < len(INV_COLS):
            ws.add_cols(len(INV_COLS) - ws.col_count)
        top = ws.row_values(1)
        if len(top) < len(INV_LABELS):
            ws.update([INV_LABELS[len(top):]], f'{chr(ord("A") + len(top))}1', value_input_option='RAW')
        _cache['heading_ok'] = True


def _issued():
    try:
        with open(ISSUED_FILE) as f:
            d = json.load(f)
        return {'last': dict(d.get('last') or {}), 'tokens': dict(d.get('tokens') or {})}
    except (OSError, ValueError):
        return {'last': {}, 'tokens': {}}


def _save_issued(d):
    now = time.time()
    d['tokens'] = {k: v for k, v in d['tokens'].items() if now - v.get('at', 0) < 90 * 86400}
    tmp = ISSUED_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f)
    os.replace(tmp, ISSUED_FILE)


def create(inv, lines, token=''):
    """inv: INV_COLS fields except id/created/status/total/due; lines: [{description, quantity, unit_price}].
    token: the New invoice page's one-time token. Returns (the invoice, True) when that page already made one
    (nothing new is written), else (the new invoice, False)."""
    with FileLock(LOCK), _lock:
        issued = _issued()
        if token and token in issued['tokens']:
            done = get(issued['tokens'][token]['id'])
            if done:
                return done, True
        ws = _ws('Invoices')
        year = date.today().year
        prefix = f'INV-{year}-'
        nums = [int(v[len(prefix):]) for v in ws.col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
        n = max(nums + [int(issued['last'].get(str(year), 0))], default=0) + 1
        iid = f'{prefix}{n:03d}'
        rows = [dict(ln, invoice_id=iid, amount=round((ln['quantity'] or 1) * ln['unit_price'], 2)) for ln in lines]
        total = round(sum(r['amount'] for r in rows), 2)
        today = date.today()
        row = dict(inv, id=iid, created=today.isoformat(), status='Open', total=total,
                   due=inv.get('due') or (today + timedelta(days=TERMS_DAYS)).isoformat())
        # Recorded before the writes: a retry after a timeout part-way is refused rather than billed twice.
        issued['last'][str(year)] = n
        if token:
            issued['tokens'][token] = {'id': iid, 'at': time.time()}
        _save_issued(issued)
        _heading(ws)
        ws.append_row([_cell(row.get(c)) for c in INV_COLS], value_input_option='RAW')
        _ws('Lines').append_rows([[_cell(r.get(c)) for c in LINE_COLS] for r in rows], value_input_option='RAW')
        refresh()
        return dict(row, lines=rows), False


def update(inv_id, expect=None, **fields):
    """Change fields on one invoice. expect: the status it must still have, checked under the lock. Returns the row, or
    None when the row is gone from the sheet or its status is not `expect` (nothing is changed). Cells are read as
    stored, not as shown, so an amount column formatted as Currency keeps its number."""
    # The file lock too: the app runs several worker processes, and a create or delete in another one shifts rows.
    with FileLock(LOCK), _lock:
        ws = _ws('Invoices')
        col = ws.col_values(1)
        if inv_id not in col:
            return None
        n = col.index(inv_id) + 1
        cur = dict(zip(INV_COLS, ['' if v is None else v for v in ws.row_values(n, value_render_option='UNFORMATTED_VALUE')] + [''] * len(INV_COLS)))
        if expect and cur['status'] != expect:
            return None
        cur.update(fields)
        _heading(ws)
        for k in NUMERIC & set(INV_COLS):
            cur[k] = _num(cur[k])
        ws.update([[_cell(cur[c]) for c in INV_COLS]], f'A{n}', value_input_option='RAW')
        refresh()
        return cur


def delete(inv_id):
    """Remove an invoice and its lines from the sheet, and move its PDF to the Drive trash (recoverable there).
    Only after the treasurer confirms by email (inv.py)."""
    import re
    from storage import DRIVE, sheets_client
    with FileLock(LOCK), _lock:
        inv = get(inv_id)
        ws = _ws('Invoices')
        col = ws.col_values(1)
        if inv_id in col:       # already gone when someone deleted the row by hand
            ws.delete_rows(col.index(inv_id) + 1)
        lw = _ws('Lines')
        for n in sorted((i + 1 for i, v in enumerate(lw.col_values(1)) if v == inv_id), reverse=True):
            lw.delete_rows(n)
        refresh()
    m = re.search(r'/d/([\w-]+)', (inv or {}).get('pdf_url') or '')
    if m:
        try:
            sheets_client().http_client.session.patch(f'{DRIVE}/{m.group(1)}', params={'supportsAllDrives': 'true'}, json={'trashed': True})
        except Exception:        # the record is gone either way; a PDF left behind is harmless
            pass


def setup_sheet(book):
    """Headers and the How to use tab (first run, or to bring them up to date)."""
    have = {w.title: w for w in book.worksheets()}
    for title, labels in (('Invoices', INV_LABELS), ('Lines', LINE_LABELS)):
        ws = have.get(title) or book.add_worksheet(title, rows=500, cols=len(labels))
        if ws.col_count < len(labels):
            ws.add_cols(len(labels) - ws.col_count)
        ws.update([labels], 'A1', value_input_option='RAW')
    guide = have.get('How to use') or book.add_worksheet('How to use', rows=40, cols=1)
    guide.clear()
    guide.update([[t] for t in [
        'DRSCCA Invoices',
        'Invoices the region sends to get paid: sponsors, event partners (Grand Prix, FSAE), series fees, other regions.',
        'Invoices: one row per invoice, numbered INV-YEAR-NNN in order. Status: Open (waiting for payment), Paid (Paid on, Payment reference, Amount paid), Void (Closing note says why).',
        'Lines: the line items of each invoice, joined by Invoice #. Amount = Quantity x Unit price; the invoice Total is the sum.',
        'PDF: the invoice as sent, in the PDFs folder next to this sheet.',
        'Payment goes to Detroit Region SCCA, PO Box 401018, Redford, MI 48240-9018. Deposit it in QuickBooks to the program\'s revenue account (and the event project when there is one), then mark the invoice Paid.',
        'The apps at drapps.drscca.org write here and make the PDFs. If they are gone, add a row here, write the invoice from the last PDF as a template, and keep the numbering going.']],
        'A1', value_input_option='RAW')
    guide.format('A1', {'textFormat': {'bold': True, 'fontSize': 14}})
    for w in book.worksheets():
        if w.title in ('Sheet1',):
            book.del_worksheet(w)
    book.reorder_worksheets([guide] + [w for w in book.worksheets() if w.title != 'How to use'])


# The invoice as a PDF (fpdf2, no I/O).

def money(v):
    return f"${(v or 0):,.2f}"


def _long_date(s):
    """2026-11-08 -> November 8, 2026 (anything else is shown as given)."""
    try:
        d = date.fromisoformat(str(s)[:10])
        return f"{d:%B} {d.day}, {d.year}"
    except ValueError:
        return str(s or '')


def pdf_bytes(inv, region='Detroit Region SCCA'):
    from fpdf import FPDF
    p = FPDF(unit='pt', format='Letter')
    p.set_auto_page_break(True, margin=72)
    p.add_page()
    L, W = 54, 612 - 108
    navy, grey, ink, rule, panel = (20, 40, 80), (115, 120, 130), (30, 30, 35), (215, 219, 226), (243, 245, 249)
    red = (200, 30, 40)

    def label(x, y, w, t, align='L'):
        p.set_xy(x, y)
        p.set_font('Helvetica', 'B', 7.5)
        p.set_text_color(*grey)
        p.set_char_spacing(0.8)
        p.cell(w, 10, t.upper(), align=align)
        p.set_char_spacing(0)

    # A navy band across the top with a thin red line under it.
    p.set_fill_color(*navy)
    p.rect(0, 0, 612, 10, 'F')
    p.set_fill_color(*red)
    p.rect(0, 10, 612, 2, 'F')

    # The region logo (static/drscca-logo.png, 700x471) at the top left, the region's name and address beside it.
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'drscca-logo.png')
    lh, top = 62, 40
    tx = L
    if os.path.exists(logo):
        p.image(logo, x=L, y=top, h=lh)
        tx = L + lh * 700 / 471 + 14
    p.set_text_color(*navy)
    p.set_font('Helvetica', 'B', 15)
    p.set_xy(tx, top)
    p.cell(W / 2, 18, region)
    p.set_font('Helvetica', '', 8.5)
    p.set_text_color(*grey)
    y = top + 20
    for t in ['Sports Car Club of America', *REMIT['lines'], f"{REMIT['email']}  |  {REMIT['web']}"]:
        p.set_xy(tx, y)
        p.cell(W / 2, 11, t)
        y += 11
    p.set_text_color(*navy)
    p.set_font('Helvetica', 'B', 24)
    p.set_char_spacing(3)
    p.set_xy(L + W / 2, top - 2)
    p.cell(W / 2, 28, 'INVOICE', align='R')
    p.set_char_spacing(0)
    y = max(y, top + lh) + 20

    # The summary panel: number, dates and the amount due.
    ph = 46
    p.set_fill_color(*panel)
    p.rect(L, y, W, ph, 'F')
    p.set_fill_color(*navy)
    p.rect(L, y, 3, ph, 'F')
    cells = [('Invoice number', inv['id']), ('Issued', _long_date(inv['created'])), ('Due', _long_date(inv['due']))]
    cw = W * 0.22
    for i, (k, v) in enumerate(cells):
        x = L + 16 + i * cw
        label(x, y + 10, cw, k)
        p.set_xy(x, y + 22)
        p.set_font('Helvetica', 'B', 10.5)
        p.set_text_color(*ink)
        p.cell(cw, 14, v)
    status = inv.get('status') or 'Open'
    due_now = 0 if status in ('Paid', 'Void') else inv['total']
    label(L + W - 16 - W * 0.3, y + 10, W * 0.3, 'Amount due' if status == 'Open' else 'Status', 'R')
    p.set_xy(L + W - 16 - W * 0.3, y + 20)
    p.set_font('Helvetica', 'B', 15)
    p.set_text_color(*((20, 120, 60) if status == 'Paid' else grey if status == 'Void' else navy))
    p.cell(W * 0.3, 18, money(due_now) if status == 'Open' else status.upper(), align='R')
    y += ph + 24

    # Bill to, and what it is for.
    label(L, y, W / 2, 'Bill to')
    has_for = inv.get('event_name') or inv.get('program_name')
    if has_for:
        label(L + W / 2 + 20, y, W / 2 - 20, 'For')
    y += 14
    bill = [inv['bill_name'], inv.get('bill_contact') and f"Attn: {inv['bill_contact']}", *(inv.get('bill_address') or '').splitlines(), inv.get('bill_email')]
    fory = y
    p.set_text_color(*ink)
    for t in [b for b in bill if b]:
        p.set_xy(L, y)
        p.set_font('Helvetica', 'B' if t == inv['bill_name'] else '', 10.5 if t == inv['bill_name'] else 10)
        p.cell(W / 2, 14, t)
        y += 14
    for i, t in enumerate([inv.get('event_name'), inv.get('program_name')]):
        if t:
            p.set_xy(L + W / 2 + 20, fory)
            p.set_font('Helvetica', 'B' if i == 0 else '', 10.5 if i == 0 else 10)
            p.set_text_color(*(ink if i == 0 else grey))
            p.multi_cell(W / 2 - 20, 14, t)
            fory = p.get_y()
    y = max(y, fory) + 22

    # The line items.
    cols = [(W * 0.58, 'Description', 'L'), (W * 0.10, 'Qty', 'R'), (W * 0.16, 'Unit price', 'R'), (W * 0.16, 'Amount', 'R')]
    x = L
    for w, t, a in cols:
        label(x + (4 if a == 'L' else 0), y, w - 4, t, a)
        x += w
    y += 14
    p.set_draw_color(*navy)
    p.set_line_width(1.2)
    p.line(L, y, L + W, y)
    y += 4
    p.set_draw_color(*rule)
    p.set_line_width(0.5)
    for ln in inv['lines']:
        q = ln['quantity'] or 1
        p.set_text_color(*ink)
        p.set_font('Helvetica', '', 10)
        n = len(p.multi_cell(cols[0][0] - 8, 14, ln['description'], dry_run=True, output='LINES')) or 1
        h = 14 * n
        if y + h + 10 > 792 - 72:     # start the row on a new page, not across the break
            p.add_page()
            y = 54
        p.set_xy(L + 4, y + 4)
        p.multi_cell(cols[0][0] - 8, 14, ln['description'], new_x='RIGHT', new_y='TOP')
        p.set_xy(L + cols[0][0], y + 4)
        p.cell(cols[1][0], 14, f'{q:g}', align='R')
        p.cell(cols[2][0], 14, money(ln['unit_price']), align='R')
        p.set_font('Helvetica', 'B', 10)
        p.cell(cols[3][0] - 4, 14, money(ln['amount']), align='R')
        y += h + 10
        p.line(L, y, L + W, y)

    # The total, in a navy band at the right.
    y += 12
    if y + 28 > 792 - 72:
        p.add_page()
        y = 54
    tw = W * 0.42
    p.set_fill_color(*navy)
    p.rect(L + W - tw, y, tw, 28, 'F')
    p.set_text_color(255, 255, 255)
    p.set_font('Helvetica', 'B', 10)
    p.set_xy(L + W - tw + 12, y)
    p.set_char_spacing(0.8)
    p.cell(tw / 2, 28, 'TOTAL DUE' if status == 'Open' else 'TOTAL')
    p.set_char_spacing(0)
    p.set_font('Helvetica', 'B', 13)
    p.set_xy(L + W - tw / 2, y)
    p.cell(tw / 2 - 8, 28, money(inv['total']), align='R')
    y += 28
    if status == 'Paid':
        p.set_xy(L + W - tw, y + 6)
        p.set_font('Helvetica', '', 9.5)
        p.set_text_color(20, 120, 60)
        ref = f" ({inv['paid_ref']})" if inv.get('paid_ref') else ''
        p.cell(tw - 8, 14, f"Paid {_long_date(inv.get('paid_date'))}{ref}: {money(inv.get('paid_amount'))}. Thank you.", align='R')
    elif status == 'Void':
        p.set_xy(L + W - tw, y + 6)
        p.set_font('Helvetica', 'B', 9.5)
        p.set_text_color(*red)
        p.cell(tw - 8, 14, 'This invoice is void. Nothing is owed.', align='R')
    y += 30

    # How to pay, in a light panel.
    rows = [('Payable to', REMIT['payee']), ('Mail to', f"{REMIT['lines'][0]}, {REMIT['lines'][1]}"),
            ('Reference', f"Write {inv['id']} on your check"), ('Questions', REMIT['email'])]
    bh = 22 + 15 * len(rows) + 8
    if status != 'Open':
        bh = 0
    elif y + bh > 792 - 72:
        p.add_page()
        y = 54
    if bh:
        p.set_fill_color(*panel)
        p.rect(L, y, W, bh, 'F')
        p.set_fill_color(*navy)
        p.rect(L, y, 3, bh, 'F')
        label(L + 16, y + 10, W, 'How to pay')
        yy = y + 26
        for k, v in rows:
            p.set_xy(L + 16, yy)
            p.set_font('Helvetica', '', 9.5)
            p.set_text_color(*grey)
            p.cell(72, 13, k)
            p.set_font('Helvetica', '', 10)
            p.set_text_color(*ink)
            p.cell(W - 100, 13, v)
            yy += 15
    y += bh
    if inv.get('notes'):
        y += 20 if bh else 0
        label(L, y, W, 'Notes')
        p.set_xy(L, y + 14)
        p.set_font('Helvetica', '', 10)
        p.set_text_color(*ink)
        p.multi_cell(W, 14, inv['notes'])

    p.set_auto_page_break(False)      # the footer sits in the bottom margin, on this page
    p.set_draw_color(*rule)
    p.set_line_width(0.5)
    p.line(L, 792 - 50, L + W, 792 - 50)
    p.set_xy(L, 792 - 44)
    p.set_font('Helvetica', '', 8.5)
    p.set_text_color(*grey)
    p.cell(W / 3, 12, region)
    p.cell(W / 3, 12, 'Thank you for your business.', align='C')
    p.cell(W / 3, 12, REMIT['web'], align='R')
    return bytes(p.output())


def save_pdf(inv, data):
    """Put the PDF in the PDFs folder; returns its link."""
    from storage import drive_upload

    class F:      # the shape drive_upload expects (a Werkzeug FileStorage)
        filename = f"{inv['id']} {inv['bill_name']}.pdf"[:120]
        stream = io.BytesIO(data)
        mimetype = 'application/pdf'
    return drive_upload(F, os.environ['INVOICES_PDF_FOLDER_ID']).get('webViewLink', '')
