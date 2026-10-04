# Deterministic offline paper automation

This workflow runs locally on supplied historical/synthetic candles. It does not
use brokerage accounts, credentials, remote prices, Telegram, WebSockets, or live
orders. It is an offline paper-mode program, not a profitability forecast.

## Run the verified workflow

From the repository root:

The offline runtime needs Python 3.10+ and PyYAML 6+. The minimal dependency is
listed in `requirements-offline.txt`; this excludes live broker/data clients.
The validation runtime already supplied PyYAML, so no installation was performed.

```sh
python tools/offline_checks.py
PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
  python -m trading_bot.paper_runner config/examples/offline_fixture.json \
  --checkpoint data/paper_replay_checkpoint.json
```

If installed with the project's Python dependencies, the same entrypoint is
`trading-paper`. Both command forms print the same result JSON to stdout and
exit successfully with code `0`. Python callers of `main(argv)` still receive
the result dictionary; the process entrypoint `cli(argv)` returns an integer.
Local wheel/launcher validation builds and installs only this repository's server
package into a temporary directory, with dependency fetching and network disabled.
The provided 90-bar fixture is synthetic and demonstrates a simulated buy/sell
cycle only. Its prices and returns are not market evidence.

Re-running the same command validates and resumes the existing checkpoint. It
does not duplicate completed fills. To start a different experiment, supply a new
checkpoint path. Changing previously processed candles, strategy parameters,
fees, or risk settings is rejected without overwriting the saved checkpoint.
Appending valid future candles is supported.

## Inputs and options

The fixture is a JSON object:

```json
{
  "mode": "paper",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "candles": [[1700006400000, 100, 110, 90, 105, 1000]]
}
```

Each row is opening timestamp in milliseconds, open, high, low, close, and volume.
Prices must be finite and positive; volume is finite/nonnegative; OHLC bounds must
be consistent. Completed bars must be chronological and non-overlapping for the
fixed timeframe. Gaps are allowed; missing bars are not invented.

The runner supports one USDT-quoted spot symbol and one position, with no leverage
or short selling. Defaults are illustrative assumptions:

- `--strategy ma_cross` or `rsi`
- `--fast-period 3`, `--slow-period 5`, `--rsi-period 14`
- `--initial-balance 10000`, `--position-size-pct 20`
- `--fee-pct 0.1`, `--slippage-pct 0.05`
- `--max-daily-loss-pct 3`, `--max-drawdown-pct 10`

MA slow periods can be at most 500; RSI periods at most 499, matching the bounded
500-bar strategy history. Existing strategies wait for at least 50 completed
bars, and RSI additionally waits for the full period+1 Wilder seed. Quantities
are rounded down to eight decimal places for this emulator; this is not a claim
about an exchange's lot-size or minimum-order rules.

## Execution, accounting, and restart

- Completed-bar signals execute at the following available bar's opening price
- A signal on the final supplied bar remains pending until a future bar is added
- Explicit slippage adjusts buy prices upward and sell prices downward
- Fees are charged only on successful fills; unfunded buys/oversells are rejected
- One authoritative paper ledger tracks cash, holdings, trades, and fees
- Equity/return include open holdings marked at the last supplied close
- Fixed 28-digit Decimal precision and HALF_EVEN accounting make replay stable
- A checkpoint is atomically written after each completed bar
- One runner owns the checkpoint from before loading/validation through its final
  save. A concurrent runner receives `CheckpointInUseError` before exchange
  startup and cannot overwrite saved progress
- Relative paths and symlink aliases resolve to the same checkpoint and lock.
  Atomic writes update the canonical target while preserving a supplied alias
- Resume rebuilds indicators and independently replays the verified prefix,
  then compares all economic, risk, and pending-signal state before continuing
- Saved trade chronology is independent of JSON dictionary key order

Daily loss and drawdown controls block new buys based on bar-close equity and
still permit exits. Daily loss tracking resets on the fixture's UTC trading day;
the drawdown halt stays latched for the experiment. These controls do not model
intrabar stops, forced liquidation, latency, or guaranteed loss caps.

## Checkpoint ownership and recovery

