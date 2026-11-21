"""Native SQLite snapshot, immutable backup verification and candidate restore."""

import hashlib
import importlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal

TOOL = ROOT / "tools/stock_order_backup.py"
FIXTURE = ROOT / "config/examples/synthetic_stock_order_events.json"
ACCOUNT = "demo-stock-orders"
GUARD = """
import runpy, socket, sys
def refused(*args, **kwargs):
    raise AssertionError('Offline backup forbids network and DNS')
socket.socket.connect = refused
socket.create_connection = refused
socket.getaddrinfo = refused
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class StockOrderBackupTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), "Expected the stock-order backup implementation")
        self.backup = importlib.import_module("tools.stock_order_backup")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "orders.sqlite"
        self.destination = self.directory / "backup"
        self.candidate = self.directory / "candidate.sqlite"
        self.events = json.loads(FIXTURE.read_text())["events"]
        self.journal = StockOrderJournal.create(self.database, account_ref=ACCOUNT)

    def record(self, count):
        for event in self.events[:count]:
            self.journal.record(event)

    def create(self):
        return self.backup.create_backup(self.database, self.destination, account_ref=ACCOUNT)

    def verify(self):
        return self.backup.verify_backup(self.destination, account_ref=ACCOUNT)

    def restore(self, path=None):
        return self.backup.restore_candidate(
            self.destination,
            path or self.candidate,
            account_ref=ACCOUNT,
        )

    def contents(self, directory):
        return {
            path.name: (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in directory.iterdir()
        }

    def assert_verified(self, result, snapshot):
        self.assertEqual("paper", result["mode"])
        self.assertEqual(ACCOUNT, result["account_ref"])
        self.assertIs(result["replay_verified"], True)
        self.assertIs(result["candidate_restore_only"], True)
        for key in (
            "live_orders_enabled",
            "account_binding_verified",
            "economic_reconciliation_verified",
            "lifecycle_confirmation_verified",
            "safe_to_submit",
        ):
            self.assertIs(result[key], False)
        self.assertEqual(snapshot, result["snapshot"])

    def test_native_capture_includes_committed_wal_and_exports_standalone_delete(self):
        # Copying only the main file loses these committed events.
        with closing(sqlite3.connect(self.database)) as keeper:
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            original_main = self.database.read_bytes()
            self.record(4)
            self.assertEqual(original_main, self.database.read_bytes())
            wal = Path(str(self.database) + "-wal")
            original_wal = wal.read_bytes()
            self.assertGreater(len(original_wal), 0)
            expected = self.journal.snapshot()
            created = self.create()
            self.assert_verified(created, expected)
            self.assertEqual(4, created["snapshot"]["event_count"])
            self.assertEqual("-29730", created["snapshot"]["cash_delta_krw"])
            self.assertEqual(3, created["snapshot"]["orders"][0]["filled_quantity"])
            self.assertEqual(original_main, self.database.read_bytes())
            self.assertEqual(original_wal, wal.read_bytes())
        self.assertEqual(
            {"journal.sqlite", "manifest.json"}, {path.name for path in self.destination.iterdir()}
        )
        self.assertEqual(0o700, stat.S_IMODE(self.destination.stat().st_mode))
        for path in self.destination.iterdir():
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        with closing(
            sqlite3.connect(
                (self.destination / "journal.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
            )
        ) as database:
            self.assertEqual(("delete",), database.execute("PRAGMA journal_mode").fetchone())
        self.assert_verified(self.verify(), expected)

    def test_restored_uncertainty_and_partial_fills_stay_blocked_then_resume_paper(self):
        for count, state in ((2, "uncertain"), (4, "partially_filled")):
            with self.subTest(count=count):
                destination = self.directory / ("backup-" + str(count))
                candidate = self.directory / ("restored-" + str(count) + ".sqlite")
                self.record(count)
                expected = self.journal.snapshot()
                self.backup.create_backup(self.database, destination, account_ref=ACCOUNT)
                report = self.backup.restore_candidate(destination, candidate, account_ref=ACCOUNT)
                self.assert_verified(report, expected)
                self.assertEqual(state, report["snapshot"]["orders"][0]["status"])
                self.assertTrue(report["snapshot"]["blocked"])
                restored = StockOrderJournal.open_existing(candidate, account_ref=ACCOUNT)
                another = deepcopy(self.events[0])
                another.update(event_id="another.reserve", client_order_id="another-order")
                with self.assertRaises(ValueError):
                    restored.record(another)
                for event in self.events[count:8]:
                    restored.record(event)
                resumed = restored.snapshot()
                self.assertFalse(resumed["blocked"])
                self.assertEqual("canceled", resumed["orders"][0]["status"])
                self.assertEqual(5, resumed["orders"][0]["filled_quantity"])
                self.assertEqual(expected, self.journal.snapshot())
                self.assertEqual(0o600, stat.S_IMODE(candidate.stat().st_mode))

    def test_readonly_verification_and_restore_do_not_change_backup_directory(self):
        self.record(14)
        expected = self.journal.snapshot()
        self.create()
        for path in self.destination.iterdir():
            path.chmod(0o400)
        self.destination.chmod(0o500)
        before = self.contents(self.destination)
        directory_stat = self.destination.stat()
        self.assert_verified(self.verify(), expected)
        self.assert_verified(self.restore(), expected)
        self.assertEqual(before, self.contents(self.destination))
        self.assertEqual(directory_stat.st_mtime_ns, self.destination.stat().st_mtime_ns)
        self.assertEqual(0o500, stat.S_IMODE(self.destination.stat().st_mode))
        self.destination.chmod(0o700)

    def test_capture_refuses_a_real_source_commit_during_native_backup(self):
        self.record(1)
        real_connect = sqlite3.connect
        journal, event = self.journal, self.events[1]

        class ChangingConnection(sqlite3.Connection):
            def backup(self, target, **kwargs):
                result = super().backup(target, **kwargs)
                journal.record(event)
                return result

        def connect(database, *args, **kwargs):
            if str(database).startswith(self.database.as_uri() + "?"):
                kwargs["factory"] = ChangingConnection
            return real_connect(database, *args, **kwargs)

        with patch.object(self.backup.sqlite3, "connect", side_effect=connect):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual("uncertain", self.journal.snapshot()["orders"][0]["status"])
        self.assertFalse((self.destination / "manifest.json").exists())
        if self.destination.exists():
            with self.assertRaises(ValueError):
                self.verify()

    def test_manifest_rejects_duplicate_nonfinite_malformed_and_oversized_values(self):
        self.record(2)
        self.create()
        path = self.destination / "manifest.json"
        original = path.read_bytes()
        manifest = json.loads(original)
        malformed = [
            b'{"schema_version":1,"schema_version":1}',
            b'{"schema_version":NaN}',
            b'{"schema_version":Infinity}',
            b'{"schema_version":' + b"9" * 10000 + b"}",
            b" " * 65537,
            b"[" * 1000 + b"]" * 1000,
        ]
        for field, value in (
            ("schema_version", True),
            ("mode", "live"),
            ("account_ref", "different-account"),
            ("database_sha256", "A" * 64),
            ("snapshot_sha256", "0" * 64),
            ("event_count", True),
            ("event_count", -1),
            ("event_count", 3),
            ("created_at_utc", "not-a-time"),
            ("live_orders_enabled", True),
            ("candidate_restore_only", False),
        ):
            altered = {**manifest, field: value}
            malformed.append(json.dumps(altered).encode())
        malformed.extend(
            (
                json.dumps({**manifest, "unknown": "EXAMPLE-SECRET"}).encode(),
                json.dumps(
                    {key: value for key, value in manifest.items() if key != "database_sha256"}
                ).encode(),
            )
        )
        for raw in malformed:
            with self.subTest(raw=raw[:80]):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.verify()
        path.write_bytes(original)
        self.assertEqual(2, self.verify()["snapshot"]["event_count"])

    def test_recomputed_file_hash_cannot_hide_invalid_journal_replay(self):
        self.record(4)
        self.create()
        path = self.destination / "journal.sqlite"
        with closing(sqlite3.connect(path)) as database:
            database.execute("UPDATE events SET event_json='{}' WHERE sequence=4")
            database.commit()
        manifest_path = self.destination / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["database_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["database_bytes"] = path.stat().st_size
        manifest_path.write_text(json.dumps(manifest))
        before = self.contents(self.destination)
        with self.assertRaises(ValueError):
            self.verify()
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.candidate.exists())
        self.assertEqual(before, self.contents(self.destination))

    def test_unmanifested_files_sidecars_and_final_symlinks_are_preserved_and_refused(self):
        self.create()
        for name in (
            "other.json",
            "journal.sqlite-wal",
            "journal.sqlite-shm",
            "journal.sqlite-journal",
        ):
            with self.subTest(name=name):
                entry = self.destination / name
                entry.symlink_to(self.directory / "missing-sidecar-target")
                with self.assertRaises(ValueError):
                    self.verify()
                self.assertTrue(entry.is_symlink())
                entry.unlink()
        for name in ("journal.sqlite", "manifest.json"):
            with self.subTest(name=name):
                entry = self.destination / name
                saved = self.directory / (name + ".saved")
                entry.rename(saved)
                entry.symlink_to(saved)
                with self.assertRaises(ValueError):
                    self.verify()
                self.assertTrue(entry.is_symlink())
                entry.unlink()
                saved.rename(entry)
        alias = self.directory / "backup-alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.backup.verify_backup(alias, account_ref=ACCOUNT)

    def test_existing_create_and_restore_targets_and_sidecars_survive_refusal(self):
        self.record(2)
        self.create()
        before = self.contents(self.destination)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(before, self.contents(self.destination))
        for suffix in ("", "-wal", "-shm", "-journal"):
            for kind in ("file", "directory", "dangling", "symlink"):
                with self.subTest(suffix=suffix, kind=kind):
                    candidate = self.directory / (kind + (suffix or "main") + ".sqlite")
                    collision = Path(str(candidate) + suffix)
                    if kind == "file":
                        collision.write_bytes(b"existing-evidence")
                    elif kind == "directory":
                        collision.mkdir()
                        (collision / "sentinel").write_text("keep")
                    else:
                        collision.symlink_to(
                            self.database
                            if kind == "symlink"
                            else self.directory / "missing-target"
                        )
                    with self.assertRaises(ValueError):
                        self.restore(candidate)
                    if kind == "file":
                        self.assertEqual(b"existing-evidence", collision.read_bytes())
                    elif kind == "directory":
                        self.assertEqual("keep", (collision / "sentinel").read_text())
                    else:
                        self.assertTrue(collision.is_symlink())
                    if suffix:
                        self.assertFalse(candidate.exists())
        self.assertEqual(before, self.contents(self.destination))

    def test_restore_cannot_place_candidate_inside_backup_or_aliased_parent(self):
        self.create()
        alias = self.directory / "alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        before = self.contents(self.destination)
        for parent in (self.destination, alias):
            with self.subTest(parent=parent.name), self.assertRaises(ValueError):
                self.restore(parent / "candidate.sqlite")
        self.assertEqual(before, self.contents(self.destination))

    def test_create_cannot_take_an_unused_source_reserved_sidecar_basename(self):
        for suffix in ("-wal", "-shm", "-journal"):
            destination = Path(str(self.database) + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.backup.create_backup(self.database, destination, account_ref=ACCOUNT)
            self.assertFalse(destination.exists())

    def test_missing_wrong_account_and_oversized_input_never_create_candidate(self):
        self.create()
        with self.assertRaises(ValueError):
            self.backup.verify_backup(self.destination, account_ref="wrong-account")
        with self.assertRaises(ValueError):
            self.backup.restore_candidate(
                self.destination, self.candidate, account_ref="wrong-account"
            )
        self.assertFalse(self.candidate.exists())
        oversized = self.directory / "oversized.sqlite"
        with oversized.open("wb") as output:
            output.truncate(100000001)
        with self.assertRaises(ValueError):
            self.backup.create_backup(
                oversized, self.directory / "large-backup", account_ref=ACCOUNT
            )
        self.assertFalse((self.directory / "large-backup").exists())
        missing = self.directory / "absent" / "orders.sqlite"
        with self.assertRaises(ValueError):
            self.backup.create_backup(
                missing, self.directory / "missing-backup", account_ref=ACCOUNT
            )
        self.assertFalse(missing.parent.exists())

    def test_replacement_of_backup_file_after_publication_flush_is_refused_and_preserved(self):
        self.record(2)
        real_fsync = os.fsync
        replacement = {}

        def replace_after_flush(descriptor):
            real_fsync(descriptor)
            if (
                self.destination.exists()
                and stat.S_ISDIR(os.fstat(descriptor).st_mode)
                and os.fstat(descriptor).st_ino == self.destination.stat().st_ino
            ):
                path = self.destination / "journal.sqlite"
                raw = path.read_bytes()
                path.rename(self.directory / "original-backup.sqlite")
                path.write_bytes(raw)
                replacement["inode"] = path.stat().st_ino

        with patch.object(self.backup.os, "fsync", side_effect=replace_after_flush):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual(replacement["inode"], (self.destination / "journal.sqlite").stat().st_ino)
        self.assertEqual(2, self.journal.snapshot()["event_count"])

    def test_failed_parent_directory_flush_refuses_and_preserves_verified_backup(self):
        self.record(2)
        expected = self.journal.snapshot()
        real_fsync = os.fsync
        parent_identity = (self.directory.stat().st_dev, self.directory.stat().st_ino)

        def refuse_parent_flush(descriptor):
            info = os.fstat(descriptor)
            if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == parent_identity:
                # The final parent flush must occur after complete publication.
                self.assert_verified(self.verify(), expected)
                raise OSError("EXAMPLE-SECRET-PARENT-FSYNC-FAILURE")
            return real_fsync(descriptor)

        with patch.object(self.backup.os, "fsync", side_effect=refuse_parent_flush):
            with self.assertRaises(ValueError) as caught:
                self.create()
        self.assertNotIn("EXAMPLE-SECRET", str(caught.exception))
        self.assertEqual(expected, self.journal.snapshot())
        self.assertEqual(
            {"journal.sqlite", "manifest.json"},
            {entry.name for entry in self.destination.iterdir()},
        )
        self.assert_verified(self.verify(), expected)

    def test_parent_replacement_at_final_flush_is_refused_and_preserved(self):
        self.record(2)
        expected = self.journal.snapshot()
        parent = self.directory / "publication-parent"
        parent.mkdir(mode=0o700)
        self.destination = parent / "backup"
        detached = self.directory / "detached-parent"
        parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
        real_fsync = os.fsync

        def replace_parent(descriptor):
            info = os.fstat(descriptor)
            real_fsync(descriptor)
            if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == parent_identity:
                parent.rename(detached)
                parent.mkdir(mode=0o700)
                (parent / "sentinel").write_text("replacement-parent-evidence")

        with patch.object(self.backup.os, "fsync", side_effect=replace_parent):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual("replacement-parent-evidence", (parent / "sentinel").read_text())
        self.assertEqual({"sentinel"}, {entry.name for entry in parent.iterdir()})
        self.assert_verified(
            self.backup.verify_backup(detached / "backup", account_ref=ACCOUNT), expected
        )
        self.assertEqual(expected, self.journal.snapshot())

    def test_backup_file_replacement_during_parent_flush_is_refused_and_preserved(self):
        self.record(2)
        real_fsync = os.fsync
        parent_identity = (self.directory.stat().st_dev, self.directory.stat().st_ino)
        replacement = {}

        def replace_file(descriptor):
            info = os.fstat(descriptor)
            real_fsync(descriptor)
            if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == parent_identity:
                path = self.destination / "journal.sqlite"
                raw = path.read_bytes()
                path.rename(self.directory / "detached-parent-flush.sqlite")
                path.write_bytes(raw)
                replacement["inode"] = path.stat().st_ino

        with patch.object(self.backup.os, "fsync", side_effect=replace_file):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual(replacement["inode"], (self.destination / "journal.sqlite").stat().st_ino)
        self.assertEqual(2, self.journal.snapshot()["event_count"])

    def test_replacement_of_restored_candidate_at_final_flush_is_refused_and_preserved(self):
        self.record(2)
        self.create()
        real_fsync = os.fsync
        replacement = {}

        def replace_after_flush(descriptor):
            real_fsync(descriptor)
            if (
                self.candidate.exists()
                and stat.S_ISDIR(os.fstat(descriptor).st_mode)
                and os.fstat(descriptor).st_ino == self.directory.stat().st_ino
            ):
                raw = self.candidate.read_bytes()
                self.candidate.rename(self.directory / "original-candidate.sqlite")
                self.candidate.write_bytes(raw)
                replacement["inode"] = self.candidate.stat().st_ino

        with patch.object(self.backup.os, "fsync", side_effect=replace_after_flush):
            with self.assertRaises(ValueError):
                self.restore()
        self.assertEqual(replacement["inode"], self.candidate.stat().st_ino)
        self.assertEqual(2, self.verify()["snapshot"]["event_count"])

    def test_path_resolution_failures_have_static_api_diagnostics(self):
        loop = self.directory / "EXAMPLE-SECRET-PATH"
        loop.symlink_to(loop, target_is_directory=True)
        with self.assertRaises(ValueError) as caught:
            self.backup.create_backup(self.database, loop / "new-backup", account_ref=ACCOUNT)
        self.assertNotIn("EXAMPLE-SECRET", str(caught.exception))
        self.assertNotIn(str(self.directory), str(caught.exception))

    def test_native_capture_supports_stdlib_without_exported_sqlite_error_constants(self):
        # Older supported Python sqlite3 modules do not export every result code.
        with patch.dict(self.backup.sqlite3.__dict__):
            for name in ("SQLITE_BUSY", "SQLITE_LOCKED"):
                self.backup.sqlite3.__dict__.pop(name, None)
            result = self.create()
        self.assertEqual(0, result["snapshot"]["event_count"])
        self.assertTrue(result["replay_verified"])

    def test_byte_hash_rejects_a_semantically_unchanged_database_edit(self):
        self.create()
        path = self.destination / "journal.sqlite"
        raw = bytearray(path.read_bytes())
        # The empty index page's free bytes do not affect SQLite logical state.
        raw[-1] ^= 1
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "edited.sqlite"
            copy.write_bytes(raw)
            with closing(sqlite3.connect(copy)) as database:
                database.execute("PRAGMA journal_mode=WAL")
            replay = StockOrderJournal.open_existing(copy, account_ref=ACCOUNT).snapshot()
            self.assertEqual(self.journal.snapshot(), replay)
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.verify()

    def test_forged_hash_cannot_admit_wal_mode_or_an_oversized_database(self):
        self.create()
        path = self.destination / "journal.sqlite"
        with closing(sqlite3.connect(path)) as database:
            database.execute("PRAGMA journal_mode=WAL")
        manifest_path = self.destination / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["database_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(
            {"journal.sqlite", "manifest.json"},
            {entry.name for entry in self.destination.iterdir()},
        )
        with self.assertRaises(ValueError):
            self.verify()
        with path.open("r+b") as output:
            output.truncate(100000001)
        with self.assertRaises(ValueError):
            self.verify()

    def test_create_existing_regular_directory_and_dangling_targets_are_preserved(self):
        for kind in ("regular", "directory", "dangling", "symlink"):
            destination = self.directory / kind
            if kind == "regular":
                destination.write_text("keep-existing")
            elif kind == "directory":
                destination.mkdir()
                (destination / "sentinel").write_text("keep-existing")
            else:
                destination.symlink_to(
                    self.database if kind == "symlink" else self.directory / "absent-target"
                )
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.backup.create_backup(self.database, destination, account_ref=ACCOUNT)
            if kind == "regular":
                self.assertEqual("keep-existing", destination.read_text())
            elif kind == "directory":
                self.assertEqual("keep-existing", (destination / "sentinel").read_text())
            else:
                self.assertTrue(destination.is_symlink())

    def test_create_rejects_source_symlinks_and_restore_requires_private_existing_parent(self):
        alias = self.directory / "source-alias.sqlite"
        alias.symlink_to(self.database)
        with self.assertRaises(ValueError):
            self.backup.create_backup(alias, self.destination, account_ref=ACCOUNT)
        self.assertFalse(self.destination.exists())
        self.create()
        parent = self.directory / "shared"
        parent.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            self.restore(parent / "candidate.sqlite")
        self.assertEqual([], list(parent.iterdir()))
        absent = self.directory / "absent-parent"
        with self.assertRaises(ValueError):
            self.restore(absent / "candidate.sqlite")
        self.assertFalse(absent.exists())

    def test_replaced_backup_directory_is_preserved_on_write_failure(self):
        real_fsync = os.fsync
        swapped = False

        def replace_directory(descriptor):
            nonlocal swapped
            real_fsync(descriptor)
            if not swapped and (self.destination / "journal.sqlite").exists():
                swapped = True
                self.destination.rename(self.directory / "detached-backup")
                self.destination.mkdir()
                (self.destination / "sentinel").write_text("replacement-evidence")

        with patch.object(self.backup.os, "fsync", side_effect=replace_directory):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual("replacement-evidence", (self.destination / "sentinel").read_text())
        self.assertEqual({"sentinel"}, {entry.name for entry in self.destination.iterdir()})

    def test_write_failure_keeps_source_and_never_repairs_partial_backup(self):
        self.record(2)
        expected = self.journal.snapshot()
        real_fsync = os.fsync

        def fail_manifest(descriptor):
            if (self.destination / "journal.sqlite").exists():
                raise OSError("EXAMPLE-SECRET-WRITE-FAILURE")
            return real_fsync(descriptor)

        with patch.object(self.backup.os, "fsync", side_effect=fail_manifest):
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual(expected, self.journal.snapshot())
        before = self.contents(self.destination)
        with self.assertRaises(ValueError):
            self.verify()
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(before, self.contents(self.destination))

    def run_cli(self, arguments):
        environment = dict(os.environ)
        environment.update(
            {
                "KIS_VTS_ACCESS_TOKEN": "EXAMPLE-SECRET-TOKEN",
                "KIS_VTS_APPKEY": "EXAMPLE-SECRET-APPKEY",
                "KIS_VTS_APPSECRET": "EXAMPLE-SECRET-APPSECRET",
                "KIS_VTS_CANO": "12345678",
                "KIS_VTS_ACNT_PRDT_CD": "01",
                "HTTPS_PROXY": "https://EXAMPLE-SECRET.invalid",
            }
        )
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", GUARD, str(TOOL), *arguments],
            cwd=self.directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("EXAMPLE-SECRET", result.stdout + result.stderr)
        self.assertNotIn("12345678", result.stdout + result.stderr)
        self.assertNotIn(str(self.directory), result.stdout + result.stderr)
        return result

    def test_actual_stdlib_cli_create_verify_restore_is_offline_and_private(self):
        self.record(14)
        shared = ["--account-ref", ACCOUNT]
        commands = [
            [
                "create",
                "--database",
                str(self.database),
                "--destination",
                str(self.destination),
                *shared,
            ],
            ["verify", "--backup", str(self.destination), *shared],
            [
                "restore",
                "--backup",
                str(self.destination),
                "--destination",
                str(self.candidate),
                *shared,
            ],
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                result = self.run_cli(["--mode", "paper", *command])
                self.assertEqual(0, result.returncode, result.stderr)
                self.assert_verified(json.loads(result.stdout), self.journal.snapshot())
        self.assertEqual(
            14,
            StockOrderJournal.open_existing(self.candidate, account_ref=ACCOUNT).snapshot()[
                "event_count"
            ],
        )

    def test_actual_cli_static_refusal_never_echoes_paths_or_credentials(self):
        for arguments in (
            ["--mode", "EXAMPLE-SECRET-MODE", "verify"],
            ["verify", "--backup", str(self.destination), "--account-ref", ACCOUNT],
            [
                "--mode",
                "paper",
                "verify",
                "--backup",
                str(self.destination),
                "--account-ref",
                ACCOUNT,
            ],
            [
                "--mode",
                "paper",
                "create",
                "--database",
                str(self.database),
                "--destination",
                str(self.destination),
                "--account-ref",
                "EXAMPLE-SECRET/<bad>",
            ],
        ):
            with self.subTest(arguments=arguments[:3]):
                result = self.run_cli(arguments)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertTrue(result.stderr)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
