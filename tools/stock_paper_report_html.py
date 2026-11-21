"""Generate private static HTML for an audited stock PAPER strategy run.

No server, JavaScript, CDN, account credentials or order execution.
The database is inspected read-only before generating any view.
"""

import argparse
import html
import json
import os
import sqlite3
import sys
from decimal import Context, Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_forward import audit_forward


def _escape(value):
    return html.escape(str(value), quote=True)


def generate(
    fixture, database, *, initial_cash, fee_pct="0.1", fast=5, slow=20, allocation_pct="20"
):
    audit = audit_forward(
        fixture,
        database,
        initial_cash=initial_cash,
        fee_pct=fee_pct,
        fast=fast,
        slow=slow,
        allocation_pct=allocation_pct,
    )
    with localcontext(Context(prec=28)):
        closes = [Decimal(str(row[4])) for row in fixture["candles"]]
        if not closes or any(not value.is_finite() or value <= 0 for value in closes):
            raise ValueError("Cannot chart invalid stock closing values")
        lo, hi = min(closes), max(closes)
        width, height = 900, 230
        span = hi - lo
        coords = []
        for i, value in enumerate(closes):
            ratio = float((value - lo) / span) if span else 0.5
            x = 16 + i * (width - 32) / max(1, len(closes) - 1)
            y = height - 16 - ratio * (height - 32)
            coords.append(f"{x:.2f},{y:.2f}")
    cards = [
        ("모드", "PAPER ONLY"),
        ("종목", audit["symbol"]),
        ("통화", audit["quote_currency"]),
        (
            "데이터",
            (
                "합성 데이터 · 실제 시세 아님"
                if fixture.get("provider") == "synthetic_fixture"
                else "공급된 분봉 데이터"
            ),
        ),
        ("입력 분봉", audit["bars"]),
        ("가상 체결", audit["simulated_fills"]),
        ("현금", audit["cash"]),
        ("평가금액", audit["marked_equity"]),
        ("모의 수익률(%)", audit["total_return_pct"]),
        ("보유 수량", json.dumps(audit["holdings"], ensure_ascii=False)),
        ("시세 SHA-256", audit["history_sha256"]),
    ]
    items = "".join(
        '<div class="metric"><small>'
        + _escape(label)
        + "</small><strong>"
        + _escape(value)
        + "</strong></div>"
        for label, value in cards
    )
    # No script, linked images, forms, websocket or external resources.
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>주식 모의매매 감사 리포트</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:#0b1220;color:#eef3fc;margin:0}}
main{{max-width:1120px;margin:auto;padding:32px 18px}}
h1{{font-size:26px;margin:0 0 10px}}
p{{color:#a9bad3;line-height:1.7}}
.warning{{background:#241a17;border:1px solid #895538;border-radius:10px;padding:14px;color:#ffdfc4}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:20px 0}}
.metric{{border:1px solid #2c3b56;border-radius:12px;background:#162036;padding:16px;min-width:0}}
.metric small{{display:block;color:#9aadc9;margin-bottom:9px}}
.metric strong{{font-size:16px;overflow-wrap:anywhere}}
section{{background:#162036;border-radius:12px;padding:16px;margin:16px 0}}
svg{{width:100%;height:auto;background:#0f1728;border-radius:10px}}
</style></head><body><main>
<h1>주식 자동 모의매매 리포트</h1>
<p>저장된 모의 거래 기록과 전략 재계산 결과를 대조한 보고서입니다.</p>
<div class="warning"><strong>실거래 아님.</strong> 실제 토스증권 계좌, 주문,
잔고 및 수익률을 나타내지 않습니다. 시세와 체결 가정이 포함된 연구용 모의 결과입니다.</div>
<div class="grid">{items}</div>
<section><h2>공급된 분봉 종가 추이</h2>
<svg role="img" aria-label="검증된 분봉 종가, 자산 곡선 아님"
viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
<polyline points="{" ".join(coords)}" fill="none"
stroke="#61d4b0" stroke-width="2.5" stroke-linecap="round"/></svg>
<p>이 그래프는 종가 추이이며 자산곡선이나 실제 거래 체결 호가가 아닙니다.</p></section>
<p>네트워크 요청 없음 · 외부 리소스 없음 · 거래 실행 기능 없음</p>
</main></body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--initial-cash", required=True)
    parser.add_argument("--fee-pct", default="0.1")
    parser.add_argument("--allocation-pct", default="20")
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    args = parser.parse_args(argv)
    paths = [args.fixture.resolve(), args.database.resolve(), args.output.resolve()]
    if len(set(paths)) != 3:
        parser.error("Output, paper fixture and ledger must differ")
    try:
        with args.fixture.open("rb") as stream:
            contents = stream.read(10_000_001)
        if len(contents) > 10_000_000:
            raise ValueError("Fixture too large")
        page = generate(
            json.loads(contents),
            args.database,
            initial_cash=args.initial_cash,
            fee_pct=args.fee_pct,
            fast=args.fast,
            slow=args.slow,
            allocation_pct=args.allocation_pct,
        )
        dest = args.output.resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(page)
            stream.flush()
            os.fsync(stream.fileno())
    except (ValueError, OSError, TypeError, KeyError, ArithmeticError, sqlite3.Error):
        print(
            "Paper stock HTML report refused invalid data; ledger was not changed.", file=sys.stderr
        )
        return 2
    print(str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
