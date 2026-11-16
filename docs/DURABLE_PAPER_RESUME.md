# Opt-in historical paper replay recovery (v32)

Create NEW runs with `paper_operate.py --resumable`. Recovery never applies to
unmarked v31 directories. A private immutable resume manifest binds frozen input,
initial evidence/account identity and source-code/runtime fingerprints before
activation. This is consistency checking, not authenticated storage.

Explicit `paper_resume.py DIRECTORY` acquires a run-level lock, checks captured
files, reproduces the original MA/RSI run in memory and requires stored fills to
be an exact prefix. The current account must have no additional orders or
incompatible settings. Rebuild strategy/risk state from the original inputs in a
shadow PaperExchange. Already-saved fills are replayed ONLY in memory; forward
only the new suffix through the actual PaperTradingSession. Preserve the original
sizing, next-bar timing and close-based risk semantics. This is historical
simulation, not a prospective streaming executor or a new fill engine.

Unknown orders are refused by default. Explicit `--reconcile-saved-fills` permits
read-back of already-validated saved simulated fills only. Any unresolved order
WITHOUT a saved fill stops recovery even with the flag. Never blindly resubmit,
clear an unknown intent or infer rejection. No real provider can be selected.

Resume holds the original account owner lease and a run lock, attempts a persisted
halt before/after execution and never overwrites completion or evidence. Each
successful resume writes a new after-evidence file; incomplete older exports stay
untouched. Completed directories refuse another resume before opening the account.
Catchable exits attempt halt; SIGKILL cannot run finalizers. A saved active mode
is not proof of liveness. Recovery replays history and is bounded, not instant.

No DB schema migration, automatic background restart, website controls, account
secrets, network feed, main merge or live enablement. Private local POSIX storage.


## Commands
Use the existing repository package setup. No new dependency is required.

```bash
mkdir -p .private
chmod 700 .private
# New opt-in run. The demo uses SYNTHETIC, not live prices.
python tools/paper_operate.py --demo --resumable --directory .private/replay-recover-001
# ONLY for an interrupted, incomplete opted-in run:
python tools/paper_resume.py .private/replay-recover-001
# Explicitly allow read-back of a saved simulated fill with a lost acknowledgement:
python tools/paper_resume.py .private/replay-recover-001 --reconcile-saved-fills
```

A run which has already published `result.json` is refused without opening its
account. The creation command never accepts an existing directory. A failure
before `resume.json` was durably published is not automatically recoverable.
Retain all artifacts; do not delete an UNKNOWN intent or manufacture a manifest.

The source fingerprints cover engine/strategy/data/execution modules and the
Python implementation + major/minor version. Changed code/runtime refuses resume;
there is no force flag. Reconstruct the original environment to inspect/recover.
These local unsigned hashes do not defend against malicious same-user rewrites.

Recovered completion adds `recovery.existing_fills_skipped`,
`recovery.saved_fills_reconciled` and `final_evidence` (a new relative filename).
The final account retains its original ID and all original fills/fees. The old
`after.paper-evidence.json`, when present, is not overwritten or described as the
new final observation. The whole result is compared with the original replay.

After argument parsing: stdout is exactly one result/error JSON. Exit0 means a
completed halted historical run;2 means invalid/conflicting/already-complete or
failed operation;3 means halt unconfirmed;4 means manual reconciliation is still
required;130 means interrupted. Stderr can contain dependency diagnostics. All
failure paths require retaining and inspecting the run directory. No raw paths
or account data are included in fixed runtime errors.

Stored marks must match the frozen input's open/close timestamps/prices and only
the configured symbol. Old trade sequence, full order inventory and initial
account are checked before any new order. Folder/lock identity is checked before
acquiring the account, before each new submission and before completion publish.
An exclusive owner plus the run lock coordinates this workflow; bypassing the
workflow to edit an account externally is not supported. Opening the legitimate
account may normalize saved SUBMITTING to UNKNOWN as in the existing session.

Recovery is **deterministic catch-up**, not a durable bar-index cursor. The original
strategy runs from the beginning in a shadow ledger; prior effects are never
sent to the persistent account. Replay cost grows with history and existing
5000-bar limits are not performance/long-duration guarantees. No fills are
inferred from marks alone. Close-based risk gates retain their original limits,
not guaranteed liquidation or loss caps.

## Verification
```bash
python -m unittest discover -s tests -p 'test_order_durable_resume.py' -v
python -m unittest discover -s tests -p 'test_order_durable_run.py' -v
```

Tests use actual SQLite, original MA/RSI × USDT/KRW and separate processes stopped
before strategy execution, before effect, after saved effect and after confirmed
order. They verify no duplicate effects, exact original results, explicit
acknowledgement recovery, mark/input/engine/account conflicts, private files,
owner/run-lock contention, finalization failure and original risk behavior.
A file access-time false positive and late directory-replacement detection were
reproduced before correction. No broker, prospective feed, physical power-loss,
full-load performance, independent reviewer or browser E2E evidence is implied.
