"""Local, read-only operator dashboard for the bounded Binance paper session."""

import argparse
import html
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import binance_paper_forward_supervisor as supervisor
from tools import binance_public_archive as archive

MAX_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def require(condition, reason):
    if not condition:
        raise supervisor.SupervisorError(reason)


def integer(value, *, minimum=None, maximum=None):
    require(type(value) is int, "invalid_report_integer")
    require(minimum is None or value >= minimum, "invalid_report_integer")
    require(maximum is None or value <= maximum, "invalid_report_integer")
    return value


def checked_digest(value):
    require(isinstance(value, str) and supervisor.DIGEST.fullmatch(value), "invalid_report_digest")
    return value


def format_atoms(value, scale):
    integer(value)
    integer(scale, minimum=0, maximum=18)
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), 10**scale)
    return f"{sign}{whole:,}" if scale == 0 else f"{sign}{whole:,}.{fraction:0{scale}d}"


def format_bps(value):
    return format_atoms(value, 2) + "%"


def check_head(path, expected):
    """Check persisted metadata and row count only; not an economic replay."""
    with archive.connection(path, readonly=True) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT ledger_id, head_seq, head_hash, schema_version FROM paper_meta LIMIT 2"
        ).fetchall()
        require(len(rows) == 1, "missing_account_metadata")
        row = rows[0]
        count = db.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0]
        require(
            row["schema_version"] == 1
            and row["ledger_id"] == expected["ledger_id"]
            and row["head_seq"] == expected["head"]["sequence"]
            and row["head_hash"] == expected["head"]["hash"]
            and count == row["head_seq"],
            "account_head_mismatch",
        )


def report_rows(session, report, identity):
    require(isinstance(report, dict), "invalid_report")
    require(report["mode"] == "offline_paper_forward", "invalid_report_mode")
    require(report["live_orders_enabled"] is False, "invalid_execution_flag")
    require(report["source"] == archive.SOURCE, "invalid_report_source")
    require(
        report["source_id"] == archive.digest("paper-forward-source-v1", identity),
        "source_identity_mismatch",
    )
    session_id = checked_digest(report["session_sha256"])
    checked_digest(report["archive_head_sha256"])
    count = integer(report["input_bar_count"], minimum=2, maximum=archive.MAX_FORWARD_BARS)
    bar_limit = integer(report["max_bar_count"], minimum=count, maximum=archive.MAX_FORWARD_BARS)
    require(bar_limit in (240, archive.MAX_FORWARD_BARS), "invalid_bar_capacity")
    event_limit = integer(report.get("max_event_count", 2048), minimum=1)
    require(event_limit in (2048, supervisor.MAX_EVENT_COUNT), "invalid_event_capacity")
    replay = report["replay"]
    checked_digest(replay["scenario_sha256"])
    require(replay["mode"] == "offline_paper_forward_reconstruction", "invalid_replay_mode")
    step = archive.INTERVALS[identity["interval"]]
    require(
        replay["evaluation_end_ms"] == session.start_ms + count * step - 1,
        "invalid_report_end",
    )
    require(
        replay["base_asset"] == session.template["market"]["base_asset"]
        and replay["quote_asset"] == session.template["account"]["quote_asset"]
        and replay["quote_scale"] == session.template["quote_scale"],
        "report_market_mismatch",
    )
    scale = integer(replay["quote_scale"], minimum=0, maximum=18)
    expected = [item["id"] for item in session.template["strategies"]]
    require(
        1 <= len(expected) <= 8
        and all(isinstance(item, str) and MAX_ID.fullmatch(item) for item in expected)
        and len(expected) == len(set(item.lower() for item in expected)),
        "invalid_strategy_identity",
    )
    require(
        [item["id"] for item in replay["results"]] == expected
        and [item["strategy_id"] for item in report["accounts"]] == expected,
        "strategy_identity_mismatch",
    )
    rows = []
    for result, item in zip(replay["results"], report["accounts"]):
        checkpoint = item["checkpoint"]
        sequence = integer(checkpoint["head"]["sequence"], minimum=1, maximum=event_limit)
        checked_digest(checkpoint["head"]["hash"])
        require(
            checkpoint["ledger_id"] == f"forward-{session_id}-{item['strategy_id']}"
            and result["journal_events"] == sequence
            and item["remaining_event_capacity"] == event_limit - sequence,
            "report_checkpoint_mismatch",
        )
        require(type(result["buys_halted"]) is bool, "invalid_risk_flag")
        initial = integer(result["initial_cash_atoms"], minimum=1)
        equity = integer(result["final_equity_atoms"], minimum=0)
        realized = integer(result["realized_pnl_atoms"])
        unrealized = integer(result["unrealized_pnl_atoms"])
        require(equity == initial + realized + unrealized, "inconsistent_equity")
        drawdown = integer(result["max_sampled_drawdown_bps"], minimum=0, maximum=10_000)
        check_head(session.directory / f"forward-{item['strategy_id']}.sqlite", checkpoint)
        rows.append(
            {
                "id": item["strategy_id"],
                "equity": format_atoms(equity, scale),
                "cash": format_atoms(integer(result["final_cash_atoms"], minimum=0), scale),
                "realized_pnl": format_atoms(realized, scale),
                "unrealized_pnl": format_atoms(unrealized, scale),
                "return_pct": format_bps(integer(result["return_bps"])),
                "drawdown_pct": format_bps(drawdown),
                "fees": format_atoms(integer(result["fees_atoms"], minimum=0), scale),
                "fills": integer(result["fills"], minimum=0),
                "remaining_lots": integer(result["remaining_lots"], minimum=0),
                "buys_halted": result["buys_halted"],
                "events": sequence,
                "event_limit": event_limit,
                "checkpoint_sha256": checkpoint["head"]["hash"],
            }
        )
    return rows, count, bar_limit


