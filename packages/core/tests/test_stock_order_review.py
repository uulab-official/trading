"""Offline comparison is evidence for review, never permission to submit."""

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = ROOT / "packages/core/trading_core"
sys.path.insert(0, str(ROOT / "packages/core"))
NOW = datetime(2026, 11, 21, 6, tzinfo=timezone.utc)
DATE = "2026-11-21"


def observed_row(**changes):
    return {
        "ord_dt": "20261121", "ord_gno_brno": "06010", "odno": "0000000001",
        "orgn_odno": "0000000000", "pdno": "005930", "sll_buy_dvsn_cd": "02",
        "ord_dvsn_cd": "00", "ord_qty": "5", "tot_ccld_qty": "5",
        "rmn_qty": "0", "cncl_cfrm_qty": "0", "rjct_qty": "0",
        "ord_unpr": "70000", "tot_ccld_amt": "350000", "avg_prvs": "70000",
        "cncl_yn": "N", "ord_tmd": "150000", "excg_id_dvsn_cd": "KRX", **changes,
    }


def observations(rows=None, **changes):
    rows = [observed_row()] if rows is None else rows
    return {
        "schema_version": 1, "mode": "paper", "live_orders_enabled": False,
        "provider": "kis_vts_readonly_order_snapshot", "market": "KRX", "currency": "KRW",
        "account_ref": "paper-desk", "account_binding_verified": False,
        "inquiry_date": DATE, "observed_at_utc": NOW.isoformat(),
        "pagination_complete": True, "pages_fetched": 1, "row_count": len(rows),
        "atomic_snapshot_verified": False, "economic_reconciliation_verified": False,
        "rows": rows, **changes,
    }


class StockOrderReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("trading_core.stock_order_review")
        except ModuleNotFoundError as error:
            if error.name != "trading_core.stock_order_review":
                raise
            self.fail("The audited read-only stock-order review is not implemented")
        journal_module = importlib.import_module("trading_core.stock_order_journal")
        self.Journal = journal_module.StockOrderJournal
        self.review = self.module.review_orders
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "stock.sqlite"
        self.journal = self.Journal.create(self.path, account_ref="paper-desk")

    def add_order(self, client="buy-1", *, order_date=DATE, broker_id="0000000001",
                  branch="06010", state="filled", filled_quantity=None, side="buy",
                  quantity=5, limit_price="70000", fill_price="70000"):
        def record(kind, **data):
            return self.journal.record({
                "event_id": client + "-" + kind, "kind": kind,
                "client_order_id": client, "data": data,
            })
        record("reserve", order_date=order_date, symbol="005930", side=side,
               quantity=quantity, limit_price=limit_price,
               max_notional=str(quantity * int(limit_price)))
        if state == "reserved":
            return
        if state == "uncertain":
            record("submission_uncertain")
            return
        if broker_id is not None:
            record("acknowledge", broker_order_id=broker_id, broker_org_no=branch)
        if filled_quantity is None:
            filled_quantity = quantity if state == "filled" else 0
        if filled_quantity:
            record("fill", execution_id=client + "-execution", quantity=filled_quantity,
                   price=fill_price, fee_krw="13", tax_krw="0")
        if state in ("cancel_pending", "canceled"):
            record("cancel_requested")
        if state in ("canceled", "rejected"):
            record(state, evidence_id=client + "-synthetic-evidence")

    def report(self, document=None, **options):
        return self.review(self.journal, document, now=NOW, **options)

    def assert_unverified(self, report):
        self.assertEqual(report["mode"], "paper")
        for name in ("live_orders_enabled", "account_binding_verified",
                     "economic_reconciliation_verified", "lifecycle_confirmation_verified",
                     "safe_to_submit"):
            self.assertIs(report[name], False)

    def assert_refused(self, document, **options):
        with self.assertRaises(ValueError) as caught:
            self.review(self.journal, document, now=NOW, **options)
        self.assertEqual(str(caught.exception), "Stock-order review refused")
        self.assertTrue(caught.exception.__suppress_context__)

    def test_fresh_equal_closed_order_exposes_bounded_comparison_without_verification(self):
        self.add_order()
        report = self.report(observations())
        self.assertEqual(set(report), {
            "schema_version", "kind", "mode", "live_orders_enabled", "account_ref",
            "generated_at_utc", "account_binding_verified", "economic_reconciliation_verified",
            "lifecycle_confirmation_verified", "safe_to_submit", "comparison_status",
            "needs_attention", "journal_blocked", "journal", "observation_summary", "orders",
            "unmatched_observations", "findings", "summary",
        })
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["kind"], "stock_order_review")
        self.assertEqual(report["account_ref"], "paper-desk")
        self.assertEqual(report["generated_at_utc"], "2026-11-21T06:00:00+00:00")
        self.assertEqual(report["comparison_status"], "observations_equal")
        self.assertIs(report["needs_attention"], False)
        self.assertIs(report["journal_blocked"], False)
        self.assertEqual(report["journal"], self.journal.snapshot())
        self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
        self.assertEqual(report["orders"][0]["observed"], observed_row())
        self.assertEqual(report["orders"][0]["differences"], [])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["unmatched_observations"], [])
        self.assertEqual(report["observation_summary"], {
            "inquiry_date": DATE, "observed_at_utc": NOW.isoformat(), "pages_fetched": 1,
            "row_count": 1, "age_seconds": 0, "freshness": "fresh",
        })
        self.assertEqual(report["summary"], {
            "order_count": 1, "scoped_order_count": 1, "matched_order_count": 1,
            "different_order_count": 0, "unmatched_observation_count": 0,
            "unresolved_order_count": 0, "findings_count": 0,
        })
        self.assert_unverified(report)

    def test_numeric_padding_matches_but_is_preserved_in_the_report(self):
        self.add_order()
        row = observed_row(ord_gno_brno="6010", odno="1", orgn_odno="")
        report = self.report(observations([row]))
        order = report["orders"][0]
        self.assertEqual(order["comparison"], "observations_equal")
        self.assertEqual(order["observed"]["odno"], "1")
        self.assertEqual(order["observed"]["orgn_odno"], "")
        self.assertEqual(order["local"]["broker_order_id"], "0000000001")
        self.assert_unverified(report)

    def test_same_broker_number_on_other_dates_or_branches_is_not_a_match(self):
        self.add_order("old", order_date="2026-11-20")
        self.add_order("other-branch", branch="06011")
        self.add_order("today")
        report = self.report(observations())
        self.assertEqual([entry["comparison"] for entry in report["orders"]],
                         ["out_of_scope", "not_observed", "observations_equal"])
        self.assertIsNone(report["orders"][0]["observed"])
        self.assertIsNone(report["orders"][1]["observed"])
        self.assertEqual(report["summary"]["scoped_order_count"], 2)
        self.assertEqual(report["summary"]["matched_order_count"], 1)
        self.assertEqual(report["findings"], [{
            "code": "local_order_not_observed", "client_order_id": "other-branch",
            "broker_order_id": "0000000001",
        }])

    def test_missing_observed_order_and_unknown_observation_are_both_findings(self):
        self.add_order()
        unknown = observed_row(odno="2")
        report = self.report(observations([unknown]))
        self.assertEqual(report["comparison_status"], "needs_review")
        self.assertIs(report["needs_attention"], True)
        self.assertEqual(report["orders"][0]["comparison"], "not_observed")
        self.assertEqual(report["unmatched_observations"], [unknown])
        self.assertEqual(report["findings"], [
            {"code": "local_order_not_observed", "client_order_id": "buy-1",
             "broker_order_id": "0000000001"},
            {"code": "unmatched_observation", "client_order_id": None, "broker_order_id": "2"},
        ])
        self.assert_unverified(report)

    def test_unacknowledged_order_is_never_guessed_from_identical_terms(self):
        self.add_order(state="uncertain")
        report = self.report(observations())
        self.assertEqual(report["orders"][0]["comparison"], "not_acknowledged")
        self.assertIsNone(report["orders"][0]["observed"])
        self.assertEqual(report["unmatched_observations"], [observed_row()])
        self.assertTrue(report["journal_blocked"])
        self.assertEqual(report["summary"]["matched_order_count"], 0)
        self.assertEqual([finding["code"] for finding in report["findings"]], [
            "unresolved_local_order", "local_order_not_acknowledged", "unmatched_observation",
        ])
        self.assert_unverified(report)

    def test_differences_expose_only_compared_terms_with_exact_money_and_stable_field_order(self):
        self.add_order()
        changed = observed_row(pdno="000660", sll_buy_dvsn_cd="01", ord_dvsn_cd="01",
                               ord_qty="6", ord_unpr="70001", tot_ccld_qty="4",
                               tot_ccld_amt="280000", rmn_qty="2")
        report = self.report(observations([changed]))
        self.assertEqual(report["orders"][0]["differences"], [
            {"field": "symbol", "local": "005930", "observed": "000660"},
            {"field": "side", "local": "buy", "observed": "sell"},
            {"field": "order_division", "local": "00", "observed": "01"},
            {"field": "quantity", "local": 5, "observed": 6},
            {"field": "limit_price", "local": "70000", "observed": "70001"},
            {"field": "filled_quantity", "local": 5, "observed": 4},
            {"field": "gross_krw", "local": "350000", "observed": "280000"},
            {"field": "working_quantity", "local": 0, "observed": 2},
        ])
        self.assertEqual(report["orders"][0]["comparison"], "different")
        self.assertEqual(report["summary"]["different_order_count"], 1)
        self.assertEqual(report["findings"][0]["code"], "order_fields_differ")
        self.assert_unverified(report)

    def test_equal_cumulative_observations_cannot_clear_any_unresolved_local_state(self):
        for state, filled in (("accepted", 0), ("partially_filled", 2), ("cancel_pending", 2)):
            with self.subTest(state=state):
                path = Path(self.temp.name) / (state + ".sqlite")
                self.journal = self.Journal.create(path, account_ref="paper-desk")
                self.add_order(state=state, filled_quantity=filled)
                row = observed_row(tot_ccld_qty=str(filled), tot_ccld_amt=str(filled * 70000),
                                   rmn_qty=str(5 - filled), cncl_yn="Y", cncl_cfrm_qty="3",
                                   rjct_qty="5")
                report = self.report(observations([row]))
                self.assertEqual(report["comparison_status"], "observations_equal")
                self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
                self.assertEqual(report["orders"][0]["local"]["status"], state)
                self.assertTrue(report["journal_blocked"])
                self.assertTrue(report["needs_attention"])
                self.assertEqual(report["summary"]["unresolved_order_count"], 1)
                self.assertEqual(report["findings"][0]["code"], "unresolved_local_order")
                self.assert_unverified(report)

    def test_closed_partial_order_compares_working_quantity_not_historical_unfilled_quantity(self):
        self.add_order(state="canceled", filled_quantity=2)
        row = observed_row(tot_ccld_qty="2", tot_ccld_amt="140000", rmn_qty="0",
                           cncl_cfrm_qty="3", cncl_yn="Y")
        report = self.report(observations([row]))
        self.assertEqual(report["orders"][0]["local"]["unfilled_quantity"], 3)
        self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
        self.assertFalse(report["needs_attention"])
        self.assert_unverified(report)

    def test_related_child_requires_review_without_summing_its_cumulative_fills(self):
        self.add_order()
        child = observed_row(odno="2", orgn_odno="0000000001", tot_ccld_qty="2",
                             tot_ccld_amt="140000")
        report = self.report(observations([child, observed_row()]))
        self.assertEqual(report["orders"][0]["comparison"], "related_history")
        self.assertEqual(report["orders"][0]["local"]["filled_quantity"], 5)
        self.assertEqual(report["orders"][0]["observed"]["tot_ccld_qty"], "5")
        self.assertEqual(report["orders"][0]["differences"], [])
        self.assertEqual(report["unmatched_observations"], [child])
        self.assertEqual(report["comparison_status"], "needs_review")
        self.assertTrue(report["needs_attention"])
        self.assert_unverified(report)

    def test_nonzero_original_reference_requires_review_even_when_parent_is_absent(self):
        self.add_order()
        report = self.report(observations([observed_row(orgn_odno="0000000009")]))
        self.assertEqual(report["orders"][0]["comparison"], "related_history")
        self.assertEqual(report["findings"], [{
            "code": "related_order_history", "client_order_id": "buy-1",
            "broker_order_id": "0000000001",
        }])
        self.assert_unverified(report)

    def test_related_history_never_hides_direct_field_differences(self):
        self.add_order()
        report = self.report(observations([
            observed_row(orgn_odno="9", tot_ccld_amt="349999"),
        ]))
        self.assertEqual(report["orders"][0]["comparison"], "different")
        self.assertEqual(report["orders"][0]["differences"], [
            {"field": "gross_krw", "local": "350000", "observed": "349999"},
        ])
        self.assertEqual({finding["code"] for finding in report["findings"]},
                         {"order_fields_differ", "related_order_history"})

    def test_unresolved_order_outside_inquiry_date_stays_visible_and_blocking(self):
        self.add_order(order_date="2026-11-20", state="uncertain")
        report = self.report(observations([]))
        self.assertEqual(report["orders"][0]["comparison"], "out_of_scope")
        self.assertEqual(report["comparison_status"], "observations_equal")
        self.assertEqual(report["summary"]["scoped_order_count"], 0)
        self.assertTrue(report["journal_blocked"])
        self.assertTrue(report["needs_attention"])
        self.assertEqual(report["findings"], [{
            "code": "unresolved_local_order", "client_order_id": "buy-1",
            "broker_order_id": None,
        }])
        self.assert_unverified(report)

    def test_journal_only_monitoring_is_explicit_and_preserves_unresolved_attention(self):
        self.add_order()
        report = self.report()
        self.assertEqual(report["comparison_status"], "not_supplied")
        self.assertEqual(report["orders"][0]["comparison"], "not_supplied")
        self.assertIsNone(report["observation_summary"])
        self.assertIsNone(report["orders"][0]["observed"])
        self.assertEqual(report["summary"]["scoped_order_count"], 0)
        self.assertFalse(report["needs_attention"])
        self.add_order("uncertain", broker_id=None, state="uncertain")
        pending = self.report()
        self.assertEqual(pending["comparison_status"], "not_supplied")
        self.assertTrue(pending["needs_attention"])
        self.assertTrue(pending["journal_blocked"])
        self.assert_unverified(pending)

    def test_stale_and_any_future_timestamp_require_attention_without_altering_comparison(self):
        self.add_order()
        cases = ((timedelta(seconds=300), "fresh", None),
                 (timedelta(seconds=300, microseconds=1), "stale", "observation_stale"),
                 (timedelta(microseconds=-1), "future", "observation_future"))
        for age, freshness, code in cases:
            with self.subTest(freshness=freshness):
                document = observations(observed_at_utc=(NOW - age).isoformat())
                report = self.report(document)
                self.assertEqual(report["observation_summary"]["freshness"], freshness)
                self.assertEqual(report["observation_summary"]["age_seconds"], age.total_seconds())
                self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
                self.assertEqual(report["comparison_status"], "needs_review" if code else "observations_equal")
                self.assertEqual(report["needs_attention"], code is not None)
                self.assertEqual([finding["code"] for finding in report["findings"]],
                                 [code] if code else [])
                self.assert_unverified(report)

    def test_historical_inquiry_is_valid_after_collectors_thirty_day_window(self):
        self.add_order(order_date="2025-01-01")
        document = observations([observed_row(ord_dt="20250101")], inquiry_date="2025-01-01",
                                observed_at_utc="2025-01-01T06:00:00+00:00")
        report = self.report(document)
        self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
        self.assertEqual(report["observation_summary"]["freshness"], "stale")
        self.assertTrue(report["needs_attention"])

    def test_deterministic_ordering_does_not_depend_on_observation_page_order(self):
        self.add_order()
        rows = [observed_row(odno="10"), observed_row(ord_gno_brno="06011", odno="1"),
                observed_row(odno="2"), observed_row()]
        first = self.report(observations(rows))
        second = self.report(observations(list(reversed(rows))))
        self.assertEqual(first, second)
        self.assertEqual([(row["ord_gno_brno"], row["odno"])
                          for row in first["unmatched_observations"]],
                         [("06010", "2"), ("06010", "10"), ("06011", "1")])

    def test_exact_top_level_schema_rejects_missing_and_untrusted_metadata(self):
        for key in observations():
            with self.subTest(missing=key):
                document = observations()
                del document[key]
                self.assert_refused(document)
        for key in ("token", "raw_response", "balance", "safe_to_submit",
                    "lifecycle_confirmation_verified"):
            with self.subTest(extra=key):
                self.assert_refused(observations(**{key: "SENSITIVE-SENTINEL"}))
        for value in ([], (), "SENSITIVE-SENTINEL", 0, True):
            with self.subTest(document_type=type(value).__name__):
                self.assert_refused(value)

    def test_fixed_metadata_and_false_verification_flags_are_type_sensitive(self):
        invalid = {
            "schema_version": (True, 1.0, "1", 2),
            "mode": ("live", None, False),
            "provider": ("kis_vts", "KIS_VTS_READONLY_ORDER_SNAPSHOT", {}),
            "market": ("NXT", "NASDAQ", None),
            "currency": ("USD", "krw", None),
            "account_ref": ("another-account", "<script>", "", "a" * 65, None),
            "pagination_complete": (False, 1, "true", None),
        }
        for key in ("live_orders_enabled", "account_binding_verified",
                    "atomic_snapshot_verified", "economic_reconciliation_verified"):
            invalid[key] = (True, 0, "false", None)
        for key, values in invalid.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    self.assert_refused(observations(**{key: value}))

    def test_page_and_row_counts_are_bounded_consistent_and_never_boolean(self):
        for count in (0, -1, 21, True, 1.0, "1"):
            with self.subTest(pages=count):
                self.assert_refused(observations(pages_fetched=count))
        for count in (0, -1, 301, True, 1.0, "1"):
            with self.subTest(rows=count):
                self.assert_refused(observations(row_count=count))
        for rows in ({}, (), "rows", None):
            with self.subTest(rows_type=type(rows).__name__):
                document = observations()
                document["rows"] = rows
                self.assert_refused(document)
        rows = [observed_row(odno=str(number)) for number in range(1, 302)]
        self.assert_refused(observations(rows[:16]))
        self.assert_refused(observations(rows, pages_fetched=20))
        report = self.report(observations(rows[:300], pages_fetched=20))
        self.assertEqual(report["summary"]["unmatched_observation_count"], 300)
        empty = self.report(observations([]))
        self.assertEqual(empty["comparison_status"], "observations_equal")

    def test_exact_row_schema_allows_only_optional_exchange_and_rejects_raw_fields(self):
        row = observed_row()
        del row["excg_id_dvsn_cd"]
        self.add_order()
        report = self.report(observations([row]))
        self.assertNotIn("excg_id_dvsn_cd", report["orders"][0]["observed"])
        self.assertEqual(report["orders"][0]["comparison"], "observations_equal")
        for key in row:
            with self.subTest(missing=key):
                candidate = dict(row)
                del candidate[key]
                self.assert_refused(observations([candidate]))
        for key in ("cano", "account_number", "raw_response", "ODNO", "verified"):
            with self.subTest(extra=key):
                self.assert_refused(observations([observed_row(**{key: "SENSITIVE-SENTINEL"})]))
        for candidate in (None, [], "row"):
            self.assert_refused(observations([candidate]))
        self.assert_refused(observations([observed_row(excg_id_dvsn_cd="NXT")]))

    def test_saved_numbers_are_canonical_strings_not_silently_normalized(self):
        for key, maximum in (("ord_qty", 10), ("tot_ccld_qty", 10), ("rmn_qty", 10),
                             ("cncl_cfrm_qty", 10), ("rjct_qty", 10),
                             ("ord_unpr", 19), ("tot_ccld_amt", 19)):
            invalid = ("", "00", "01", "-1", "+1", "1.0", "1e2", "1 ", " 1", "١",
                       1, 1.0, True, None, float("nan"), "1" * (maximum + 1))
            for value in invalid:
                with self.subTest(field=key, value=value):
                    self.assert_refused(observations([observed_row(**{key: value})]))
        for value in ("00", "070000", "70000.0", "70000.10", ".5", "1.", "-1.5",
                      "1e2", "١.٥", 1.5, None, "1" * 20):
            with self.subTest(average=value):
                self.assert_refused(observations([observed_row(avg_prvs=value)]))
        for value in ("0", "0.5", "70000.125", "9999999999999999999"):
            with self.subTest(canonical_average=value):
                report = self.report(observations([observed_row(avg_prvs=value)]))
                self.assertEqual(report["unmatched_observations"][0]["avg_prvs"], value)

    def test_row_identity_codes_and_clock_are_strict_ascii_and_bounded(self):
        invalid = {
            "ord_dt": ("2026-11-21", "20261120", "20260230", "２０２６１１２１", None),
            "ord_gno_brno": ("", "060100", "6A10", 6010, None),
            "odno": ("0", "0000000000", "", "1" * 11, "١", 1, None),
            "orgn_odno": (" ", "1" * 11, "-1", "١", 0, None),
            "pdno": ("5930", "0059300", "ABC123", "００５９３０", 5930, None),
            "sll_buy_dvsn_cd": ("00", "1", "buy", 2, None),
            "ord_dvsn_cd": ("0", "000", "AA", 0, None),
            "cncl_yn": ("y", "true", True, None),
            "ord_tmd": ("240000", "236000", "235960", "90000", "09:00:00", None),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    self.assert_refused(observations([observed_row(**{key: value})]))

    def test_duplicate_numeric_broker_identities_reject_padding_aliases(self):
        rows = [observed_row(), observed_row(odno="1", ord_gno_brno="6010")]
        self.assert_refused(observations(rows))
        report = self.report(observations([observed_row(), observed_row(ord_gno_brno="06011")]))
        self.assertEqual(report["summary"]["unmatched_observation_count"], 2)

    def test_saved_dates_and_utc_timestamp_must_be_valid_collector_canonical_values(self):
        for value in ("2026-02-30", "20261121", "2026-1-21", "0000-11-21", None, 20261121):
            with self.subTest(inquiry=value):
                self.assert_refused(observations(inquiry_date=value))
        invalid = ("2026-11-21T06:00:00", "2026-11-21T06:00:00Z",
                   "2026-11-21 06:00:00+00:00", "2026-11-21T15:00:00+09:00",
                   "2026-11-21T06:00:00.0+00:00", "2026-11-21T06:00:00.000000+00:00",
                   "2026-11-21T06:00:00.1+00:00", "2026-11-21T24:00:00+00:00",
                   "2026-11-21T06:00:60+00:00", "2026-02-30T06:00:00+00:00",
                   "2026-11-21T06:00:00+00:00SENSITIVE-SENTINEL", None, 1)
        for value in invalid:
            with self.subTest(observed=value):
                self.assert_refused(observations(observed_at_utc=value))

    def test_inquiry_date_is_bounded_by_observation_kst_date_not_utc_date(self):
        self.assert_refused(observations(observed_at_utc="2026-11-20T14:59:59+00:00"))
        report = self.report(observations(observed_at_utc="2026-11-20T15:00:00+00:00"))
        self.assertEqual(report["observation_summary"]["inquiry_date"], DATE)

    def test_review_options_reject_naive_clocks_and_boolean_or_unbounded_maximum_age(self):
        for value in (0, -1, 86401, True, False, 1.0, "300", None):
            with self.subTest(maximum=value):
                self.assert_refused(observations(), max_age_seconds=value)
        for value in (datetime(2026, 11, 21), "2026-11-21T06:00:00+00:00", 1, lambda: NOW):
            with self.subTest(clock=type(value).__name__):
                with self.assertRaisesRegex(ValueError, "^Stock-order review refused$"):
                    self.review(self.journal, observations(), now=value)
        for value in (1, 86400):
            report = self.report(observations([]), max_age_seconds=value)
            self.assertEqual(report["comparison_status"], "observations_equal")
        other_zone = NOW.astimezone(timezone(timedelta(hours=9)))
        report = self.review(self.journal, observations([]), now=other_zone)
        self.assertEqual(report["generated_at_utc"], NOW.isoformat())

    def test_default_clock_is_aware_current_utc(self):
        before = datetime.now(timezone.utc)
        report = self.review(self.journal)
        after = datetime.now(timezone.utc)
        generated = datetime.fromisoformat(report["generated_at_utc"])
        self.assertEqual(generated.utcoffset(), timedelta(0))
        self.assertLessEqual(before, generated)
        self.assertLessEqual(generated, after)

    def test_fake_journal_cannot_supply_an_unaudited_snapshot(self):
        class FakeJournal:
            def snapshot(inner_self):
                return self.journal.snapshot()
        for candidate in (FakeJournal(), self.journal.snapshot(), self.path, None):
            with self.subTest(journal=type(candidate).__name__):
                with self.assertRaisesRegex(ValueError, "^Stock-order review refused$"):
                    self.review(candidate, now=NOW)

    def test_every_review_audits_the_current_journal_and_refuses_tampered_history(self):
        self.add_order()
        self.report(observations())
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE events SET event_hash=? WHERE sequence=1", ("0" * 64,))
            db.commit()
        self.assert_refused(observations())

    def test_source_events_main_database_permissions_and_observation_document_do_not_change(self):
        self.add_order()
        self.journal = self.Journal.open_existing(self.path, account_ref="paper-desk", read_only=True)
        before = self.journal.snapshot()
        database = self.path.read_bytes()
        permissions = stat.S_IMODE(self.path.stat().st_mode)
        document = observations()
        supplied = deepcopy(document)
        report = self.report(document)
        self.assertEqual(document, supplied)
        self.assertEqual(self.journal.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), database)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), permissions)
        report["orders"][0]["observed"]["tot_ccld_qty"] = "999"
        report["orders"][0]["local"]["executions"][0]["fee_krw"] = "999"
        self.assertEqual(document, supplied)
        self.assertEqual(self.journal.snapshot(), before)
        again = self.report(document)
        self.assertEqual(again["orders"][0]["observed"]["tot_ccld_qty"], "5")
        self.assertEqual(again["orders"][0]["local"]["fee_krw"], "13")

    def test_standalone_no_site_import_and_real_process_review_require_no_network(self):
        self.add_order()
        script = """
import json, socket, sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError('Network access forbidden')
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
from stock_order_journal import StockOrderJournal
from stock_order_review import review_orders
journal = StockOrderJournal.open_existing(sys.argv[2], account_ref='paper-desk', read_only=True)
result = review_orders(journal, json.loads(sys.argv[3]),
                       now=datetime(2026, 11, 21, 6, tzinfo=timezone.utc))
print(json.dumps(result, sort_keys=True, allow_nan=False))
"""
        result = subprocess.run([sys.executable, "-B", "-S", "-c", script, str(MODULE_DIR),
                                 str(self.path), json.dumps(observations())],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.report(observations()))


if __name__ == "__main__":
    unittest.main()
