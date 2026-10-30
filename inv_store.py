"""Reads and writes the region's invoices: the DRSCCA Invoices spreadsheet (INVOICES_SHEET_ID) in the DRSCCA Apps
shared drive, Invoices folder, with each invoice's PDF in the PDFs folder beside it (INVOICES_PDF_FOLDER_ID).

  Invoices   one row per invoice (INV-2026-001): who it bills, program and event, dates, total, status, payment
  Lines      one row per line item, joined by invoice id

Plain column headers, so the region can keep invoicing by hand from the sheet if the app is gone.
"""
import io
import os
import threading
import time
from datetime import date, timedelta

from filelock import FileLock

from storage import DATA_DIR, open_book, worksheet

INV_COLS = ['id', 'created', 'status', 'program', 'event_code', 'event_name', 'bill_name', 'bill_contact', 'bill_email', 'bill_address',
            'total', 'due', 'notes', 'created_by', 'pdf_url', 'paid_date', 'paid_ref', 'paid_amount', 'closed_by', 'closed_note']
INV_LABELS = ['Invoice #', 'Date', 'Status', 'Program', 'Event code', 'Event', 'Bill to', 'Attention', 'Email', 'Address',
              'Total', 'Due', 'Notes', 'Created by', 'PDF', 'Paid on', 'Payment reference', 'Amount paid', 'Closed by', 'Closing note']
LINE_COLS = ['invoice_id', 'description', 'quantity', 'unit_price', 'amount']
LINE_LABELS = ['Invoice #', 'Description', 'Quantity', 'Unit price', 'Amount']
NUMERIC = {'total', 'paid_amount', 'quantity', 'unit_price', 'amount'}
STATUSES = ['Open', 'Paid', 'Void']
TERMS_DAYS = 30
REMIT = {'payee': 'Detroit Region SCCA', 'lines': ['PO Box 401018', 'Redford, MI 48240-9018'], 'email': 'treasurer@drscca.org', 'web': 'drscca.org'}
CACHE_SECONDS = 15
LOCK = os.path.join(DATA_DIR, 'invoices.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('INVOICES_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['INVOICES_SHEET_ID'], title)


def refresh():
    _cache.clear()


