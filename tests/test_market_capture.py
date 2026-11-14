"""Real capture persistence with synthetic public responses; no exchange traffic."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/data', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from trading_bot import market_capture as capture
from trading_data.upbit_history import UpbitHistoryClient, candles_hash, validate_market_fixture

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=3,
              retention_bars=4, poll_seconds=60)


class Clock:
    def __init__(self, now=None):
        self.now = now or BASE + timedelta(minutes=10, seconds=6)
        self.waited = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waited.append(seconds)
        self.now += timedelta(seconds=seconds)


def provider(clock, *, callback=None, calls=None):
    """Keep original provider parsing/provenance; only replace HTTP boundary."""
    def get(url):
        query = parse_qs(urlsplit(url).query)
        end = datetime.fromisoformat(query['to'][0].replace('Z', '+00:00'))
        count = int(query['count'][0])
        if calls is not None:
            calls.append((clock(), count, end))
        rows = []
        for i in range(count):
            at = end - timedelta(minutes=count-i)
            price = 100 + (at.minute % 7)
            rows.append(dict(market='KRW-BTC', unit=1,
                candle_date_time_utc=at.strftime('%Y-%m-%dT%H:%M:%S'),
                opening_price=price, high_price=price+1, low_price=price-1,
                trade_price=price, candle_acc_trade_volume='1.25'))
        if callback is not None:
            callback()
        return json.dumps(rows[::-1]).encode(), {}
    return UpbitHistoryClient(get=get, clock=clock, sleep=lambda _: None)


def fixture(clock, count=3, end=None):
    return provider(clock).download('KRW-BTC', unit=1, count=count,
                                    end=end or clock()-timedelta(seconds=6))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'capture'
        self.clock = Clock()

    def collect(self, **options):
        defaults = dict(config=CONFIG, max_polls=1, clock=self.clock,
                        sleep=self.clock.sleep, client=provider(self.clock))
        defaults.update(options)
        return capture.collect_capture(self.directory, **defaults)

    def create(self):
        from trading_bot.market_capture_store import capture_writer
        with capture_writer(self.directory, config=CONFIG, now=self.clock()) as writer:
            writer.attempt(self.clock())
            writer.apply(fixture(self.clock), at=self.clock())

    def test_bounded_collection_accepts_original_fixture_and_reports_stopped(self):
        result = self.collect()
        self.assertEqual(result['exit_code'], 0)
        report = result['observation']
        self.assertEqual(report['status'], 'stopped')
        self.assertEqual(report['window']['bars'], 3)
        self.assertEqual(report['capture']['accepted_bars_total'], 3)
        self.assertEqual(report['latest']['volume'], '1.25')
        self.assertEqual(report['boundaries']['process_liveness'], 'not_asserted')

    def test_progression_requests_one_overlap_and_trims_visible_retention(self):
        calls = []
        result = self.collect(max_polls=3, client=provider(self.clock, calls=calls))
        self.assertEqual([x[1] for x in calls], [3, 2, 2])
        report = result['observation']
        self.assertEqual(report['window']['bars'], 4)
        self.assertEqual(report['capture']['accepted_bars_total'], 5)
        self.assertEqual(report['capture']['evicted_bars_total'], 1)
        self.assertEqual(report['capture']['successful_polls'], 3)
        self.assertTrue(all(0 < delay <= .1 for delay in self.clock.waited))

    def test_same_boundary_reobservation_does_not_count_new_candles(self):
        calls = []
        result = self.collect(config=dict(CONFIG, poll_seconds=5), max_polls=2,
                              client=provider(self.clock, calls=calls))
        self.assertEqual([x[1] for x in calls], [3, 1])
        self.assertEqual(result['observation']['capture']['accepted_bars_total'], 3)

    def test_invalid_max_polls_precedes_capture_creation_and_provider_contact(self):
        for value in [0, True, 10001, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.collect(max_polls=value)
        self.assertFalse(self.directory.exists())

    def test_late_response_after_signal_is_not_accepted(self):
        signal_number = [0]
        result = self.collect(stop=lambda: signal_number[0],
            client=provider(self.clock, callback=lambda: signal_number.__setitem__(0, 15)))
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(result['observation']['window']['bars'], 0)
        self.assertEqual(result['observation']['capture']['successful_polls'], 0)

    def test_clock_regression_after_response_cannot_advance_cursor(self):
        result = self.collect(client=provider(self.clock,
            callback=lambda: setattr(self.clock, 'now', self.clock()-timedelta(seconds=1))))
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['window']['bars'], 0)
        self.assertEqual(result['observation']['status'], 'clock_error')

    def test_excess_catchup_stops_before_network(self):
        self.create()
        self.clock.now += timedelta(minutes=200)
        calls = []
        result = self.collect(config=None, client=provider(self.clock, calls=calls))
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(calls, [])
        self.assertEqual(result['observation']['capture']['last_error'], 'catchup_limit')

    def test_conflicting_overlap_preserves_complete_previous_window(self):
        self.create()
        prior = capture.capture_status(self.directory, now=self.clock())
        self.clock.now += timedelta(minutes=1)
        client = provider(self.clock)
        download = client.download
        def changed(*args, **kwargs):
            result = download(*args, **kwargs)
            result['candles'][0][1:5] = ['99', '100', '98', '99']
            result['data_provenance']['candles_sha256'] = candles_hash(result['candles'])
            return result
        client.download = changed
        result = self.collect(config=None, client=client)
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['window'], prior['window'])
        self.assertEqual(result['observation']['capture']['last_error'], 'history_conflict')

    def test_valid_but_wrong_requested_boundary_is_rejected(self):
        class WrongClient:
            def download(_, *args, **kwargs):
                return fixture(self.clock, end=BASE+timedelta(minutes=9))
        result = self.collect(client=WrongClient())
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['capture']['last_error'], 'data_rejected')
        self.assertEqual(result['observation']['window']['bars'], 0)

    def test_temporary_failure_recovers_after_bounded_interruptible_backoff(self):
        from trading_data.upbit_history import MarketDataUnavailable
        real = provider(self.clock)
        calls = []
        class Flaky:
            def download(_, *args, **kwargs):
                calls.append(self.clock())
                if len(calls) == 1:
                    raise MarketDataUnavailable('hidden', reason='rate_limited',
                                                retryable=True, retry_after=30)
                return real.download(*args, **kwargs)
        result = self.collect(config=dict(CONFIG, poll_seconds=5), max_polls=2, client=Flaky())
        self.assertEqual(result['exit_code'], 0)
        self.assertGreaterEqual((calls[1]-calls[0]).total_seconds(), 30)
        self.assertEqual(result['observation']['capture']['consecutive_failures'], 0)

    def test_terminal_and_unknown_errors_are_never_retried(self):
        from trading_data.upbit_history import MarketDataUnavailable
        for reason in ('rate_blocked', 'cooldown_required', 'request_rejected', 'unavailable'):
            with self.subTest(reason=reason):
                self.directory = self.root / reason
                calls = []
                class Rejected:
                    def download(_, *args, **kwargs):
                        calls.append(True)
                        raise MarketDataUnavailable('private provider error', reason=reason)
                result = self.collect(max_polls=4, client=Rejected())
                self.assertEqual(result['exit_code'], 3)
                self.assertEqual(len(calls), 1)
                expected = 'request_rejected' if reason == 'unavailable' else reason
                self.assertEqual(result['observation']['capture']['last_error'], expected)
                again = self.collect(config=None, client=Rejected())
                self.assertEqual(again['exit_code'], 3)
                self.assertEqual(len(calls), 1)

    def test_resume_retry_cannot_bypass_saved_provider_cooldown(self):
        from trading_bot.market_capture_store import capture_writer
        with capture_writer(self.directory, config=dict(CONFIG, poll_seconds=5), now=self.clock()) as writer:
            writer.attempt(self.clock())
            writer.fail('rate_limited', at=self.clock(), retryable=True)
        saved = self.clock()
        calls = []
        result = self.collect(config=None, client=provider(self.clock, calls=calls))
        self.assertEqual(result['exit_code'], 0)
        self.assertGreaterEqual((calls[0][0]-saved).total_seconds(), 30)

    def test_resume_uncertain_fetching_state_does_not_contact_provider(self):
        from trading_bot.market_capture_store import capture_writer
        with capture_writer(self.directory, config=CONFIG, now=self.clock()) as writer:
            writer.attempt(self.clock())
        before = capture.capture_status(self.directory, now=self.clock())
        calls = []
        result = self.collect(config=None, client=provider(self.clock, calls=calls))
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(calls, [])
        self.assertEqual(result['observation']['capture'], before['capture'])

    def test_current_status_checks_data_and_observation_age_independently(self):
        self.create()
        current = capture.capture_status(self.directory, now=self.clock())
        self.assertEqual(current['status'], 'current')
        self.assertEqual(current['freshness']['data_age_ms'], 6000)
        self.clock.now += timedelta(seconds=141)
        self.assertEqual(capture.capture_status(self.directory, now=self.clock())['status'], 'stale')
        from trading_bot.market_capture_store import capture_writer
        old = fixture(self.clock, end=BASE+timedelta(minutes=10))
        with capture_writer(self.directory) as writer:
            writer.attempt(self.clock())
            writer.apply(old, at=self.clock())
        report = capture.capture_status(self.directory, now=self.clock())
        self.assertEqual(report['status'], 'stale')
        self.assertEqual(report['freshness']['observation_age_ms'], 0)

    def test_observer_clock_error_nulls_invalid_ages_and_does_not_write(self):
        self.create()
        before = {p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        report = capture.capture_status(self.directory, now=BASE)
        self.assertEqual(report['status'], 'clock_error')
        self.assertIsNone(report['freshness']['data_age_ms'])
        self.assertIsNone(report['freshness']['observation_age_ms'])
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()})

    def test_export_is_original_five_field_subset_and_never_overwrites(self):
        self.collect()
        output = self.root / 'export.json'
        before = {p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        report = capture.export_capture(self.directory, output, count=2)
        data = json.loads(output.read_bytes())
        self.assertEqual(set(data), {'mode','symbol','timeframe','candles','data_provenance'})
        self.assertEqual(validate_market_fixture(data)['bar_count'], 2)
        self.assertEqual(report['bar_count'], 2)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            capture.export_capture(self.directory, output)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()})

    def test_export_rejects_capture_directory_and_public_parent(self):
        self.collect()
        with self.assertRaises(ValueError):
            capture.export_capture(self.directory, self.directory / 'not-here.json')
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaises(ValueError):
            capture.export_capture(self.directory, public / 'not-here.json')
        self.assertFalse((public/'not-here.json').exists())

    def test_signal_during_retry_wait_stops_without_another_request(self):
        from trading_data.upbit_history import MarketDataUnavailable
        signum, calls = [0], []
        class Failing:
            def download(_, *args, **kwargs):
                calls.append(True)
                raise MarketDataUnavailable('private', reason='transport_unavailable', retryable=True)
        def sleep(seconds):
            self.clock.sleep(seconds)
            signum[0] = 2
        result = self.collect(max_polls=5, client=Failing(), stop=lambda: signum[0], sleep=sleep)
        self.assertEqual(result['exit_code'], 130)
        self.assertEqual(calls, [True])
        self.assertEqual(result['observation']['capture']['last_error'], 'transport_unavailable')
        self.assertEqual(result['observation']['status'], 'stopped')

    def test_retryable_failure_at_explicit_bound_preserves_retrying_and_failure_exit(self):
        from trading_data.upbit_history import MarketDataUnavailable
        class Failing:
            def download(_, *args, **kwargs):
                raise MarketDataUnavailable('private', reason='rate_limited', retryable=True, retry_after=15)
        result = self.collect(client=Failing())
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['status'], 'retrying')
        self.assertEqual(self.clock.waited, [])

    def test_stopped_terminal_and_explicitly_blocked_retryable_reason_cannot_resume(self):
        from trading_bot.market_capture_store import capture_writer
        for reason, stopped in [('rate_blocked', True), ('transport_unavailable', False)]:
            with self.subTest(reason=reason):
                self.directory = self.root / reason
                with capture_writer(self.directory, config=CONFIG, now=self.clock()) as writer:
                    writer.attempt(self.clock())
                    writer.fail(reason, at=self.clock(), retryable=False)
                    if stopped:
                        writer.stop(self.clock())
                calls = []
                result = self.collect(config=None, client=provider(self.clock, calls=calls))
                self.assertEqual(result['exit_code'], 3)
                self.assertEqual(calls, [])

    def test_future_verification_and_extra_fixture_fields_never_advance(self):
        for change in ('future', 'extra'):
            with self.subTest(change=change):
                self.directory = self.root / change
                class InvalidClient:
                    def download(_, *args, **kwargs):
                        value = fixture(self.clock)
                        if change == 'future':
                            value['data_provenance']['retrieved_at'] = (self.clock()+timedelta(seconds=1)).isoformat()
                        else:
                            value['authorization'] = 'ignored-fields-are-not-allowed'
                        return value
                result = self.collect(client=InvalidClient())
                self.assertEqual(result['exit_code'], 3)
                self.assertEqual(result['observation']['window']['bars'], 0)

    def test_uncertain_export_keeps_possible_output_and_source(self):
        self.collect()
        from trading_bot.local_report_io import ReportPublicationUncertain
        from trading_bot import local_report_io
        output = self.root/'uncertain.json'
        original = local_report_io.os.fsync
        def fail_directory(fd):
            import stat
            if stat.S_ISDIR(local_report_io.os.fstat(fd).st_mode):
                raise OSError('injected publication confirmation failure')
            return original(fd)
        before = {p.name:p.read_bytes() for p in self.directory.iterdir()}
        with patch.object(local_report_io.os, 'fsync', side_effect=fail_directory), \
                self.assertRaises(ReportPublicationUncertain):
            capture.export_capture(self.directory, output)
        self.assertTrue(output.exists())
        validate_market_fixture(json.loads(output.read_bytes()))
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.directory.iterdir()})

    def test_private_export_parent_is_rechecked_inside_pinned_publisher(self):
        self.collect()
        parent = self.root/'out'
        parent.mkdir(mode=0o700)
        original = capture.publish_new_report
        def changed(path, body, **options):
            parent.chmod(0o755)
            return original(path, body, **options)
        with patch.object(capture, 'publish_new_report', side_effect=changed), self.assertRaises(ValueError):
            capture.export_capture(self.directory, parent/'fixture.json')
        self.assertFalse((parent/'fixture.json').exists())

    def test_signal_cannot_erase_received_terminal_provider_refusal(self):
        for status, headers, reason in [(418, {}, 'rate_blocked'),
                (429, {'Retry-After': '600'}, 'cooldown_required')]:
            with self.subTest(status=status):
                self.directory = self.root / str(status)
                signal_number = [0]
                def get(url):
                    signal_number[0] = 15
                    raise HTTPError(url, status, 'private response', headers, None)
                with patch.object(capture, 'PublicCandleTransport', return_value=get):
                    result = self.collect(client=None, stop=lambda: signal_number[0])
                self.assertEqual(result['exit_code'], 143)
                self.assertEqual(result['observation']['capture']['last_error'], reason)
                self.assertEqual(result['observation']['status'], 'stopped')
                calls = []
                resumed = self.collect(config=None, client=provider(self.clock, calls=calls))
                self.assertEqual(resumed['exit_code'], 3)
                self.assertEqual(calls, [])

    def test_signal_inside_client_retry_keeps_unknown_outcome_nonresumable(self):
        for kind in ('rate', 'connection'):
            with self.subTest(kind=kind):
                self.directory = self.root / kind
                signal_number = [0]
                def get(url):
                    signal_number[0] = 2
                    if kind == 'rate':
                        raise HTTPError(url, 429, 'private response', {'Retry-After': '30'}, None)
                    raise URLError('private connection failure')
                with patch.object(capture, 'PublicCandleTransport', return_value=get):
                    result = self.collect(client=None, stop=lambda: signal_number[0])
                self.assertEqual(result['exit_code'], 130)
                self.assertEqual(result['observation']['capture']['phase'], 'fetching')
                calls = []
                resumed = self.collect(config=None, client=provider(self.clock, calls=calls))
                self.assertEqual(resumed['exit_code'], 3)
                self.assertEqual(calls, [])

    def test_signal_on_refused_resume_cannot_relabel_existing_fetching_or_blocked(self):
        from trading_bot.market_capture_store import capture_writer
        for phase in ('fetching', 'blocked'):
            with self.subTest(phase=phase):
                self.directory = self.root / phase
                with capture_writer(self.directory, config=CONFIG, now=self.clock()) as writer:
                    writer.attempt(self.clock())
                    if phase == 'blocked':
                        writer.fail('transport_unavailable', at=self.clock(), retryable=False)
                before = capture.capture_status(self.directory, now=self.clock())
                stopped = self.collect(config=None, stop=lambda: 15)
                self.assertEqual(stopped['exit_code'], 143)
                self.assertEqual(stopped['observation']['capture'], before['capture'])
                calls = []
                resumed = self.collect(config=None, client=provider(self.clock, calls=calls))
                self.assertEqual(resumed['exit_code'], 3)
                self.assertEqual(calls, [])

    def test_signal_cannot_turn_explicitly_nonretryable_transport_error_into_retryable(self):
        from trading_data.upbit_history import MarketDataUnavailable
        signal_number = [0]
        class Rejected:
            def download(_, *args, **kwargs):
                signal_number[0] = 15
                raise MarketDataUnavailable('private', reason='transport_unavailable', retryable=False)
        result = self.collect(client=Rejected(), stop=lambda: signal_number[0])
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(result['observation']['capture']['phase'], 'blocked')
        calls = []
        resumed = self.collect(config=None, client=provider(self.clock, calls=calls))
        self.assertEqual(resumed['exit_code'], 3)
        self.assertEqual(calls, [])

    def test_signal_with_regressed_resume_clock_keeps_state_and_signal_exit(self):
        self.create()
        before = capture.capture_status(self.directory, now=self.clock())['capture']
        self.clock.now -= timedelta(seconds=1)
        result = self.collect(config=None, stop=lambda: 15)
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(result['observation']['status'], 'clock_error')
        self.assertEqual(result['observation']['capture'], before)

    def test_export_rejects_source_moved_into_output_parent_before_publication(self):
        self.collect()
        from trading_bot.market_capture_store import read_capture
        before = read_capture(self.directory)
        parent = self.root/'out'
        parent.mkdir(mode=0o700)
        original = capture.publish_new_report
        def substitute(path, body, **options):
            parent.rename(self.root/'old-out')
            self.directory.rename(parent)
            return original(path, body, **options)
        with patch.object(capture, 'publish_new_report', side_effect=substitute), \
                self.assertRaises((ValueError, OSError)):
            capture.export_capture(self.directory, parent/'fixture.json')
        self.assertFalse((parent/'fixture.json').exists())
        self.assertEqual(read_capture(parent), before)

    def test_export_rechecks_source_when_output_parent_inode_and_spelling_stay_same(self):
        self.collect()
        from trading_bot.market_capture_store import read_capture
        before = read_capture(self.directory)
        container = self.root/'container'
        container.mkdir(mode=0o700)
        parent = container/'out'
        parent.mkdir(mode=0o700)
        inode = parent.stat().st_ino
        original = capture.publish_new_report
        def substitute(path, body, **options):
            container.rename(self.root/'old-container')
            self.directory.rename(container)
            (self.root/'old-container/out').rename(parent)
            self.assertEqual(parent.stat().st_ino, inode)
            return original(path, body, **options)
        with patch.object(capture, 'publish_new_report', side_effect=substitute), \
                self.assertRaises((ValueError, OSError)):
            capture.export_capture(self.directory, parent/'fixture.json')
        self.assertFalse((parent/'fixture.json').exists())
        self.assertEqual(read_capture(container), before)

    def test_source_relocation_after_export_link_is_uncertain_and_preserves_output(self):
        self.collect()
        from trading_bot.market_capture_store import read_capture
        from trading_bot import local_report_io
        before = read_capture(self.directory)
        output = self.root/'uncertain-source.json'
        original = local_report_io.os.link
        def moved(*args, **kwargs):
            result = original(*args, **kwargs)
            self.directory.rename(self.root/'moved-capture')
            return result
        with patch.object(local_report_io.os, 'link', side_effect=moved), \
                self.assertRaises(local_report_io.ReportPublicationUncertain):
            capture.export_capture(self.directory, output)
        self.assertTrue(output.exists())
        validate_market_fixture(json.loads(output.read_bytes()))
        self.assertEqual(read_capture(self.root/'moved-capture'), before)


if __name__ == '__main__':
    unittest.main()
