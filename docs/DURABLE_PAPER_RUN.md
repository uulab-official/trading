# Durable historical paper strategy run (v31)

## Purpose and boundaries
Explicitly run the existing MA/RSI PaperRunner into a **new** PaperTradingSession.
Reuse the original strategy, minimum-50-bar warm-up, next-observed-bar-open
execution, sizing, day/drawdown gates and PaperExchange accounting. No alternate
fill/risk algorithm, live mode, provider, credentials, package dependency or DB
schema migration is introduced. Legacy `PaperRunner.run` and checkpoint recovery
remain separate and unchanged in behavior.

This is historical simulation, not prospective/live trading. Slippage/fees are
model assumptions; stored prices are not fresh. Daily/drawdown gates retain their
original close-based semantics, do not guarantee a maximum loss and do not force
liquidation. An unexecuted final signal remains pending, not an invented fill.

## One-command operation
With the repository's existing packages installed:

```bash
python tools/paper_operate.py --demo --directory .private/strategy-demo-001
# Or supply local historical data (never both a file and --demo):
python tools/paper_operate.py /path/to/history.json \
  --directory .private/strategy-run-001 --strategy ma_cross \
  --initial-balance 1000000 --position-size-pct 20 \
  --fee-pct 0.1 --slippage-pct 0.05
```

The parent directory must exist. Create `.private` with mode700 first. A run
always creates a NEW mode700 leaf directory; all files use mode600. Existing
paths (including empty folders, symlinks and interrupted runs) are never reused.
The explicit `--demo` fixture is synthetic; its numbers are not market evidence.

Input JSON has `symbol`, `timeframe`, and six-column `candles`; optional `mode`
must be `paper`. No other keys are accepted. Example shape:
`{"symbol":"BTC/USDT","timeframe":"1h","candles":[[1700000000000,"100","102","98","101","1"]]}`.
A source importer with extra metadata must be explicitly converted to this shape;
metadata is not silently ignored or copied into the output.

Limits: 1–5000 chronological nonoverlapping valid bars and 4MiB input. A gap is
not filled; a signal uses the next supplied bar's open. A short run can legitimately
produce zero trades. This snapshot-backed account validates growing history on
writes and is not designed as a high-frequency execution engine. The maximum
input bound is not a throughput or long-duration validation claim.

## Artifacts and lifecycle
The operation creates, in order:

- `input.json`: canonical immutable-in-workflow input/config and its reported hash.
- `account.sqlite`: real local durable simulated account, usable by the existing monitor.
- `before.paper-evidence.json`: complete initial account, in halted mode.
- `after.paper-evidence.json`: complete final account, after a confirmed halted transition.
- `result.json`: completion only after owner close, final evidence verification and
  whole-account comparison. Includes original replay output and accounting deltas.

The account starts halted, activates for this explicit replay, then attempts a
persisted halt on normal completion or a catchable error/cancellation. The runner
uses the original durable manager, not a separately restored balance. Ambiguous
order outcome stops before another candle; no automatic reconciliation/retry is
attempted. Rejected inputs and failed operations never fabricate completion.

**SIGKILL and power loss cannot run finalization.** Stored mode may remain active,
with SUBMITTING/UNKNOWN and possibly a saved account fill. No replay cursor or
strategy-resume implementation exists here. Inspect the surviving account with
the existing read-only monitor and use existing explicit owner/reconciliation
APIs as appropriate; do not delete the directory or rerun it as recovery. A new
run is a separate simulated account, not continuation of the original.

If the halt write fails, return no success (`durable_replay_stop_unconfirmed`).
Outputs are published with the existing private no-clobber publisher; on any
output error, retain the directory and inspect potentially published files.
Checksums are not signatures or proof the current path still contains the bytes.

## CLI result channels
After argument parsing, stdout contains one JSON result or fixed error object.
Successful execution returns0, ordinary failure2, unconfirmed halt3, interrupt130.
Stderr is reserved for library diagnostics; they cannot corrupt machine JSON on
stdout. No raw exception, path, token or account content is added to error JSON.
Argparse help/usage remains conventional. Existing evidence CLI channels do not
change. No background work, daemon or scheduled action is created.

## Monitor and independent checking
```bash
python apps/dashboard/server.py \
  --paper-session .private/strategy-run-001/account.sqlite --port 8765
python tools/paper_evidence.py verify \
  .private/strategy-run-001/after.paper-evidence.json
python tools/paper_evidence.py compare \
  .private/strategy-run-001/before.paper-evidence.json \
  .private/strategy-run-001/after.paper-evidence.json
```

Use `/paper-account.html` and `/paper-orders.html` locally. The observer can read
committed state while the runner owns the writer lease. Mode is a saved decision,
not process liveness. No new browser code, HTTP writes or public hosting is added.
Actual data, frozen inputs and reports are plaintext and must remain private.

## Verification
Tests compare entire original replay results for MA/RSI and KRW/USDT, risk gates,
next-bar timing, pending final signals and legacy checkpoint resume. They exercise
actual SQLite, evidence export/compare, owner reads, lost acknowledgements,
cancellation, failed halt, file protections and actual subprocess termination.
No profitability, real provider, physical power-loss, other-OS, full-load maximum
or browser-to-server E2E claim follows from these tests.