def collect(session, *, clock=archive.now_ms, max_age_ms=180_000, deep_audit=False):
    now = clock()
    result = {
        "mode": "paper_operator_dashboard",
        "live_orders_enabled": False,
        "as_of_ms": now,
        "ready": False,
        "status": "unavailable",
        "verification": "not_verified",
        "archive": None,
        "supervisor": None,
        "strategies": [],
        "input_bar_count": None,
        "max_bar_count": None,
    }
    try:
        capture = archive.status(session.archive_path, clock=clock, max_age_ms=max_age_ms)
        state = supervisor.read_state(session)
        identity = capture["identity"]
        result["archive"] = {
            key: capture[key]
            for key in (
                "status",
                "capture_current",
                "count",
                "last_open_ms",
                "last_success_ms",
                "head_sha256",
                "consecutive_failures",
            )
        }
        result["archive"]["symbol"] = identity["symbol"]
        result["archive"]["interval"] = identity["interval"]
        result["supervisor"] = {
            key: state[key]
            for key in (
                "status",
                "reason",
                "last_success_ms",
                "updated_at_ms",
                "consecutive_failures",
            )
        }
        report = state["last_report"]
        if report is None:
            result["status"] = "waiting_for_report"
            return result
        rows, count, limit = report_rows(session, report, identity)
        result.update(
            strategies=rows,
            input_bar_count=count,
            max_bar_count=limit,
            verification="persisted_head_match_only",
            quote_asset=report["replay"]["quote_asset"],
            report_archive_head=report["archive_head_sha256"],
            session_sha256=report["session_sha256"],
        )
        age = now - state["last_success_ms"]
        result["report_age_ms"] = age
        if state["status"] == "halted" or os.path.lexists(session.stop_path):
            result["status"] = "halted"
        elif not capture["capture_current"]:
            result["status"] = "archive_not_current"
        elif state["status"] != "paper_current":
            result["status"] = "supervisor_not_current"
        elif not 0 <= age <= max_age_ms:
            result["status"] = "report_stale"
        elif report["archive_head_sha256"] != capture["head_sha256"]:
            result["status"] = "awaiting_paper_update"
        elif any(row["buys_halted"] for row in rows):
            result["status"] = "risk_blocked"
        elif count >= limit or any(row["events"] >= row["event_limit"] for row in rows):
            result["status"] = "capacity_reached"
        else:
            result["status"] = "reported_current"
            result["ready"] = True
        # Explicit one-shot verification only. Serving a page never launches
        # the binary, creates an account or performs simulated order writes.
        if deep_audit and result["status"] in (
            "reported_current",
            "risk_blocked",
            "capacity_reached",
        ):
            envelope = archive.export_forward(
                session.archive_path,
                session.start_ms,
                session.template,
                expected_head=capture["head_sha256"],
                max_age_ms=max_age_ms,
                clock=clock,
            )
            envelope["trusted_checkpoints"] = [item["checkpoint"] for item in report["accounts"]]
            audited = supervisor.invoke_paper_binary(session, envelope, False, audit_only=True)
            supervisor.verify_report(envelope, audited, report)
            require(audited["replay"] == report["replay"], "deep_replay_mismatch")
            require(
                [item["checkpoint"] for item in audited["accounts"]]
                == [item["checkpoint"] for item in report["accounts"]],
                "deep_checkpoint_mismatch",
            )
            result["verification"] = "independently_replayed_at_request"
        return result
    except (
        supervisor.SupervisorError,
        archive.ArchiveError,
        OSError,
        sqlite3.Error,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
    ):
        result.update(ready=False, status="audit_or_state_refused", verification="not_verified")
        result["strategies"] = []
        return result


