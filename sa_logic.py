"""Service awards rules, free of Flask and Google so they can be tested on their own.

The awards live in a Google Sheet (tab "Awards": Month | Name | Years | Note), one row per member per month,
so anyone can fix a name by hand. drscca.org reads the same sheet and lays it out.
"""
import re
from datetime import date

MILESTONES = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)
COLS = ['Month', 'Name', 'Years', 'Note']


def valid_month(m):
    if not re.fullmatch(r'\d{4}-\d{2}', m or ''):
        return False
    return 1 <= int(m[5:]) <= 12


def next_month(today=None):
    t = today or date.today()
    return f'{t.year + (t.month == 12):04d}-{t.month % 12 + 1:02d}'


def rows_from_form(month, by_years):
    """{years: 'one name per line'} -> [[month, name, years, '']], skipping blank lines, de-duplicated per month."""
    out, seen = [], set()
    for years in sorted(by_years, reverse=True):
        for line in (by_years[years] or '').splitlines():
            name = re.sub(r'\s+', ' ', line).strip(' ,;\t')
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append([month, name, int(years), ''])
    return out


def by_month(rows):
    """[[Month, Name, Years, Note], ...] -> [(month, {years: [names]})] newest first; malformed rows skipped."""
    months = {}
    for r in rows:
        r = list(r) + [''] * (4 - len(r))
        m, n = str(r[0]).strip(), str(r[1]).strip()
        try:
            y = int(str(r[2]).strip())
        except ValueError:
            continue
        if valid_month(m) and n and y > 0:
            months.setdefault(m, {}).setdefault(y, []).append(n)
    return [(m, dict(sorted(g.items(), reverse=True))) for m, g in sorted(months.items(), reverse=True)]


def replace_month(rows, month, new_rows):
    """All rows with this month's rows swapped for new_rows, newest month first (the sheet's own order)."""
    keep = [list(r) for r in rows if str((list(r) + [''])[0]).strip() != month]
    out = keep + [list(r) for r in new_rows]
    return sorted(out, key=lambda r: str(r[0]), reverse=True)
