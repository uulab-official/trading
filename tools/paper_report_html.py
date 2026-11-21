"""Generate a standalone, local-only HTML view of an audited offline paper run.

No HTTP server, external scripts, credentials, network calls, or live execution.
"""

import argparse
import hashlib
import html
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.paper_checkpoint_audit import audit


def _money(raw):
    try:
        value = Decimal(str(raw))
        if not value.is_finite():
            raise ValueError("Non-finite economic number")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Malformed economic value") from exc
    return format(value, "f")


def _text(value):
    return html.escape(str(value), quote=True)


def _chart(candles, width=920, height=220):
    # Price path depicts fixture close prices, NOT realized profit or portfolio equity.
    prices = [Decimal(str(row[4])) for row in candles]
    if any(not p.is_finite() or p <= 0 for p in prices):
        raise ValueError("Invalid fixture close")
    lo, hi = min(prices), max(prices)
    span = hi - lo
    points = []
    for i, price in enumerate(prices):
        x = 16 + i * (width - 32) / max(len(prices) - 1, 1)
        y = height - 16 - float((price - lo) / span if span else Decimal("0.5")) * (height - 32)
        points.append(f"{x:.2f},{y:.2f}")
    return (
        f'<svg role="img" aria-label="Offline fixture close-price history (not portfolio equity)" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#4fd1c5" '
        f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def generate(fixture_path: Path, checkpoint_path: Path):
    verified = audit(fixture_path, checkpoint_path)
    raw_checkpoint = checkpoint_path.read_bytes()
    if hashlib.sha256(raw_checkpoint).hexdigest() != verified["checkpoint_sha256"]:
        raise ValueError("Checkpoint changed after audit; retry only when idle")
    state = json.loads(raw_checkpoint)
    raw_fixture = fixture_path.read_bytes()
    if hashlib.sha256(raw_fixture).hexdigest() != verified["fixture_sha256"]:
        raise ValueError("Fixture changed after audit; retry only when idle")
    fixture = json.loads(raw_fixture)
    bars = fixture["candles"][: verified["processed_bars"]]
    if not bars:
        raise ValueError("No verified candles")
    config = state["config"]
    book = state["exchange"]
    quote = config["symbol"].split("/")[1]
    balances = book["balances"]
    cash = Decimal(_money(balances[quote]))
    last_close = Decimal(_money(bars[-1][4]))
    base = config["symbol"].split("/")[0]
    holding = Decimal(_money(balances.get(base, "0")))
    equity = cash + holding * last_close
    starting = Decimal(_money(config["initial_balance"]))
    # The current offline fixture runner supports exactly one spot symbol.
    if any(
        asset not in (base, quote) and Decimal(_money(amount)) != 0
        for asset, amount in balances.items()
    ):
        raise ValueError("Unexpected holding in single-symbol paper checkpoint")
    return_pct = (equity - starting) / starting * 100
    trades = book["trades"]
    if not isinstance(trades, list):
        raise ValueError("Malformed trade list")
    trade_rows = []
    for trade in trades:
        when = datetime.fromtimestamp(trade["timestamp"] / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        trade_rows.append(
            "<tr>"
            + "".join(
                f"<td>{_text(v)}</td>"
                for v in (
                    when,
                    trade["side"],
                    _money(trade["amount"]),
                    _money(trade["average"]),
                    _money(trade["fee"]["cost"]),
                    trade["clientOrderId"],
                )
            )
            + "</tr>"
        )
    metrics = [
        ("Mode", "PAPER ONLY"),
        ("Symbol", config["symbol"]),
        ("Bars replayed", verified["processed_bars"]),
        (f"Cash ({quote})", format(cash, "f")),
        (f"Holdings ({base})", format(holding, "f")),
        (f"Marked equity ({quote})", format(equity, "f")),
        ("Return (synthetic, %)", f"{return_pct:.4f}"),
        (f"Paid fees ({quote})", _money(book["total_fees"])),
        ("Daily loss entry block", str(state["daily_loss_halted"])),
        ("Drawdown entry block", str(state["drawdown_halted"])),
        (
            "Pending signal",
            state["pending_signal"]["action"] if state["pending_signal"] else "none",
        ),
        ("Checkpoint SHA256", verified["checkpoint_sha256"]),
    ]
    metric_cards = "".join(
        f'<div class="metric"><span>{_text(k)}</span><strong>{_text(v)}</strong></div>'
        for k, v in metrics
    )
    rows = "".join(trade_rows) if trade_rows else '<tr><td colspan="6">No simulated fills</td></tr>'
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_text(csp)}">
<title>Offline Paper Trading Audit</title>
<style>
:root{{color-scheme:dark}}body{{margin:0;background:#0b1020;color:#e7eefb;font:15px system-ui,sans-serif}}
main{{max-width:1100px;margin:auto;padding:32px 18px}}h1{{margin:0 0 6px}}p{{color:#b6c5de;line-height:1.6}}
.notice{{border:1px solid #b67d31;background:#291e12;color:#ffd99c;padding:14px;border-radius:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:20px 0}}
.metric{{background:#151e33;border:1px solid #2b3a56;border-radius:12px;padding:14px;min-width:0}}
.metric span{{display:block;color:#a9bed9;font-size:12px}}.metric strong{{display:block;overflow-wrap:anywhere;margin-top:8px}}
section{{background:#151e33;border-radius:12px;padding:16px;margin:18px 0;overflow-x:auto}}
svg{{width:100%;height:auto;max-height:260px;background:#0e1729}}
table{{width:100%;border-collapse:collapse;white-space:nowrap;text-align:left}}th,td{{padding:10px;border-bottom:1px solid #2b3a56}}
th{{color:#9fb2ce}}small{{color:#9fb2ce}}
</style></head><body><main>
<h1>Paper Trading — Offline Replay Audit</h1>
<p>Verified against a deterministic local replay. Static document, no broker connection or network functionality.</p>
<div class="notice"><strong>NOT LIVE TRADING.</strong> All prices and fills are from the supplied fixture.
Neither profitability, a live exchange account, nor production readiness is verified.</div>
<div class="grid">{metric_cards}</div>
<section><h2>Fixture closing prices</h2>{_chart(bars)}
<p><small>Price history only. This chart is not a strategy equity curve, and does not imply live returns.</small></p></section>
<section><h2>Simulated fills ({len(trades)})</h2>
<table><thead><tr><th>UTC</th><th>Side</th><th>Amount</th><th>Price</th><th>Fee</th><th>Client ID</th></tr></thead>
<tbody>{rows}</tbody></table></section>
<p><small>Generated from local state. Contains trade records; keep the HTML file private.
Last validated bar: {_text(datetime.fromtimestamp(bars[-1][0]/1000,tz=timezone.utc).isoformat())}.
Live trading approved: NO.</small></p>
</main></body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    if destination == args.checkpoint.resolve() or destination == args.fixture.resolve():
        parser.error("Output cannot replace source fixture or checkpoint")
    try:
        document = generate(args.fixture, args.checkpoint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=destination.parent,
                prefix=".paper-report-",
                suffix=".tmp",
                encoding="utf-8",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        parser.exit(2, "Report generation refused; source files were not modified.\n")
    print(str(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
