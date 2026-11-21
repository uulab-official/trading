"""Offline request preparation and conservative, unauthenticated ACK parsing."""

from copy import deepcopy
from decimal import localcontext
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/core"))


class KisVtsOrderPlanTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("trading_core.kis_vts_order")
        except ModuleNotFoundError as error:
            if error.name != "trading_core.kis_vts_order":
                raise
            self.fail("The offline KIS VTS order preparation API is not implemented")
        self.inputs = {
            "client_order_id": "stock-plan-001",
            "symbol": "005930",
            "side": "buy",
            "quantity": 2,
            "limit_price": "70100",
            "max_notional": "140200",
        }

    def build(self, **changes):
        return self.module.build_order_plan(**{**self.inputs, **changes})

    def test_buy_template_uses_only_vts_contract_and_explicit_placeholders(self):
        result = self.build()
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["provider"], "kis_vts")
        self.assertIs(result["submitted"], False)
        self.assertIs(result["live_orders_enabled"], False)
        self.assertEqual(result["order"], {
            "symbol": "005930", "side": "buy", "quantity": 2,
            "limit_price": "70100", "max_notional": "140200", "notional": "140200",
            "market": "KRX", "currency": "KRW",
        })
        self.assertEqual(result["request_template"], {
            "method": "POST",
            "base_url": "https://openapivts.koreainvestment.com:29443",
            "path": "/uapi/domestic-stock/v1/trading/order-cash",
            "headers": {
                "content-type": "application/json; charset=utf-8",
                "authorization": "Bearer ${KIS_VTS_ACCESS_TOKEN}",
                "appkey": "${KIS_VTS_APPKEY}",
                "appsecret": "${KIS_VTS_APPSECRET}",
                "tr_id": "VTTC0012U", "custtype": "P",
            },
            "body": {
                "CANO": "${KIS_VTS_CANO}",
                "ACNT_PRDT_CD": "${KIS_VTS_ACNT_PRDT_CD}",
                "PDNO": "005930", "ORD_DVSN": "00", "ORD_QTY": "2",
                "ORD_UNPR": "70100", "SLL_TYPE": "",
                "EXCG_ID_DVSN_CD": "KRX", "CNDT_PRIC": "",
            },
        })

    def test_sell_uses_virtual_sell_transaction_and_general_sell_type(self):
        result = self.build(side="sell")
        self.assertEqual(result["request_template"]["headers"]["tr_id"], "VTTC0011U")
        self.assertEqual(result["request_template"]["body"]["SLL_TYPE"], "01")
        self.assertEqual(result["order"]["side"], "sell")

    def test_correlation_id_is_local_and_cannot_be_mistaken_for_broker_idempotency(self):
        result = self.build(client_order_id="local.client_42-ABC")
        self.assertEqual(result["client_order_id"], "local.client_42-ABC")
        self.assertEqual(result["client_order_id_scope"], "local_correlation_only")
        request_json = json.dumps(result["request_template"])
        self.assertNotIn("local.client_42-ABC", request_json)
        self.assertNotIn("client_order_id", request_json)
        self.assertNotIn("idempotency", request_json.lower())

    def test_builder_does_not_read_environment_or_use_network(self):
        with patch.dict(os.environ, {"KIS_VTS_ACCESS_TOKEN": "SECRET_TOKEN_VALUE"}), \
                patch.object(os._Environ, "__getitem__", side_effect=AssertionError("No env reads")), \
                patch("socket.socket.connect", side_effect=AssertionError("No socket")), \
                patch("socket.create_connection", side_effect=AssertionError("No connection")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")):
            result = self.build()
        self.assertNotIn("SECRET_TOKEN_VALUE", json.dumps(result))

    def test_quantity_requires_positive_bounded_integer_shares(self):
        for value in (True, False, None, 0, -1, "2", 2.0, 2.5, 10_000_000_000, 10 ** 5000):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                self.build(quantity=value)

    def test_prices_and_budget_are_canonical_positive_krw_integer_strings(self):
        for field in ("limit_price", "max_notional"):
            for value in (None, True, 1, 1.0, "", "0", "-1", "+1", "01", " 1", "1 ",
                          "1\n", "1\r", "1.0", "1e3", "NaN", "Infinity", "１", "١"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build(**{field: value})

    def test_excessive_numeric_text_is_rejected_before_integer_conversion(self):
        for field, value in (("limit_price", "9" * 20), ("max_notional", "9" * 30),
                             ("limit_price", "9" * 100000),
                             ("max_notional", "9" * 100000)):
            with self.subTest(field=field, digits=len(value)), self.assertRaises(ValueError):
                self.build(**{field: value})

    def test_notional_is_exact_even_beyond_float_and_decimal_context_precision(self):
        with localcontext() as context:
            context.prec = 3
            result = self.build(quantity=9_999_999_999, limit_price="9999999999999999999",
                                max_notional="99999999989999999990000000001")
        self.assertEqual(result["order"]["notional"], "99999999989999999990000000001")
        self.assertEqual(result["request_template"]["body"]["ORD_QTY"], "9999999999")

    def test_one_krw_over_budget_is_rejected_without_float_rounding(self):
        with self.assertRaises(ValueError):
            self.build(quantity=9_999_999_999, limit_price="9999999999999999999",
                       max_notional="99999999989999999990000000000")
        with self.assertRaises(ValueError):
            self.build(max_notional="140199")

    def test_minimum_valid_order_and_client_id_bounds(self):
        result = self.build(client_order_id="A" * 64, symbol="000001", quantity=1,
                            limit_price="1", max_notional="1")
        self.assertEqual(result["order"]["notional"], "1")
        self.assertEqual(self.build(client_order_id="A")["client_order_id"], "A")

    def test_client_ids_reject_control_unicode_delimiters_and_excessive_length(self):
        for value in (None, 12, "", "A" * 65, "한글", "\norder", "order\r\n", "order/id",
                      "order id", "${KIS_VTS_ACCESS_TOKEN}", "-first", "id:42"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build(client_order_id=value)

    def test_only_explicit_six_ascii_digit_stocks_and_supported_scope_are_accepted(self):
        examples = {
            "symbol": (None, 5930, "5930", "0059300", "００５９３０", "005930\n", "A05930"),
            "side": (None, "BUY", "Sell", "buy ", "short", "margin"),
            "market": (None, "NXT", "J", "krx", "KRX\n"),
            "currency": (None, "USD", "krw", "KRW\n"),
        }
        for field, values in examples.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build(**{field: value})

    def test_plan_results_do_not_share_mutable_request_state(self):
        first = self.build()
        first["request_template"]["body"]["CANO"] = "changed"
        first["order"]["quantity"] = 999
        next_plan = self.build()
        self.assertEqual(next_plan["request_template"]["body"]["CANO"], "${KIS_VTS_CANO}")
        self.assertEqual(next_plan["order"]["quantity"], 2)


class KisVtsResponseClassificationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("trading_core.kis_vts_order")
        except ModuleNotFoundError as error:
            if error.name != "trading_core.kis_vts_order":
                raise
            self.fail("The offline KIS VTS response classifier is not implemented")
        self.accepted = {
            "rt_cd": "0", "msg_cd": "APBK0013", "msg1": "Caller-supplied message",
            "output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0001569157", "ORD_TMD": "155211"},
        }

    def classify(self, payload, http_status=200):
        result = self.module.classify_order_response(payload, http_status=http_status)
        self.assertIs(result["verified_fill"], False)
        self.assertIs(result["safe_to_retry"], False)
        self.assertEqual(result["fill_state"], "unknown")
        self.assertEqual(result["response_source"], "caller_supplied_unverified_response")
        return result

    def test_accepted_acknowledgment_preserves_ids_without_claiming_a_fill(self):
        result = self.classify(self.accepted)
        self.assertEqual(result["status"], "accepted_acknowledgment")
        self.assertEqual(result["acknowledgment"], {
            "order_id": "0001569157", "forwarding_org_no": "06010", "order_time": "155211",
        })
        self.assertNotIn("filled_quantity", result)
        self.assertNotIn("msg1", result)

    def test_identifier_capacities_do_not_require_assumed_zero_padding(self):
        value = deepcopy(self.accepted)
        value["output"] = {"KRX_FWDG_ORD_ORGNO": "0", "ODNO": "1", "ORD_TMD": "000000"}
        result = self.classify(value)
        self.assertEqual(result["status"], "accepted_acknowledgment")
        self.assertEqual(result["acknowledgment"]["order_id"], "1")

    def test_non200_and_noninteger_http_status_always_mean_unknown(self):
        for status in (None, True, False, "200", 200.0, 201, 202, 301, 400, 401, 429, 500, 599):
            for payload in (self.accepted, {"rt_cd": "1", "msg_cd": "APBK0001"}):
                with self.subTest(status=status, rt_cd=payload["rt_cd"]):
                    self.assertEqual(self.classify(payload, status)["status"], "unknown")

    def test_rt_cd_requires_documented_exact_string_instead_of_coercion(self):
        for value in (None, True, False, 0, 1, "", "00", " 0", "0\n", "2", "-1", {}, []):
            payload = {**self.accepted, "rt_cd": value}
            with self.subTest(value=value):
                self.assertEqual(self.classify(payload)["status"], "unknown")

    def test_nonobject_payloads_and_malformed_output_are_unknown(self):
        for payload in (None, True, 0, "secret body", [], [self.accepted], {}):
            with self.subTest(payload_type=type(payload).__name__):
                self.assertEqual(self.classify(payload)["status"], "unknown")
        for output in (None, [], [self.accepted["output"]], "secret body", {}, {"ODNO": "123"}):
            with self.subTest(output_type=type(output).__name__):
                self.assertEqual(self.classify({"rt_cd": "0", "output": output})["status"], "unknown")

    def test_acknowledgment_requires_canonical_uppercase_keys_without_alias_conflicts(self):
        lower = {key.lower(): value for key, value in self.accepted["output"].items()}
        for output in (lower, {**self.accepted["output"], "odno": "999"},
                       {**self.accepted["output"], "ord_tmd": "000000"},
                       {**self.accepted["output"], "krx_fwdg_ord_orgno": "00001"}):
            self.assertEqual(self.classify({"rt_cd": "0", "output": output})["status"], "unknown")

    def test_invalid_acknowledgment_identifiers_are_not_accepted(self):
        cases = {
            "ODNO": (None, 1569157, True, "", "0", "0000000000", "12345678901", "abc123", "１２", "1\n"),
            "KRX_FWDG_ORD_ORGNO": (None, 6010, True, "", "123456", "abc", "１２", "1\n"),
        }
        for field, values in cases.items():
            for value in values:
                payload = deepcopy(self.accepted)
                payload["output"][field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.classify(payload)["status"], "unknown")

    def test_order_time_has_six_ascii_digits_and_valid_clock_ranges(self):
        for value in (None, 155211, True, "", "1", "15521", "1552110", "240000", "236000",
                      "235960", "999999", "１２３４５６", "12:34:56", "15521\n"):
            payload = deepcopy(self.accepted)
            payload["output"]["ORD_TMD"] = value
            with self.subTest(value=value):
                self.assertEqual(self.classify(payload)["status"], "unknown")
        payload = deepcopy(self.accepted)
        payload["output"]["ORD_TMD"] = "235959"
        self.assertEqual(self.classify(payload)["status"], "accepted_acknowledgment")

    def test_reported_business_rejection_still_has_unknown_fill_and_no_retry(self):
        result = self.classify({"rt_cd": "1", "msg_cd": "APBK0001", "msg1": "secret explanation"})
        self.assertEqual(result["status"], "business_rejected")
        self.assertEqual(result["msg_cd"], "APBK0001")
        self.assertNotIn("msg1", result)
        self.assertEqual(self.classify({"rt_cd": "1"})["status"], "business_rejected")

    def test_unsafe_message_codes_and_raw_fields_never_echo_in_classification(self):
        for code in (None, 123, True, "SECRET_TOKEN_VALUE", "code\r\ninjection", "한글", "a/b", "a" * 100000):
            result = self.classify({"rt_cd": "1", "msg_cd": code,
                                    "msg1": "SECRET_TOKEN_VALUE", "authorization": "Bearer SECRET_TOKEN_VALUE"})
            self.assertEqual(result["status"], "business_rejected")
            self.assertNotIn("msg_cd", result)
            self.assertNotIn("SECRET_TOKEN_VALUE", json.dumps(result))

    def test_extra_fill_or_account_claims_cannot_upgrade_acknowledgment_evidence(self):
        payload = deepcopy(self.accepted)
        payload.update({"filled_quantity": "2", "account": "SECRET_ACCOUNT", "verified_fill": True})
        payload["output"]["filled"] = "2"
        before = deepcopy(payload)
        result = self.classify(payload)
        self.assertEqual(result["status"], "accepted_acknowledgment")
        self.assertEqual(payload, before)
        self.assertNotIn("SECRET_ACCOUNT", json.dumps(result))
        self.assertNotIn("filled", result["acknowledgment"])

    def test_hostile_unknown_response_does_not_echo_payload(self):
        payload = {"rt_cd": "0", "msg1": "SECRET_TOKEN_VALUE",
                   "output": {"ODNO": "SECRET_TOKEN_VALUE"}}
        result = self.classify(payload)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("SECRET_TOKEN_VALUE", json.dumps(result))
        self.assertNotIn("acknowledgment", result)


if __name__ == "__main__":
    unittest.main()
