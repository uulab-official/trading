"""Run the bundled crypto, KRW/USD stock and order experiments without network.

Optional retained output is a NEW private directory. The generated entry page
links synthetic ledgers, independent audits, backups and recovery evidence.
Requires the existing offline dependencies; no broker credentials are needed.
"""

import asyncio
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
                 ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(location))

from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from tools.broker_readiness import inspect_environment
from tools.paper_backup import backup_pair, verify_backup as verify_crypto_backup
from tools.paper_checkpoint_audit import audit as audit_crypto
from tools.paper_portfolio_report_html import render as render_portfolios
from tools.paper_portfolio_status import inspect_portfolios
from tools.paper_report_html import generate as generate_crypto_html
from tools.stock_order_journal import SafeArgumentParser, read_bounded_json
from tools.stock_order_lifecycle_smoke import run_smoke as run_lifecycle_smoke
from tools.stock_order_review import write_reports
from tools.stock_order_review_smoke import run_smoke as run_order_review_smoke
from tools.stock_paper_smoke import run_smoke as run_stock_smoke


KST = timezone(timedelta(hours=9))
COMPONENT_STATUS = {
    "crypto": "synthetic_crypto_paper_verified",
    "stock_krw": "synthetic_stock_paper_smoke_verified",
    "stock_usd": "synthetic_stock_paper_smoke_verified",
    "order_lifecycle": "synthetic_stock_order_lifecycle_verified",
    "order_review": "synthetic_stock_order_review_verified",
}
REPORT_LINKS = (
    ("통합 원장 조회", "portfolios.html", "독립 검증한 코인·KRW·USD 원장 3개"),
    ("코인 전략", "crypto/report.html", "90개 합성 시간봉 · 중간 재시작·중복 방지·백업"),
    ("국내 주식 전략", "stock-krw/report.html", "55개 합성 분봉 · KRW 원장·재생 감사·백업"),
    ("미국 주식 전략", "stock-usd/report.html", "가상 종목 DEMO · USD 원장·재생 감사·백업"),
    ("주문 관측값 일치", "order-review/review.html", "가짜 KIS 응답으로 로컬 주문 2개 비교"),
    ("의도적 차이 탐지", "order-review/review-difference.html", "체결 수량·누적 대금의 차이 확인"),
    ("부분 체결·취소 경합", "order-lifecycle/report.json", "결과 불명·중복 전달·금지된 상태 변경 검증"),
    ("백업·복원 검증", "order-review/smoke.json", "새 후보 DB의 상태·중복 재생 일치"),
    ("증권사 설정 진단", "broker-readiness.json", "환경변수 유무만 확인 · 실제 인증 미검증"),
    ("전체 검증 요약", "summary.json", "각 실험의 결과와 확인 항목"),
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


@contextmanager
def _without_network():
    """Scoped Python regression guard, not an OS sandbox or a thread-safe API."""
    attempted = [0]

    def refused(*args, **kwargs):
        attempted[0] += 1
        raise RuntimeError("Paper lab forbids network and DNS")

    with ExitStack() as stack:
        for owner, names in (
            (socket, ("create_connection", "getaddrinfo", "gethostbyname",
                      "gethostbyname_ex", "gethostbyaddr", "getnameinfo")),
            (socket.socket, ("connect", "connect_ex", "sendto", "sendmsg")),
        ):
            for name in names:
                if hasattr(owner, name):
                    stack.enter_context(patch.object(owner, name, refused))
        yield attempted


def _crypto_exercise(directory):
    os.mkdir(directory, 0o700)
    fixture = read_bounded_json(ROOT / "config/examples/offline_fixture.json")
    if (fixture.get("mode") != "paper" or fixture.get("symbol") != "BTC/USDT"
            or fixture.get("timeframe") != "1h"
            or fixture.get("description") != "Synthetic deterministic test data, not a market performance claim"
            or len(fixture.get("candles", [])) != 90):
        raise ValueError("Expected bundled synthetic crypto fixture")
    fixture_path = directory / "candles.json"
    checkpoint = directory / "checkpoint.json"
    write_reports([(fixture_path, _json(fixture))])
    config = PaperRunConfig()
    paused = asyncio.run(PaperRunner(config).run(
        fixture["candles"], checkpoint_path=checkpoint, stop_after=30))
    if paused["status"] != "paused" or paused["bars_processed"] != 30:
        raise ValueError("Synthetic crypto pause did not preserve progress")
    complete = asyncio.run(PaperRunner(config).run(fixture["candles"], checkpoint_path=checkpoint))
    before = checkpoint.read_bytes()
    repeated = asyncio.run(PaperRunner(config).run(fixture["candles"], checkpoint_path=checkpoint))
    if (complete["status"] != "fixture_complete" or complete["bars_processed"] != 90
            or complete != repeated or before != checkpoint.read_bytes()):
        raise ValueError("Synthetic crypto repeat changed checkpoint or trades")
    audited = audit_crypto(fixture_path, checkpoint)
    backup_pair(fixture_path, checkpoint, directory / "backup")
    backup = verify_crypto_backup(directory / "backup", deep=True)
    page = generate_crypto_html(fixture_path, checkpoint)
    if (audited["processed_bars"] != 90 or audited["trade_count"] != len(complete["trades"])
            or backup["economic_replay_verified"] is not True):
        raise ValueError("Synthetic crypto audit and backup disagree")
    write_reports([(directory / "audit.json", _json(audited)),
                   (directory / "report.html", page)])
    return {
        "status": "synthetic_crypto_paper_verified", "mode": "paper",
        "live_orders_enabled": False, "symbol": "BTC/USDT", "quote_currency": "USDT",
        "bars": audited["processed_bars"], "simulated_fills": audited["trade_count"],
        "paused_after_bars": 30, "resume_verified": True,
        "duplicate_replay_prevented": True, "backup_verified": True,
        "static_html_verified": True,
    }


def _portfolio_report(directory, stock_krw, stock_usd, clock):
    krw_settings, usd_settings = stock_krw["settings"], stock_usd["settings"]
    report = inspect_portfolios(
        crypto_fixture=directory / "crypto/candles.json",
        crypto_checkpoint=directory / "crypto/checkpoint.json",
        stock_fixture=directory / "stock-krw/candles.json",
        stock_database=directory / "stock-krw/strategy.sqlite",
        stock_cash=krw_settings["initial_cash"], stock_fee_pct=krw_settings["fee_pct"],
        fast=krw_settings["fast"], slow=krw_settings["slow"],
        allocation_pct=krw_settings["allocation_pct"],
    )
    usd = inspect_portfolios(
        stock_fixture=directory / "stock-usd/candles.json",
        stock_database=directory / "stock-usd/strategy.sqlite",
        stock_cash=usd_settings["initial_cash"], stock_fee_pct=usd_settings["fee_pct"],
        fast=usd_settings["fast"], slow=usd_settings["slow"],
        allocation_pct=usd_settings["allocation_pct"],
    )
    report["portfolios"].extend(usd["portfolios"])
    report["generated_at_utc"] = clock.isoformat()
    report["provenance"] = "bundled_synthetic_examples"
    cards = report["portfolios"]
    if (len(cards) != 3 or [card["type"] for card in cards] != ["crypto", "stock", "stock"]
            or [card["quote_currency"] for card in cards[1:]] != ["KRW", "USD"]
            or report["cross_portfolio_total"] is not None):
        raise ValueError("Synthetic paper portfolios were not kept separate")
    return report


def _exercise(directory, clock, retained, attempted):
    components = {"crypto": _crypto_exercise(directory / "crypto")}
    components["stock_krw"] = run_stock_smoke(directory / "stock-krw", currency="KRW")
    components["stock_usd"] = run_stock_smoke(directory / "stock-usd", currency="USD")
    components["order_lifecycle"] = run_lifecycle_smoke(directory / "order-lifecycle")["summary"]
    components["order_review"] = run_order_review_smoke(directory / "order-review", now=clock)
    for name, status in COMPONENT_STATUS.items():
        result = components[name]
        if (result["status"] != status or result["mode"] != "paper"
                or result["live_orders_enabled"] is not False
                or any(value is not True for value in result.get("checks", {}).values())):
            raise ValueError("Synthetic component did not finish successfully")
        # Child exercises retain files inside the lab's workspace; a temporary
        # parent removes those files before returning this aggregate summary.
        if "artifacts_retained" in result:
            result["artifacts_retained"] = retained
    portfolios = _portfolio_report(directory, components["stock_krw"], components["stock_usd"], clock)
    readiness = inspect_environment()
    if attempted[0]:
        raise ValueError("A component attempted network access")
    summary = {
        "schema_version": 1, "status": "synthetic_paper_lab_verified", "mode": "paper",
        "provenance": "bundled_synthetic_examples", "generated_at_utc": clock.isoformat(),
        "live_orders_enabled": False, "safe_to_submit": False, "account_binding_verified": False,
        "actual_broker_requests": 0, "network_attempts": attempted[0],
        "network_guard": "python_socket_and_dns", "artifacts_retained": retained,
        "portfolio_count": 3, "cross_portfolio_total": None, "components": components,
        "broker_readiness": readiness,
        "checks": {
            "crypto_resume_and_duplicate_replay": True, "three_independent_portfolio_audits": True,
            "three_deep_verified_portfolio_backups": True, "order_lifecycle_faults": True,
            "order_observation_difference_detected": True, "order_restore_and_duplicate_replay": True,
            "no_actual_broker_requests": True,
        },
        "warning": "Synthetic experiments only; no authenticated accounts, actual fills, FX or investment performance",
    }
    # Generate every document before the grouped exclusive write. Its owned-file
    # cleanup removes both success markers after a handled publication failure.
    index = render_index(summary)
    portfolio_page = render_portfolios(portfolios)
    outputs = [
        (directory / "broker-readiness.json", _json(readiness)),
        (directory / "portfolios.json", _json(portfolios)),
        (directory / "portfolios.html", portfolio_page),
        (directory / "summary.json", _json(summary)),
        (directory / "index.html", index),
    ]
    if attempted[0]:
        raise ValueError("Report generation attempted network access")
    write_reports(outputs)
    return summary


def render_index(summary):
    if (not isinstance(summary, dict) or summary.get("schema_version") != 1
            or summary.get("status") != "synthetic_paper_lab_verified"
            or summary.get("mode") != "paper" or summary.get("live_orders_enabled") is not False
            or summary.get("safe_to_submit") is not False
            or summary.get("account_binding_verified") is not False
            or summary.get("cross_portfolio_total") is not None
            or summary.get("actual_broker_requests") != 0
            or summary.get("provenance") != "bundled_synthetic_examples"):
        raise ValueError("Only completed synthetic paper labs can be rendered")
    components = summary["components"]
    if set(components) != set(COMPONENT_STATUS) or any(
            components[name]["status"] != status for name, status in COMPONENT_STATUS.items()):
        raise ValueError("Incomplete paper lab")
    stamp = datetime.fromisoformat(summary["generated_at_utc"])
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Paper lab timestamp must be aware")
    text = lambda value: html.escape(str(value), quote=True)
    generated = text(stamp.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"))
    cards = []
    for name, title in (("crypto", "코인 · USDT"), ("stock_krw", "국내 주식 · KRW"),
                        ("stock_usd", "미국 주식 · USD")):
        item = components[name]
        cards.append("<article class='metric'><span>" + title + "</span><h2>" +
                     text(item["symbol"]) + "</h2><p>합성 봉 <strong>" + text(item["bars"]) +
                     "개</strong> · 가상 체결 <strong>" + text(item["simulated_fills"]) +
                     "건</strong></p><small>재생 감사 · 중복 방지 · 백업 확인</small></article>")
    links = "".join("<a class='report' href='" + path + "'><strong>" + title +
                    " <span aria-hidden='true'>↗</span></strong><span>" + detail + "</span></a>"
                    for title, path, detail in REPORT_LINKS)
    labels = {"toss_market_data": "토스 시세 조회", "toss_token_issuance": "토스 토큰 발급 설정",
              "kis_vts_quote": "KIS 모의 현재가 조회", "kis_vts_order_inquiry": "KIS 모의 주문 내역 조회"}
    environment_rows = []
    for capability in summary["broker_readiness"]["capabilities"]:
        present = capability["configuration_present"]
        state = "설정 있음 · 인증 미검증" if present else "설정 필요 · 인증 미검증"
        missing = ", ".join(capability["missing_variables"]) if not present else "유효성·권한·계좌 연결은 별도 확인"
        environment_rows.append("<tr><th scope='row'>" + text(labels[capability["id"]]) +
                                "</th><td>" + state + "</td><td>" + text(missing) + "</td></tr>")
    return """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>유유랩 모의매매 실험실</title><style>
:root{color-scheme:light;--ink:#18273c;--muted:#64748b;--blue:#285de5;--line:#dfe6f0}
*{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:var(--ink);font:15px/1.65 system-ui,sans-serif}
main{max-width:1150px;margin:auto;padding:34px 24px 50px}.brand{font-size:12px;letter-spacing:.12em;color:var(--blue);font-weight:800}
header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin:18px 0}h1{font-size:32px;margin:0;letter-spacing:-.04em}h2{font-size:21px;margin:7px 0}
p{margin:5px 0;color:var(--muted)}.badge{background:#deefe6;color:#226445;padding:8px 13px;border-radius:9px;white-space:nowrap;font-weight:700}
.notice{padding:17px 20px;border-radius:12px;background:#eaf0ff;border:1px solid #d6e1ff;color:#325182}.notice strong{display:block;color:#243f6b}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin:23px 0}.metric,.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:22px}.metric>span,small{color:var(--muted);font-size:13px}.metric h2{overflow-wrap:anywhere}
.panel{margin-top:20px}.panel h2{font-size:19px;margin:0 0 13px}.reports{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.report{text-decoration:none;color:var(--ink);border:1px solid var(--line);padding:16px;border-radius:10px;display:block}.report:hover,.report:focus-visible{border-color:var(--blue);background:#f5f8ff}.report strong{display:block;color:#234dbc}.report>span{display:block;font-size:13px;color:var(--muted);margin-top:4px}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}th,td{padding:12px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600;min-width:150px}td:last-child{overflow-wrap:anywhere;min-width:240px}footer{color:var(--muted);font-size:12px;margin-top:24px}
@media(max-width:720px){main{padding:24px 14px}.metrics,.reports{grid-template-columns:1fr}header{display:block}.badge{display:inline-block;margin-top:12px}h1{font-size:27px}.panel,.metric{padding:18px}}
@media print{body{background:white}main{padding:0}.panel,.metric{break-inside:avoid}a{color:inherit}.report{padding:10px}}
</style></head><body><main><div class="brand">UULAB / PAPER LAB</div>
<header><div><h1>모의매매 실험실</h1><p>코인·국내 주식·미국 주식·주문 복원 결과를 함께 확인합니다.</p></div><span class="badge">전체 모의 검증 완료</span></header>
<div class="notice"><strong>합성 데이터로 실행한 로컬 실험입니다.</strong>실제 시세·계좌·수익률 검증을 뜻하지 않습니다. 각 원장은 별도로 보관하며 USDT·KRW·USD 금액은 합산하지 않습니다. 실제 증권사 요청 0건.</div>
<p style="margin-top:12px;font-size:13px">실험 기준 시각: """ + generated + "</p><section class='metrics'>" + "".join(cards) + """</section>
<section class="panel"><h2>실험 결과 열기</h2><div class="reports">""" + links + """</div>
<p style="margin-top:15px;font-size:13px">주문 생명주기와 주문 대조는 같은 합성 시나리오를 따로 실행한 결과입니다. 두 실험의 체결 수를 합산하지 않습니다. 복원 DB는 새 경로의 후보로 남습니다.</p></section>
<section class="panel"><h2>증권사 연결 준비 상태</h2><p>설정값의 존재 여부만 확인합니다. 값·계좌번호는 이 보고서에 기록하지 않으며 토큰을 발급하거나 조회를 실행하지 않습니다.</p>
<div class="table-wrap" role="region" aria-label="증권사 설정 상태" tabindex="0"><table><thead><tr><th>기능</th><th>현재 확인 결과</th><th>필요한 설정</th></tr></thead><tbody>""" + "".join(environment_rows) + """</tbody></table></div></section>
<footer>정적 실험 보고서 · 각 링크는 같은 결과 폴더의 파일을 엽니다. 보고서를 이동할 때는 폴더 전체를 함께 보관하세요. 현재 인증·실시간 운영 상태는 표시하지 않습니다.</footer>
</main></body></html>"""


def run_lab(output_dir=None, *, now=None):
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Paper lab clock must be aware")
    clock = clock.astimezone(timezone.utc)
    with _without_network() as attempted:
        if output_dir is None:
            with TemporaryDirectory(prefix="trading-paper-lab-") as temporary:
                return _exercise(Path(temporary), clock, False, attempted)
        destination = Path(output_dir)
        os.mkdir(destination, 0o700)
        return _exercise(destination, clock, True, attempted)


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-dir", type=Path,
                        help="New private result directory in an existing trusted parent")
    try:
        args = parser.parse_args(argv)
        summary = run_lab(args.output_dir)
    except Exception:
        print("Paper lab failed. No broker order sent. Retained evidence may be incomplete.",
              file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
