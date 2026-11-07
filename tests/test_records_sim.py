"""The Sim Racing import's naming rules (tools/records/sim_import.py). Made-up names only: the repo is public."""
import importlib.util
import pathlib
from datetime import datetime

spec = importlib.util.spec_from_file_location('sim_import', pathlib.Path(__file__).parent.parent / 'tools' / 'records' / 'sim_import.py')
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def test_series_from_the_league_season_name():
    assert S.series('DRSCCA F4 - FALL 2025') == 'F4'
    assert S.series('Global Mazda MX5 Season 2') == 'MX5'
    assert S.series('DRSCCA RAY 1600 SUMMER 2025') == 'FF1600'
    assert S.series('SCCA Spec Racer Ford - Season 2') == 'SRF'
    assert S.series('DRSCCA PCUP fall') == 'PCUP'


def test_season_label_from_the_name_or_the_first_race():
    assert S.label('DRSCCA GT4 WINTER 2025', datetime(2025, 2, 6)) == 'Winter 2025'
    assert S.label('DRSCCA PCUP fall', datetime(2026, 9, 16)) == 'Fall 2026'
    assert S.label('Global Mazda MX5 Season 2', datetime(2024, 5, 16)) == 'Spring II 2024'
    assert S.season_word('Spring II 2024') == 'Spring II'


def test_race_dates_and_driver_names():
    assert S.when('Sep 14, 2026, 4:05 PM') == datetime(2026, 9, 14, 16, 5)
    assert S.driver('Pat Example2') == 'Pat Example'      # iRacing numbers repeated names
    assert S.driver('Lee Sample') == 'Lee Sample'
