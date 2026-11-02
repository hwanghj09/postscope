import importlib.util
from pathlib import Path
from datetime import datetime,timezone
import pytest

spec=importlib.util.spec_from_file_location('daily_window',Path(__file__).resolve().parents[1]/'scripts/collect_daily.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    monkeypatch.delenv('POSTSCOPE_START_DATE',raising=False)
    monkeypatch.delenv('POSTSCOPE_LOOKBACK_DAYS',raising=False)


def test_fixed_history_start_is_seoul_midnight_not_rolling_window():
    config={'start_date':'2025-08-01','lookback_days':90}
    for end in ['2026-11-02T12:00:00+00:00','2027-02-02T12:00:00+00:00']:
        start=module.collection_start(config,datetime.fromisoformat(end))
        assert start.isoformat()=='2025-07-31T15:00:00+00:00'


def test_fallback_period_can_include_more_than_a_year():
    end=datetime(2026,11,2,12,tzinfo=timezone.utc)
    assert module.collection_start({'lookback_days':459},end).isoformat()=='2025-07-31T15:00:00+00:00'


@pytest.mark.parametrize('value',['2025-13-01','20250801','2025-08-01T00:00:00','2027-01-01'])
def test_invalid_or_future_start_is_rejected(value):
    with pytest.raises(ValueError):module.collection_start({'start_date':value},datetime(2026,11,2,tzinfo=timezone.utc))


def test_explicit_environment_start_overrides_config(monkeypatch):
    monkeypatch.setenv('POSTSCOPE_START_DATE','2025-09-01')
    assert module.collection_start({'start_date':'2025-08-01'},datetime(2026,11,2,tzinfo=timezone.utc)).isoformat()=='2025-08-31T15:00:00+00:00'
