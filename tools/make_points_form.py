"""Build static/worker-points-form.xlsx: the region's worker point form (the layout volunteer leads have always used),
with room for 80 names and a Total line, so Members > Points > Add points can read it back. Run after changing it:
venv/bin/python tools/make_points_form.py"""
import os
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

ROWS = 40                  # two columns of 40 names
wb = Workbook()
ws = wb.active
ws.title = 'Point Submission'
bold = Font(bold=True)
for r, text in enumerate(['DETROIT REGION SCCA', 'Event name:', 'Dates:', 'WORKER POINTS', 'SPECIALTY:'], 1):
    ws.cell(r, 1, text).font = Font(bold=r in (1, 4), size=14 if r == 1 else 11)
head = 7
line = Side(style='thin', color='999999')
for col in (1, 4):
    ws.cell(head, col, "Worker's Name").font = bold
    ws.cell(head, col + 1, 'Points').font = bold
    for r in range(head + 1, head + 1 + ROWS):
        ws.cell(r, col).border = Border(bottom=line)
        ws.cell(r, col + 1).border = Border(bottom=line)
tot = head + ROWS + 2
ws.cell(tot, 4, 'Total').font = bold
ws.cell(tot, 5, f'=SUM(B{head + 1}:B{head + ROWS})+SUM(E{head + 1}:E{head + ROWS})').font = bold
foot = tot + 3
for k, text in enumerate(['Submitted by:', '', 'Upload this sheet in DRSCCA Apps: Members > Points > Add points,',
                          'or email it to membership@drscca.org. Due within 30 days of the event.', '',
                          'Points per day: event chair (one per event) 4; special events 3;',
                          'chief (one per specialty) or assistant chair (one per event) 2; worker 1.']):
    ws.cell(foot + k, 1, text)
ws.column_dimensions['A'].width = 30
ws.column_dimensions['B'].width = 9
ws.column_dimensions['C'].width = 4
ws.column_dimensions['D'].width = 30
ws.column_dimensions['E'].width = 9
for r in range(head, tot + 1):
    for c in (2, 5):
        ws.cell(r, c).alignment = Alignment(horizontal='center')
ws.print_title_rows = f'{head}:{head}'
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'worker-points-form.xlsx')
wb.save(out)
print(out)
