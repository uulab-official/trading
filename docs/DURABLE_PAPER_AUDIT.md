# Read-only completed historical run audit (v34)

## Design
Inspect a completed, opt-in v32 historical run without opening its durable
writer, creating/acquiring owner or run locks, submitting/reconciling an order,
changing control, or writing any run file. Keep all fingerprinted engine modules
unchanged. Existing v33 preflight remains for incomplete runs; it must not infer
completion from a marker. This new audit requires the recorded resume manifest.
Unmarked v31 runs remain unsupported rather than inventing a source fingerprint.

Read private bounded canonical result/input/manifest/evidence files. Validate the
recorded engine fingerprint and initial state with the original recovery loader.
Recompute the original full strategy report in memory. Require exact canonical
report equality (including risk flags, pending final signal, and no-trade results)
and all saved fills, final marks, halted mode and confirmed acknowledgements.
Recompute the stored before/after comparison from the exact validated captures;
only its generated comparison timestamp may differ. That timestamp still must be
valid UTC. Resolve final evidence by a strict local filename, never a path supplied
by result JSON. Both ordinary and resumed completed reports are supported.

The current validated DB snapshot must equal the final evidence's account,
revision and digest. Recheck account and all source identities after calculation;
changed inputs/results/account are blocked, not mixed with earlier observations.
An owner lease is not checked. A stable account can be read while owned, but a
new revision (even a reopen/price/control-only write) invalidates the match.
No historical completion certificate is issued when current account differs.

Result `verified_completion` means these captured artifacts agreed at inspection,
NOT authentication, fresh quotes, profitability, process liveness or approval to
trade/resume. Recovery counters are only structurally bounded: the snapshots do
not prove how many effects a particular recovery attempt skipped. They are not
included in audited totals. File access times may change; contents must not.

## Command
With the existing repository packages installed:

```bash
python tools/paper_audit.py .private/replay-recover-001
```

Use an already-completed `--resumable` run, never manufacture its manifest. Exit 0
means the captured completion matched. Exit 2 means blocked/unavailable/invalid
arguments. Exit 130 means interrupted. One JSON result goes to stdout; diagnostics
stay on stderr. No --force, --live, repair or execution switch. No new dependency,
HTTP route, account schema, scheduler or broker integration.

Read limits: original 4 MiB input/5000 bars and account/evidence bounds; 20 MiB
completion report. Work is bounded but replays the entire history and is not a
polling endpoint. Checksums and recorded engine fingerprints are not signatures;
a same-user adversary can rewrite a self-consistent directory. Preserve failed
artifacts; this command neither deletes nor repairs them.
