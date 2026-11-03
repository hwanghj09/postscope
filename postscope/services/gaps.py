"""Calendar-day gaps and retrospective, deterministic Isolation Forest scores."""
import math
import random
from datetime import date, timedelta


def _average(rows):
    known = [row for row in rows if row['status'] == 'observed']
    return {'average': sum(row['count'] for row in known)/7 if len(known) == 7 else None,
            'observed_days': len(known), 'active_days': sum(row['count'] > 0 for row in known)}


def _path_adjustment(size):
    if size <= 1:
        return 0.0
    return 2*sum(1/i for i in range(1, size))-2*(size-1)/size


def isolation_scores(vectors, trees=96, seed=1729):
    """Standard random isolation trees; scores are not event probabilities."""
    if len(vectors) < 2 or len({tuple(row) for row in vectors}) < 2:
        return None
    rng = random.Random(seed)
    size = min(128, len(vectors))
    limit = math.ceil(math.log2(size))

    def build(indices, depth):
        ranges = [(i, min(vectors[j][i] for j in indices), max(vectors[j][i] for j in indices))
                  for i in range(len(vectors[0]))]
        varying = [entry for entry in ranges if entry[1] < entry[2]]
        if len(indices) <= 1 or depth >= limit or not varying:
            return (len(indices),)
        feature, low, high = rng.choice(varying)
        cut = low+(high-low)*rng.random()
        # A strict upper bound guarantees two nonempty children.
        cut = min(cut, math.nextafter(high, low))
        left = [j for j in indices if vectors[j][feature] <= cut]
        right = [j for j in indices if vectors[j][feature] > cut]
        return feature, cut, build(left, depth+1), build(right, depth+1)

    def length(tree, row, depth=0):
        if len(tree) == 1:
            return depth+_path_adjustment(tree[0])
        feature, cut, left, right = tree
        return length(left if row[feature] <= cut else right, row, depth+1)

    forest = [build(rng.sample(range(len(vectors)), size), 0) for _ in range(trees)]
    normalizer = _path_adjustment(size)
    return [round(100*2**(-sum(length(tree, row) for tree in forest)/trees/normalizer), 1)
            for row in vectors]


def analyze_gaps(daily, low_ratio=None):
    """Count zero-post days only when the entire local day was queried."""
    by_date = {row['date']: row for row in daily}
    low = {}
    baseline = None
    for i, row in enumerate(daily):
        if low_ratio is None:
            continue
        if baseline is None:
            context = daily[max(0, i-28):i]
            if len(context) == 28 and all(day['status'] == 'observed' for day in context):
                baseline = sum(day['count'] for day in context)/28
        if baseline and row['status'] == 'observed' and row['count'] <= baseline*low_ratio:
            low[row['date']] = baseline
        else:
            baseline = None
    def is_gap(row):
        return row['status'] == 'observed' and (row['date'] in low if low_ratio is not None else row['count'] == 0)
    items = []
    index = 0
    while index < len(daily):
        row = daily[index]
        if not is_gap(row):
            index += 1
            continue
        first = index
        while index+1 < len(daily) and is_gap(daily[index+1]):
            index += 1
        last = index
        bounded_left = first > 0 and (daily[first-1]['status'] == 'observed' and not is_gap(daily[first-1]) if low_ratio is not None else daily[first-1]['count'] > 0)
        bounded_right = last+1 < len(daily) and (daily[last+1]['status'] == 'observed' and not is_gap(daily[last+1]) if low_ratio is not None else daily[last+1]['count'] > 0)
        items.append({'start': daily[first]['date'], 'end': daily[last]['date'], 'days': last-first+1,
                      'baseline': low.get(daily[first]['date']),
                      'total': sum(day['count'] for day in daily[first:last+1]),
                      'complete': bounded_left and bounded_right,
                      'before': _average(daily[max(0, first-7):first]),
                      'after': _average(daily[last+1:last+8]),
                      'annual': [], 'score': None})
        index += 1

    completed = [item for item in items if item['complete']]
    for item in items:
        start = date.fromisoformat(item['start'])
        days = [start+timedelta(days=i) for i in range(item['days'])]
        for offset in (-1, 1):
            try:
                shifted = [day.replace(year=day.year+offset).isoformat() for day in days]
            except ValueError:  # Feb 29 has no same-date counterpart in a non-leap year.
                continue
            if not all(day in by_date and by_date[day]['status'] == 'observed' for day in shifted):
                continue
            # Compare only bounded gaps, not days at an unknown collection boundary.
            overlap = max((sum(other['start'] <= day <= other['end'] for day in shifted)
                           for other in completed if other is not item), default=0)
            item['annual'].append({'start': shifted[0], 'end': shifted[-1], 'overlap_days': overlap,
                                   'repeated': overlap >= min(2, item['days'])})

    eligible = [item for item in completed if item['before']['average'] is not None and item['after']['average'] is not None]
    model = {'method': 'Isolation Forest', 'samples': len(eligible), 'minimum_samples': 20,
             'trees': 96, 'seed': 1729, 'status': 'insufficient_samples',
             'features': ['log1p(days)', 'log1p(before_7d_average)', 'log1p(after_7d_average)',
                          'annual_overlap_ratio', 'annual_comparison_available']}
    if len(eligible) >= model['minimum_samples']:
        vectors = [[math.log1p(item['days']), math.log1p(item['before']['average']), math.log1p(item['after']['average']),
                    max((entry['overlap_days']/item['days'] for entry in item['annual']), default=0),
                    int(bool(item['annual']))] for item in eligible]
        scores = isolation_scores(vectors)
        model['status'] = 'ready' if scores is not None else 'insufficient_variation'
        if scores is not None:
            for item, score in zip(eligible, scores):
                item['score'] = score
    return {'items': items, 'model': model, 'low_ratio': low_ratio, 'baseline_days': 28,
            'summary': {'total': len(items), 'multi_day': sum(item['days'] >= 2 for item in items),
                        'longest_days': max((item['days'] for item in items), default=0),
                        'unknown_days': sum(row['status'] != 'observed' for row in daily)}}
