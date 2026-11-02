"""Service awards sheet (SERVICE_AWARDS_SHEET_ID, tab "Awards"). The sheet is the source of truth; edit it by hand freely."""
import os
import threading

from storage import worksheet
import sa_logic as L

TAB = 'Awards'
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get('SERVICE_AWARDS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def sheet_url():
    return f"https://docs.google.com/spreadsheets/d/{os.environ.get('SERVICE_AWARDS_SHEET_ID', '')}/edit"


def _ws():
    return worksheet(os.environ['SERVICE_AWARDS_SHEET_ID'], TAB)


def rows():
    """Data rows, read live (the sheet may have been edited by hand a moment ago)."""
    vals = _ws().get_all_values()
    return [r for r in vals[1:] if any(str(c).strip() for c in r)]


def save_month(month, new_rows):
    """Replace one month's rows and rewrite the tab in date order. Returns how many rows the month now has."""
    with _lock:
        ws = _ws()
        out = L.replace_month(rows(), month, new_rows)
        ws.batch_clear(['A2:D'])
        if out:
            ws.update([[int(c) if i == 2 and str(c).strip().isdigit() else str(c) for i, c in enumerate((list(r) + [''] * 4)[:4])] for r in out],
                      'A2', value_input_option='RAW')
    return len(new_rows)
