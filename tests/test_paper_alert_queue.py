"""Local queue requires explicit sync and explicit operator acknowledgement."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'paper_alert_queue.py'
SPEC = importlib.util.spec_from_file_location('paper_alert_queue', SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class QueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'private'
        self.folder.mkdir(mode=0o700)
        self.db = self.folder / 'paper-monitor-events.sqlite'
        self.db.touch(mode=0o600)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE state (singleton INTEGER PRIMARY KEY, status TEXT, signature TEXT, observed_at TEXT, observations INTEGER);
                CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT, status TEXT, signature TEXT);
                INSERT INTO events(sequence,observed_at,status,signature) VALUES(1,'2026-11-16T00:01:00Z','attention_required','{"capture":"stale"}');
                INSERT INTO state(singleton,status,signature,observed_at,observations) VALUES(1,'attention_required','{"capture":"stale"}','2026-11-16T00:01:00Z',1);
            """)

    def cli(self, *args):
        p = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), *args,
                            '--directory', str(self.folder)], capture_output=True,
                           text=True, timeout=8)
        return p.returncode, json.loads(p.stdout)

    def append(self, seq, status, signature):
        with sqlite3.connect(self.db) as c:
            stamp = '2026-11-16T00:02:00Z'
            c.execute('INSERT INTO events(sequence,observed_at,status,signature) VALUES(?,?,?,?)',
                      (seq, stamp, status, signature))
            c.execute('UPDATE state SET status=?,signature=?,observations=observations+1,observed_at=?',
                      (status, signature, stamp))

    def test_initial_sync(self):
        code, report = self.cli('sync')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['pending'], 1)
        self.assertFalse(report['boundaries']['production_ready'])

    def test_sync_idempotent(self):
        self.cli('sync')
        self.cli('sync')
        _, report = self.cli('list')
        self.assertEqual(report['pending'], 1)

    def test_ack_explicit_and_no_duplicate(self):
        self.cli('sync')
        code, data = self.cli('ack', '--sequence', '1', '--reason', 'operator checked logs')
        self.assertEqual(code, 0, data)
        self.assertEqual(data['acknowledged_sequence'], 1)
        self.assertEqual(self.cli('ack', '--sequence', '1', '--reason', 'operator checked logs')[0], 2)
        self.assertEqual(self.cli('list')[1]['pending'], 0)

    def test_missing_reason_refused(self):
        self.cli('sync')
        self.assertEqual(self.cli('ack', '--sequence', '1')[0], 2)

    def test_short_reason_refused(self):
        self.cli('sync')
        self.assertEqual(self.cli('ack', '--sequence', '1', '--reason', 'ok')[0], 2)

    def test_source_mutation_refused_without_losing_ack(self):
        self.cli('sync')
        self.cli('ack', '--sequence', '1', '--reason', 'operator checked logs')
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE events SET signature='changed' WHERE sequence=1")
            c.execute("UPDATE state SET signature='changed'")
        self.assertEqual(self.cli('sync')[0], 2)
        with sqlite3.connect(self.db) as c:
            self.assertIsNotNone(c.execute('SELECT acknowledged_at FROM alert_queue WHERE sequence=1').fetchone()[0])

    def test_new_recovery_event(self):
        self.cli('sync')
        self.append(2, 'healthy', '{"capture":"current"}')
        self.assertEqual(self.cli('sync')[0], 0)
        rows = self.cli('list')[1]['items']
        self.assertEqual([(x['sequence'], x['status']) for x in rows],
                         [(1, 'attention_required'), (2, 'healthy')])

    def test_new_source_event_requires_sync(self):
        self.cli('sync')
        self.append(2, 'healthy', '{"capture":"current"}')
        self.assertEqual(self.cli('list')[0], 2)
        self.assertEqual(self.cli('ack', '--sequence', '1',
                                  '--reason', 'operator reviewed incident')[0], 2)
        self.assertEqual(self.cli('sync')[0], 0)

    def test_incomplete_history_refused(self):
        self.append(3, 'healthy', '{"capture":"current"}')
        self.assertEqual(self.cli('sync')[0], 2)

    def test_missing_db_does_not_create(self):
        self.db.unlink()
        self.assertEqual(self.cli('sync')[0], 2)
        self.assertFalse(self.db.exists())

    def test_symlink_database_refused(self):
        self.db.rename(self.folder / 'real.sqlite')
        self.db.symlink_to('real.sqlite')
        self.assertEqual(self.cli('sync')[0], 2)

    def test_relative_dir_refused(self):
        p = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT),
                            'sync', '--directory', 'relative'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_check_readonly_after_sync(self):
        self.cli('sync')
        self.assertEqual(self.cli('list')[0], 0)

    def test_ack_does_not_send_notifications_or_orders(self):
        self.cli('sync')
        self.cli('ack', '--sequence', '1', '--reason', 'operator reviewed incident')
        boundaries = self.cli('list')[1]['boundaries']
        self.assertFalse(boundaries['broker_orders'])
        self.assertFalse(boundaries['external_delivery'])

    def test_real_v52_monitor_journal_roundtrip(self):
        journal = self.folder.parent / 'v52-private'
        journal.mkdir(mode=0o700)
        observed = self.folder.parent / 'observation.json'
        snapshot = {
            'schema_version': 1, 'source': 'paper_monitor',
            'status': 'attention_required', 'exit_code': 1,
            'checks': {
                'service': {'status': 'running', 'exit_code': 0, 'healthy': True},
                'capture': {'status': 'stale', 'exit_code': 1, 'healthy': False},
                'account': {'status': 'current', 'exit_code': 0, 'healthy': True},
            },
        }
        observed.write_text(json.dumps(snapshot))
        observed.chmod(0o600)
        v52 = SCRIPT.parent / 'paper_monitor_events.py'
        record = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(v52), 'record',
             '--directory', str(journal), '--input', str(observed)],
            capture_output=True, text=True, timeout=8)
        self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
        self.folder = journal
        code, status = self.cli('sync')
        self.assertEqual(code, 0, status)
        self.assertEqual(status['pending'], 1)
        code, ack = self.cli('ack', '--sequence', '1',
                             '--reason', 'operator reviewed incident')
        self.assertEqual(code, 0, ack)

    def test_special_character_directory(self):
        special = self.folder.parent / 'private?mode=memory'
        special.mkdir(mode=0o700)
        (special / module.DB_NAME).write_bytes(self.db.read_bytes())
        (special / module.DB_NAME).chmod(0o600)
        self.folder = special
        self.assertEqual(self.cli('sync')[0], 0)


if __name__ == '__main__':
    unittest.main()
