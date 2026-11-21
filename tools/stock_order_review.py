"""Read-only offline stock-order review with private JSON and Korean HTML reports."""

import html
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal
from stock_order_review import review_orders

from tools.stock_order_journal import SafeArgumentParser

MAX_OBSERVATION_BYTES = 2_000_000
SIDECARS = ("-wal", "-shm", "-journal")
KST = timezone(timedelta(hours=9))
COMPARISONS = {
    "not_supplied": "저널만 조회",
    "not_acknowledged": "접수 번호 미확인",
    "not_observed": "조회 내역 없음",
    "observations_equal": "비교한 관측값 일치",
    "different": "관측값 차이",
    "related_history": "관련 내역 확인",
    "out_of_scope": "다른 조회일",
    "needs_review": "확인이 필요한 항목",
}
STATES = {
    "reserved": "예약",
    "uncertain": "결과 불명",
    "accepted": "접수",
    "partially_filled": "부분 체결",
    "cancel_pending": "취소 대기",
    "filled": "전량 체결",
    "canceled": "취소",
    "rejected": "거절",
}
FIELDS = {
    "symbol": "종목",
    "side": "매수·매도",
    "order_division": "주문 구분",
    "quantity": "주문 수량",
    "limit_price": "지정가",
    "filled_quantity": "체결 수량",
    "gross_krw": "누적 체결 대금",
    "working_quantity": "유효·잔여 수량",
}
FINDINGS = {
    "unresolved_local_order": "미해결 주문이 남아 있습니다. 접수·체결·취소 근거를 확인하세요.",
    "local_order_not_acknowledged": "로컬 주문에 증권사 접수 번호가 없습니다. 조회값으로 주문을 추정하지 않았습니다.",
    "local_order_not_observed": "해당 조회일 자료에서 로컬 주문번호를 찾지 못했습니다.",
    "order_fields_differ": "로컬 기록과 조회 관측값에 차이가 있습니다. 아래 항목을 확인하세요.",
    "related_order_history": "원주문 참조가 있는 관련 내역입니다. 주문 간 관계를 별도로 확인하세요.",
    "unmatched_observation": "로컬 주문번호와 연결되지 않는 조회 내역이 있습니다.",
    "observation_stale": "조회 자료가 설정한 시간 기준보다 오래됐습니다. 최신 자료로 다시 점검하세요.",
    "observation_future": "조회 시각이 점검 시각보다 미래입니다. 시계와 자료의 시각을 확인하세요.",
}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate observation JSON key")
        value[key] = item
    return value


def _integer(value):
    if len(value) > 128:
        raise ValueError("Oversized observation JSON number")
    return int(value)


def _reject_float(_value):
    raise ValueError("Observation JSON requires integer metadata and string amounts")


def read_observations(path):
    """Read a bounded regular file; normalized observations contain no JSON floats."""
    target = Path(path)
    if not stat.S_ISREG(target.lstat().st_mode):
        raise ValueError("Observation input must be a regular file without a final symlink")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_size > MAX_OBSERVATION_BYTES:
            raise ValueError("Observation file type or size refused")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            raw = source.read(MAX_OBSERVATION_BYTES + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_OBSERVATION_BYTES:
        raise ValueError("Observation file exceeds configured bound")
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_int=_integer,
        parse_float=_reject_float,
        parse_constant=_reject_float,
    )


def build_review(database, *, account_ref, observations=None, now=None, max_age_seconds=300):
    journal = StockOrderJournal.open_existing(database, account_ref=account_ref, read_only=True)
    value = None if observations is None else read_observations(observations)
    return review_orders(journal, value, now=now, max_age_seconds=max_age_seconds)


def _target(path):
    value = Path(path).absolute()
    # Resolve existing parent aliases, but never follow a final output symlink.
    return value.parent.resolve(strict=True) / value.name


def _same_family(first, second):
    return first == second or any(
        str(first) + suffix == str(second) or str(second) + suffix == str(first)
        for suffix in SIDECARS
    )


def validate_destinations(inputs, outputs):
    sources = [Path(path).resolve() for path in inputs if path is not None]
    targets = [_target(path) for path in outputs]
    for index, target in enumerate(targets):
        if any(_same_family(target, source) for source in sources + targets[:index]):
            raise ValueError("Report destination conflicts with a source or reserved sibling")
    return targets


def _require_owned(target, identity):
    current = target.lstat()
    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
        raise ValueError("Reserved report path was replaced")


