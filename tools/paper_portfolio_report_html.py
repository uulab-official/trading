"""Standalone, private HTML dashboard of independently audited PAPER portfolios.

Input is a freshly generated offline audit JSON, not a live broker session.
No network requests, credentials, scripts, external resources, or order actions.
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path


def _escaped(value):
    return html.escape(str(value), quote=True)


def render(report):
    if (
        not isinstance(report, dict)
        or report.get("mode") != "paper"
        or report.get("live_orders_enabled") is not False
        or report.get("status") != "audited_offline_only"
        or report.get("cross_portfolio_total") is not None
    ):
        raise ValueError("Only independently audited offline paper status accepted")
    portfolios = report.get("portfolios")
    if not isinstance(portfolios, list) or not portfolios or len(portfolios) > 20:
        raise ValueError("Expected 1..20 audited paper portfolios")
    cards = []
    for item in portfolios:
        if not isinstance(item, dict) or item.get("type") not in ("crypto", "stock"):
            raise ValueError("Unrecognized paper portfolio type")
        if item.get("status") not in ("verified_offline_paper", "audited_stock_paper"):
            raise ValueError("Unverified paper portfolio cannot be rendered as audited")
        if item["type"] == "crypto":
            if item["status"] != "verified_offline_paper":
                raise ValueError("Crypto audit type/status contradiction")
            labels = (
                ("자산", item["symbol"]),
                ("시간봉", item["timeframe"]),
                ("완료 캔들", item["processed_bars"]),
                ("가상 거래", item["trade_count"]),
                ("일일 손실 제한", item["daily_loss_halted"]),
                ("최대 낙폭 제한", item["drawdown_halted"]),
            )
        else:
            if item["status"] != "audited_stock_paper":
                raise ValueError("Stock audit type/status contradiction")
            labels = (
                ("종목", item["symbol"]),
                ("통화", item["quote_currency"]),
                ("완료 캔들", item["processed_bars"]),
                ("가상 거래", item["trade_count"]),
                ("가상 현금", item["cash"]),
                ("가상 평가금액", item["marked_equity"]),
                ("모의 수익률 (%)", item["total_return_pct"]),
            )
        fields = "".join(
            "<div class='field'><span>"
            + _escaped(label)
            + "</span><strong>"
            + _escaped(value)
            + "</strong></div>"
            for label, value in labels
        )
        title = "암호화폐 Paper" if item["type"] == "crypto" else "주식 Paper"
        cards.append(
            "<article><h2>" + title + "</h2><p class='verified'>"
            "독립 재생 검증 · 과거 시점</p>" + fields + "</article>"
        )
    checked = _escaped(report.get("generated_at_utc", "unknown"))
    synthetic_notice = (
        '<div class="alert"><strong>합성 데이터 예제</strong> — 표시한 가격·거래·수익률은 '
        "저장소의 합성 입력으로 계산했습니다. 실제 시장 가격이나 투자 성과가 아닙니다.</div>"
        if report.get("provenance") == "bundled_synthetic_examples"
        else ""
    )
    return (
        """<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>유유랩 오프라인 모의매매 통합 조회</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#0b1220;color:#eef3fc;font-family:system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:32px 18px}
h1{font-size:26px;margin-bottom:5px}
p,small{color:#a9bad3;line-height:1.6}
.alert{background:#2b2118;color:#ffdbb4;border:1px solid #865631;
padding:16px;border-radius:12px;margin:22px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}
article{background:#172137;border:1px solid #2a3951;border-radius:14px;padding:22px}
h2{font-size:19px;margin:0}
.verified{font-size:13px;margin:5px 0 22px}
.field{display:flex;justify-content:space-between;gap:15px;border-top:1px solid #2a3951;
padding:12px 0;font-size:14px}
.field span{color:#a9bad3}.field strong{overflow-wrap:anywhere;text-align:right}
</style></head><body><main>
<h1>모의매매 통합 감사 대시보드</h1>
<p>오프라인에서 개별 검증된 코인·주식 원장 스냅샷 · 생성 시각: """
        + checked
        + """</p>
<div class="alert"><strong>실거래 아님</strong> — 실제 주문, 계좌,
실시간 시세 또는 현재 운영 상태를 표시하지 않습니다. KRW·USD·USDT는
환율·환산 규칙이 검증되지 않아 합산하지 않습니다.</div>
"""
        + synthetic_notice
        + """<section class="grid">"""
        + "".join(cards)
        + """</section>
<p><small>외부 API 호출 없음 · JavaScript 없음 · 거래 실행 기능 없음</small></p>
</main></body></html>"""
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.audit_json.resolve() == args.output.resolve():
        parser.error("Output cannot replace audit evidence")
    try:
        with args.audit_json.open("rb") as source:
            raw = source.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Audit report too large")
        page = render(json.loads(raw))
        target = args.output.resolve()
        if target.exists():
            raise FileExistsError("Do not replace an existing report")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(page)
            output.flush()
            os.fsync(output.fileno())
    except (ValueError, TypeError, OSError, KeyError):
        print(
            "Offline paper dashboard refused invalid or conflicting audit evidence.",
            file=sys.stderr,
        )
        return 2
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
