"""Inspect a saved walk-forward research report without running research."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def _html_output(raw, source, fixture=None):
    """Select a new file and bind its owner-private canonical parent."""
    if not raw:
        raise ValueError('empty_output')
    target = Path(raw).absolute()
    parent = target.parent.resolve(strict=True)
    info = parent.stat()
    target = parent / target.name
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or os.path.lexists(target)
            or target == Path(source).resolve()
            or (fixture is not None and target == Path(fixture).resolve())):
        raise ValueError('invalid_output')
    return target, (parent, info.st_dev, info.st_ino)


def main(argv=None):
    failure = 'research_report_arguments_invalid'
    published_error = ()
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('report', help='Existing local schema-1 or schema-2 research JSON')
        parser.add_argument('--fixture', help='Explicit local file to compare by raw-byte SHA-256 (max 32 MiB)')
        parser.add_argument('--html', help='NEW HTML file in an existing owner-private directory')
        args = parser.parse_args(argv)

        failure = 'research_report_output_invalid'
        # Resolve fixture aliases only after the explicit fixture check, so an
        # invalid source path is not misreported as an invalid HTML destination.
        output = _html_output(args.html, args.report) if args.html is not None else None

        failure = 'research_report_unavailable'
        from trading_bot.research_report import read_report
        report = read_report(args.report)

        fixture_check = None
        if args.fixture is not None:
            failure = 'research_report_fixture_unavailable'
            from trading_bot.research_fixture import check_fixture
            fixture_check = check_fixture(args.fixture, report['experiment']['fixture_sha256'])

        if output is not None:
            failure = 'research_report_render_failed'
            from trading_bot.research_html import render_research_report
            document = render_research_report(report, fixture_check=fixture_check).encode('utf-8')

            failure = 'research_report_output_invalid'
            target, binding = output
            target, current = _html_output(target, args.report, args.fixture)
            if current != binding:
                raise ValueError('output_parent_changed')

            # The shared publisher imports paper model definitions, but this
            # operation opens no account and invokes no execution or research.
            from trading_core.paper_evidence import _publish, PaperEvidencePublishedError
            published_error = PaperEvidencePublishedError
            failure = 'research_report_output_unavailable'
            _publish(document, target, expected_parent=binding)
    except KeyboardInterrupt:
        print(json.dumps({'error': 'research_report_interrupted_inspect_output'}))
        return 130
    except Exception as error:
        if isinstance(error, published_error):
            print(json.dumps({'error': 'research_report_published_check_required'}))
            return 3
        # Neither malformed report contents nor filesystem paths belong in stdout.
        print(json.dumps({'error': failure}))
        return 2

    result = dict(mode='paper', read_only=True, live_enabled=False,
        status='research_report_checked', validation_scope='saved_report_consistency',
        schema_version=report['schema_version'], run_id=report['run_id'],
        summary=report['summary'], robustness_available=report['schema_version'] == 2,
        html_written=output is not None)
    if fixture_check is not None:
        result['fixture_check'] = fixture_check
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 1 if fixture_check is not None and fixture_check['status'] == 'mismatch' else 0


if __name__ == '__main__':
    raise SystemExit(main())
