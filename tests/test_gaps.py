from datetime import date, timedelta
import json
import pytest
from postscope.services.gaps import analyze_gaps, isolation_scores


def days(start, counts, status='observed'):
    first=date.fromisoformat(start)
    return [{'date':(first+timedelta(days=i)).isoformat(),'count':count,'status':status}
            for i,count in enumerate(counts)]


def test_calendar_gap_and_seven_day_means_include_zero_days():
    result=analyze_gaps(days('2025-09-14',[10]*7+[0]*4+[20]*7))
    gap=result['items'][0]
    assert (gap['start'],gap['end'],gap['days'])==('2025-09-21','2025-09-24',4)
    assert gap['complete']
    assert gap['before']=={'average':10,'observed_days':7,'active_days':7}
    assert gap['after']=={'average':20,'observed_days':7,'active_days':7}
    assert gap['annual']==[] and gap['score'] is None
    assert result['model']['status']=='insufficient_samples'


def test_calendar_boundaries_use_the_selected_timezone():
    from postscope.services.analysis import calculate, date_bounds
    from postscope.services.normalizer import normalize
    messages=[normalize({'id':1,'date':'2025-09-20T14:30:00Z','text':'x'})[0],
              normalize({'id':2,'date':'2025-09-24T15:30:00Z','text':'x'})[0]]
    a,b=date_bounds('2025-09-20','2025-09-25','Asia/Seoul')
    result,_=calculate(messages,[{'start_utc':a.isoformat(),'end_utc':b.isoformat()}],
                       {'start':'2025-09-20','end':'2025-09-25','timezone':'Asia/Seoul'})
    assert [(g['start'],g['end'],g['days']) for g in result['gaps']['items']]==[('2025-09-21','2025-09-24',4)]


def test_unknown_and_partial_days_break_gaps_and_are_not_zero_activity():
    rows=days('2026-01-01',[1,0,0,0,0,1,0])
    rows[2]['status']='unknown';rows[3]['status']='partial';rows[-1]['status']='partial'
    result=analyze_gaps(rows)
    assert [(g['start'],g['days']) for g in result['items']]==[('2026-01-02',1),('2026-01-05',1)]
    assert all(not g['complete'] for g in result['items'])
    assert result['summary']['unknown_days']==3


def test_boundary_and_incomplete_context_are_never_imputed():
    rows=days('2026-01-01',[0,0,2,0,3,0])
    result=analyze_gaps(rows)
    assert [g['complete'] for g in result['items']]==[False,True,False]
    assert all(g['before']['average'] is None and g['after']['average'] is None for g in result['items'])
    assert all(g['score'] is None for g in result['items'])


@pytest.mark.parametrize('recurring',[False,True])
def test_yearly_comparison_requires_observed_counterpart_and_reports_overlap(recurring):
    rows=days('2025-09-01',[5]*400)
    for row in rows:
        if '2025-09-21'<=row['date']<='2025-09-24' or recurring and '2026-09-21'<=row['date']<='2026-09-24':
            row['count']=0
    gap=analyze_gaps(rows)['items'][0]
    assert gap['annual']==[{'start':'2026-09-21','end':'2026-09-24','overlap_days':4 if recurring else 0,'repeated':recurring}]
    rows[385]['status']='unknown'  # 2026-09-21
    assert rows[385]['date']=='2026-09-21'
    assert analyze_gaps(rows)['items'][0]['annual']==[]


def test_leap_day_is_not_silently_shifted_to_february_28():
    rows=days('2024-02-01',[2]*420)
    for row in rows:
        if row['date']=='2024-02-29':row['count']=0
    assert analyze_gaps(rows)['items'][0]['annual']==[]


def test_model_is_reproducible_and_separates_a_clear_outlier():
    vectors=[[1,2+i%3,3+i%4,0,0] for i in range(40)]+[[20,100,200,1,1]]
    scores=isolation_scores(vectors)
    assert scores==isolation_scores(vectors)
    assert scores[-1]>max(scores[:-1])
    assert all(0<=score<=100 for score in scores)
    assert isolation_scores([[1,1]]*25) is None


def test_model_is_fitted_to_completed_gaps_with_full_context_only():
    counts=[4]*7
    for i in range(25):counts += [0]+[i+1]*8
    result=analyze_gaps(days('2025-01-01',counts))
    assert result['model']['status']=='ready'
    assert result['model']['samples']==25
    assert all(gap['score'] is not None for gap in result['items'])
    json.dumps(result,allow_nan=False)
