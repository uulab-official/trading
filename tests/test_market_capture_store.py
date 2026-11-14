"""Real local durable-capture boundaries; no provider or execution account calls."""
import copy
from datetime import datetime, timedelta, timezone
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
sys.path.insert(0, str(ROOT / 'packages/data'))
sys.path.insert(0, str(ROOT / 'apps/server'))
from trading_data.upbit_history import candles_hash, validate_market_fixture
from trading_bot import market_capture_store as store

UTC = timezone.utc
BASE = datetime(2026, 10, 1, tzinfo=UTC)
START = int(BASE.timestamp()) * 1000
NOW = BASE + timedelta(minutes=3, seconds=10)
CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=3,
              retention_bars=4, poll_seconds=15)


def fixture(start=0, count=3, *, at=NOW, market='KRW-BTC', unit=1):
    rows = [[START + i * unit * 60000, '100', '102', '99', '101', '1.25']
            for i in range(start, start + count)]
    p = dict(schema_version=1, provider='upbit_public_rest', market=market,
             unit_minutes=unit, end_exclusive_ms=rows[-1][0] + unit * 60000,
             retrieved_at=at.isoformat(), bar_count=count,
             candles_sha256=candles_hash(rows), gap_policy='reject',
             missing_intervals=0, authenticated=False)
    return dict(mode='paper', symbol=market[4:] + '/KRW', timeframe=str(unit) + 'm',
                candles=rows, data_provenance=p)


