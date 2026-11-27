# Verified paper-session recovery bundle

This is a **local paper-only** disaster-recovery candidate workflow, not a
broker backup, signing service, or real-time trading service. It captures
a native SQLite snapshot of the public archive and all strategy databases,
the fixed template and last successful report. The Rust executable verifies
the complete economic and sealed-bar replay of the copied files in
`paper-forward --audit-only` mode before publication. The public archive
may have advanced past the last report; its full head and the reported
prefix head are both checked. The supervisor must be stopped or release
its owner lock for a manual backup; no concurrent account writer is allowed.

## Backup and separate trust anchor

```sh
python tools/binance_paper_recovery.py backup \
  --archive data/binance-history/btc.sqlite --template data/btc-paper-template.json \
  --binary apps/server/target/release/trading-server --session-dir data/btc-forward \
  --start-open-ms SESSION_START_UTC_MS --destination /trusted-backups/session.zip
```

Publish only a fully written, fsynced archive via exclusive creation. The
result includes bundle SHA-256 and report SHA-256. Keep those digests in
an independently controlled location, not just alongside the bundle.
The ZIP is not encrypted or signed and may contain private financial
simulation settings. Keep it private; never publish it in Git.

```sh
python tools/binance_paper_recovery.py verify --bundle /trusted-backups/session.zip \
  --expected-sha256 EXTERNAL_BUNDLE_SHA256 --binary apps/server/target/release/trading-server
python tools/binance_paper_recovery.py restore --bundle /trusted-backups/session.zip \
  --expected-sha256 EXTERNAL_BUNDLE_SHA256 --binary apps/server/target/release/trading-server \
  --destination data/recovery-candidate
```

The entire outer archive is checked against the supplied independent digest
before parsing. Only the manifest's fixed member set, regular uncompressed
files, bounded sizes and member hashes are accepted. No arbitrary path
extraction. Verify performs an audit on temporary private copies. Restore
uses a new directory only, re-audits the candidate and leaves both STOP
markers and a halted supervisor state. REVIEW.json is created only after
success. Preserve incomplete candidates for review; do not use them.
No old database is replaced and no process is started.

## Explicit reviewed recovery

Inspect the source and candidate, verify all account and archive checkpoints,
and preserve the original data. Before any continuation ensure only one
copy of a logical paper account can be active. Remove the candidate's two
STOP markers only after review. If the archive itself is halted, use the
archive's separate reviewed resume command; the paper review does not change
its metadata. Then:

```sh
python tools/binance_paper_recovery.py review-resume \
  --archive data/recovery-candidate/archive.sqlite \
  --template data/recovery-candidate/template.json \
  --binary apps/server/target/release/trading-server \
  --session-dir data/recovery-candidate/accounts \
  --start-open-ms SESSION_START_UTC_MS \
  --expected-report-sha256 EXTERNAL_REPORT_SHA256
```

This only clears the local supervisor failure latch after full historical
audit and exact report digest confirmation. The last report, account heads,
archive prefix and all economic risk latches survive. Neither real nor
paper orders are submitted by this command; start the separate collector
and paper supervisor explicitly when appropriate. An old backup can verify
its historical integrity without claiming current market freshness.

## Cross-language recovery drill

The network-disabled Docker build runs the actual compiled Rust binary from
a Python fixture-driven lab spanning 339 hourly synthetic bars (14 days
plus warmup). It verifies staged continuation, deep audit, backup, restore,
reviewed resume and tampered-bundle rejection. This is simulated elapsed
market time, NOT 14 days of actual operation. Run locally with:

```sh
PYTHONPATH=. python tools/binance_paper_recovery_lab.py --binary apps/server/target/release/trading-server
```

The test uses no credentials or actual exchange calls. True live-feed soak,
independent off-host digest retention, scheduling/retention policies and
broker account reconciliation are separate operational gates.
