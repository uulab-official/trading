"""Real SQLite streaming-account boundaries, with no provider or broker I/O."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / name))
from trading_bot import paper_stream_store as store
from trading_bot.paper_runner import PaperRunConfig

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
START = int(BASE.timestamp()) * 1000
NOW = BASE + timedelta(minutes=50, seconds=10)
CAPTURE_CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=50,
                      retention_bars=500, poll_seconds=5)
CONFIG = PaperRunConfig(symbol='BTC/KRW', timeframe='1m', initial_balance='1000000')
PATHS = [str(ROOT / name) for name in
         ('apps/server', 'packages/core', 'packages/data', 'packages/strategy')]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bar(index, *, close='100'):
    return [START + index * 60000, '100', str(max(102, int(close))), '99', close, '1']


def source(count=0, *, at=None):
    return dict(revision=2 + count, accepted_bars_total=50 + count,
                evicted_bars_total=max(0, count - 450),
                last_verified_at=(at or NOW + timedelta(minutes=count)).isoformat(),
                latest_open_ms=START + (49 + count) * 60000,
                snapshot_sha256=sha({'observation': count}))


def identity(path):
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]


class StreamStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.directory = self.parent / 'stream'
        capture = self.parent / 'capture'
        capture.mkdir(mode=0o700)
        for name in ('capture.sqlite3', '.capture.lock'):
            (capture / name).touch(mode=0o600)
        self.bootstrap = [bar(i) for i in range(50)]
        from trading_bot.paper_stream_engine import initial_state
        self.engine = initial_state(CONFIG, self.bootstrap)
        self.manifest = dict(format='uulab.continuous-paper', version=1,
            mode='paper', live_enabled=False, execution_model='delayed_next_open',
            stream_id='a' * 32, created_at=NOW.isoformat(), config=CONFIG.identity(),
            bootstrap=self.bootstrap, capture=dict(path=str(capture),
                capture_id='b' * 32, config=CAPTURE_CONFIG,
                binding=dict(parent_path=str(self.parent), parent=identity(self.parent),
                    directory=identity(capture), database=identity(capture / 'capture.sqlite3'),
                    lock=identity(capture / '.capture.lock'))),
            activation_source=source(), engine_sha256='c' * 64,
            poll_seconds=5, max_bars=100000)

    def create(self):
        return store.stream_writer(self.directory, manifest=self.manifest,
                                   engine=self.engine, at=NOW, source=source())

    def candidate(self, state, index=50, *, close='101'):
        from trading_bot.paper_stream_engine import advance_state
        row = bar(index, close=close)
        engine, fill = asyncio.run(advance_state(state, row))
        return engine, row, fill

    def commit(self, writer, *, close='101'):
        current = writer.read()['engine']
        count = current['next_index'] + 1
        engine, row, fill = self.candidate(current, 49 + count, close=close)
        return writer.commit_bar(engine, row, fill, at=NOW + timedelta(minutes=count),
                                 source=source(count))

    def test_creation_is_private_no_clobber_and_detached(self):
        with self.create() as writer:
            saved = writer.read()
            self.assertEqual(saved['control']['revision'], 0)
            self.assertEqual(saved['control']['phase'], 'waiting')
            self.assertEqual(saved['engine']['next_index'], 0)
            self.assertEqual(saved['event_head_sha256'], '0' * 64)
            changed = writer.read()
            changed['engine']['cash'] = '1'
            self.assertEqual(writer.read(), saved)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for name in (store.DB_NAME, store.LOCK_NAME):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(store.StreamUnavailable):
            with self.create():
                pass
        self.assertEqual(store.read_stream(self.directory), saved)

    def test_event_account_cursor_and_chain_commit_together(self):
        with self.create() as writer:
            first = self.commit(writer)
            second = self.commit(writer, close='102')
            events = list(writer.events())
        self.assertEqual(first['engine']['next_index'], 1)
        self.assertEqual(second['engine']['next_index'], 2)
        self.assertEqual(second['control']['revision'], 2)
        self.assertIsNone(events[0]['fill'])
        self.assertEqual(events[1]['fill']['side'], 'buy')
        self.assertEqual(events[1]['fill']['timestamp'], START + 51 * 60000)
        self.assertEqual([event['sequence'] for event in events], [1, 2])
        self.assertEqual(events[0]['previous_sha256'], '0' * 64)
        self.assertEqual(events[1]['previous_sha256'], events[0]['sha256'])
        self.assertEqual(second['event_head_sha256'], events[-1]['sha256'])
        for event in events:
            self.assertEqual(event['sha256'], sha({k: v for k, v in event.items() if k != 'sha256'}))
        self.assertEqual(store.read_stream(self.directory), second)

    def test_resume_requires_verified_exact_head_before_economic_write(self):
        with self.create() as writer:
            saved = self.commit(writer)
        with store.stream_writer(self.directory) as writer:
            with self.assertRaises(store.StreamUnavailable):
                self.commit(writer)
            with self.assertRaises(store.StreamUnavailable):
                writer.verify_head('f' * 64, saved['event_head_sha256'])
            with self.assertRaises(store.StreamUnavailable):
                writer.control('running', None, at=NOW + timedelta(minutes=2))
            writer.verify_head(saved['engine_sha256'], saved['event_head_sha256'])
            self.assertEqual(self.commit(writer)['engine']['next_index'], 2)

    def test_duplicate_stale_candidate_and_gap_do_not_advance(self):
        with self.create() as writer:
            engine, row, fill = self.candidate(self.engine)
            first = writer.commit_bar(engine, row, fill, at=NOW + timedelta(minutes=1),
                                      source=source(1))
            with self.assertRaises(store.StreamUnavailable):
                writer.commit_bar(engine, row, fill, at=NOW + timedelta(minutes=2), source=source(2))
            changed = copy.deepcopy(engine)
            changed['config']['fee_pct'] = '2'
            with self.assertRaises(store.StreamUnavailable):
                writer.commit_bar(changed, bar(52), None, at=NOW + timedelta(minutes=3), source=source(3))
            self.assertEqual(writer.read(), first)
            self.assertEqual(len(list(writer.events())), 1)

    def test_source_and_clock_regressions_preserve_head(self):
        with self.create() as writer:
            saved = self.commit(writer)
            bad_sources = [source(), dict(source(1), snapshot_sha256='e' * 64),
                dict(source(2), accepted_bars_total=53),
                dict(source(2), last_verified_at=NOW.isoformat())]
            for incoming in bad_sources:
                with self.subTest(incoming=incoming), self.assertRaises(store.StreamUnavailable):
                    writer.control('waiting', 'source_not_current',
                                   at=NOW + timedelta(minutes=2), source=incoming)
            with self.assertRaises(store.StreamUnavailable):
                writer.control('stopped', None, at=NOW)
            self.assertEqual(writer.read(), saved)

    def test_blocked_is_terminal_and_controls_do_not_rewrite_engine(self):
        with self.create() as writer:
            initial = writer.read()
            blocked = writer.control('blocked', 'retention_lost', at=NOW)
            self.assertEqual(blocked['engine'], initial['engine'])
            self.assertEqual(blocked['event_head_sha256'], initial['event_head_sha256'])
            for phase in ('running', 'waiting', 'stopped'):
                with self.assertRaises(store.StreamUnavailable):
                    writer.control(phase, None, at=NOW)
            with self.assertRaises(store.StreamUnavailable):
                self.commit(writer)
            self.assertEqual(writer.read(), blocked)

    def test_owner_and_audit_lock_contention_but_passive_head_is_readable(self):
        with self.create() as writer:
            saved = writer.read()
            self.assertEqual(store.read_stream(self.directory), saved)
            for opener in (lambda: store.stream_writer(self.directory),
                           lambda: store.stream_reader(self.directory, audit=True)):
                with self.assertRaises(store.StreamUnavailable):
                    with opener():
                        pass
            with store.stream_reader(self.directory) as reader:
                with self.assertRaises(store.StreamUnavailable):
                    list(reader.events())
        with store.stream_reader(self.directory, audit=True) as reader:
            self.assertEqual(list(reader.events()), [])
            with self.assertRaises(store.StreamUnavailable):
                with store.stream_writer(self.directory):
                    pass

    def test_capacity_preserves_every_existing_event_and_account(self):
        with self.create() as writer:
            saved = self.commit(writer)
            with patch.object(store, 'MAX_BARS', 1):
                with self.assertRaises(store.StreamCapacity):
                    self.commit(writer)
            self.assertEqual(writer.read(), saved)
            self.assertEqual(len(list(writer.events())), 1)
            blocked = writer.control('blocked', 'capacity', at=NOW + timedelta(minutes=2))
            self.assertEqual(blocked['engine'], saved['engine'])

    def test_post_commit_sync_failure_latches_handle_and_retains_complete_prefix(self):
        with self.create() as writer:
            with patch.object(store.os, 'fsync', side_effect=OSError('confirmation failed')):
                with self.assertRaises(store.StreamStorageError):
                    self.commit(writer)
            with self.assertRaises(store.StreamStorageError):
                writer.read()
        saved = store.read_stream(self.directory)
        self.assertEqual(saved['engine']['next_index'], 1)
        with store.stream_reader(self.directory, audit=True) as reader:
            self.assertEqual(len(list(reader.events())), 1)

    def test_database_capacity_is_planned_before_an_economic_write(self):
        with self.create() as writer:
            saved = self.commit(writer)
            size = (self.directory / store.DB_NAME).stat().st_size
            with patch.object(store, 'MAX_DATABASE_BYTES', size + 4096):
                with self.assertRaises(store.StreamCapacity):
                    self.commit(writer)
            self.assertEqual(writer.read(), saved)
            self.assertEqual(len(list(writer.events())), 1)

    def test_invalid_engine_and_naive_time_use_fixed_store_refusal(self):
        with self.create() as writer:
            saved = writer.read()
            candidate, row, fill = self.candidate(self.engine)
            candidate['cash'] = True
            with self.assertRaises(store.StreamUnavailable):
                writer.commit_bar(candidate, row, fill, at=NOW + timedelta(minutes=1),
                                  source=source(1))
            with self.assertRaises(store.StreamUnavailable):
                writer.control('stopped', None, at=NOW.replace(tzinfo=None))
            self.assertEqual(writer.read(), saved)

    def test_indexed_history_includes_bootstrap_and_only_requested_events(self):
        with self.create() as writer:
            self.commit(writer)
            self.commit(writer, close='102')
            self.assertEqual(writer.history(START + 48 * 60000, START + 51 * 60000),
                [bar(48), bar(49), bar(50, close='101'), bar(51, close='102')])
            self.assertEqual(writer.history(START + 51 * 60000, START + 51 * 60000),
                             [bar(51, close='102')])
            with self.assertRaises(store.StreamUnavailable):
                writer.history(START, START + 5000 * 60000)

    def test_failure_before_commit_rolls_back_and_latches_handle(self):
        with self.create() as writer:
            saved = writer.read()
            with patch.object(writer, '_commit', side_effect=sqlite3.OperationalError('disk failure')):
                with self.assertRaises(store.StreamStorageError):
                    self.commit(writer)
            with self.assertRaises(store.StreamStorageError):
                writer.control('stopped', None, at=NOW)
        self.assertEqual(store.read_stream(self.directory), saved)
        with store.stream_reader(self.directory, audit=True) as reader:
            self.assertEqual(list(reader.events()), [])

    def test_lost_commit_ack_retains_one_complete_event_on_reopen(self):
        with self.create() as writer:
            original = writer._commit
            def lose_ack():
                original()
                raise sqlite3.OperationalError('commit acknowledgement lost')
            with patch.object(writer, '_commit', side_effect=lose_ack):
                with self.assertRaises(store.StreamStorageError):
                    self.commit(writer)
        saved = store.read_stream(self.directory)
        self.assertEqual(saved['engine']['next_index'], 1)
        with store.stream_writer(self.directory) as writer:
            writer.verify_head(saved['engine_sha256'], saved['event_head_sha256'])
            resumed = self.commit(writer, close='102')
            self.assertEqual(resumed['engine']['fill_count'], 1)
            self.assertEqual(len(list(writer.events())), 2)

    def test_real_hot_journal_is_untouched_by_observer_and_recovered_by_owner(self):
        with self.create() as writer:
            saved = self.commit(writer)
        script = """import os, sys