class ConfigTests(unittest.TestCase):
    def test_configuration_is_exact_and_bounded(self):
        self.assertEqual(store.validate_config(CONFIG), CONFIG)
        cases = [dict(CONFIG, extra=True), {k:v for k,v in CONFIG.items() if k != 'market'},
                 dict(CONFIG, market='USDT-BTC'), dict(CONFIG, unit_minutes=True),
                 dict(CONFIG, bootstrap_bars=0), dict(CONFIG, bootstrap_bars=201),
                 dict(CONFIG, retention_bars=2), dict(CONFIG, retention_bars=5001),
                 dict(CONFIG, poll_seconds=4), dict(CONFIG, poll_seconds=301),
                 dict(CONFIG, poll_seconds=15.0)]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(store.CaptureUnavailable):
                store.validate_config(value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'capture'

    def create(self):
        return store.capture_writer(self.directory, config=CONFIG, now=NOW)

    def test_new_private_directory_resume_and_frozen_config(self):
        with self.create() as writer:
            first = writer.read()
            self.assertEqual(set(first), {'schema_version', 'config', 'state', 'fixture'})
            self.assertIsNone(first['fixture'])
            self.assertEqual(first['state']['phase'], 'starting')
            self.assertEqual(first['state']['revision'], 0)
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            for name in (store.DB_NAME, store.LOCK_NAME):
                self.assertEqual((self.directory/name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.read_capture(self.directory), first)
        with store.capture_writer(self.directory) as writer:
            self.assertEqual(writer.read(), first)
        with self.assertRaises(store.CaptureUnavailable):
            with self.create():
                pass
        self.assertEqual(store.read_capture(self.directory), first)

    def test_complete_progress_overlap_and_explicit_retention(self):
        later = NOW + timedelta(minutes=3)
        with self.create() as writer:
            writer.attempt(NOW)
            writer.apply(fixture(), at=NOW)
            writer.attempt(later)
            writer.apply(fixture(2, 4, at=later), at=later)
            saved = writer.read()
        rows = saved['fixture']['candles']
        self.assertEqual([row[0] for row in rows], [START+i*60000 for i in range(2, 6)])
        self.assertEqual(saved['state']['accepted_bars_total'], 6)
        self.assertEqual(saved['state']['evicted_bars_total'], 2)
        self.assertEqual(saved['state']['successful_polls'], 2)
        self.assertEqual(saved['state']['last_verified_at'], later.isoformat())
        self.assertEqual(validate_market_fixture(saved['fixture']), saved['fixture']['data_provenance'])

    def test_repeated_identical_overlap_is_new_observation_not_new_data(self):
        later = NOW + timedelta(seconds=15)
        with self.create() as writer:
            writer.attempt(NOW)
            writer.apply(fixture(), at=NOW)
            writer.attempt(later)
            writer.apply(fixture(2, 1, at=later), at=later)
            saved = writer.read()
        self.assertEqual(saved['state']['successful_polls'], 2)
        self.assertEqual(saved['state']['accepted_bars_total'], 3)
        self.assertEqual(saved['state']['evicted_bars_total'], 0)
        self.assertEqual(len(saved['fixture']['candles']), 3)
        self.assertEqual(saved['fixture']['data_provenance']['retrieved_at'], later.isoformat())

    def test_conflicting_or_missing_overlap_preserves_complete_commit(self):
        later = NOW + timedelta(minutes=2)
        conflict = fixture(2, 3, at=later)
        conflict['candles'][0][4] = '100'
        conflict['data_provenance']['candles_sha256'] = candles_hash(conflict['candles'])
        missing = fixture(3, 2, at=later)
        regressing = fixture(0, 2, at=later)
        with self.create() as writer:
            writer.apply(fixture(), at=NOW)
            before = writer.read()
            for candidate in (conflict, missing, regressing):
                with self.subTest(candidate=candidate), self.assertRaises(store.CaptureConflict):
                    writer.apply(candidate, at=later)
                self.assertEqual(writer.read(), before)

    def test_invalid_schema_market_gap_and_future_time_preserve_commit(self):
        later = NOW + timedelta(minutes=2)
        values = [dict(fixture(), extra=True), fixture(market='KRW-ETH'),
                  fixture(at=later), fixture(count=2)]
        gap = fixture()
        gap['candles'][1][0] += 60000
        gap['data_provenance']['candles_sha256'] = candles_hash(gap['candles'])
        values.append(gap)
        with self.create() as writer:
            before = writer.read()
            for candidate in values:
                with self.subTest(candidate=candidate), self.assertRaises(store.CaptureUnavailable):
                    writer.apply(candidate, at=NOW)
                self.assertEqual(writer.read(), before)

    def test_clock_and_verification_cannot_regress(self):
        later = NOW + timedelta(seconds=20)
        with self.create() as writer:
            writer.attempt(later)
            before = writer.read()
            for operation in (lambda: writer.attempt(NOW), lambda: writer.stop(NOW),
                              lambda: writer.fail('transport_unavailable', at=NOW, retryable=True),
                              lambda: writer.apply(fixture(), at=later)):
                with self.assertRaises(store.CaptureUnavailable): operation()
                self.assertEqual(writer.read(), before)
            writer.apply(fixture(at=later), at=later)
            saved = writer.read()
            self.assertEqual(saved['state']['last_verified_at'], later.isoformat())

    def test_failure_and_stop_keep_data_and_failure_evidence(self):
        later = NOW + timedelta(seconds=20)
        with self.create() as writer:
            writer.apply(fixture(), at=NOW)
            before = writer.read()['fixture']
            writer.fail('rate_limited', at=later, retryable=True)
            self.assertEqual(writer.read()['state']['phase'], 'retrying')
            writer.stop(later)
            stopped = writer.read()
            self.assertEqual(stopped['state']['phase'], 'stopped')
            self.assertEqual(stopped['state']['last_error'], 'rate_limited')
            self.assertEqual(stopped['state']['consecutive_failures'], 1)
            self.assertEqual(stopped['fixture'], before)
        with store.capture_writer(self.directory) as writer:
            writer.attempt(later)
            writer.apply(fixture(2, 1, at=later), at=later)
            self.assertEqual(writer.read()['state']['consecutive_failures'], 0)
            self.assertIsNone(writer.read()['state']['last_error'])

    def test_competing_writer_rejected_without_unlinking_lock(self):
        with self.create():
            lock_stat = (self.directory/store.LOCK_NAME).stat()
            with self.assertRaises(store.CaptureUnavailable):
                with store.capture_writer(self.directory): pass
            command = [sys.executable, '-c',
                "import sys; sys.path[:0]=sys.argv[2:]; "
                "from trading_bot.market_capture_store import capture_writer; "
                "ctx=capture_writer(sys.argv[1]); ctx.__enter__()", str(self.directory),
                str(ROOT/'apps/server'), str(ROOT/'packages/data')]
            completed = subprocess.run(command, capture_output=True, timeout=5)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual((self.directory/store.LOCK_NAME).stat().st_ino, lock_stat.st_ino)
        self.assertTrue((self.directory/store.LOCK_NAME).exists())

    def test_observation_does_not_change_bytes_or_create_files(self):
        with self.create() as writer: writer.apply(fixture(), at=NOW)
        names = sorted(path.name for path in self.directory.iterdir())
        before = {name:(self.directory/name).read_bytes() for name in names}
        store.read_capture(self.directory)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), names)
        self.assertEqual({name:(self.directory/name).read_bytes() for name in names}, before)
        missing = Path(self.temp.name) / 'missing'
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(missing)
        self.assertFalse(missing.exists())

    def test_symlinks_permissions_and_hard_links_are_rejected(self):
        with self.create(): pass
        alias = Path(self.temp.name)/'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(alias)
        for path, safe_mode in ((self.directory, 0o700),
                                (self.directory/store.DB_NAME, 0o600),
                                (self.directory/store.LOCK_NAME, 0o600)):
            path.chmod(0o755)
            with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)
            path.chmod(safe_mode)
        os.link(self.directory/store.DB_NAME, Path(self.temp.name)/'database-link')
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)

    def test_replaced_database_lock_or_directory_stops_owner(self):
        for name in (store.DB_NAME, store.LOCK_NAME, None):
            with self.subTest(name=name):
                directory = Path(self.temp.name)/('capture-'+str(name))
                with store.capture_writer(directory, config=CONFIG, now=NOW) as writer:
                    if name is None:
                        directory.rename(directory.with_name(directory.name+'-old'))
                        directory.mkdir(mode=0o700)
                    else:
                        target = directory/name
                        saved = target.read_bytes()
                        target.rename(directory/(name+'.old'))
                        target.write_bytes(saved)
                        target.chmod(0o600)
                    with self.assertRaises((store.CaptureUnavailable, store.CaptureStorageError)):
                        writer.attempt(NOW)

    def test_unexpected_schema_or_application_identity_is_not_repaired(self):
        with self.create(): pass
        db = self.directory/store.DB_NAME
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE hidden (value TEXT)')
        before = db.read_bytes()
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)
        with self.assertRaises(store.CaptureUnavailable):
            with store.capture_writer(self.directory): pass
        self.assertEqual(db.read_bytes(), before)

    def test_oversized_database_and_metadata_are_bounded(self):
        with self.create(): pass
        db = self.directory/store.DB_NAME
        with patch.object(store, 'MAX_DATABASE_BYTES', db.stat().st_size-1):
            with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)
        with sqlite3.connect(db) as connection:
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute('UPDATE capture_metadata SET body=?', ('x'*4097,))
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)

    def test_wal_or_nonempty_journal_is_not_observer_recovery(self):
        with self.create(): pass
        for suffix in ('-wal', '-shm', '-journal'):
            target = self.directory/(store.DB_NAME+suffix)
            target.write_bytes(b'x'*512)
            target.chmod(0o600)
            with self.subTest(suffix=suffix), self.assertRaises(store.CaptureUnavailable):
                store.read_capture(self.directory)
            self.assertEqual(target.read_bytes(), b'x'*512)
            target.unlink()

    def test_failed_sql_transaction_does_not_advance_rows_or_metadata(self):
        later = NOW + timedelta(minutes=1)
        with self.create() as writer:
            writer.apply(fixture(), at=NOW)
            before = writer.read()
            original = store._write_snapshot
            def write_then_fail(*args, **kwargs):
                original(*args, **kwargs)
                raise sqlite3.OperationalError('disk full after row and metadata writes')
            with patch.object(store, '_write_snapshot', side_effect=write_then_fail):
                with self.assertRaises(store.CaptureStorageError):
                    writer.apply(fixture(2, 2, at=later), at=later)
            self.assertEqual(writer.read(), before)
        self.assertEqual(store.read_capture(self.directory), before)

    def test_full_retained_window_stays_within_observer_work_budget(self):
        config = dict(CONFIG, bootstrap_bars=200, retention_bars=5000)
        at = BASE + timedelta(minutes=200, seconds=10)
        with store.capture_writer(self.directory, config=config, now=at) as writer:
            writer.apply(fixture(0, 200, at=at), at=at)
            end = 200
            while end < 5000:
                count = min(200, 5000-end+1)
                end += count-1
                at = BASE + timedelta(minutes=end, seconds=10)
                writer.apply(fixture(end-count, count, at=at), at=at)
            self.assertEqual(len(writer.read()['fixture']['candles']), 5000)
            at += timedelta(minutes=199)
            writer.apply(fixture(4999, 200, at=at), at=at)
        saved = store.read_capture(self.directory)
        self.assertEqual(len(saved['fixture']['candles']), 5000)
        self.assertEqual(saved['state']['accepted_bars_total'], 5199)
        self.assertEqual(saved['state']['evicted_bars_total'], 199)
        before = (self.directory/store.DB_NAME).read_bytes()
        with patch.object(store, 'MAX_VM_OPERATIONS', 1000):
            with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)
        self.assertEqual((self.directory/store.DB_NAME).read_bytes(), before)

    def test_malformed_state_and_overlong_candle_text_are_rejected(self):
        with self.create() as writer: writer.apply(fixture(), at=NOW)
        db = self.directory/store.DB_NAME
        with sqlite3.connect(db) as connection:
            original = json.loads(connection.execute('SELECT body FROM capture_metadata').fetchone()[0])
        for update in ({'revision':True}, {'accepted_bars_total':4}, {'successful_polls':0},
                       {'last_error':'unknown'}, {'created_at':(NOW+timedelta(seconds=1)).isoformat()},
                       {'phase':'running', 'last_attempt_at':(NOW+timedelta(seconds=1)).isoformat(),
                        'updated_at':(NOW+timedelta(seconds=2)).isoformat()}):
            modified = copy.deepcopy(original)
            modified['state'].update(update)
            with sqlite3.connect(db) as connection:
                connection.execute('UPDATE capture_metadata SET body=?', (json.dumps(modified),))
            with self.subTest(update=update), self.assertRaises(store.CaptureUnavailable):
                store.read_capture(self.directory)
        with sqlite3.connect(db) as connection:
            connection.execute('UPDATE capture_metadata SET body=?', (json.dumps(original),))
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute('UPDATE candles SET volume=?', ('1'*101,))
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)

    def test_parent_replacement_is_rejected_without_writing_new_binding(self):
        parent = Path(self.temp.name)/'parent'
        parent.mkdir(mode=0o700)
        directory = parent/'capture'
        with store.capture_writer(directory, config=CONFIG, now=NOW) as writer:
            original = writer.read()
            parent.rename(parent.with_name('moved-parent'))
            parent.mkdir(mode=0o700)
            with self.assertRaises(store.CaptureUnavailable): writer.attempt(NOW)
            self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(store.read_capture(parent.with_name('moved-parent')/'capture'), original)

    def test_crash_journal_requires_explicit_owner_recovery(self):
        with self.create() as writer: writer.apply(fixture(), at=NOW)
        before = store.read_capture(self.directory)
        script = """import os, sys
sys.path[:0] = sys.argv[2:]
from trading_bot.market_capture_store import capture_writer
with capture_writer(sys.argv[1]) as writer:
    writer.connection.execute('PRAGMA cache_size=1')
    writer.connection.execute('BEGIN IMMEDIATE')
    writer.connection.execute("UPDATE candles SET close='100'")
    writer.connection.execute("UPDATE capture_metadata SET body='{}'")
    os._exit(9)
"""
        completed = subprocess.run([sys.executable, '-c', script, str(self.directory),
            str(ROOT/'apps/server'), str(ROOT/'packages/data')], capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 9, completed.stderr.decode())
        journal = self.directory/(store.DB_NAME+'-journal')
        self.assertTrue(journal.exists())
        self.assertGreater(journal.stat().st_size, 512)
        db_bytes, journal_bytes = (self.directory/store.DB_NAME).read_bytes(), journal.read_bytes()
        with self.assertRaises(store.CaptureUnavailable): store.read_capture(self.directory)
        self.assertEqual((self.directory/store.DB_NAME).read_bytes(), db_bytes)
        self.assertEqual(journal.read_bytes(), journal_bytes)
        with store.capture_writer(self.directory) as writer:
            self.assertEqual(writer.read(), before)
        self.assertEqual(store.read_capture(self.directory), before)

    def test_failed_post_commit_confirmation_is_uncertain_not_normal_success(self):
        with self.create() as writer:
            with patch.object(store.os, 'fsync', side_effect=OSError('directory sync failed')):
                with self.assertRaises(store.CaptureStorageError): writer.apply(fixture(), at=NOW)
        # A committed artifact is deliberately retained after uncertain confirmation.
        self.assertEqual(store.read_capture(self.directory)['state']['successful_polls'], 1)

    def test_database_substitution_inside_connect_is_rejected_before_schema_writes(self):
        foreign = Path(self.temp.name)/'foreign.sqlite3'
        original_connect = sqlite3.connect
        with original_connect(foreign) as connection:
            connection.execute('CREATE TABLE sentinel (value TEXT)')
            connection.execute("INSERT INTO sentinel VALUES ('preserve foreign database')")
        foreign_bytes = foreign.read_bytes()
        target = self.directory/store.DB_NAME
        def substitute(*args, **kwargs):
            target.rename(self.directory/(store.DB_NAME+'.original'))
            target.write_bytes(foreign_bytes)
            target.chmod(0o600)
            return original_connect(*args, **kwargs)
        with patch.object(store.sqlite3, 'connect', side_effect=substitute):
            with self.assertRaises((store.CaptureUnavailable, store.CaptureStorageError)):
                with self.create(): pass
        self.assertEqual(target.read_bytes(), foreign_bytes)

    def test_resume_sqlite_operational_failure_is_a_storage_error(self):
        with self.create(): pass
        before = (self.directory/store.DB_NAME).read_bytes()
        with patch.object(store, '_connect', side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(store.CaptureStorageError):
                with store.capture_writer(self.directory): pass
        self.assertEqual((self.directory/store.DB_NAME).read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
