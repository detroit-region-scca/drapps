from datetime import date

import equip_pdf
import equip_store as E


def blank(cols, **kw):
    return dict({c: '' for c in cols}, **kw)


def test_report_builds_with_every_section():
    items = [blank(E.ITEM_COLS, id='EQ-001', name='Race trailer – 22’', category='Trailer', status='In use', place='Farm',
                   cost=10000.0, life_years=20.0, year=2016.0, quantity=1.0, replace_cost=None, replace_year=None),
             blank(E.ITEM_COLS, id='EQ-002', name='Cones', category='Course', status='In use', place='Farm', quantity=300.0,
                   cost=None, life_years=None, year=None, replace_cost=None, replace_year=None)]
    places = [blank(E.PLACE_COLS, id='PL-001', name='Farm', cost_month=None, cost_year=1200.0)]
    tasks = [blank(E.TASK_COLS, id='TK-001', task='Renew registration', due='2026-11-20', repeat_months=12.0, cost=50.0),
             blank(E.TASK_COLS, id='TK-002', task='Software', due='2026-01-05', repeat_months=12.0, cost=300.0, auto='yes', category='Software and web')]
    docs = [dict(blank(E.DOC_COLS, for_id='EQ-001', for_name='Race trailer'), have=[('Title', [])], missing=['Registration'], others=[], error='')]
    pdf = equip_pdf.build(items, places, tasks, docs=docs, today=date(2026, 11, 4))
    assert pdf[:5] == b'%PDF-' and len(pdf) > 3000
    assert equip_pdf.build(items, places, tasks, docs=None, today=date(2026, 11, 4))[:5] == b'%PDF-'


def test_clean_makes_text_the_built_in_font_can_draw():
    assert equip_pdf.clean('A – B “q” × 2') == 'A - B "q" x 2'