def utc_label(stamp):
    if stamp is None:
        return "—"
    try:
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except (ValueError, OSError, OverflowError):
        return "—"


def page(data, *, refresh=False):
    esc = lambda value: html.escape(str(value), quote=True)
    status = esc(data["status"])
    market = data.get("archive") or {}
    supervisor_state = data.get("supervisor") or {}
    quote = esc(data.get("quote_asset", "—"))
    rows = []
    for item in data["strategies"]:
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{esc(item[key])}</td>"
                for key in (
                    "id",
                    "equity",
                    "cash",
                    "realized_pnl",
                    "unrealized_pnl",
                    "return_pct",
                    "drawdown_pct",
                    "fees",
                    "fills",
                    "remaining_lots",
                )
            )
            + f'<td>{esc("차단" if item["buys_halted"] else "미차단")}</td>'
            + f'<td>{item["events"]:,} / {item["event_limit"]:,}</td>'
            + f'<td><code>{esc(item["checkpoint_sha256"][:12])}</code></td></tr>'
        )
    table = "".join(rows) or '<tr><td colspan="13">검증 가능한 보고서 없음</td></tr>'
    refresh_tag = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
{refresh_tag}<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Operations</title><style>
:root{{color-scheme:light;--line:#dbe2ea;--ink:#172536;--muted:#56687a}}
*{{box-sizing:border-box}}body{{font:14px system-ui,sans-serif;margin:0;background:#f5f7fa;color:var(--ink)}}
main{{max-width:1440px;padding:32px 22px;margin:auto}}h1{{font-size:24px;margin:0 0 6px}}
h2{{font-size:17px;margin:0 0 12px}}p{{line-height:1.6}}small{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}
article{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:14px}}
.cards article{{margin-top:0}}strong{{font-size:21px}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;width:100%;white-space:nowrap;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:12px;border-bottom:1px solid var(--line)}}th{{color:var(--muted)}}
code{{overflow-wrap:anywhere}}.notice{{border-left:3px solid #8a99aa;padding-left:12px}}
</style></head><body><main><h1>모의매매 운영 센터</h1>
<p class="notice">실거래 비활성 · 로컬 읽기 전용 · 과거 마감 봉 기반 모의 결과입니다.
실제 체결, 수익 보장, 거래소 계좌 상태를 의미하지 않습니다.</p>
<div class="cards"><article><small>운영 상태</small><p><strong>{status}</strong></p>
<small>{esc(data["verification"])}</small></article>
<article><small>공개 시세 수집</small><p><strong>{esc(market.get("status", "—"))}</strong></p>
<small>{esc(market.get("symbol", "—"))} · {esc(market.get("interval", "—"))}</small></article>
<article><small>감독기 상태</small><p><strong>{esc(supervisor_state.get("status", "—"))}</strong></p>
<small>연속 오류 {esc(supervisor_state.get("consecutive_failures", "—"))}</small></article>
<article><small>세션 캔들</small><p><strong>{esc(data["input_bar_count"])} / {esc(data["max_bar_count"])}</strong></p>
<small>고정 시작점 · 자동 초기화 없음</small></article></div>
<article><h2>계좌별 성과 및 위험 · {quote}</h2><div class="scroll"><table>
<thead><tr><th>전략</th><th>순자산</th><th>현금</th><th>실현 손익</th><th>미실현 손익</th>
<th>수익률</th><th>표본 MDD</th><th>수수료</th><th>체결</th><th>보유 lot</th>
<th>매수 제한</th><th>원장 용량</th><th>체크포인트</th></tr></thead><tbody>{table}</tbody></table></div></article>
<article><h2>데이터 및 감사 상태</h2><p>마지막 캔들 시작: {esc(utc_label(market.get("last_open_ms")))}<br>
마지막 모의 보고서: {esc(utc_label(supervisor_state.get("last_success_ms")))}<br>
조회 시각: {esc(utc_label(data["as_of_ms"]))}</p>
<p><small>기본 조회는 원장 head와 행 수 일치 검사입니다. 전체 경제 원장 재감사는 별도
--deep-audit 명령으로 수행합니다. 각 계좌의 통화는 합산하지 않습니다.</small></p>
<code>{esc(market.get("head_sha256", "—"))}</code></article></main></body></html>"""


class LocalServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def serve(session, port, max_age_ms):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # Prevent DNS rebinding and reject arbitrary path/file access.
            if self.headers.get_all("Host") != [f"127.0.0.1:{self.server.server_port}"]:
                self.send_error(403)
                return
            if self.path not in ("/", "/api/status", "/healthz"):
                self.send_error(404)
                return
            data = collect(session, max_age_ms=max_age_ms)
            if self.path == "/":
                content = page(data, refresh=True).encode("utf-8")
                content_type = "text/html; charset=utf-8"
                code = 200
            else:
                content = archive.canonical(data).encode("ascii")
                content_type = "application/json"
                code = 503 if self.path == "/healthz" and not data["ready"] else 200
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)

    with LocalServer(("127.0.0.1", port), Handler) as server:
        print(f"Paper dashboard: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--start-open-ms", type=int, required=True)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--max-age-ms", type=int, default=180_000)
    parser.add_argument("--format", choices=("json", "html"), default="json")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--deep-audit", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(1_000 <= args.max_age_ms <= 86_400_000, "invalid_age")
        require(1024 <= args.port <= 65535, "invalid_port")
        require(not (args.serve and args.deep_audit), "deep_audit_is_one_shot")
        session = supervisor.make_session(
            args.archive,
            args.session_dir,
            args.binary,
            args.template,
            args.start_open_ms,
            args.max_failures,
        )
        if args.serve:
            serve(session, args.port, args.max_age_ms)
            return 0
        data = collect(session, max_age_ms=args.max_age_ms, deep_audit=args.deep_audit)
        print(page(data) if args.format == "html" else archive.canonical(data))
        return 0 if data["ready"] else 2
    except KeyboardInterrupt:
        return 130
    except (
        supervisor.SupervisorError,
        archive.ArchiveError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ):
        parser.exit(2, "Paper operator dashboard refused; inspect local configuration.\n")


if __name__ == "__main__":
    raise SystemExit(main())
