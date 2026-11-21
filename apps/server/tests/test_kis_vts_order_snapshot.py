"""KIS daily-order inquiry contracts, using fake HTTP and forbidden sockets."""

import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/kis_vts_order_snapshot.py"
sys.path.insert(0, str(ROOT))
DATE = "2026-11-21"
NOW = datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc)
AUTH = {
    "token": "SYNTHETIC-ACCESS-TOKEN",
    "appkey": "SYNTHETIC-APPKEY",
    "appsecret": "SYNTHETIC-APPSECRET",
    "cano": "87654321",
    "acnt_prdt_cd": "01",
}
ENV = {"KIS_VTS_" + key.upper(): value for key, value in AUTH.items() if key != "token"}
ENV["KIS_VTS_ACCESS_TOKEN"] = AUTH["token"]


def order_row(**changes):
    # Synthetic values follow the public portal's field names. A rejected
    # order can have zero remaining quantity without any execution.
    row = {
        "ord_dt": "20261121",
        "ord_gno_brno": "06010",
        "odno": "0000001234",
        "orgn_odno": "",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_dvsn_cd": "00",
        "ord_qty": "10",
        "ord_unpr": "70000",
        "tot_ccld_qty": "0",
        "tot_ccld_amt": "0",
        "avg_prvs": "0",
        "rmn_qty": "0",
        "cncl_cfrm_qty": "0",
        "rjct_qty": "10",
        "cncl_yn": "",
        "ord_tmd": "090001",
        "excg_id_dvsn_Cd": "KRX",
        "ctac_tlno": "SYNTHETIC-PRIVATE-PHONE",
        "inqr_ip_addr": "PRIVATE-IP",
        "ordr_empno": "PRIVATE-EMPLOYEE",
        "prdt_name": "UNNEEDED-NAME",
    }
    row.update(changes)
    return row


def page(rows=None, **changes):
    body = {
        "rt_cd": "0",
        "msg_cd": "KIOK0510",
        "msg1": "PRIVATE-MESSAGE",
        "ctx_area_fk100": "",
        "ctx_area_nk100": "",
        "output1": [order_row()] if rows is None else rows,
        "output2": {"tot_ord_qty": "999999", "tot_ccld_amt": "PRIVATE-SUMMARY"},
    }
    body.update(changes)
    return body


class Response:
    def __init__(self, data, *, continuation="D", status=200, location=None, headers=None):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status = self.code = status
        self.msg = "PRIVATE-HTTP-MESSAGE"
        self.headers = Message()
        self.headers["content-type"] = "application/json; charset=utf-8"
        self.headers["tr_id"] = "VTTC0081R"
        if continuation is not None:
            self.headers["tr_cont"] = continuation
        if location is not None:
            self.headers["Location"] = location
        for key, value in headers or ():
            self.headers[key] = value
        self.read_limits = []

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, limit):
        self.read_limits.append(limit)
        return self.data[:limit]

    def close(self):
        pass


class _ObserverTestCase(unittest.TestCase):
    def setUp(self):
        try:
            self.observer = importlib.import_module("tools.kis_vts_order_snapshot")
        except ModuleNotFoundError as error:
            if error.name != "tools.kis_vts_order_snapshot":
                raise
            self.fail("The read-only KIS VTS order observer is not implemented")
        self.requests = []
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def open_pages(self, responses):
        pending = iter(responses)

        def opened(request, *, timeout):
            self.requests.append(request)
            self.assertEqual(timeout, 10)
            return next(pending)

        return opened

    def fetch(self, responses=None, **changes):
        values = dict(
            AUTH,
            account_ref="paper-vts",
            max_pages=20,
            now=lambda: NOW,
            opener=self.open_pages([Response(page())] if responses is None else responses),
        )
        values.update(changes)
        inquiry_date = values.pop("inquiry_date", DATE)
        return self.observer.fetch_order_snapshot(inquiry_date, **values)


