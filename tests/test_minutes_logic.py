from datetime import date

import minutes_logic as L


def test_names_match_what_the_website_reads():
    assert L.file_name(date(2026, 11, 11), 'Board meeting') == '2026-11 Board Minutes.pdf'
    assert L.file_name(date(2026, 3, 4), 'Special meeting') == '2026-03 Board Minutes (special meeting).pdf'
    assert L.file_name(date(2023, 3, 10), 'Written consent') == '2023-03 Board Minutes (written consent).pdf'
    assert L.file_name(date(2025, 6, 1), 'Budgets approved by email') == '2025 Budgets approved by email.pdf'
    assert L.file_name(date(2026, 11, 11), 'Board meeting', draft=True) == '2026-11 Board Minutes (draft).pdf'
    assert L.published_name('2026-11 Board Minutes (draft).pdf') == '2026-11 Board Minutes.pdf'


def test_uploads():
    assert L.kind_of_upload('minutes.PDF') == 'pdf'
    assert L.kind_of_upload('Oct minutes.docx') == 'convert'
    assert L.kind_of_upload('photo.jpg') is None
    assert L.original_name(date(2026, 10, 14), 'Board meeting', '2026_10 DRSCCA BOD Minutes.doc') == '2026-10 Board Minutes (original).doc'


def test_parse_for_listing():
    assert L.parse('2026-03 Board Minutes (special meeting).pdf') == (date(2026, 3, 1), 'March (special meeting)')
    assert L.parse('2026-11 Board Minutes.pdf') == (date(2026, 11, 1), 'November')