sys.path[:0] = sys.argv[2:]
from trading_bot.paper_stream_store import stream_writer
with stream_writer(sys.argv[1]) as writer:
    writer.connection.execute('PRAGMA cache_size=1')
    writer.connection.execute('BEGIN IMMEDIATE')
    writer.connection.execute("UPDATE stream_head SET body='{}'")
    writer.connection.execute("UPDATE stream_events SET body='{}'")
    os._exit(9)
"""
        completed = subprocess.run([sys.executable, '-c', script, str(self.directory), *PATHS],
                                   capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 9, completed.stderr.decode())
        db = self.directory / store.DB_NAME
        journal = self.directory / (store.DB_NAME + '-journal')
        self.assertGreater(journal.stat().st_size, 512)
        before = db.read_bytes(), journal.read_bytes()
        with self.assertRaises(store.StreamUnavailable):
            store.read_stream(self.directory)
        self.assertEqual((db.read_bytes(), journal.read_bytes()), before)
        with store.stream_writer(self.directory) as writer:
            self.assertEqual(writer.read(), saved)
        self.assertEqual(store.read_stream(self.directory), saved)

    def test_actual_process_death_inside_commit_keeps_previous_complete_prefix(self):
        with self.create() as writer:
            saved = self.commit(writer)
        script = """import asyncio, os, sys
