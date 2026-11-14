"""Optional observed-file acceptance under the saved research configuration.

This compares original producer input constraints and five metadata fields. It
does not authenticate a provider, reproduce results or execute a strategy.
"""
from decimal import DecimalException

from . import research_report
from .research_fixture import read_fixture_snapshot, validate_fixture_check


_METADATA = ('symbol', 'timeframe', 'bars', 'first_bar_ms', 'last_bar_ms')
_FIELDS = {'status', 'scope', 'run_id', 'fixture_sha256', 'expected', 'observed',
           'mismatches', 'error'}


def _metadata(spec):
    frame = spec['timeframe']
    duration = int(frame[:-1]) * {'m':60000, 'h':3600000, 'd':86400000, 'w':604800000}[frame[-1]]
    return dict(symbol=spec['symbol'], timeframe=frame, bars=spec['bars'],
                first_bar_ms=spec['first_bar_ms'],
                last_bar_ms=spec['first_bar_ms'] + (spec['bars']-1)*duration)


def _checked_metadata(value, report, digest):
    if type(value) is not dict or set(value) != set(_METADATA):
        raise ValueError('invalid_metadata')
    source = report['experiment']
    spec = research_report.experiment(dict(
        **{key:value[key] for key in _METADATA if key != 'last_bar_ms'},
        fixture_sha256=digest, candidates=source['candidates'], settings=source['settings']))
    if report['schema_version'] == 2:
        research_report.check_robustness_budget(spec)
    last = research_report.integer(value['last_bar_ms'], 0, 253402300799999)
    clean = _metadata(spec)
    if last != clean['last_bar_ms']:
        raise ValueError('invalid_last_bar')
    return clean


def validate_fixture_data_check(value, report, fixture_check):
    """Validate/copy the report-and-byte-bound result without reading or parsing."""
    try:
        report = research_report.validate_report(report)
        byte_check = validate_fixture_check(fixture_check, report['experiment']['fixture_sha256'])
        digest = byte_check['actual_sha256']
        if (type(value) is not dict or set(value) != _FIELDS
                or value['scope'] != 'research_input_consistency'
                or type(value['status']) is not str
                or value['status'] not in {'matched', 'mismatch', 'invalid'}
                or value['run_id'] != report['run_id']
                or value['fixture_sha256'] != digest
                or type(value['mismatches']) is not list):
            raise ValueError('invalid_context')
        expected = _checked_metadata(value['expected'], report, report['experiment']['fixture_sha256'])
        if expected != _metadata(report['experiment']):
            raise ValueError('foreign_metadata')
        if value['status'] == 'invalid':
            if (value['observed'] is not None or value['mismatches']
                    or value['error'] != 'research_input_invalid'):
                raise ValueError('invalid_failed_context')
            observed, differences = None, []
        else:
            observed = _checked_metadata(value['observed'], report, digest)
            differences = [key for key in _METADATA if expected[key] != observed[key]]
            if (value['mismatches'] != differences or value['error'] is not None
                    or value['status'] != ('mismatch' if differences else 'matched')):
                raise ValueError('inconsistent_context')
        return dict(status=value['status'], scope='research_input_consistency',
                    run_id=report['run_id'], fixture_sha256=digest, expected=expected,
                    observed=observed, mismatches=differences, error=value['error'])
    except (ValueError, KeyError, TypeError, DecimalException, OverflowError):
        raise ValueError('research_fixture_data_check_invalid') from None


def check_fixture_data(path, report):
    """Observe once, then apply the producer's rules to those exact bytes."""
    report = research_report.validate_report(report)
    spec = report['experiment']
    byte_check, raw = read_fixture_snapshot(path, spec['fixture_sha256'])
    result = dict(status='invalid', scope='research_input_consistency',
                  run_id=report['run_id'], fixture_sha256=byte_check['actual_sha256'],
                  expected=_metadata(spec), observed=None, mismatches=[], error='research_input_invalid')
    # These modules define pure validation. Only the explicit data path imports
    # the existing data/provenance definitions; no acquisition is invoked.
    from .fixture_input import load_fixture
    from .research_input import prepare_research_input
    try:
        data = load_fixture(raw)
        observed_spec, clean = prepare_research_input(
            data, byte_check['actual_sha256'], candidates=spec['candidates'],
            settings=spec['settings'], robustness=report['schema_version'] == 2)
        observed = _metadata(observed_spec)
        observed['last_bar_ms'] = clean[-1][0]
        differences = [key for key in _METADATA if result['expected'][key] != observed[key]]
        result.update(status='mismatch' if differences else 'matched', observed=observed,
                      mismatches=differences, error=None)
    except (ValueError, TypeError, KeyError, DecimalException, OverflowError, RecursionError):
        # Expected invalid source content gets a bounded diagnostic, not parsed
        # values or an exception message. Unexpected failures remain failures.
        pass
    return byte_check, validate_fixture_data_check(result, report, byte_check)
