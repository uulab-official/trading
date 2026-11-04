"""Bounded, ordered comparison of saved evidence; no DB, network or execution.

Every adjacent interval is checked before an overall report can be returned.
Input order is explicit: neither capture time nor filename selects the order.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from trading_core.paper_evidence import PaperEvidenceInvalid, _read_evidence
from trading_core.paper_evidence_compare import (
    PaperEvidenceComparisonError, _compare_captured_evidence, _DIFF_CONTEXT,
)

MAX_SNAPSHOTS = 32
MAX_SEQUENCE_BYTES = 64 * 1024 * 1024
_AMOUNT_FIELDS = ('delta', 'from_new_fills', 'rounding_delta')


def compare_evidence_sequence(paths):
    """Read each argument once, reconcile all intervals, then return one report.

    Accept only a bounded list/tuple, never an unbounded iterator. At most the
    first and two adjacent full captures are retained. Reports describe captured
    bytes, not the current contents of their paths or an authenticated history.
    """
    if not isinstance(paths, (list, tuple)) or not 2 <= len(paths) <= MAX_SNAPSHOTS:
        raise PaperEvidenceInvalid('paper_evidence_invalid')
    paths = tuple(paths)  # Caller mutation cannot change the remaining inputs.
    total_bytes = 0

    def read(path):
        nonlocal total_bytes
        capture = _read_evidence(path)
        total_bytes += len(capture[0])
        if total_bytes > MAX_SEQUENCE_BYTES:
            raise PaperEvidenceInvalid('paper_evidence_invalid')
        return capture

    baseline = previous = read(paths[0])
    intervals, warnings = [], []
    totals = dict(new_fills=0, new_orders=0, observed_fill_confirmations=0,
                  changed_order_observations=0, control_change_intervals=0)
    sums = {group: {key: Decimal(0) for key in _AMOUNT_FIELDS} for group in ('cash', 'fees')}
    repeated = 0
    for index, path in enumerate(paths[1:], 1):
        current = read(path)
        try:
            pair = _compare_captured_evidence(previous, current)
        except PaperEvidenceComparisonError as error:
            error.interval_index = index
            raise
        repeated += pair['relation'] == 'same_snapshot'
        counts = dict(new_fills=pair['new_fills']['count'], new_orders=pair['orders']['new']['count'],
                      confirmed_existing_fills=pair['orders']['confirmed_existing_fills'])
        totals['new_fills'] += counts['new_fills']
        totals['new_orders'] += counts['new_orders']
        totals['observed_fill_confirmations'] += counts['confirmed_existing_fills']
        totals['changed_order_observations'] += pair['orders']['changed']['count']
        totals['control_change_intervals'] += pair['control']['changed']
        with localcontext(_DIFF_CONTEXT):
            for group in sums:
                for key in _AMOUNT_FIELDS:
                    # Generated differences may have more digits than source
                    # coefficients; do not reapply the input coefficient bound.
                    sums[group][key] += Decimal(pair[group][key])
        intervals.append(dict(index=index, before=pair['before'], after=pair['after'],
            relation=pair['relation'], revision_delta=pair['revision_delta'],
            cash=pair['cash'], fees=pair['fees'], **counts,
            unresolved_before=pair['orders']['unresolved_before'],
            unresolved_after=pair['orders']['unresolved_after'],
            control_changed=pair['control']['changed'], warnings=pair['warnings']))
        for warning in pair['warnings']:
            if warning not in warnings:
                warnings.append(warning)
        previous = current

    # Full endpoint data, not truncated interval detail arrays, determine totals.
    overall = pair if len(paths) == 2 else _compare_captured_evidence(baseline, previous)
    matches = (totals['new_fills'] == overall['new_fills']['count']
               and totals['new_orders'] == overall['orders']['new']['count']
               and all(sums[group][key] == Decimal(overall[group][key])
                       for group in sums for key in _AMOUNT_FIELDS))
    if not matches:
        raise PaperEvidenceComparisonError('sequence_totals_conflict')
    for warning in overall['warnings']:
        if warning not in warnings:
            warnings.append(warning)
    return dict(schema_version=1, source='paper_evidence_sequence', mode='paper',
        read_only=True, live_enabled=False, scope='supplied_observations_not_continuous_history',
        compared_at=datetime.now(timezone.utc).isoformat(), snapshot_count=len(paths),
        verified_intervals=len(intervals), repeated_intervals=repeated,
        input_bytes=total_bytes, intervals=intervals, interval_totals=totals,
        overall=overall, reconciliation=dict(all_intervals_valid=True,
            interval_totals_match=True, endpoint_valid=True, live_authorization=False),
        warnings=warnings)
