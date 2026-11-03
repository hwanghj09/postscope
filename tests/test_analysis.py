import json
import pytest
from datetime import datetime,timezone
from postscope.services.analysis import calculate,exposure,union_windows,date_bounds
from postscope.services.normalizer import normalize,parse_time
from conftest import import_data,analyze


def test_spec_reference_messages(client,workspace,sample):
    assert import_data(client,workspace,sample,True)['state']=='completed'
    response=analyze(client,workspace);assert response.status_code==200,response.text
    d=response.json()
    assert d['metrics']['total']==3
    assert d['weekday']==[3,0,0,0,0,0,0]
    assert d['hours'][0]==2 and d['hours'][12]==1
    with client.app_instance.state.store.connect() as db:
        saved=json.loads(db.execute('SELECT result FROM analysis_runs WHERE id=?',(d['id'],)).fetchone()[0])
    assert [x['seconds'] for x in saved['intervals']]==[30,43170]
    assert d['metrics']['median_interval']==21600
    assert d['metrics']['daily_average']==3
    assert sum(map(sum,d['heatmap']))==3


def test_spec_albums_and_types(client,workspace,sample):
    import_data(client,workspace,sample,True)
    assert analyze(client,workspace,unit='albums').json()['metrics']['total']==2
    assert analyze(client,workspace,content='document').json()['metrics']['total']==2
    assert analyze(client,workspace,unit='albums',content='document').json()['metrics']['total']==1
    assert analyze(client,workspace,content='pdf').json()['metrics']['total']==2


def test_unknown_coverage_no_denominator(client,workspace,sample):
    import_data(client,workspace,sample)
    d=analyze(client,workspace).json()
    assert d['metrics']['daily_average'] is None
    assert d['metrics']['hourly_rate'] is None
    assert all(x is None for row in d['rates'] for x in row)
    assert d['daily'][0]['status']=='unknown'
    assert d['daily'][0]['count']==3
    assert d['quality']['coverage']=='내보내기 범위 미확인'
    albums=analyze(client,workspace,unit='albums').json()
    assert albums['incomplete_albums']==1


def test_no_album_identifier_rejects_switch(client,workspace,sample):
    for m in sample['messages']:m.pop('grouped_id',None)
    import_data(client,workspace,sample)
    assert analyze(client,workspace,unit='albums').status_code==400


def test_album_forms_before_date_and_type_filter(client,workspace,sample):
    sample['messages'][0]['grouped_id']='G1'
    import_data(client,workspace,sample,True)
    # G1 begins 23:59 on the previous day, so is outside the selected day entirely.
    d=analyze(client,workspace,unit='albums',content='document').json()
    assert d['metrics']['total']==0


@pytest.mark.parametrize('count',[0,1])
def test_zero_one_results(client,workspace,sample,count):
    import_data(client,workspace,sample)
    d=analyze(client,workspace,sender='user:9007199254740993' if count else 'unknown',start='2026-11-03',end='2026-11-03').json()
    assert d['metrics']['total']==count
    assert d['metrics']['median_interval'] is None
    if not count:assert d['insight'] is None and d['metrics']['peak_hour_share'] is None
    json.dumps(d,allow_nan=False)


def test_comparison_overlap_and_zero_base(client,workspace,sample):
    import_data(client,workspace,sample,True)
    assert analyze(client,workspace,compare_start='2026-11-02',compare_end='2026-11-04').status_code==400
    d=analyze(client,workspace,compare_start='2026-10-31',compare_end='2026-10-31').json()
    assert d['comparison']['baseline']['total']==0
    assert d['comparison']['percent'] is None and d['comparison']['difference']==3


def test_snapshot_and_cache_revision(client,workspace,sample):
    import_data(client,workspace,sample)
    first=analyze(client,workspace).json()
    assert analyze(client,workspace).json()['id']==first['id']
    sample['messages'].append({'id':6,'date':'2026-11-02T06:00:00Z','from_id':'user3','text':''})
    import_data(client,workspace,sample)
    second=analyze(client,workspace).json()
    assert second['id']!=first['id'] and second['metrics']['total']==4
    with client.app_instance.state.store.connect() as db:
        saved=json.loads(db.execute('SELECT result FROM analysis_runs WHERE id=?',(first['id'],)).fetchone()[0])
        assert saved['metrics']['total']==3


def test_overlap_windows_union_and_gap_exclusion(sample):
    messages=[]
    for raw in sample['messages']:
        m,_=normalize(raw);messages.append(m)
    config={'start':'2026-11-02','end':'2026-11-02','timezone':'Asia/Seoul','session_minutes':10}
    windows=[{'start_utc':'2026-11-01T15:00:00+00:00','end_utc':'2026-11-01T16:00:00+00:00'},
             {'start_utc':'2026-11-01T15:30:00+00:00','end_utc':'2026-11-01T16:00:00+00:00'},
             {'start_utc':'2026-11-02T03:00:00+00:00','end_utc':'2026-11-02T04:00:00+00:00'}]
    d,_=calculate(messages,windows,config)
    assert d['metrics']['observed_hours']==2
    assert d['metrics']['median_interval']==30
    assert d['intervals'][1]['included'] is False
    assert d['daily'][0]['status']=='partial'


def test_dst_repeated_and_missing_hours():
    for day,hours in [('2026-11-01',25),('2026-03-08',23)]:
        a,b=date_bounds(day,day,'America/New_York')
        matrix,daily=exposure([(a,b)],__import__('zoneinfo').ZoneInfo('America/New_York'))
        assert sum(map(sum,matrix))==hours
        assert daily[day]==hours
    a,b=date_bounds('2026-11-01','2026-11-01','America/New_York')
    matrix,_=exposure([(a,b)],__import__('zoneinfo').ZoneInfo('America/New_York'))
    assert matrix[6][1]==2
    with pytest.raises(ValueError):parse_time('2026-11-01T01:30:00','America/New_York')


def test_fractional_timezone():
    a,b=date_bounds('2026-11-02','2026-11-02','Asia/Kathmandu')
    matrix,daily=exposure([(a,b)],__import__('zoneinfo').ZoneInfo('Asia/Kathmandu'))
    assert daily['2026-11-02']==24
    assert matrix[0]==[1]*24


def test_weekday_weekend_means_exclude_partial_days_from_numerator_and_denominator():
    messages=[normalize({'id':1,'date':'2026-11-02T00:30:00Z','text':'x'})[0],
              normalize({'id':2,'date':'2026-11-07T00:30:00Z','text':'x'})[0]]
    windows=[{'start_utc':'2026-11-02T00:00:00+00:00','end_utc':'2026-11-03T00:00:00+00:00'},
             {'start_utc':'2026-11-07T00:00:00+00:00','end_utc':'2026-11-07T01:00:00+00:00'}]
    result,_=calculate(messages,windows,{'start':'2026-11-02','end':'2026-11-07','timezone':'UTC'})
    assert result['weekday_occurrences']==[1,0,0,0,0,0,0]
    assert result['weekday_hour_complete'][0][0]==1
    assert result['weekday_hour_complete'][5][0]==0
    assert result['heatmap'][5][0]==1