def _num(v):
    try:
        return float(v) if str(v).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _snapshot():
    hit = _cache.get('snap')
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    res = open_book(os.environ['INVOICES_SHEET_ID']).values_batch_get(['Invoices!A:T', 'Lines!A:E'],
                                                                     params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], []]
    invs = [dict(zip(INV_COLS, list(r) + [''] * len(INV_COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
    lines = [dict(zip(LINE_COLS, list(r) + [''] * len(LINE_COLS))) for r in vr[1][1:] if r and str(r[0]).strip()]
    for row in invs + lines:
        for k in list(row):
            row[k] = _num(row[k]) if k in NUMERIC else str(row[k])
    _cache['snap'] = (time.time(), (invs, lines))
    return invs, lines


def invoices():
    return sorted(_snapshot()[0], key=lambda r: r['id'], reverse=True)


def get(inv_id):
    invs, lines = _snapshot()
    r = next((r for r in invs if r['id'] == inv_id), None)
    return dict(r, lines=[ln for ln in lines if ln['invoice_id'] == inv_id]) if r else None


def _cell(v):
    return '' if v is None else v if isinstance(v, (int, float)) else str(v)


def create(inv, lines):
    """inv: INV_COLS fields except id/created/status/total/due; lines: [{description, quantity, unit_price}]. Returns the new invoice."""
    with FileLock(LOCK), _lock:
        ws = _ws('Invoices')
        year = date.today().year
        prefix = f'INV-{year}-'
        nums = [int(v[len(prefix):]) for v in ws.col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
        iid = f'{prefix}{max(nums, default=0) + 1:03d}'
        rows = [dict(ln, invoice_id=iid, amount=round((ln['quantity'] or 1) * ln['unit_price'], 2)) for ln in lines]
        total = round(sum(r['amount'] for r in rows), 2)
        today = date.today()
        row = dict(inv, id=iid, created=today.isoformat(), status='Open', total=total,
                   due=inv.get('due') or (today + timedelta(days=TERMS_DAYS)).isoformat())
        ws.append_row([_cell(row.get(c)) for c in INV_COLS], value_input_option='RAW')
        _ws('Lines').append_rows([[_cell(r.get(c)) for c in LINE_COLS] for r in rows], value_input_option='RAW')
        refresh()
        return dict(row, lines=rows)


def update(inv_id, **fields):
    with _lock:
        ws = _ws('Invoices')
        n = ws.col_values(1).index(inv_id) + 1
        cur = dict(zip(INV_COLS, ws.row_values(n) + [''] * len(INV_COLS)))
        cur.update(fields)
        for k in NUMERIC & set(INV_COLS):
            cur[k] = _num(cur[k])
        ws.update([[_cell(cur[c]) for c in INV_COLS]], f'A{n}', value_input_option='RAW')
        refresh()


def setup_sheet(book):
    """Headers and the How to use tab (first run, or to bring them up to date)."""
    have = {w.title: w for w in book.worksheets()}
    for title, labels in (('Invoices', INV_LABELS), ('Lines', LINE_LABELS)):
        ws = have.get(title) or book.add_worksheet(title, rows=500, cols=len(labels))
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
        'The apps at drevent.drscca.org write here and make the PDFs. If they are gone, add a row here, write the invoice from the last PDF as a template, and keep the numbering going.']],
        'A1', value_input_option='RAW')
    guide.format('A1', {'textFormat': {'bold': True, 'fontSize': 14}})
    for w in book.worksheets():
        if w.title in ('Sheet1',):
            book.del_worksheet(w)
    book.reorder_worksheets([guide] + [w for w in book.worksheets() if w.title != 'How to use'])


# The invoice as a PDF (fpdf2, no I/O).

def money(v):
    return f"${(v or 0):,.2f}"


def pdf_bytes(inv, region='Detroit Region SCCA'):
    from fpdf import FPDF
    p = FPDF(unit='pt', format='Letter')
    p.set_auto_page_break(True, margin=54)
    p.add_page()
    L, W = 54, 612 - 108
    navy, grey = (20, 40, 80), (110, 110, 110)
    # The region logo (static/drscca-logo.png, 700x471) at the top left, the region's name and address beside it.
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'drscca-logo.png')
    lh = 68
    tx = L
    if os.path.exists(logo):
        p.image(logo, x=L, y=48, h=lh)
        tx = L + lh * 700 / 471 + 14
    p.set_text_color(*navy)
    p.set_font('Helvetica', 'B', 17)
    p.set_xy(tx, 50)
    p.cell(W / 2, 20, region)
    p.set_font('Helvetica', 'B', 26)
    p.set_xy(L + W / 2, 50)
    p.cell(W / 2, 30, 'INVOICE', align='R')
    p.set_font('Helvetica', '', 9.5)
    p.set_text_color(*grey)
    y = 72
    for t in ['Sports Car Club of America', *REMIT['lines'], f"{REMIT['email']} - {REMIT['web']}"]:
        p.set_xy(tx, y)
        p.cell(W / 2, 12.5, t)
        y += 12.5
    y = max(y, 48 + lh)
    p.set_text_color(0, 0, 0)
    y2 = 88
    for k, v in (('Invoice #', inv['id']), ('Date', inv['created']), ('Due', inv['due'])):
        p.set_xy(L + W / 2, y2)
        p.set_font('Helvetica', '', 10)
        p.cell(W / 4, 14, k, align='R')
        p.set_font('Helvetica', 'B', 10)
        p.cell(W / 4, 14, v, align='R')
        y2 += 15
    y = max(y, y2) + 22
    p.set_draw_color(*navy)
    p.set_line_width(1.2)
    p.line(L, y, L + W, y)
    y += 14
    p.set_xy(L, y)
    p.set_font('Helvetica', 'B', 9)
    p.set_text_color(*grey)
    p.cell(W / 2, 12, 'BILL TO')
    if inv.get('event_name') or inv.get('program'):
        p.set_xy(L + W / 2, y)
        p.cell(W / 2, 12, 'FOR', align='R')
    p.set_text_color(0, 0, 0)
    y += 14
    bill = [inv['bill_name'], inv.get('bill_contact') and f"Attn: {inv['bill_contact']}", *(inv.get('bill_address') or '').splitlines(), inv.get('bill_email')]
    fory = y
    for t in [b for b in bill if b]:
        p.set_xy(L, y)
        p.set_font('Helvetica', 'B' if t == inv['bill_name'] else '', 10.5)
        p.cell(W / 2, 14, t)
        y += 14
    for t in [inv.get('event_name'), inv.get('program_name')]:
        if t:
            p.set_xy(L + W / 2, fory)
            p.set_font('Helvetica', '', 10.5)
            p.multi_cell(W / 2, 14, t, align='R')
            fory += 14
    y = max(y, fory) + 18
    cols = [(W * 0.58, 'Description', 'L'), (W * 0.12, 'Qty', 'R'), (W * 0.15, 'Unit price', 'R'), (W * 0.15, 'Amount', 'R')]
    p.set_fill_color(*navy)
    p.set_text_color(255, 255, 255)
    p.set_font('Helvetica', 'B', 10)
    p.set_xy(L, y)
    for w, t, a in cols:
        p.cell(w, 20, f' {t} ' if a == 'L' else f'{t} ', fill=True, align=a)
    y += 20
    p.set_text_color(0, 0, 0)
    p.set_font('Helvetica', '', 10.5)
    p.set_draw_color(220, 220, 220)
    p.set_line_width(0.6)
    for ln in inv['lines']:
        q = ln['quantity'] or 1
        p.set_xy(L, y)
        p.multi_cell(cols[0][0], 18, f" {ln['description']}", new_x='RIGHT', new_y='TOP')
        h = max(18, p.get_y() - y) if p.get_y() > y else 18
        p.set_xy(L + cols[0][0], y)
        p.cell(cols[1][0], 18, f'{q:g} ', align='R')
        p.cell(cols[2][0], 18, f"{money(ln['unit_price'])} ", align='R')
        p.cell(cols[3][0], 18, f"{money(ln['amount'])} ", align='R')
        y += h + 4
        p.line(L, y, L + W, y)
        y += 4
    y += 6
    p.set_xy(L + W * 0.55, y)
    p.set_font('Helvetica', 'B', 12)
    p.cell(W * 0.30, 22, 'Total due ', align='R')
    p.cell(W * 0.15, 22, f"{money(inv['total'])} ", align='R')
    y += 46
    p.set_xy(L, y)
    p.set_font('Helvetica', 'B', 10)
    p.set_text_color(*navy)
    p.cell(W, 14, 'How to pay')
    y += 16
    p.set_text_color(0, 0, 0)
    p.set_font('Helvetica', '', 10)
    for t in [f"Please make checks payable to {REMIT['payee']} and mail to {REMIT['lines'][0]}, {REMIT['lines'][1]}.",
              f"Write invoice number {inv['id']} on your check. Questions: {REMIT['email']}."]:
        p.set_xy(L, y)
        p.multi_cell(W, 14, t)
        y = p.get_y()
    if inv.get('notes'):
        y += 10
        p.set_xy(L, y)
        p.set_font('Helvetica', 'I', 10)
        p.multi_cell(W, 14, inv['notes'])
    p.set_auto_page_break(False)      # the footer sits in the bottom margin, on this page
    p.set_xy(L, 792 - 40)
    p.set_font('Helvetica', '', 9)
    p.set_text_color(*grey)
    p.cell(W, 12, f'Thank you for supporting the {region}.', align='C')
    return bytes(p.output())


def save_pdf(inv, data):
    """Put the PDF in the PDFs folder; returns its link."""
    from storage import drive_upload

    class F:      # the shape drive_upload expects (a Werkzeug FileStorage)
        filename = f"{inv['id']} {inv['bill_name']}.pdf"[:120]
        stream = io.BytesIO(data)
        mimetype = 'application/pdf'
    return drive_upload(F, os.environ['INVOICES_PDF_FOLDER_ID']).get('webViewLink', '')