class KisVtsOrderSnapshotTests(_ObserverTestCase):
    def test_fixed_readonly_get_contract_and_allowlisted_rejected_observation(self):
        result = self.fetch()
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.netloc, "openapivts.koreainvestment.com:29443")
        self.assertEqual(url.path, "/uapi/domestic-stock/v1/trading/inquire-daily-ccld")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            parse_qs(url.query, keep_blank_values=True),
            {
                "CANO": ["87654321"],
                "ACNT_PRDT_CD": ["01"],
                "INQR_STRT_DT": ["20261121"],
                "INQR_END_DT": ["20261121"],
                "SLL_BUY_DVSN_CD": ["00"],
                "PDNO": [""],
                "ORD_GNO_BRNO": [""],
                "ODNO": [""],
                "CCLD_DVSN": ["00"],
                "INQR_DVSN": ["00"],
                "INQR_DVSN_1": [""],
                "INQR_DVSN_3": ["00"],
                "EXCG_ID_DVSN_CD": ["KRX"],
                "CTX_AREA_FK100": [""],
                "CTX_AREA_NK100": [""],
            },
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer SYNTHETIC-ACCESS-TOKEN")
        self.assertEqual(request.get_header("Appkey"), "SYNTHETIC-APPKEY")
        self.assertEqual(request.get_header("Appsecret"), "SYNTHETIC-APPSECRET")
        self.assertEqual(request.get_header("Tr_id"), "VTTC0081R")
        self.assertEqual(request.get_header("Tr_cont"), "")
        self.assertEqual(request.get_header("Custtype"), "P")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertEqual(result["mode"], "paper")
        self.assertIs(result["live_orders_enabled"], False)
        self.assertEqual(result["account_ref"], "paper-vts")
        self.assertIs(result["account_binding_verified"], False)
        self.assertIs(result["economic_reconciliation_verified"], False)
        self.assertIs(result["atomic_snapshot_verified"], False)
        self.assertIs(result["pagination_complete"], True)
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["observed_at_utc"], "2026-11-21T01:00:00+00:00")
        self.assertEqual(
            result["rows"],
            [
                {
                    "ord_dt": "20261121",
                    "ord_gno_brno": "06010",
                    "odno": "0000001234",
                    "orgn_odno": "",
                    "pdno": "005930",
                    "sll_buy_dvsn_cd": "02",
                    "ord_dvsn_cd": "00",
                    "ord_qty": "10",
                    "ord_unpr": "70000",
                    "tot_ccld_qty": "0",
                    "tot_ccld_amt": "0",
                    "avg_prvs": "0",
                    "rmn_qty": "0",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "10",
                    "cncl_yn": "",
                    "ord_tmd": "090001",
                    "excg_id_dvsn_cd": "KRX",
                }
            ],
        )
        serialized = json.dumps(result)
        for private in ("87654321", "SYNTHETIC-", "PRIVATE-", "UNNEEDED-", "999999"):
            self.assertNotIn(private, serialized)
        for invented in ("status", "executions", "fills", "total_filled_quantity"):
            self.assertNotIn(invented, result["rows"][0])

    def test_pagination_carries_exact_both_cursors_and_never_sums_linked_history(self):
        responses = [
            Response(
                page(ctx_area_fk100="87654321 01 CONTEXT  ", ctx_area_nk100="  NEXT-KEY  "),
                continuation="M",
            ),
            Response(
                page(
                    [order_row(odno="0000001235", orgn_odno="0000001234")],
                    ctx_area_fk100="SECOND-CONTEXT",
                    ctx_area_nk100="SECOND-KEY",
                ),
                continuation="F",
            ),
            Response(
                page([order_row(odno="0000001236", orgn_odno="0000001234")]), continuation="E"
            ),
        ]
        result = self.fetch(responses)
        self.assertEqual(result["pages_fetched"], 3)
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(
            [row["odno"] for row in result["rows"]], ["0000001234", "0000001235", "0000001236"]
        )
        self.assertEqual(
            [request.get_header("Tr_cont") for request in self.requests], ["", "N", "N"]
        )
        second = parse_qs(urlsplit(self.requests[1].full_url).query, keep_blank_values=True)
        third = parse_qs(urlsplit(self.requests[2].full_url).query, keep_blank_values=True)
        self.assertEqual(second["CTX_AREA_FK100"], ["87654321 01 CONTEXT  "])
        self.assertEqual(second["CTX_AREA_NK100"], ["  NEXT-KEY  "])
        self.assertEqual(third["CTX_AREA_FK100"], ["SECOND-CONTEXT"])
        self.assertEqual(third["CTX_AREA_NK100"], ["SECOND-KEY"])
        self.assertNotIn("CONTEXT", json.dumps(result))
        self.assertNotIn("NEXT-KEY", json.dumps(result))

    def test_empty_terminal_page_is_complete_without_inventing_orders(self):
        for terminal in ("D", "E"):
            with self.subTest(terminal=terminal):
                result = self.fetch([Response(page([]), continuation=terminal)])
                self.assertEqual(result["rows"], [])
                self.assertEqual(result["row_count"], 0)
                self.assertIs(result["pagination_complete"], True)

    def test_casing_is_normalized_but_conflicting_case_keys_are_refused(self):
        body = {key.upper(): value for key, value in page().items()}
        body["OUTPUT1"] = [{key.upper(): value for key, value in order_row().items()}]
        response = Response(body, continuation=None, headers=(("TR_CONT", "D"),))
        self.assertEqual(self.fetch([response])["rows"][0]["odno"], "0000001234")
        bad = [
            Response(page(RT_CD="1")),
            Response(page([order_row(ODNO="0000007777")])),
            Response(page(), headers=(("TR_CONT", "M"),)),
        ]
        for response in bad:
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.fetch([response])

    def test_raw_duplicate_keys_and_nonfinite_json_are_rejected_even_in_private_fields(self):
        for raw in (
            b'{"rt_cd":"0","rt_cd":"1","output1":[]}',
            b'{"rt_cd":"0","output1":[],"private":{"x":1,"x":1}}',
            b'{"rt_cd":"0","output1":[],"private":NaN}',
            b'{"rt_cd":"0","output1":[],"private":Infinity}',
            b"[]",
            b"null",
            b"{",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.fetch([Response(raw)])

    def test_json_numeric_literals_are_bounded_on_python_310_and_finite(self):
        for number in ("9" * 129, "1e999", "-1e999", "1." + "0" * 129):
            raw = ('{"rt_cd":"0","output1":[],"private":' + number + "}").encode()
            with self.subTest(size=len(number)), self.assertRaises(ValueError):
                self.fetch([Response(raw)])

    def test_identical_case_aliases_do_not_hide_different_json_types(self):
        compatible = page(RT_CD="0")
        self.assertEqual(self.fetch([Response(compatible)])["row_count"], 1)
        for first, second in ((True, 1), ({"value": True}, {"value": 1})):
            with self.subTest(first=first), self.assertRaises(ValueError):
                self.fetch([Response(page(private=first, PRIVATE=second))])

    def test_missing_unknown_or_malformed_continuation_is_not_success(self):
        for signal in (None, "", " ", "N", "X", "DM", "d"):
            with self.subTest(signal=signal), self.assertRaises(ValueError):
                self.fetch([Response(page(), continuation=signal)])

    def test_continuation_requires_both_bounded_nonblank_printable_cursors(self):
        for key in ("ctx_area_fk100", "ctx_area_nk100"):
            for value in (None, "", " ", "x" * 101, "x\n", "x\x00", "é", 123, True):
                body = page(ctx_area_fk100="CONTEXT", ctx_area_nk100="NEXT")
                body[key] = value
                with self.subTest(key=key, case=repr(value)), self.assertRaises(ValueError):
                    self.fetch([Response(body, continuation="M")])
            body = page(ctx_area_fk100="CONTEXT", ctx_area_nk100="NEXT")
            del body[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.fetch([Response(body, continuation="F")])

    def test_cursor_loops_and_page_limit_refuse_without_an_extra_request(self):
        first = Response(
            page([], ctx_area_fk100="CONTEXT", ctx_area_nk100="NEXT"), continuation="M"
        )
        for responses, limit, calls in (([first], 1, 1), ([first, first], 20, 2)):
            self.requests.clear()
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.fetch(responses, max_pages=limit)
            self.assertEqual(len(self.requests), calls)

    def test_vts_row_and_total_page_boundaries(self):
        with self.assertRaises(ValueError):
            self.fetch([Response(page([order_row(odno=str(index + 1)) for index in range(16)]))])
        responses = []
        for index in range(20):
            rows = [order_row(odno=str(index * 15 + offset + 1)) for offset in range(15)]
            responses.append(
                Response(
                    page(
                        rows,
                        ctx_area_fk100="CONTEXT-" + str(index),
                        ctx_area_nk100="NEXT-" + str(index),
                    ),
                    continuation="D" if index == 19 else "M",
                )
            )
        result = self.fetch(responses)
        self.assertEqual(result["pages_fetched"], 20)
        self.assertEqual(result["row_count"], 300)
        responses[-1].headers.replace_header("tr_cont", "M")
        self.requests.clear()
        with self.assertRaises(ValueError):
            self.fetch(responses)
        self.assertEqual(len(self.requests), 20)

    def test_duplicate_identity_refuses_same_and_changed_rows_across_pages(self):
        for repeated in (
            order_row(),
            order_row(tot_ccld_qty="5"),
            order_row(odno="1234", ord_gno_brno="6010"),
        ):
            with self.subTest(changed=repeated["tot_ccld_qty"]):
                with self.assertRaises(ValueError):
                    self.fetch([Response(page([order_row(), repeated]))])
                with self.assertRaises(ValueError):
                    self.fetch(
                        [
                            Response(
                                page(ctx_area_fk100="CONTEXT", ctx_area_nk100="NEXT"),
                                continuation="M",
                            ),
                            Response(page([repeated])),
                        ]
                    )
        # A different branch/order is distinct; an original-order relationship
        # does not make two broker records the same row.
        result = self.fetch(
            [
                Response(
                    page(
                        [
                            order_row(),
                            order_row(ord_gno_brno="06011"),
                            order_row(odno="0000001235", orgn_odno="0000001234"),
                        ]
                    )
                )
            ]
        )
        self.assertEqual(result["row_count"], 3)

    def test_wrong_date_invalid_identity_and_malformed_numeric_observations_refuse(self):
        invalid = {
            "ord_dt": ("20261120", "20260230", "2026-11-21", "２０２６１１２１"),
            "ord_gno_brno": ("", "123456", "BRANCH", 6010),
            "odno": ("", "0", "0000000000", "1" * 11, 1234),
            "orgn_odno": (None, "secret", "1" * 11),
            "pdno": ("A005930", "00593", "００５９３０", "PRIVATE-PHONE"),
            "sll_buy_dvsn_cd": ("00", "BUY", 2),
            "ord_dvsn_cd": ("", "000", "ZZ", 0),
            "ord_qty": ("-1", "1.5", "1e2", "1" * 11, True, 10),
            "tot_ccld_qty": ("NaN", " 1", "１", None),
            "rmn_qty": ("+1", "1\n"),
            "cncl_cfrm_qty": (None,),
            "rjct_qty": ([],),
            "ord_unpr": ("-1", "1e3", "1" * 20, None),
            "tot_ccld_amt": ("NaN", "Infinity", "1" * 20, True),
            "avg_prvs": ("-0.1", "1e3", "NaN", ".5", "1.", "1" * 20),
            "cncl_yn": ("MAYBE", None),
            "ord_tmd": ("240000", "096000", "090060", "90001", ""),
            "excg_id_dvsn_Cd": ("NXT", "SOR", "ALL", "", None),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.fetch([Response(page([order_row(**{field: value})]))])

    def test_required_observation_fields_cannot_be_silently_dropped_or_misspelled(self):
        required = (
            "ord_dt",
            "ord_gno_brno",
            "odno",
            "orgn_odno",
            "pdno",
            "sll_buy_dvsn_cd",
            "ord_dvsn_cd",
            "ord_qty",
            "ord_unpr",
            "tot_ccld_qty",
            "tot_ccld_amt",
            "avg_prvs",
            "rmn_qty",
            "cncl_cfrm_qty",
            "rjct_qty",
            "cncl_yn",
            "ord_tmd",
        )
        for field in required:
            row = order_row()
            del row[field]
            if field == "cncl_cfrm_qty":
                row["cnc_cfrm_qty"] = "0"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.fetch([Response(page([row]))])
        # Older public examples omit the optional exchange response field.
        row = order_row()
        del row["excg_id_dvsn_Cd"]
        self.assertNotIn("excg_id_dvsn_cd", self.fetch([Response(page([row]))])["rows"][0])

    def test_observation_values_remain_exact_and_do_not_infer_economic_consistency(self):
        row = order_row(
            ord_qty="9999999999",
            tot_ccld_qty="0000000002",
            ord_unpr="9999999999999999999",
            tot_ccld_amt="9999999999999999998",
            avg_prvs="123.5000",
            rmn_qty="9999999999",
            cncl_yn="N",
        )
        normalized = self.fetch([Response(page([row]))])["rows"][0]
        self.assertEqual(normalized["ord_qty"], "9999999999")
        self.assertEqual(normalized["tot_ccld_qty"], "2")
        self.assertEqual(normalized["ord_unpr"], "9999999999999999999")
        self.assertEqual(normalized["tot_ccld_amt"], "9999999999999999998")
        self.assertEqual(normalized["avg_prvs"], "123.5")
        self.assertEqual(normalized["odno"], "0000001234")
        self.assertNotIn("status", normalized)

    def test_past_30_kst_calendar_dates_are_checked_before_http(self):
        at_midnight_kst = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
        for inquiry_date in ("2026-11-21", "2026-10-23"):
            body = page([order_row(ord_dt=inquiry_date.replace("-", ""))])
            self.fetch([Response(body)], inquiry_date=inquiry_date, now=lambda: at_midnight_kst)
        self.requests.clear()
        for inquiry_date in (
            "2026-10-22",
            "2026-11-22",
            "20261121",
            "2026-2-3",
            "2026-02-30",
            None,
            20261121,
            "２０２６-１１-２１",
        ):
            with self.subTest(inquiry_date=inquiry_date), self.assertRaises(ValueError):
                self.fetch(inquiry_date=inquiry_date, now=lambda: at_midnight_kst)
        self.assertEqual(self.requests, [])

    def test_invalid_configuration_and_credentials_never_reach_http(self):
        cases = (
            ("account_ref", ""),
            ("account_ref", "1account"),
            ("account_ref", "한글"),
            ("account_ref", "a" * 65),
            ("account_ref", "paper\n"),
            ("max_pages", 0),
            ("max_pages", 21),
            ("max_pages", True),
            ("max_pages", "1"),
            ("cano", "1234567"),
            ("cano", "１２３４５６７８"),
            ("cano", 12345678),
            ("acnt_prdt_cd", "1"),
            ("acnt_prdt_cd", "01\n"),
            ("token", None),
            ("token", ""),
            ("token", "Bearer token"),
            ("token", "abc:def"),
            ("token", "a\r\nheader"),
            ("token", "a" * 4097),
            ("appkey", None),
            ("appkey", "a\x00"),
            ("appkey", "é"),
            ("appsecret", "a\t"),
            ("appsecret", "a" * 4097),
            ("now", lambda: datetime(2026, 11, 21)),
            ("now", lambda: "not-a-time"),
        )
        for field, value in cases:
            with self.subTest(field=field, value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.fetch(**{field: value})
        self.assertEqual(self.requests, [])

    def test_alias_cannot_copy_an_account_number_or_credential_into_output(self):
        for alias in (
            "paper-87654321",
            "SYNTHETIC-ACCESS-TOKEN",
            "SYNTHETIC-APPKEY",
            "SYNTHETIC-APPSECRET",
        ):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                self.fetch(account_ref=alias)
        self.assertEqual(self.requests, [])

    def test_non_200_broker_errors_and_oversized_or_malformed_pages_fail_once(self):
        responses = [Response(page(), status=status) for status in (201, 301, 401, 429, 500)]
        responses += [
            Response(page(rt_cd="1")),
            Response(page(rt_cd=0)),
            Response(page(output1={})),
            Response(page(output1=[None])),
            Response(b"x" * 2_000_001),
        ]
        for response in responses:
            self.requests.clear()
            with self.subTest(status=response.status, size=len(response.data)):
                with self.assertRaises(ValueError) as error:
                    self.fetch([response])
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(responses[-1].read_limits, [2_000_001])

    def test_transport_errors_are_sanitized_without_retry_or_exception_chaining(self):
        def failed(request, *, timeout):
            self.requests.append(request)
            private = "PRIVATE-ERROR " + AUTH["token"] + " " + AUTH["cano"]
            raise HTTPError(request.full_url, 401, private, {}, BytesIO(private.encode()))

        with self.assertRaises(ValueError) as error:
            self.fetch(opener=failed)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn(AUTH["cano"], str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertTrue(error.exception.__suppress_context__)

    def test_real_default_transport_disables_environment_proxies(self):
        destinations = []

        def https_open(handler, request):
            destinations.append((request.host, request._tunnel_host))
            self.requests.append(request)
            return Response(page())

        with (
            patch.dict(
                os.environ,
                {
                    "HTTPS_PROXY": "http://proxy.invalid:3128",
                    "https_proxy": "http://proxy.invalid:3128",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
                clear=True,
            ),
            patch("urllib.request.HTTPSHandler.https_open", https_open),
        ):
            self.observer.fetch_order_snapshot(
                DATE, account_ref="paper-vts", now=lambda: NOW, **AUTH
            )
        self.assertEqual(destinations, [("openapivts.koreainvestment.com:29443", None)])

    def test_real_default_transport_refuses_all_redirects_without_followup(self):
        for status in (301, 302, 303, 307, 308):
            for destination in (
                "https://openapivts.koreainvestment.com:29443/other",
                "https://steal.invalid/",
            ):
                self.requests.clear()

                def redirected(handler, request):
                    self.requests.append(request)
                    return Response({}, status=status, location=destination)

                with self.subTest(status=status, destination=destination):
                    with patch("urllib.request.HTTPSHandler.https_open", redirected):
                        with self.assertRaises(ValueError):
                            self.observer.fetch_order_snapshot(
                                DATE, account_ref="paper-vts", now=lambda: NOW, **AUTH
                            )
                    self.assertEqual(len(self.requests), 1)

    def test_http_308_is_denied_on_python_310_without_inherited_handler(self):
        absent = object()
        inherited = HTTPRedirectHandler.__dict__.get("http_error_308", absent)
        if inherited is not absent:
            delattr(HTTPRedirectHandler, "http_error_308")
        try:

            def redirected(handler, request):
                self.requests.append(request)
                return Response({}, status=308, location="https://steal.invalid/")

            with patch("urllib.request.HTTPSHandler.https_open", redirected):
                with self.assertRaises(ValueError):
                    self.observer.fetch_order_snapshot(
                        DATE, account_ref="paper-vts", now=lambda: NOW, **AUTH
                    )
            self.assertEqual(len(self.requests), 1)
        finally:
            if inherited is not absent:
                HTTPRedirectHandler.http_error_308 = inherited


class KisVtsOrderSnapshotCliTests(_ObserverTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix="kis order snapshot ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "new-snapshot.json"
        self.today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        self.arguments = [
            "--mode",
            "paper",
            "--allow-vts-readonly",
            "--date",
            self.today,
            "--account-ref",
            "paper-vts",
            "--max-pages",
            "20",
            "--output",
            str(self.target),
        ]

    def invoke(self, arguments=None, *, responses=None):
        stdout, stderr = StringIO(), StringIO()
        pending = iter(
            [Response(page([order_row(ord_dt=self.today.replace("-", ""))]))]
            if responses is None
            else responses
        )

        def https_open(handler, request):
            self.requests.append(request)
            return next(pending)

        with (
            patch.dict(os.environ, ENV, clear=True),
            patch("urllib.request.HTTPSHandler.https_open", https_open),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = self.observer.main(self.arguments if arguments is None else arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def changed(self, option, value):
        arguments = self.arguments.copy()
        arguments[arguments.index(option) + 1] = value
        return arguments

    def test_cli_writes_new_private_snapshot_and_preserves_journals(self):
        existing = {}
        for name in ("journal.sqlite", "stock-ledger.json", "crypto-ledger.json"):
            path = self.directory / name
            path.write_bytes(b"existing-accounting-evidence\n")
            existing[path] = path.read_bytes()
        old_umask = os.umask(0)
        try:
            code, stdout, stderr = self.invoke()
        finally:
            os.umask(old_umask)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        snapshot = json.loads(self.target.read_text())
        self.assertTrue(snapshot["pagination_complete"])
        self.assertFalse(snapshot["economic_reconciliation_verified"])
        self.assertEqual(set(self.directory.iterdir()), {*existing, self.target})
        for path, before in existing.items():
            self.assertEqual(path.read_bytes(), before)
        for private in ("SYNTHETIC-", "PRIVATE-", "87654321", str(self.target)):
            self.assertNotIn(private, self.target.read_text() + stdout + stderr)

    def test_cli_failures_never_retain_partial_results_or_private_error_text(self):
        first = page(
            [order_row(ord_dt=self.today.replace("-", ""))],
            ctx_area_fk100="PRIVATE-CURSOR",
            ctx_area_nk100="PRIVATE-NEXT",
        )
        code, stdout, stderr = self.invoke(
            responses=[
                Response(first, continuation="M"),
                Response(page(rt_cd="1", msg1=AUTH["token"])),
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("PRIVATE-", stderr)
        self.assertNotIn("SYNTHETIC-", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(len(self.requests), 2)

    def test_cli_output_collisions_and_invalid_parents_refuse_before_http(self):
        self.target.write_bytes(b"prior-evidence")
        self.target.chmod(0o640)
        before = self.target.stat()
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.target.read_bytes(), b"prior-evidence")
        self.assertEqual(self.target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(self.requests, [])
        for destination in (
            self.directory / "missing" / "snapshot.json",
            self.target / "snapshot.json",
            self.directory / ("a" * 256),
        ):
            with self.subTest(destination=destination.name):
                code, stdout, stderr = self.invoke(self.changed("--output", str(destination)))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(str(destination), stderr)
                self.assertEqual(self.requests, [])

    def test_cli_preserves_existing_and_dangling_output_symlinks(self):
        destination = self.directory / "existing.json"
        destination.write_bytes(b"prior-evidence")
        for resolved in (destination, self.directory / "missing.json"):
            self.target.symlink_to(resolved)
            code, stdout, stderr = self.invoke()
            self.assertEqual(code, 2)
            self.assertEqual(self.requests, [])
            self.assertTrue(self.target.is_symlink())
            self.assertEqual(self.target.readlink(), resolved)
            self.assertEqual(destination.read_bytes(), b"prior-evidence")
            self.assertFalse((self.directory / "missing.json").exists())
            self.target.unlink()

    def test_cli_requires_explicit_mode_optin_date_and_alias_without_echoing_arguments(self):
        cases = [
            self.changed("--mode", "live"),
            self.changed("--date", "2000-01-01"),
            self.changed("--account-ref", "PRIVATE-SECRET\n"),
            self.changed("--max-pages", "9" * 100000),
            self.arguments + ["--PRIVATE-SECRET"],
            self.changed("--output", str(self.directory / "PRIVATE-SECRET" / "out.json")),
        ]
        for option in ("--mode", "--allow-vts-readonly", "--date", "--account-ref", "--output"):
            arguments = self.arguments.copy()
            start = arguments.index(option)
            del arguments[start : start + (1 if option == "--allow-vts-readonly" else 2)]
            cases.append(arguments)
        for arguments in cases:
            with self.subTest(argument_count=len(arguments)):
                code, stdout, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn("PRIVATE-SECRET", stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(self.requests, [])
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_cli_removes_its_own_output_if_final_write_fails(self):
        with patch.object(self.observer.os, "fsync", side_effect=OSError("PRIVATE-ERROR")):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("PRIVATE", stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_replaced_output_is_preserved_and_cannot_report_snapshot_success(self):
        response = Response(page([order_row(ord_dt=self.today.replace("-", ""))]))
        original_read = response.read

        def replace_during_http(limit):
            self.target.unlink()
            self.target.write_bytes(b"replacement-evidence")
            return original_read(limit)

        response.read = replace_during_http
        code, stdout, stderr = self.invoke(responses=[response])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(self.target.read_bytes(), b"replacement-evidence")

    def test_actual_cli_runs_fake_http_under_socket_dns_guards_without_site_packages(self):
        fixture = page([order_row(ord_dt=self.today.replace("-", ""))])
        guard = (
            "import json,runpy,socket,sys,urllib.request\n"
            "from email.message import Message\n"
            "def forbidden(*args,**kwargs):\n"
            " raise AssertionError('Network forbidden')\n"
            "socket.socket.connect=forbidden\n"
            "socket.create_connection=forbidden\n"
            "socket.getaddrinfo=forbidden\n"
            "class Response:\n"
            " status=code=200\n"
            " msg='OK'\n"
            " headers=Message()\n"
            " headers['tr_cont']='D'\n"
            " headers['tr_id']='VTTC0081R'\n"
            " def __enter__(self): return self\n"
            " def __exit__(self,*args): return False\n"
            " def info(self): return self.headers\n"
            " def read(self,n): return " + repr(json.dumps(fixture).encode()) + "[:n]\n"
            " def close(self): pass\n"
            "def fake_http(self,request):\n"
            " assert request.get_method()=='GET'\n"
            " assert request.full_url.startswith('https://openapivts.koreainvestment.com:29443/"
            "uapi/domestic-stock/v1/trading/inquire-daily-ccld?')\n"
            " assert request.get_header('Tr_id')=='VTTC0081R'\n"
            " assert request._tunnel_host is None\n"
            " return Response()\n"
            "urllib.request.HTTPSHandler.https_open=fake_http\n"
            "sys.argv=sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0],run_name='__main__')\n"
        )
        environment = dict(ENV, PATH=os.defpath, HTTPS_PROXY="http://proxy.invalid:3128")
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", guard, str(SCRIPT), *self.arguments],
            cwd=self.directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(json.loads(self.target.read_text())["pagination_complete"])
        for private in ("SYNTHETIC-", "PRIVATE-", AUTH["cano"]):
            self.assertNotIn(private, self.target.read_text() + result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
