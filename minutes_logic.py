"""Board meeting minutes: naming rules, free of Flask and Google so they test alone.

The website lists the public Board Minutes folder by year and reads the month from the file name, so every file is
named the same way: "2026-11 Board Minutes.pdf", "2026-03 Board Minutes (special meeting).pdf",
"2025 Budgets approved by email.pdf".
"""
import re
from datetime import date

KINDS = ['Board meeting', 'Special meeting', 'Written consent', 'Budgets approved by email']
CONVERTIBLE = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword',
    '.odt': 'application/vnd.oasis.opendocument.text',
    '.rtf': 'application/rtf',
    '.txt': 'text/plain',
    '.html': 'text/html',
}
PDF = 'application/pdf'


def file_name(meeting, kind, draft=False):
    """The standard PDF name for a meeting date (date) and kind."""
    if kind == 'Budgets approved by email':
        base = f'{meeting.year} Budgets approved by email'
    else:
        suffix = {'Special meeting': ' (special meeting)', 'Written consent': ' (written consent)'}.get(kind, '')
        base = f'{meeting.year}-{meeting.month:02d} Board Minutes{suffix}'
    return base + (' (draft)' if draft else '') + '.pdf'


def original_name(meeting, kind, upload_name):
    """What the uploaded original is kept as, next to the PDF in the private folder."""
    ext = (re.search(r'\.[A-Za-z0-9]{2,5}$', upload_name or '') or [''])[0].lower()
    return file_name(meeting, kind).replace('.pdf', f' (original){ext}')


def kind_of_upload(upload_name):
    """'pdf', 'convert' or None (not a format the app can handle)."""
    ext = (re.search(r'\.[A-Za-z0-9]{2,5}$', upload_name or '') or [''])[0].lower()
    if ext == '.pdf':
        return 'pdf'
    return 'convert' if ext in CONVERTIBLE else None


def mime_for(upload_name):
    ext = (re.search(r'\.[A-Za-z0-9]{2,5}$', upload_name or '') or [''])[0].lower()
    return PDF if ext == '.pdf' else CONVERTIBLE.get(ext, 'application/octet-stream')


def published_name(draft_name):
    return draft_name.replace(' (draft)', '')


def parse(name):
    """(date or None, label) for listing files by month."""
    m = re.match(r'^(\d{4})-(\d{2}) Board Minutes(?: \((.*?)\))?(?: \(draft\))?\.pdf$', name or '')
    if m:
        d = date(int(m.group(1)), int(m.group(2)), 1)
        return d, d.strftime('%B') + (f' ({m.group(3)})' if m.group(3) and m.group(3) != 'draft' else '')
    m = re.match(r'^(\d{4}) Budgets approved by email', name or '')
    if m:
        return date(int(m.group(1)), 12, 31), 'Budgets approved by email'
    return None, name