from datetime import datetime
sys.path[:0] = sys.argv[2:]
from trading_bot.paper_stream_store import stream_writer
from trading_bot.paper_stream_engine import advance_state
with stream_writer(sys.argv[1]) as writer:
    saved = writer.read()
    writer.verify_head(saved['engine_sha256'], saved['event_head_sha256'])
    row = [saved['engine']['last_open_ms']+60000, '100', '102', '99', '102', '1']
    engine, fill = asyncio.run(advance_state(saved['engine'], row))
    source = dict(saved['control']['source'])
    source.update(revision=4, accepted_bars_total=52, latest_open_ms=row[0],
                  last_verified_at='2026-10-01T00:52:10+00:00', snapshot_sha256='d'*64)
    writer.connection.execute('PRAGMA cache_size=1')
    writer._commit = lambda: os._exit(11)
    writer.commit_bar(engine, row, fill, at=datetime.fromisoformat(source['last_verified_at']), source=source)
"""
        completed = subprocess.run([sys.executable, '-c', script, str(self.directory), *PATHS],
                                   capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 11, completed.stderr.decode())
        with store.stream_writer(self.directory) as writer:
            self.assertEqual(writer.read(), saved)
            self.assertEqual(len(list(writer.events())), 1)

    def test_forked_owner_is_refused_without_unlocking_parent(self):
        with self.create() as writer:
            read_fd, write_fd = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                try:
                    writer.read()
                except store.StreamUnavailable:
                    os.write(write_fd, b'refused')
                finally:
                    writer.binding.close()
                    os.close(write_fd)
                    os._exit(0)
            os.close(write_fd)
            try:
                self.assertEqual(os.read(read_fd, 32), b'refused')
            finally:
                os.close(read_fd)
                os.waitpid(child, 0)
            with self.assertRaises(store.StreamUnavailable):
                with store.stream_writer(self.directory):
                    pass
            self.assertEqual(self.commit(writer)['engine']['next_index'], 1)

    def test_file_and_directory_replacement_refuse_without_touching_replacement(self):
        for target in (store.DB_NAME, store.LOCK_NAME, 'directory'):
            with self.subTest(target=target):
                self.directory = self.parent / ('stream-' + target.replace('.', '_'))
                with self.create() as writer:
                    if target == 'directory':
                        self.directory.rename(self.directory.with_name(self.directory.name + '-old'))
                        self.directory.mkdir(mode=0o700)
                        replacement = self.directory / 'sentinel'
                    else:
                        path = self.directory / target
                        path.rename(self.directory / (target + '.old'))
                        replacement = path
                    replacement.write_bytes(b'preserve replacement')
                    replacement.chmod(0o600)
                    with self.assertRaises(store.StreamUnavailable):
                        writer.read()
                    self.assertEqual(replacement.read_bytes(), b'preserve replacement')

    def test_parent_replacement_stops_owner_before_mutation(self):
        parent = self.parent / 'owned'
        parent.mkdir(mode=0o700)
        self.directory = parent / 'stream'
        with self.create() as writer:
            original = writer.read()
            parent.rename(self.parent / 'owned-old')
            parent.mkdir(mode=0o700)
            with self.assertRaises(store.StreamUnavailable):
                self.commit(writer)
            self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(store.read_stream(self.parent / 'owned-old' / 'stream'), original)

    def test_substitution_during_connect_precedes_any_sqlite_setting(self):
        foreign = self.parent / 'foreign.sqlite3'
        original_connect = sqlite3.connect
        with original_connect(foreign) as connection:
            connection.execute('CREATE TABLE sentinel (value TEXT)')
            connection.execute("INSERT INTO sentinel VALUES ('preserve')")
        foreign_bytes = foreign.read_bytes()
        target = self.directory / store.DB_NAME
        def substitute(*args, **kwargs):
            target.rename(self.directory / (store.DB_NAME + '.original'))
            target.write_bytes(foreign_bytes)
            target.chmod(0o600)
            return original_connect(*args, **kwargs)
        with patch.object(store.sqlite3, 'connect', side_effect=substitute):
            with self.assertRaises((store.StreamUnavailable, store.StreamStorageError)):
                with self.create():
                    pass
        self.assertEqual(target.read_bytes(), foreign_bytes)

    def test_unsupported_schema_and_invalid_head_hash_are_refused(self):
        for corruption in ('schema', 'hash', 'oversized'):
            with self.subTest(corruption=corruption):
                self.directory = self.parent / ('stream-' + corruption)
                with self.create():
                    pass
                db = self.directory / store.DB_NAME
                with sqlite3.connect(db) as connection:
                    if corruption == 'schema':
                        connection.execute('CREATE TABLE unexpected (value TEXT)')
                    elif corruption == 'hash':
                        connection.execute("UPDATE stream_head SET sha256=?", ('f' * 64,))
                    else:
                        connection.execute('PRAGMA ignore_check_constraints=ON')
                        connection.execute('UPDATE stream_head SET body=?',
                                           (' ' * (store.MAX_HEAD_BYTES + 1),))
                before = db.read_bytes()
                with self.assertRaises(store.StreamUnavailable):
                    store.read_stream(self.directory)
                self.assertEqual(db.read_bytes(), before)

    def test_deleted_interior_event_is_detected_by_archive_iteration(self):
        with self.create() as writer:
            for _ in range(3):
                self.commit(writer)
        with sqlite3.connect(self.directory / store.DB_NAME) as connection:
            connection.execute('DELETE FROM stream_events WHERE sequence=2')
        # A passive head is deliberately not a complete historical audit.
        self.assertEqual(store.read_stream(self.directory)['engine']['next_index'], 3)
        with store.stream_reader(self.directory, audit=True) as reader:
            with self.assertRaises(store.StreamUnavailable):
                list(reader.events())

    def test_partial_creation_and_mutated_manifest_do_not_initialize(self):
        with self.assertRaises(store.StreamUnavailable):
            with store.stream_writer(self.directory, manifest=self.manifest):
                pass
        self.assertFalse(self.directory.exists())
        self.manifest['activation_source']['accepted_bars_total'] = True
        with self.assertRaises(store.StreamUnavailable):
            with self.create():
                pass
        self.assertFalse(self.directory.exists())

    def test_sell_reconstruction_preserves_original_decimal_operation_order(self):
        from trading_bot.paper_stream_engine import initial_state, advance_state
        config = PaperRunConfig(symbol='BTC/KRW', timeframe='1m',
            initial_balance='1000000000000000000', fast_period=2, slow_period=3,
            fee_pct='0.123456789', slippage_pct='0.000123456')
        self.bootstrap = [[START + i * 60000, '100000000.123456989',
            '100000001.123456989', '99999999.123456989', '100000000.123456989', '1']
            for i in range(50)]
        self.engine = initial_state(config, self.bootstrap)
        self.manifest.update(config=config.identity(), bootstrap=self.bootstrap)
        rows = [
            [START + 50 * 60000, '100000000.123456989', '110000001.1358026879',
             '99999999.123456989', '110000000.1358026879', '1'],
            [START + 51 * 60000, '100000000.123457089', '110000001.1358026879',
             '99999999.123457089', '110000000.1358026879', '1'],
            [START + 52 * 60000, '100000000.123457189', '100000001.123457189',
             '89999999.1111112901', '90000000.1111112901', '1'],
            [START + 53 * 60000, '100000000.123457289', '100000001.123457289',
             '89999999.1111112901', '90000000.1111112901', '1'],
        ]
        with self.create() as writer:
            state = self.engine
            for count, row in enumerate(rows, 1):
                state, fill = asyncio.run(advance_state(state, row))
                saved = writer.commit_bar(state, row, fill,
                    at=NOW + timedelta(minutes=count), source=source(count))
            self.assertEqual(saved['engine']['cash'], '999506289149831118.2654606144')
            self.assertEqual(Decimal(saved['engine']['holding']), Decimal('0'))
            self.assertEqual(saved['engine']['fill_count'], 2)

    def test_original_tiny_fill_fee_is_not_rejected_by_order_quantity_bounds(self):
        from trading_bot.paper_stream_engine import initial_state, advance_state
        self.bootstrap = [[START + i * 60000, '0.00000000000000001',
            '0.00000000000000003', '0.000000000000000001', '0.00000000000000001', '1']
            for i in range(50)]
        cases = [('0', '1E-17', '1.999999E-39'),
                 ('1E-18', '1.00000000000000000001E-17', '1.99999900000000000001999999E-39')]
        for index, (slippage, price, fee) in enumerate(cases):
            with self.subTest(slippage=slippage):
                self.directory = self.parent / ('tiny-stream-' + str(index))
                config = PaperRunConfig(symbol='BTC/KRW', timeframe='1m', initial_balance='1E-18',
                    fast_period=2, slow_period=3, fee_pct='1E-18', slippage_pct=slippage)
                self.engine = initial_state(config, self.bootstrap)
                self.manifest.update(config=config.identity(), bootstrap=self.bootstrap)
                with self.create() as writer:
                    state = self.engine
                    for count in (1, 2):
                        row = [START + (49 + count) * 60000, '0.00000000000000001',
                            '0.00000000000000003', '0.000000000000000001', '0.00000000000000002', '1']
                        state, fill = asyncio.run(advance_state(state, row))
                        saved = writer.commit_bar(state, row, fill,
                            at=NOW + timedelta(minutes=count), source=source(count))
                    self.assertEqual(saved['engine']['fill_count'], 1)
                    self.assertEqual(fill['average'], price)
                    self.assertEqual(fill['fee']['cost'], fee)
                    self.assertEqual(saved['engine']['total_fees'], fee)

    def test_archive_and_history_cross_batch_boundary_without_truncation(self):
        with self.create() as writer:
            for _ in range(70):
                self.commit(writer)
            self.assertEqual([event['sequence'] for event in writer.events()], list(range(1, 71)))
            history = writer.history(START + 50 * 60000, START + 119 * 60000)
            self.assertEqual([row[0] for row in history], [START + i * 60000 for i in range(50, 120)])
        with store.stream_reader(self.directory, audit=True) as reader:
            self.assertEqual([event['sequence'] for event in reader.events()], list(range(1, 71)))

    def test_readonly_refuses_sidecars_without_recovery_or_modification(self):
        with self.create():
            pass
        database = self.directory / store.DB_NAME
        before = database.read_bytes()
        for suffix, body in (('-wal', b''), ('-shm', b''), ('-journal', b'unconfirmed')):
            with self.subTest(suffix=suffix):
                sidecar = self.directory / (store.DB_NAME + suffix)
                sidecar.write_bytes(body)
                sidecar.chmod(0o600)
                try:
                    with self.assertRaises(store.StreamUnavailable):
                        store.read_stream(self.directory)
                    self.assertEqual(database.read_bytes(), before)
                    self.assertEqual(sidecar.read_bytes(), body)
                finally:
                    sidecar.unlink()

    def test_alias_hardlink_and_insecure_file_are_not_valid_ownership(self):
        with self.create():
            pass
        alias = self.parent / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(store.StreamUnavailable):
            store.read_stream(alias)
        database = self.directory / store.DB_NAME
        other = self.parent / 'linked-db'
        os.link(database, other)
        with self.assertRaises(store.StreamUnavailable):
            store.read_stream(self.directory)
        other.unlink()
        database.chmod(0o644)
        with self.assertRaises(store.StreamUnavailable):
            store.read_stream(self.directory)
        database.chmod(0o600)
        self.assertEqual(store.read_stream(self.directory)['engine']['next_index'], 0)

    def test_external_write_invalidates_owned_expected_head_before_any_new_effect(self):
        with self.create() as writer:
            saved = self.commit(writer)
            database = self.directory / store.DB_NAME
            with sqlite3.connect(database) as connection:
                connection.execute('UPDATE stream_head SET sha256=?', ('e' * 64,))
            changed = database.read_bytes()
            with self.assertRaises(store.StreamUnavailable):
                self.commit(writer)
            self.assertEqual(database.read_bytes(), changed)
            self.assertEqual(saved['engine']['next_index'], 1)


if __name__ == '__main__':
    unittest.main()
