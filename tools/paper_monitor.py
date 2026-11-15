"""One-shot read-only checks of a supervised PAPER pair and its native stores.

No service activation, network connection, automatic retry, or alert delivery.
A nonzero exit permits an operator's external scheduler to notify them.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


TOOLS = Path(__file__).resolve().parent
_SPECS = {
    'service': ('paper_service.py', 'running'),
    'capture': ('market_capture.py', 'current'),
    'account': ('paper_stream.py', 'current'),
}
_MAX_OUTPUT = 65536


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('paper_monitor_arguments_invalid')


def _arguments(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in _SPECS:
        parser.add_argument('--' + name, required=(name == 'service'))
    parser.add_argument('--timeout-seconds', type=int, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 60:
        raise ValueError('paper_monitor_arguments_invalid')
    for name in _SPECS:
        value = getattr(args, name)
        if value is not None and (not Path(value).is_absolute() or '..' in Path(value).parts):
            raise ValueError('paper_monitor_arguments_invalid')
    return args


def _run_probe(name, directory, timeout):
    script, expected = _SPECS[name]
    # No shell or inherited PYTHONPATH. Native tools enforce physical
    # directory identity and their own bounded read-only status contract.
    command = [sys.executable, '-I', '-B', str(TOOLS / script), 'check', directory]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'exit_code': None, 'healthy': False}
    except OSError:
        return {'status': 'unavailable', 'exit_code': None, 'healthy': False}
    if len(result.stdout) > _MAX_OUTPUT or len(result.stderr) > _MAX_OUTPUT:
        return {'status': 'invalid_response', 'exit_code': result.returncode, 'healthy': False}
    try:
        payload = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        return {'status': 'invalid_response', 'exit_code': result.returncode, 'healthy': False}
    if not isinstance(payload, dict) or type(payload.get('status')) is not str:
        return {'status': 'invalid_response', 'exit_code': result.returncode, 'healthy': False}
    status = payload['status']
    healthy = result.returncode == 0 and status == expected
    return {'status': status if len(status) <= 64 else 'invalid_response',
            'exit_code': result.returncode, 'healthy': healthy}


def inspect(args, probe=_run_probe):
    results = {}
    for name in _SPECS:
        directory = getattr(args, name)
        if directory is not None:
            results[name] = probe(name, directory, args.timeout_seconds)
    healthy = all(item['healthy'] for item in results.values())
    return {'schema_version': 1, 'source': 'paper_monitor',
            'status': 'healthy' if healthy else 'attention_required',
            'exit_code': 0 if healthy else 1, 'checks': results,
            'boundaries': {'read_only': True, 'live_enabled': False,
                           'orders_submitted': False, 'notification_delivered': False,
                           'production_ready': False}}


def main(argv=None):
    try:
        args = _arguments(argv)
        report = inspect(args)
        code = report['exit_code']
    except (ValueError, OSError):
        code = 2
        report = {'schema_version': 1, 'source': 'paper_monitor',
                  'status': 'unavailable', 'exit_code': 2, 'checks': {},
                  'boundaries': {'read_only': True, 'live_enabled': False,
                                 'orders_submitted': False,
                                 'notification_delivered': False,
                                 'production_ready': False}}
    try:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)
    except OSError:
        return 4
    return code


if __name__ == '__main__':
    raise SystemExit(main())
