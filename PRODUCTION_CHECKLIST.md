# Paper operating checklist and remaining live evidence

The supported path is a local historical paper workflow and a read-only local
observer. Follow [PRODUCTION_PAPER_OPERATIONS.md](docs/PRODUCTION_PAPER_OPERATIONS.md)
for exact commands, probe contracts, budgets, shutdown, recovery and rollback.
This checklist replaces the previous credential-and-live-switch deployment recipe.
No entry below is prechecked as evidence that an operator performed it.

## Before a local paper observation

- [ ] Record the exact code revision and Python version; use the existing package setup.
- [ ] Use operator-owned private directories (`0700`) and private files (`0600`). Keep accounts, frozen inputs, reports and evidence out of public delivery.
- [ ] Select synthetic or explicitly supplied historical input and a **new** run directory; choose `--resumable` at creation if explicit historical recovery is required.
- [ ] Confirm the intended source paths and independent source meanings. Admission reservations are not account balances or replay fills.
- [ ] Inspect the completed run with `paper_audit.py` and/or persisted account with `paper_session_status.py`; retain failures and all artifacts.
- [ ] Start the observer on `127.0.0.1` only. Do not expose it via public hosting, proxies, tunnels or port forwarding.
- [ ] Check liveness separately from configured-source readability using `paper_dashboard_check.py`.
- [ ] Interpret probe exit `0` as valid alive/ready, `1` as validated degraded/not_configured, `2` as arguments/transport/protocol failure, and `130` as interrupted.
- [ ] Treat unavailable data and previous-inspection warnings as unavailable/currently unverified, never empty accounts or fresh prices.
- [ ] Record resource limits and actual observations; do not call accepted limits a maximum-throughput or soak test.

## On error, maintenance or rollback

- [ ] Preserve incomplete runs and possible report publications. A publication-confirmation error is not permission to retry automatically.
- [ ] Use read-only preflight for incomplete opted-in historical runs and audit for completed opted-in runs; neither authorizes execution.
- [ ] Explicit recovery rechecks original engine/runtime fingerprints and owner locks. Saved-fill reconciliation requires the explicit recovery option after inspection; unknown intents without saved fills stay blocked.
- [ ] Stop the writer and confirm exit before backing up the whole quiescent directory. Preserve original files, modes and sidecars; copying only a live DB is insufficient.
- [ ] Stop the observer using SIGINT/SIGTERM; expected exits are `130`/`143`. Startup/configuration failure is `2`.
- [ ] Roll back code through a separately prepared exact pinned revision/environment, preserving data/evidence. Do not rewrite manifests, delete unknown intents or apply unverified migrations.

## Evidence still required for any future live service

Configuration completeness is not operational readiness.
`ConfigManager.is_live_configuration_complete()` checks settings only;
`is_production_ready()` returns `False`. Config CLI validation retains its
settings-validation exit codes and always describes operational readiness as
unverified. Dashboard readiness checks readability of local sources only.

The following require implementation and recorded
verification before a live service could be assessed:

- [ ] Prospective market-feed continuity, timestamp/freshness handling, gaps and reconnect behavior under real operational conditions.
- [ ] Authoritative broker/account reconciliation, partial fills, ambiguous submission outcomes, idempotence and settlement across restart.
- [ ] Operational risk controls and stop behavior against actual execution; modeled historical gates do not guarantee a maximum loss.
- [ ] A reviewed credential/permission boundary and deployment architecture appropriate to the intended service. The current observer is not an authenticated public web server.
- [ ] Reproducible dependency/build and deployment/rollback evidence for the actual runtime, separate from legacy Docker/Rust scripts.
- [ ] Long-duration soak, resource measurements, alert delivery, disk-full/backup/restore drills and recovery decisions on the supported operating environment.
- [ ] Actual browser visual/accessibility and end-to-end verification, with results recorded for the supported release.
- [ ] Explicit scope for OS/filesystem portability and concurrent service ownership.

No live broker connection, credentials, external notification, public deployment,
actual visual QA or soak completion is implied by this checklist or synthetic
tests. Preserve the evidence and record unresolved findings before deciding any
future scope.
