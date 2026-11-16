# Read-only historical recovery preflight (v33)

## Design
Inspect an existing v32 opt-in directory without opening a PaperTradingSession,
acquiring/creating its writer or run lock, changing control, reconciling an order,
exporting evidence, or publishing a result. Reuse v32 _load, original PaperRunner
and _check_account; do not modify fingerprinted execution modules. Previously
created v32 manifests remain subject to their original engine fingerprint.

Read a validated account observation, reproduce the historical strategy only in
memory, then recheck frozen source files, directory identity and account revision
and digest. Refuse a changed observation instead of presenting mixed evidence.
Treat the presence of result.json as a stop marker, NOT verified completion.
Unknown orders with saved fills require explicit reconciliation; without a saved
fill they remain blocked. No inference of rejected orders or permission to retry.

Output has status compatible_snapshot, reconciliation_required, blocked, or
unavailable. Counts for compatible observations include saved/expected/remaining
fills and saved-fill acknowledgements needing confirmation. No future profit,
realized P&L or live readiness is implied. Owner/process availability is NOT
checked; inspecting a stable account while its owner holds a lease is allowed.
The normal resume command must independently recheck everything under its locks.

No new account schema, runtime dependency, web route, provider or live mode.
Input is bounded by the original 5000-bar/4MiB run and account/evidence limits.
The diagnostic can be expensive; this is an explicit CLI, not a polling endpoint.
Filesystem access time may change on reads; contents, control and inventory must
not. Normal SQLite read locks are retained. No same-user tamper-proof guarantee.

## Run
Use existing repository packages and an existing incomplete opt-in directory.
Do not make or edit resume.json to enable recovery on old unmarked runs.

```bash
python tools/paper_preflight.py .private/replay-recover-001
```

The command writes one JSON result to stdout; diagnostics remain on stderr.
Exit 0: compatible captured prefix, NOT approval/availability to resume.
Exit 4: saved simulated fills have unconfirmed acknowledgements; the inspector
has not reconciled them. Exit 2: blocked/unavailable/invalid arguments. Exit 130:
interrupted inspection. There is no --force, --live or --reconcile-saved-fills
switch. Those operations cannot be enabled by this command.

Main reasons are completion_present (marker NOT validated), manifest_missing,
engine_changed, input_changed, initial_evidence_changed, account_changed,
mark_changed, missing_fill, fill_history_changed, source_changed and
observation_changed. Missing/corrupt/busy sources are unavailable, not zero
balances or automatically safe retries. Error reports contain fixed codes, not
paths, raw provider responses or account details. Successful snapshot identifiers
are private and should not be published.

A compatible result contains the saved revision/digest, stored mode and original
engine fill counts. It does not preserve a lock or authorize an execution. The
separate explicit operation remains:

```bash
python tools/paper_resume.py .private/replay-recover-001
# Only after reviewing stored simulated-fill evidence, when appropriate:
python tools/paper_resume.py .private/replay-recover-001 --reconcile-saved-fills
```

An unresolved intent without a saved fill stays blocked even under that explicit
option. This is historical simulation, not a broker or prospective feed. Recovery
must recheck all conditions because files, account state or owner availability can
change immediately after inspection. No polling, scheduled checks or automatic
retry is introduced.

## Verification scope
Tests use real PaperTradingSession/SQLite plus original MA/RSI replays, with
intentional interruptions and source changes. They check inventory/byte identity,
read-only SQLite URIs, no writer/run-lock creation, active-owner observation,
missing-fill refusal, source/directory/account replacement, cancellation and CLI
channels. A separate child process exits after saving a fill; preflight reports
confirmation required without mutation, and explicit original recovery completes.
No new UI, provider request, long-duration/max-input throughput or other-OS test
is implied. Execution-source fingerprints remain byte-identical to v32.