Checkpointed replay requires POSIX file locking on a local filesystem, as used
by the cloud Linux validation environment. An unsupported platform fails before
checkpoint access or paper-exchange startup. In-memory replay needs no lock file
or POSIX dependency. Windows and shared/distributed filesystem locking are not
validated.

The runner retains a small `.CHECKPOINT_NAME.lock` file beside the checkpoint.
Its presence does not mean a run is active: ownership is a kernel file lock,
released on normal completion, errors, cancellation, or process exit. Do not
delete or replace this lock file while runners might use the checkpoint; all
cooperating writers must lock the same inode. A symlink or non-regular lock file
is rejected. Permissions or filesystem errors remain errors; they do not enable
an unlocked fallback.

When the checkpoint is already in use, wait for its current run to finish and
retry the same command. After an interrupted run, rerun the command to validate
and resume the last completed atomic checkpoint. This does not add a force-unlock
mode or alter the existing prefix, economic-state, or pending-signal validation.
Manual checkpoint edits or programs that ignore the lock are not protected.

## CLI failures and retry steps

Both `trading-paper` and `python -m trading_bot.paper_runner` use these exit codes:

- `0`: replay completed, or `--help` was requested
- `2`: invalid command arguments, fixture, configuration, or checkpoint
- `3`: the checkpoint is already owned by another runner
- `4`: local file access failed, including missing paths or permissions
- `130`: keyboard interruption; rerun the same command to validate and resume

Expected replay failures print concise guidance to stderr rather than a result
dictionary or traceback. Input contents and saved configuration are not dumped.
Normal argument usage errors use argparse's usage message. Unexpected errors
retain their exception traceback instead of being treated as a successful run.

For code `2`, check that the fixture is valid paper-mode JSON with six-column
OHLCV rows, and use the original symbol, timeframe, strategy, and numeric options.
Do not edit a saved checkpoint to bypass verification. Preserve it and use a new
checkpoint path if you intended a different experiment. For code `3`, wait and
retry; do not remove the lock file. For code `4`, check local paths, permissions,
and available storage before retrying. A failure or interruption may occur after
some bars have been saved: the last valid checkpoint is authoritative, and normal
resume verifies it before continuing. If no checkpoint was saved, retry starts
from the beginning. This does not add automatic retry or trading activation.

## Verification and remaining limits

`tools/offline_checks.py` runs the targeted safety, data, strategy, ledger, and
runner suites while socket connections and DNS are forbidden. It includes the
CLI entrypoint, real existing MA strategy, next-open execution, fees/slippage,
marked holdings, corrupted checkpoint rejection, and restart before/after fills.
It also checks competing writers, alias exclusion, failed writes, cancellation,
and actual process-exit lock release while preserving the earlier contracts.
CLI checks run isolated subprocesses with network/DNS forbidden inside each
child. They cover the configured console-script target, module parity, malformed
inputs, checkpoint preservation, busy aliases, file errors, and an immediate
SIGINT interruption after an atomic save followed by exact resume. A separate
local wheel smoke test confirmed the generated installed launcher exits `0`,
matches module JSON, and resumes without duplicate fills.

This does not validate a live trading system. Existing live adapter code remains
guarded by exact live configuration and an explicit runtime activation flag, but
the restored data package deliberately raises `LiveMarketDataUnavailable` for
live feed construction. Rust/server deployment, real exchange compatibility,
credentials, account setup, orders, and live activation were not exercised.

The old backtest engine uses a separate broker. Its causal fill timing, marked
equity, rejected fees, and oversells are now repaired in the separate
[offline backtest accounting milestone](OFFLINE_BACKTEST_ACCOUNTING.md).
Run `python tools/offline_checks.py --include-backtest` to include those checks;
this additionally needs the existing pandas/numpy dependencies. Backtest
[realized trade reporting](OFFLINE_REALIZED_REPORTING.md) now uses fee-inclusive
FIFO matching; legacy risk statistics remain unverified. The old
`TradingBot` Portfolio path also remains separate from the authoritative fixture
runner and has not been declared accounting-complete. Full legacy pytest/CI and
Docker builds were not validated; pytest/ccxt are absent from the local runtime.
