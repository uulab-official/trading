# Offline paper checkpoint audit (read-only)

This audit checks a **saved, existing** JSON paper checkpoint against its local
fixture. It uses the same strategy engine to independently replay all processed
bars and compares the entire checkpoint state (economic, pending signal, and risk).
It does **not** connect to a market, activate a broker, or approve live trading.

## Run

From the repository root with Python 3.10+ and the documented offline
dependencies installed:

```sh
# First create a checkpoint using the documented offline paper runner:
./test-trading.sh

# Supply the checkpoint path printed/used by your runner.
python tools/paper_checkpoint_audit.py \
  config/examples/offline_fixture.json \
  data/owner-trial/checkpoint.json
```

**Important:** If your runner used a different checkpoint path, substitute that
exact path. The audit does not create a checkpoint. It prints one JSON report
with `status=verified_offline_paper`, bar count, hashes, halt flags, and
`live_trading_approved=false`. A passed audit means that the saved
paper state matches this replay; it is **not** evidence of profitability or
broker readiness. Synthetic fixture history should never be marketed as
real backtest performance.

Exit codes:

- `0`: paper state independently verified; **no** live approval
- `2`: missing, invalid, inconsistent, or inaccessible file
- `3`: another process owns the checkpoint lock

The audit holds the same POSIX ownership lock as the paper replay to prevent
cooperative concurrent modifications while validating. It does not overwrite
the checkpoint, change strategies, or "repair" values. It may create a
persistent neighboring `.lock` file as part of safe read ownership.
Do not remove lock files while writers could be running. Linux/macOS local
POSIX locking is required; distributed filesystems and Windows are not
validated. Untrusted fixture files should be treated as untrusted inputs.

## Checks

```sh
python tools/offline_checks.py
python tools/offline_checks.py --include-backtest
```

CI also runs `apps/server/tests` in the Python matrix. The audit's
regressions cover correct state, untouched checkpoint bytes, fabricated
economic state, changed fixture history, missing checkpoint, and a live-mode
fixture. The offline suite explicitly forbids network connections and DNS.

## Production release blockers (not yet resolved)

- Restore and validate a genuine market-connected **paper** feed with freshness
  and gap protection; the current offline data package deliberately lacks live
  feed support.
- Reconcile broker balances, orders, fills, partial fills, fees, and startup
  state against authoritative exchange responses before any live execution.
- Make **live execution separately gated**, test with sandbox/testnet, require
  an operator kill switch, and verify cancel/restart/idempotency behavior.
- End-to-end test both Rust and Python services and establish a single
  authoritative reporting source; current dashboards do not consume this
  checkpoint.
- Validate alerts, backups, recovery, clock drift, key permissions, and
  operational incident drills. No automatic promotion from paper to live.

Do **not** point a real-money bot at this paper audit or interpret this document
as authorization to trade.