def write_reports(outputs):
    """Reserve every private output before writing; preserve concurrent replacements.

    Outputs form one handled-failure group, not an atomic multi-file publication.
    Consume them after successful command completion. Hard kills may leave files.
    """
    targets = validate_destinations([], [path for path, _ in outputs])
    encoded = [text.encode("utf-8") for _, text in outputs]
    owned = []
    closed = set()
    close_identities = {}
    try:
        for target, data in zip(targets, encoded):
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            try:
                identity = os.fstat(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            owned.append((target, descriptor, identity, data))
        for target, descriptor, identity, data in owned:
            _require_owned(target, identity)
            remaining = memoryview(data)
            while remaining:
                count = os.write(descriptor, remaining)
                if count <= 0:
                    raise OSError("Report write made no progress")
                remaining = remaining[count:]
            os.fsync(descriptor)
            _require_owned(target, identity)
        # Keep descriptors open through the final checks so an unlinked inode
        # cannot be reused and mistaken for the owned file during cleanup.
        for target, _descriptor, identity, _data in owned:
            _require_owned(target, identity)
        # Include descriptor-close errors in the failure group. Mark a close as
        # attempted before invoking it: an OS close can release the descriptor
        # and still report an error, so retrying could close a reused number.
        for _target_path, descriptor, _identity, _data in owned:
            close_identities[descriptor] = os.fstat(descriptor)
            closed.add(descriptor)
            os.close(descriptor)
    except BaseException:
        for target, descriptor, identity, _data in owned:
            try:
                _require_owned(target, identity)
                if descriptor in closed:
                    # Once closed, the inode is no longer pinned. Require its
                    # final metadata as well before removing an owned name.
                    current, final = target.lstat(), close_identities[descriptor]
                    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                    if any(getattr(current, field) != getattr(final, field) for field in fields):
                        continue
                target.unlink()
            except (OSError, ValueError):
                pass
        raise
    finally:
        for _target_path, descriptor, _identity, _data in owned:
            if descriptor not in closed:
                closed.add(descriptor)
                try:
                    os.close(descriptor)
                except OSError:
                    # The primary failure already triggered owned-path cleanup.
                    # Continue closing the remaining descriptors, never retry.
                    pass


def _text(value):
    return html.escape(str(value), quote=True)


def _number(value):
    text = str(value)
    if len(text) <= 100 and text.lstrip("-").isascii() and text.lstrip("-").isdigit():
        return _text(format(int(text), ","))
    return _text(text)


def _time(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Report timestamp must be aware")
    return _text(moment.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"))


def _paired(local, observed):
    return (
        "<strong>"
        + _number(local)
        + "</strong><span class='observed'>조회 "
        + ("—" if observed is None else _number(observed))
        + "</span>"
    )


def render_review_html(report):
    if (
        type(report) is not dict
        or report.get("kind") != "stock_order_review"
        or report.get("schema_version") != 1
        or report.get("mode") != "paper"
        or any(
            report.get(key) is not False
            for key in (
                "live_orders_enabled",
                "account_binding_verified",
                "economic_reconciliation_verified",
                "lifecycle_confirmation_verified",
                "safe_to_submit",
            )
        )
    ):
        raise ValueError("Only unverified paper review reports can be rendered")
    summary = report["summary"]
    attention = report["needs_attention"]
    headline = COMPARISONS[report["comparison_status"]]
    if report["comparison_status"] == "observations_equal" and summary["scoped_order_count"] == 0:
        headline = "비교 대상 없음"
    synthetic_notice = (
        "<div class='notice attention'><strong>합성 데이터 예제</strong>"
        "가격·주문·조회 내역은 테스트용으로 만든 값입니다.</div>"
        if report.get("example_provenance") == "synthetic_stock_order_review"
        else ""
    )
    cards = "".join(
        "<article class='metric'><span>"
        + label
        + "</span><strong>"
        + _number(summary[key])
        + "</strong></article>"
        for label, key in (
            ("기록된 주문", "order_count"),
            ("미해결 주문", "unresolved_order_count"),
            ("관측값이 다른 주문", "different_order_count"),
            ("연결 안 된 조회 내역", "unmatched_observation_count"),
        )
    )
    rows, differences = [], []
    for item in report["orders"]:
        local, observed = item["local"], item["observed"]
        changed = {difference["field"] for difference in item["differences"]}
        values = []
        for local_key, observed_key in (
            ("quantity", "ord_qty"),
            ("filled_quantity", "tot_ccld_qty"),
            ("working_quantity", "rmn_qty"),
            ("gross_krw", "tot_ccld_amt"),
        ):
            values.append(
                "<td"
                + (" class='changed'" if local_key in changed else "")
                + ">"
                + _paired(local[local_key], None if observed is None else observed[observed_key])
                + "</td>"
            )
        side = "매수" if local["side"] == "buy" else "매도"
        rows.append(
            "<tr><td class='identity'><strong>"
            + _text(local["client_order_id"])
            + "</strong><span>"
            + _text(local["symbol"])
            + " · "
            + side
            + " · "
            + _text(local["order_date"])
            + "</span></td><td>"
            + _text(STATES.get(local["status"], local["status"]))
            + "</td><td><span class='pill'>"
            + _text(COMPARISONS[item["comparison"]])
            + "</span></td>"
            + "".join(values)
            + "</tr>"
        )
        for difference in item["differences"]:
            differences.append(
                "<li><strong>"
                + _text(local["client_order_id"])
                + " · "
                + _text(FIELDS.get(difference["field"], difference["field"]))
                + "</strong><span>로컬 "
                + _text(difference["local"])
                + " / 조회 "
                + _text(difference["observed"])
                + "</span></li>"
            )
    if not rows:
        rows.append("<tr><td colspan='7' class='empty'>기록된 주문이 없습니다.</td></tr>")
    findings = []
    for finding in report["findings"]:
        identity = finding.get("client_order_id") or finding.get("broker_order_id")
        label = ("<strong>" + _text(identity) + "</strong> ") if identity else ""
        findings.append(
            "<li>" + label + _text(FINDINGS.get(finding["code"], finding["code"])) + "</li>"
        )
    finding_section = (
        "<ul class='findings'>" + "".join(findings) + "</ul>"
        if findings
        else "<p class='empty'>현재 점검 범위에서 추가 확인 항목이 없습니다.</p>"
    )
    difference_section = (
        (
            "<section class='panel'><h2>항목별 차이</h2><ul class='differences'>"
            + "".join(differences)
            + "</ul></section>"
        )
        if differences
        else ""
    )
    unmatched_rows = "".join(
        "<tr><td>"
        + _text(row["odno"])
        + "</td><td>"
        + _text(row["pdno"])
        + "</td><td>"
        + _number(row["ord_qty"])
        + "</td><td>"
        + _number(row["tot_ccld_qty"])
        + "</td><td>"
        + _number(row["tot_ccld_amt"])
        + "</td></tr>"
        for row in report["unmatched_observations"]
    )
    unmatched_section = (
        (
            "<section class='panel'><h2>연결 안 된 조회 내역</h2>"
            "<p>누락·거절·추가 체결로 단정하지 않습니다. 주문 관계를 확인하세요.</p>"
            "<div class='table-wrap'><table><thead><tr><th>주문번호</th><th>종목</th>"
            "<th>주문 수량</th><th>누적 체결</th><th>대금 (원)</th></tr></thead><tbody>"
            + unmatched_rows
            + "</tbody></table></div></section>"
        )
        if unmatched_rows
        else ""
    )
    observation = report["observation_summary"]
    if observation is None:
        observation_text = "조회 자료 없음 · 로컬 저널 상태만 표시합니다."
    else:
        freshness = {
            "fresh": "설정 시간 이내",
            "stale": "오래된 조회 자료",
            "future": "미래 조회 시각",
        }
        observation_text = (
            "조회일 "
            + _text(observation["inquiry_date"])
            + " · 수집 시작 "
            + _time(observation["observed_at_utc"])
            + "<br>"
            + freshness[observation["freshness"]]
            + " · "
            + _number(observation["row_count"])
            + "건 / "
            + _number(observation["pages_fetched"])
            + "페이지"
        )
    return (
        """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>유유랩 주식 주문 점검 · PAPER</title>
<style>
:root{color-scheme:light;--ink:#172638;--muted:#66758a;--line:#e4e9f1;--blue:#245bec}
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:var(--ink);font:14px/1.6 system-ui,-apple-system,sans-serif}
main{max-width:1240px;margin:auto;padding:34px 24px 48px}.brand{font-size:12px;letter-spacing:.1em;font-weight:800;color:var(--blue)}
header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin:20px 0}h1{font-size:29px;letter-spacing:-.04em;margin:0 0 5px}
h2{font-size:17px;margin:0 0 14px}p{margin:6px 0;color:var(--muted)}.badge,.pill{display:inline-block;border-radius:8px;background:#eef3fd;color:#34527c;padding:4px 9px;font-size:12px;white-space:nowrap}
.badge{font-weight:700;padding:8px 12px}.attention{background:#fff1d8;color:#8b5b09}.notice{padding:15px 18px;background:#eaf0fe;border:1px solid #d9e4ff;border-radius:12px;color:#315183;margin-bottom:22px}
.notice strong{display:block;color:#234475}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0}
.metric,.panel{background:white;border:1px solid var(--line);border-radius:14px}.metric{padding:18px 20px}.metric span{color:var(--muted);font-size:13px}.metric>strong{display:block;font-size:30px;line-height:1.5;font-weight:750;font-variant-numeric:tabular-nums}
.panel{margin-top:18px;padding:22px}.metadata{display:grid;grid-template-columns:1fr 2fr;gap:20px}.meta-label{display:block;color:var(--muted);font-size:12px;margin-bottom:4px}.metadata strong{overflow-wrap:anywhere}
.table-wrap{overflow:auto;margin:0 -4px}table{border-collapse:collapse;width:100%;text-align:left;font-variant-numeric:tabular-nums}th{color:var(--muted);font-size:12px;font-weight:600;background:#f8fafc;white-space:nowrap}
th,td{padding:13px 11px;border-bottom:1px solid var(--line);vertical-align:middle}td{white-space:nowrap;font-size:13px}td.identity{min-width:190px;white-space:normal;overflow-wrap:anywhere}.identity>span,.observed{display:block;color:var(--muted);font-size:11px;margin-top:3px}.changed{background:#fff8e9;color:#8b5b09}
.empty{padding:10px 0;color:var(--muted)}.findings{margin:0;padding-left:20px}.findings li{margin:9px 0}.differences{margin:0;padding:0;list-style:none}.differences li{display:flex;justify-content:space-between;gap:20px;padding:11px 0;border-bottom:1px solid var(--line)}.differences span{color:#8b5b09}
.footnote{display:flex;justify-content:space-between;gap:20px;margin-top:16px;font-size:12px;color:var(--muted)}.footnote strong{color:var(--ink)}footer{font-size:12px;color:var(--muted);margin-top:24px}.table-wrap:focus{outline:2px solid var(--blue);outline-offset:3px}
@media(max-width:720px){main{padding:22px 14px}header{display:block}.badge{margin-top:12px}h1{font-size:25px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{padding:14px}.metric>strong{font-size:26px}.metadata{grid-template-columns:1fr}.panel{padding:17px}.footnote,.differences li{display:block}.differences span{display:block}}
@media print{body{background:white}main{padding:0}.panel,.metric{break-inside:avoid}.table-wrap{overflow:visible}header{margin-top:10px}}
</style></head><body><main><div class="brand">UULAB / TRADING</div>
<header><div><h1>주식 주문 점검</h1><p>로컬 저널과 저장된 주문 관측값을 함께 확인합니다.</p></div>
<span class="badge"""
        + (" attention" if attention else "")
        + '">PAPER · '
        + _text(headline)
        + "</span></header>"
        + synthetic_notice
        + """
<div class="notice"><strong>계좌 연결과 개별 체결 증명은 미검증 상태입니다.</strong>
관측값이 같아도 실제 체결·비용·잔고를 검증한 것은 아닙니다. 이 보고서는 주문을 전송하거나 미해결 상태를 해제하지 않습니다.</div>
<section class="panel metadata"><div><span class="meta-label">로컬 계정 별칭</span><strong>"""
        + _text(report["account_ref"])
        + "</strong><p>점검 시각 "
        + _time(report["generated_at_utc"])
        + "</p></div><div><span class='meta-label'>조회 자료</span>"
        + observation_text
        + "</div></section><section class='metrics'>"
        + cards
        + "</section>"
        + "<section class='panel'><h2>주문별 비교</h2><p>범위 내 주문 "
        + _number(summary["scoped_order_count"])
        + "개 · 연결된 조회 "
        + _number(summary["matched_order_count"])
        + "건</p>"
        "<p>굵은 숫자는 로컬 기록, 아래 숫자는 조회 관측값입니다. 수량 단위는 주입니다.</p>"
        "<div class='table-wrap' role='region' aria-label='주문별 비교 표' tabindex='0'><table><thead><tr>"
        "<th>주문</th><th>로컬 상태</th><th>비교 결과</th><th>주문 수량</th><th>체결 수량</th>"
        "<th>유효·잔여 수량</th><th>누적 대금 (원)</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div><div class='footnote'><span>로컬 현금 순변화 <strong>"
        + _number(report["journal"]["cash_delta_krw"])
        + "원</strong> · 수수료·세금 반영</span>"
        "<span>계좌 잔고나 투자 성과를 나타내지 않습니다.</span></div></section>"
        + difference_section
        + "<section class='panel'><h2>확인할 항목</h2>"
        + finding_section
        + "</section>"
        + unmatched_section
        + "<footer>저장된 시점의 로컬 점검 보고서 · 계정 별칭과 주문 내역을 포함하므로 비공개로 보관하세요.</footer>"
        "</main></body></html>"
    )


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--account-ref", required=True)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-html", type=Path)
    try:
        args = parser.parse_args(argv)
        requested = [path for path in (args.output_json, args.output_html) if path is not None]
        validate_destinations([args.database, args.observations], requested)
        report = build_review(
            args.database,
            account_ref=args.account_ref,
            observations=args.observations,
            max_age_seconds=args.max_age_seconds,
        )
        serialized = (
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
        outputs = []
        if args.output_json is not None:
            outputs.append((args.output_json, serialized))
        if args.output_html is not None:
            outputs.append((args.output_html, render_review_html(report)))
        write_reports(outputs)
    except Exception:
        print(
            "Offline stock order review refused. No journal event or broker order was changed.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 3 if report["needs_attention"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
