from datetime import date

import sa_logic as L


def test_rows_from_form_one_name_per_line_highest_milestone_first():
    rows = L.rows_from_form('2026-11', {5: 'Emily Brown\n\n  Cade  Wilson ,', 20: 'Donald Badaczewski', 10: ''})
    assert rows == [['2026-11', 'Donald Badaczewski', 20, ''], ['2026-11', 'Emily Brown', 5, ''], ['2026-11', 'Cade Wilson', 5, '']]


def test_rows_from_form_drops_repeats():
    assert len(L.rows_from_form('2026-11', {5: 'Ann Lee\nann lee', 10: 'Ann Lee'})) == 1


def test_by_month_groups_and_skips_hand_typos():
    rows = [['2026-10', 'A B', '5'], ['2026-10', 'C D', '20'], ['2026-09', 'E F', '10'], ['Oct 2026', 'X', '5'], ['2026-10', '', '5'], ['2026-10', 'G', 'x']]
    assert L.by_month(rows) == [('2026-10', {20: ['C D'], 5: ['A B']}), ('2026-09', {10: ['E F']})]


def test_replace_month_swaps_only_that_month():
    rows = [['2026-10', 'A', '5', ''], ['2026-09', 'B', '5', '']]
    out = L.replace_month(rows, '2026-10', [['2026-10', 'C', 10, '']])
    assert out == [['2026-10', 'C', 10, ''], ['2026-09', 'B', '5', '']]


def test_next_month_rolls_over_the_year():
    assert L.next_month(date(2026, 12, 5)) == '2027-01'
    assert L.next_month(date(2026, 3, 1)) == '2026-04'


def test_page_renders_for_a_month_with_and_without_awards():
    from flask import Flask
    import os
    app = Flask('t', template_folder=os.path.join(os.path.dirname(__file__), '..', 'templates'))
    app.add_url_rule('/sa/', 'sa.home', lambda: '')
    app.add_url_rule('/', 'home', lambda: '')
    with app.test_request_context('/sa/'):
        from flask import render_template_string
        tpl = open(os.path.join(os.path.dirname(__file__), '..', 'templates', 'sa.html')).read().split('{% block content %}')[1].split('{% endblock %}')[0]
        for current in ({}, {20: ['Donald Badaczewski'], 5: ['Emily Brown']}):
            html = render_template_string(tpl, sheet_url='x', milestones=L.MILESTONES, months=[], month='2026-10', current=current)
            assert 'Save 2026-10' in html
