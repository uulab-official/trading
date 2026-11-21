"""Durable paper-only stock events, exact accounting and SQLite recovery."""

import hashlib
import importlib
import json
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = ROOT / "packages/core/trading_core"
sys.path.insert(0, str(ROOT / "packages/core"))


def event(identity, kind, client="buy-1", **data):
    return {"event_id": identity, "kind": kind, "client_order_id": client, "data": data}


def reservation(identity="reserve-1", client="buy-1", **changes):
    return event(
        identity,
        "reserve",
        client,
        **{
            "order_date": "2026-11-21",
            "symbol": "005930",
            "side": "buy",
            "quantity": 5,
            "limit_price": "70000",
            "max_notional": "350000",
            **changes,
        },
    )


def acknowledgment(identity="ack-1", client="buy-1", **changes):
    return event(
        identity,
        "acknowledge",
        client,
        **{
            "broker_order_id": "0000000001",
            "broker_org_no": "06010",
            **changes,
        },
    )


def fill(identity="fill-1", client="buy-1", **changes):
    return event(
        identity,
        "fill",
        client,
        **{
            "execution_id": "exec-1",
            "quantity": 2,
            "price": "69900",
            "fee_krw": "13",
            "tax_krw": "0",
            **changes,
        },
    )


class StockOrderJournalTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("trading_core.stock_order_journal")
        except ModuleNotFoundError as error:
            if error.name != "trading_core.stock_order_journal":
                raise
            self.fail("The durable stock-order event journal is not implemented")
        self.Journal = self.module.StockOrderJournal
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "stock.sqlite"
        self.journal = self.Journal.create(self.path, account_ref="paper-desk")

    def accepted(self, **changes):
        self.journal.record(reservation(**changes))
        return self.journal.record(acknowledgment())

    def cancel(self):
        self.journal.record(event("cancel-1", "cancel_requested"))
        return self.journal.record(event("canceled-1", "canceled", evidence_id="sim-cancel-1"))

    def assert_unchanged_failure(self, candidate, error=ValueError):
        before = self.journal.snapshot()
        with self.assertRaises(error):
            self.journal.record(candidate)
        self.assertEqual(before, self.journal.snapshot())

    def test_empty_snapshot_exposes_only_paper_deltas_and_account_alias(self):
        self.assertEqual(
            self.journal.snapshot(),
            {
                "mode": "paper",
                "live_orders_enabled": False,
                "provenance": "synthetic_or_caller_supplied_unverified",
                "account_ref": "paper-desk",
                "currency": "KRW",
                "market": "KRX",
                "event_count": 0,
                "execution_count": 0,
                "pending_client_order_ids": [],
                "blocked": False,
                "cash_delta_krw": "0",
                "position_deltas": {},
                "orders": [],
            },
        )

    def test_partial_cancellation_preserves_exact_executed_cost_and_unfilled_shares(self):
        self.accepted()
        self.journal.record(fill())
        result = self.cancel()
        self.assertEqual(result["cash_delta_krw"], "-139813")
        self.assertEqual(result["position_deltas"], {"005930": 2})
        self.assertFalse(result["blocked"])
        self.assertEqual(result["pending_client_order_ids"], [])
        self.assertEqual(
            result["orders"],
            [
                {
                    "client_order_id": "buy-1",
                    "order_date": "2026-11-21",
                    "symbol": "005930",
                    "side": "buy",
                    "quantity": 5,
                    "limit_price": "70000",
                    "max_notional": "350000",
                    "status": "canceled",
                    "broker_order_id": "0000000001",
                    "broker_org_no": "06010",
                    "filled_quantity": 2,
                    "unfilled_quantity": 3,
                    "working_quantity": 0,
                    "gross_krw": "139800",
                    "fee_krw": "13",
                    "tax_krw": "0",
                    "cash_delta_krw": "-139813",
                    "executions": [
                        {
                            "execution_id": "exec-1",
                            "quantity": 2,
                            "price": "69900",
                            "fee_krw": "13",
                            "tax_krw": "0",
                            "gross_krw": "139800",
                            "cash_delta_krw": "-139813",
                        }
                    ],
                }
            ],
        )
        reopened = self.Journal.open_existing(self.path, account_ref="paper-desk", read_only=True)
        self.assertEqual(result, reopened.snapshot())

    def test_uncertainty_blocks_new_intent_after_restart_without_resending(self):
        self.journal.record(reservation())
        uncertain = self.journal.record(event("uncertain-1", "submission_uncertain"))
        self.journal = self.Journal.open_existing(self.path, account_ref="paper-desk")
        self.assertEqual(uncertain, self.journal.snapshot())
        self.assertEqual(uncertain["orders"][0]["status"], "uncertain")
        self.assertTrue(uncertain["blocked"])
        self.assertEqual(uncertain["pending_client_order_ids"], ["buy-1"])
        self.assert_unchanged_failure(reservation("reserve-2", "buy-2"))
        self.assertEqual(self.journal.record(reservation()), uncertain)
        self.assertEqual(self.journal.record(acknowledgment())["orders"][0]["status"], "accepted")

    def test_every_unresolved_state_blocks_a_different_new_intent(self):
        steps = [
            reservation(),
            event("uncertain-1", "submission_uncertain"),
            acknowledgment(),
            fill(),
            event("cancel-1", "cancel_requested"),
        ]
        for step in steps:
            self.journal.record(step)
            with self.subTest(state=self.journal.snapshot()["orders"][0]["status"]):
                self.assert_unchanged_failure(reservation("reserve-2", "buy-2"))

    def test_identical_event_id_is_idempotent_and_changed_payload_is_refused(self):
        first = self.journal.record(reservation())
        self.assertEqual(first, self.journal.record(deepcopy(reservation())))
        for candidate in (
            reservation(quantity=4),
            acknowledgment(identity="reserve-1"),
            reservation(client="changed"),
        ):
            with self.subTest(kind=candidate["kind"]):
                self.assert_unchanged_failure(candidate)
        self.assertEqual(self.journal.snapshot()["event_count"], 1)

    def test_execution_redelivery_with_new_event_id_counts_once_even_after_cancel(self):
        self.accepted()
        self.journal.record(fill())
        canceled = self.cancel()
        duplicate = self.journal.record(fill(identity="duplicate-fill"))
        self.assertEqual(duplicate["event_count"], 6)
        self.assertEqual(duplicate["execution_count"], 1)
        self.assertEqual(duplicate["orders"], canceled["orders"])
        self.assertEqual(duplicate["cash_delta_krw"], canceled["cash_delta_krw"])
        self.assertEqual(self.journal.record(fill(identity="duplicate-fill")), duplicate)
        for changes in ({"quantity": 1}, {"price": "69800"}, {"fee_krw": "14"}, {"tax_krw": "1"}):
            self.assert_unchanged_failure(fill(identity="conflicting-fill", **changes))

    def test_semantic_redeliveries_after_terminal_state_cannot_regress_status(self):
        self.journal.record(reservation())
        self.journal.record(event("uncertain-1", "submission_uncertain"))
        self.journal.record(acknowledgment())
        self.journal.record(fill(quantity=5))
        for candidate in (
            reservation("duplicate-reserve"),
            acknowledgment("duplicate-ack"),
            event("duplicate-uncertain", "submission_uncertain"),
            fill("duplicate-exec", quantity=5),
        ):
            result = self.journal.record(candidate)
            self.assertEqual(result["orders"][0]["status"], "filled")
            self.assertEqual(result["execution_count"], 1)
            self.assertEqual(result["orders"][0]["filled_quantity"], 5)
        self.assertEqual(result["event_count"], 8)
        self.assert_unchanged_failure(acknowledgment("changed-ack", broker_org_no="06011"))
        self.assert_unchanged_failure(reservation("changed-reserve", quantity=4))

    def test_late_partial_fill_during_cancellation_keeps_cancel_pending(self):
        self.accepted()
        self.journal.record(event("cancel-1", "cancel_requested"))
        pending = self.journal.record(fill())
        self.assertEqual(pending["orders"][0]["status"], "cancel_pending")
        self.assertEqual(pending["orders"][0]["working_quantity"], 3)
        canceled = self.journal.record(event("canceled-1", "canceled", evidence_id="sim-cancel"))
        self.assertEqual(canceled["orders"][0]["filled_quantity"], 2)
        self.assertEqual(canceled["orders"][0]["unfilled_quantity"], 3)

    def test_full_fill_wins_cancel_race_and_new_cancel_confirmation_is_refused(self):
        self.accepted()
        self.journal.record(fill())
        self.journal.record(event("cancel-1", "cancel_requested"))
        final = self.journal.record(fill("fill-2", execution_id="exec-2", quantity=3, fee_krw="20"))
        self.assertEqual(final["orders"][0]["status"], "filled")
        self.assertEqual(final["orders"][0]["working_quantity"], 0)
        self.assertEqual(final["cash_delta_krw"], "-349533")
        self.assert_unchanged_failure(event("canceled-1", "canceled", evidence_id="too-late"))
        redelivery = self.journal.record(event("duplicate-cancel", "cancel_requested"))
        self.assertEqual(redelivery["orders"], final["orders"])

    def test_new_fills_after_terminal_cancel_or_rejection_are_refused(self):
        self.accepted()
        self.cancel()
        self.assert_unchanged_failure(fill())
        self.journal.record(reservation("reserve-2", "buy-2"))
        rejected = self.journal.record(
            event("reject-2", "rejected", "buy-2", evidence_id="sim-reject")
        )
        self.assertEqual(rejected["orders"][1]["unfilled_quantity"], 5)
        self.assertEqual(rejected["orders"][1]["working_quantity"], 0)
        self.assert_unchanged_failure(fill("fill-2", "buy-2"))
        self.assert_unchanged_failure(acknowledgment("ack-2", "buy-2"))

    def test_rejection_requires_zero_fills_and_final_evidence_is_immutable(self):
        self.accepted()
        self.journal.record(fill())
        self.assert_unchanged_failure(event("reject-1", "rejected", evidence_id="sim-reject"))
        self.cancel()
        self.journal.record(reservation("reserve-2", "buy-2"))
        terminal = event("reject-2", "rejected", "buy-2", evidence_id="sim-reject")
        rejected = self.journal.record(terminal)
        duplicate = self.journal.record({**terminal, "event_id": "duplicate-reject"})
        self.assertEqual(duplicate["orders"], rejected["orders"])
        self.assert_unchanged_failure(
            event("reject-change", "rejected", "buy-2", evidence_id="changed")
        )

    def test_ack_is_required_and_invalid_out_of_order_lifecycle_does_not_write(self):
        self.assert_unchanged_failure(acknowledgment())
        self.journal.record(reservation())
        for candidate in (
            fill(),
            event("cancel-1", "cancel_requested"),
            event("canceled-1", "canceled", evidence_id="sim-cancel"),
        ):
            self.assert_unchanged_failure(candidate)
        self.journal.record(acknowledgment())
        self.assert_unchanged_failure(event("uncertain-1", "submission_uncertain"))
        self.assert_unchanged_failure(event("canceled-1", "canceled", evidence_id="sim-cancel"))

    def test_limit_price_and_remaining_quantity_are_enforced_for_buys_and_sells(self):
        self.accepted()
        self.assert_unchanged_failure(fill(price="70001"))
        self.assert_unchanged_failure(fill(quantity=6))
        self.journal.record(fill(quantity=4))
        self.assert_unchanged_failure(fill("fill-2", execution_id="exec-2", quantity=2))
        self.cancel()
        self.journal.record(reservation("reserve-sell", "sell-1", side="sell"))
        self.journal.record(acknowledgment("ack-sell", "sell-1", broker_order_id="2"))
        self.assert_unchanged_failure(
            fill("sell-fill", "sell-1", execution_id="exec-sell", price="69999")
        )
        sold = self.journal.record(
            fill(
                "sell-fill",
                "sell-1",
                execution_id="exec-sell",
                quantity=1,
                price="70100",
                fee_krw="10",
                tax_krw="140",
            )
        )
        self.assertEqual(sold["orders"][1]["cash_delta_krw"], "69950")
        self.assertEqual(sold["cash_delta_krw"], "-209663")
        self.assertEqual(sold["position_deltas"], {"005930": 3})

    def test_exact_large_integer_costs_ignore_decimal_context(self):
        self.accepted(
            quantity=9_999_999_999,
            limit_price="9999999999999999999",
            max_notional="99999999989999999990000000001",
        )
        with localcontext() as context:
            context.prec = 2
            context.Emax = 2
            result = self.journal.record(
                fill(
                    quantity=9_999_999_999,
                    price="9999999999999999999",
                    fee_krw="99999999999999999999999999999",
                    tax_krw="1",
                )
            )
        order = result["orders"][0]
        self.assertEqual(order["gross_krw"], "99999999989999999990000000001")
        self.assertEqual(order["cash_delta_krw"], "-199999999989999999990000000001")
        self.assertEqual(order["status"], "filled")

    def test_broker_identity_is_immutable_and_unique_per_date_and_organization(self):
        self.accepted()
        self.assert_unchanged_failure(acknowledgment("changed", broker_order_id="2"))
        self.cancel()
        self.journal.record(reservation("reserve-2", "buy-2"))
        self.assert_unchanged_failure(acknowledgment("ack-2", "buy-2"))
        self.assert_unchanged_failure(
            acknowledgment("ack-unpadded", "buy-2", broker_order_id="1", broker_org_no="6010")
        )
        self.journal.record(acknowledgment("ack-2", "buy-2", broker_org_no="06011"))
        self.journal.record(event("reject-2", "rejected", "buy-2", evidence_id="sim-reject"))
        self.journal.record(reservation("reserve-3", "buy-3", order_date="2026-11-22"))
        result = self.journal.record(acknowledgment("ack-3", "buy-3"))
        self.assertEqual(result["orders"][2]["broker_order_id"], "0000000001")

    def test_net_flat_position_deltas_are_omitted_and_sell_improvement_can_exceed_planned_cap(self):
        self.accepted()
        self.journal.record(fill(quantity=5))
        self.journal.record(reservation("reserve-sell", "sell-1", side="sell"))
        self.journal.record(acknowledgment("ack-sell", "sell-1", broker_order_id="2"))
        result = self.journal.record(
            fill(
                "sell-fill",
                "sell-1",
                execution_id="exec-sell",
                quantity=5,
                price="70100",
                fee_krw="10",
                tax_krw="140",
            )
        )
        self.assertEqual(result["orders"][1]["gross_krw"], "350500")
        self.assertEqual(result["orders"][1]["max_notional"], "350000")
        self.assertEqual(result["position_deltas"], {})
        self.assertEqual(result["cash_delta_krw"], "837")

    def test_execution_identity_cannot_move_orders_same_day_but_can_recur_next_day(self):
        self.accepted()
        self.journal.record(fill(quantity=5))
        self.journal.record(reservation("reserve-2", "buy-2"))
        self.journal.record(acknowledgment("ack-2", "buy-2", broker_order_id="2"))
        self.assert_unchanged_failure(fill("fill-2", "buy-2", quantity=5))
        self.journal.record(event("reject-2", "rejected", "buy-2", evidence_id="sim-reject"))
        self.journal.record(reservation("reserve-3", "buy-3", order_date="2026-11-22"))
        self.journal.record(acknowledgment("ack-3", "buy-3"))
        result = self.journal.record(fill("fill-3", "buy-3", quantity=5))
        self.assertEqual(result["execution_count"], 2)
        self.assertEqual(result["position_deltas"], {"005930": 10})

    def test_input_and_returned_snapshot_mutations_do_not_change_persisted_history(self):
        candidate = reservation()
        original = deepcopy(candidate)
        result = self.journal.record(candidate)
        candidate["data"]["quantity"] = 999
        result["orders"][0]["quantity"] = 999
        result["pending_client_order_ids"].clear()
        fresh = self.journal.record(original)
        self.assertEqual(fresh["orders"][0]["quantity"], 5)
        self.assertEqual(fresh["pending_client_order_ids"], ["buy-1"])

    def test_event_shapes_and_correlation_labels_are_strict(self):
        for value in (
            None,
            [],
            "secret",
            {},
            {**reservation(), "secret": "private"},
            {key: value for key, value in reservation().items() if key != "data"},
            {**reservation(), "data": []},
            {**reservation(), "kind": []},
            {**reservation(), "kind": "RESERVE"},
        ):
            self.assert_unchanged_failure(value)
        for field in ("event_id", "client_order_id"):
            for value in (None, True, 7, "", "-first", "A" * 65, "한글", "１", "id\n", "id/id"):
                self.assert_unchanged_failure({**reservation(), field: value})
        for kind in ("submission_uncertain", "cancel_requested"):
            self.assert_unchanged_failure(event("bad", kind, secret="private"))

    def test_reservation_reuses_canonical_krx_krw_plan_validation(self):
        cases = {
            "order_date": (
                None,
                "20261121",
                "2026-2-01",
                "2026-02-30",
                "２０２６-11-21",
                "2026-11-21\n",
            ),
            "symbol": (True, "5930", "００５９３０", "005930\n"),
            "side": (None, "BUY", "short"),
            "quantity": (True, 0, -1, 5.0, Decimal("5"), "5", 10_000_000_000),
            "limit_price": (True, 70000, "070000", "0", "1.0", "1e3", "１", "9" * 20),
            "max_notional": (None, "0349999", "349999", "9" * 30),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    self.assert_unchanged_failure(reservation(**{field: value}))
        self.assert_unchanged_failure(reservation(currency="USD"))

    def test_fill_numeric_fields_are_bounded_canonical_integers(self):
        self.accepted()
        cases = {
            "quantity": (True, 0, -1, "2", 2.0, Decimal("2"), 10_000_000_000),
            "price": (True, 1, "", "0", "01", "+1", "1.0", "1e2", "１", "1\n", "9" * 20),
            "fee_krw": (False, 0, Decimal("0"), "", "00", "-1", "+1", "0.0", "٠", "9" * 30),
            "tax_krw": (None, 0.0, "00", "-1", "NaN", "Infinity", "9" * 100000),
            "execution_id": (None, "", "-bad", "exec/id", "é", "E" * 65),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    self.assert_unchanged_failure(fill(**{field: value}))
        self.assert_unchanged_failure(fill(secret="private"))

    def test_acknowledgment_and_terminal_evidence_labels_are_bounded(self):
        self.journal.record(reservation())
        for field, values in {
            "broker_order_id": (None, True, 1, "0", "0000", "9" * 11, "１", "1\n"),
            "broker_org_no": (None, 1, "", "9" * 6, "１", "1\n"),
        }.items():
            for value in values:
                self.assert_unchanged_failure(acknowledgment(**{field: value}))
        for value in (None, True, "", "-bad", "label/id", "E" * 65):
            self.assert_unchanged_failure(event("reject", "rejected", evidence_id=value))
        result = self.journal.record(acknowledgment(broker_order_id="1", broker_org_no="0"))
        self.assertEqual(result["orders"][0]["broker_org_no"], "0")

    def test_create_is_exclusive_mode_0600_and_never_replaces_paths(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.Journal.create(self.path, account_ref="paper-desk")
        self.assertEqual(before, self.path.read_bytes())
        target = Path(self.temp.name) / "untouched"
        target.write_text("keep", encoding="utf-8")
        link = Path(self.temp.name) / "linked.sqlite"
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.Journal.create(link, account_ref="paper-desk")
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        missing_parent = Path(self.temp.name) / "missing/stock.sqlite"
        with self.assertRaises(FileNotFoundError):
            self.Journal.create(missing_parent, account_ref="paper-desk")
        self.assertFalse(missing_parent.parent.exists())

    def test_create_refuses_existing_sqlite_sidecars_without_changing_evidence(self):
        for suffix in ("-wal", "-shm", "-journal"):
            for entry_kind in ("regular", "live-symlink", "dangling-symlink", "directory"):
                with self.subTest(suffix=suffix, entry_kind=entry_kind):
                    path = Path(self.temp.name) / (entry_kind + suffix + ".sqlite")
                    sibling = Path(str(path) + suffix)
                    target = path.with_name(path.name + ".evidence")
                    if entry_kind == "regular":
                        sibling.write_bytes(b"prior-evidence\n")
                    elif entry_kind == "directory":
                        sibling.mkdir()
                        (sibling / "evidence").write_bytes(b"prior-evidence\n")
                    else:
                        if entry_kind == "live-symlink":
                            target.write_bytes(b"prior-evidence\n")
                        sibling.symlink_to(target)
                    original = sibling.lstat()
                    with self.assertRaises(FileExistsError):
                        self.Journal.create(path, account_ref="paper-desk")
                    self.assertFalse(path.exists())
                    preserved = sibling.lstat()
                    self.assertEqual(
                        (preserved.st_dev, preserved.st_ino, preserved.st_mode),
                        (original.st_dev, original.st_ino, original.st_mode),
                    )
                    if entry_kind == "regular":
                        self.assertEqual(sibling.read_bytes(), b"prior-evidence\n")
                    elif entry_kind == "directory":
                        self.assertEqual((sibling / "evidence").read_bytes(), b"prior-evidence\n")
                    else:
                        self.assertEqual(sibling.readlink(), target)
                        if entry_kind == "live-symlink":
                            self.assertEqual(target.read_bytes(), b"prior-evidence\n")
                        else:
                            self.assertFalse(target.exists())

    def test_create_rechecks_sidecars_after_reserving_main_without_deleting_new_evidence(self):
        path = Path(self.temp.name) / "appearing-sidecar.sqlite"
        sibling = Path(str(path) + "-journal")
        real_fchmod = self.module.os.fchmod

        def sidecar_appears(descriptor, mode):
            real_fchmod(descriptor, mode)
            sibling.write_bytes(b"concurrent-evidence\n")

        with patch.object(self.module.os, "fchmod", side_effect=sidecar_appears):
            with self.assertRaises(FileExistsError):
                self.Journal.create(path, account_ref="paper-desk")
        self.assertEqual(sibling.read_bytes(), b"concurrent-evidence\n")
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_open_missing_wrong_alias_invalid_alias_and_symlink_never_write(self):
        missing = Path(self.temp.name) / "missing.sqlite"
        for read_only in (False, True):
            with self.assertRaises(FileNotFoundError):
                self.Journal.open_existing(missing, account_ref="paper-desk", read_only=read_only)
        self.assertFalse(missing.exists())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.Journal.open_existing(self.path, account_ref="other-desk")
        for alias in (None, 1, "", "1desk", "한글", "desk/id", "a" * 65):
            with self.assertRaises(ValueError):
                self.Journal.create(missing, account_ref=alias)
            self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            self.Journal.open_existing(self.path, account_ref="paper-desk", read_only="yes")
        link = Path(self.temp.name) / "link.sqlite"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            self.Journal.open_existing(link, account_ref="paper-desk")
        self.assertEqual(before, self.path.read_bytes())

    def test_read_only_record_fails_before_opening_any_connection(self):
        view = self.Journal.open_existing(self.path, account_ref="paper-desk", read_only=True)
        before = self.path.read_bytes()
        with patch.object(
            view, "_connect", side_effect=AssertionError("A write connection was opened")
        ):
            with self.assertRaises(PermissionError):
                view.record(reservation())
        self.assertEqual(before, self.path.read_bytes())

    def test_reads_do_not_repair_a_changed_journal_mode(self):
        self.accepted()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            db.execute("PRAGMA journal_mode=DELETE")
        before = self.path.read_bytes()
        for action in (self.journal.snapshot, lambda: self.journal.record(fill())):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(before, self.path.read_bytes())

    def test_concurrent_writers_cannot_reserve_two_active_orders(self):
        barrier = Barrier(2)

        def write(client):
            other = self.Journal.open_existing(self.path, account_ref="paper-desk")
            barrier.wait(timeout=5)
            try:
                return other.record(reservation("reserve-" + client, client))
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ("order-a", "order-b")))
        self.assertEqual(sum(result is not None for result in results), 1)
        result = self.journal.snapshot()
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(len(result["pending_client_order_ids"]), 1)

    def test_concurrent_duplicate_fills_do_not_double_count(self):
        self.accepted()
        barrier = Barrier(2)

        def write(identity):
            other = self.Journal.open_existing(self.path, account_ref="paper-desk")
            barrier.wait(timeout=5)
            return other.record(fill(identity))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write, ("fill-a", "fill-b")))
        result = self.journal.snapshot()
        self.assertEqual(result["event_count"], 4)
        self.assertEqual(result["execution_count"], 1)
        self.assertEqual(result["cash_delta_krw"], "-139813")

    def test_reader_replay_is_one_consistent_snapshot_during_a_concurrent_append(self):
        self.accepted()
        view = self.Journal.open_existing(self.path, account_ref="paper-desk", read_only=True)
        reading_events, written = Event(), Event()
        connect = view._connect

        def reader_connect(**kwargs):
            db = connect(**kwargs)

            def trace(sql):
                if "FROM events" in sql:
                    reading_events.set()
                    if not written.wait(timeout=5):
                        raise AssertionError("Writer did not finish")

            db.set_trace_callback(trace)
            return db

        with (
            patch.object(view, "_connect", side_effect=reader_connect),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(view.snapshot)
            self.assertTrue(reading_events.wait(timeout=5))
            try:
                self.journal.record(fill())
            finally:
                written.set()
            result = future.result(timeout=5)
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(result["execution_count"], 0)
        self.assertEqual(result["orders"][0]["status"], "accepted")
        self.assertEqual(self.journal.snapshot()["event_count"], 3)

    def test_exception_after_tail_update_rolls_back_event_and_tail_atomically(self):
        self.accepted()
        before = self.journal.snapshot()
        real_connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                result = super().execute(sql, parameters)
                if sql.startswith("UPDATE journal_tail"):
                    raise sqlite3.OperationalError("injected failure before commit")
                return result

        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=FailedCommit)

        with patch.object(self.module.sqlite3, "connect", side_effect=connect):
            with self.assertRaises(ValueError):
                self.journal.record(fill())
        self.assertEqual(
            self.Journal.open_existing(self.path, account_ref="paper-desk").snapshot(), before
        )
        self.assertEqual(self.journal.record(fill())["execution_count"], 1)

    def test_child_process_exit_before_commit_recovers_without_partial_fill(self):
        self.accepted()
        before = self.journal.snapshot()
        script = """
import json, os, sqlite3, sys
sys.path.insert(0, sys.argv[1])
from stock_order_journal import StockOrderJournal
real_connect = sqlite3.connect
class CrashBeforeCommit(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if sql.startswith('UPDATE journal_tail'):
            os._exit(73)
        return result
def connect(*args, **kwargs):
    return real_connect(*args, **kwargs, factory=CrashBeforeCommit)
sqlite3.connect = connect
journal = StockOrderJournal.open_existing(sys.argv[2], account_ref='paper-desk')
journal.record(json.loads(sys.argv[3]))
os._exit(74)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                script,
                str(MODULE_DIR),
                str(self.path),
                json.dumps(fill()),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        self.journal = self.Journal.open_existing(self.path, account_ref="paper-desk")
        self.assertEqual(self.journal.snapshot(), before)
        self.assertEqual(self.journal.record(fill())["execution_count"], 1)

    def test_deletion_changed_hashes_and_committed_tail_corruption_are_detected(self):
        self.accepted()
        self.journal.record(fill())
        statements = (
            "DELETE FROM events WHERE sequence=3",
            "DELETE FROM events WHERE sequence=2",
            "DELETE FROM events",
            "UPDATE events SET event_hash='" + "0" * 64 + "' WHERE sequence=3",
            "UPDATE events SET prev_hash='" + "0" * 64 + "' WHERE sequence=2",
            "UPDATE journal_tail SET sequence=2",
            "UPDATE journal_tail SET event_hash='" + "0" * 64 + "'",
            "DELETE FROM journal_tail",
        )
        for index, statement in enumerate(statements):
            with self.subTest(statement=statement):
                corrupt = Path(self.temp.name) / ("corrupt-" + str(index) + ".sqlite")
                with (
                    closing(sqlite3.connect(self.path)) as source,
                    closing(sqlite3.connect(corrupt)) as target,
                ):
                    source.backup(target)
                    target.execute(statement)
                    target.commit()
                with self.assertRaises(ValueError):
                    self.Journal.open_existing(corrupt, account_ref="paper-desk")

    def test_configuration_or_schema_edits_are_refused_without_repair(self):
        self.accepted()
        statements = (
            "UPDATE journal_config SET config_json='{}'",
            "DELETE FROM journal_config",
            "CREATE TABLE unexpected (value TEXT)",
            "ALTER TABLE events ADD COLUMN hidden TEXT",
            "CREATE TRIGGER hidden AFTER INSERT ON events BEGIN DELETE FROM events; END",
        )
        for index, statement in enumerate(statements):
            with self.subTest(statement=statement):
                corrupt = Path(self.temp.name) / ("schema-" + str(index) + ".sqlite")
                with (
                    closing(sqlite3.connect(self.path)) as source,
                    closing(sqlite3.connect(corrupt)) as target,
                ):
                    source.backup(target)
                    target.execute(statement)
                    target.commit()
                with self.assertRaises(ValueError):
                    self.Journal.open_existing(corrupt, account_ref="paper-desk")

    def test_every_read_and_write_reaudits_changed_history(self):
        self.accepted()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE events SET event_json='{}' WHERE sequence=1")
        for action in (
            self.journal.snapshot,
            lambda: self.journal.record(fill()),
            lambda: self.journal.record(reservation()),
        ):
            with self.assertRaises(ValueError):
                action()

    def test_replay_refuses_invalid_lifecycle_even_if_hash_chain_is_recomputed(self):
        self.accepted()
        self.journal.record(fill())
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT sequence, event_json, prev_hash FROM events ORDER BY sequence"
            ).fetchall()
            previous = rows[0][2]
            for sequence, body, _ in rows:
                if sequence == 2:
                    changed = json.loads(body)
                    changed.update(kind="submission_uncertain", data={})
                    body = json.dumps(changed, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(
                    (str(sequence) + "\n" + previous + "\n" + body).encode()
                ).hexdigest()
                db.execute(
                    "UPDATE events SET event_json=?, prev_hash=?, event_hash=? WHERE sequence=?",
                    (body, previous, digest, sequence),
                )
                previous = digest
            db.execute("UPDATE journal_tail SET event_hash=?", (previous,))
        with self.assertRaises(ValueError):
            self.journal.snapshot()

    def test_direct_module_import_under_no_site_packages_and_no_network_is_supported(self):
        script = """
import json, socket, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError('Network access forbidden')
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
from stock_order_journal import StockOrderJournal
journal = StockOrderJournal.open_existing(sys.argv[2], account_ref='paper-desk', read_only=True)
print(json.dumps(journal.snapshot()))
"""
        result = subprocess.run(
            [sys.executable, "-S", "-c", script, str(MODULE_DIR), str(self.path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.journal.snapshot())


if __name__ == "__main__":
    unittest.main()
