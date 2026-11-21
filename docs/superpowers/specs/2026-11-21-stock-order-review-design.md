# Stock Order Review and Recovery Design

## Intent and authorization

The user requested continued completion of the stock/Toss/KIS paper-trading
workflow after durable lifecycle journaling and read-only KIS observation were
published as `2e78574fe8b6a9c9095080e5120ec754e0475618`. Repeated direct
continuation requests authorize implementation and main publication without a
new design-approval pause. The choices below are implementation decisions, not
additional requirements attributed to the user.

Build an offline operator workflow to compare already-collected observations
with an audited journal, preserve/recover journal snapshots, and inspect the
result in a private static Korean HTML report. Actual authenticated requests,
order submission, inferred fills, and automatic repair remain outside this
increment. Existing credential variables are absent; synthetic/fake-response
integration is the reproducible execution path.

## Approach and boundaries

Use the existing Python 3.10+ standard-library and SQLite stack. A pure review
module reads audited journal state and validates a saved observer document.
A separate backup tool preserves a standalone snapshot and restores only a new
candidate path. Root integrates a static HTML/JSON review CLI and synthetic demo.

An automatically mutating reconciliation adapter would need authenticated
execution identifiers and tested broker semantics which are not established.
A hosted dashboard would add deployment/authentication/state synchronization
without helping this local trial. The selected approach produces reviewable
local evidence with no server cost or new dependency.

All components remain paper-only. `account_binding_verified`,
`economic_reconciliation_verified`, `lifecycle_confirmation_verified`, and
`safe_to_submit` remain false. Equal cumulative observations do not verify
individual fills, fees, taxes, balances, terminal states, or source authenticity.
Account aliases must match but do not authenticate the underlying account.

## Task A: read-only observation comparison

Create `packages/core/trading_core/stock_order_review.py` and
`packages/core/tests/test_stock_order_review.py`.

Public API:

```python
review_orders(journal, observations=None, *, now=None, max_age_seconds=300)
```

`journal` is an actual `StockOrderJournal`; call its read-only `snapshot()` to
audit all state. `now` is an aware datetime or None (current UTC).
`max_age_seconds` is an integer 1..86400, never bool. Direct module import under
Python `-S` must work, following existing core patterns. No HTTP, environment
credentials, report writes, event mutation, or repair. The existing SQL-read-only
journal audit can cause SQLite WAL/SHM housekeeping; source immutability here
means unchanged journal/event semantics, not an unchanged directory listing.

If observations are supplied, strictly validate the existing
`kis_vts_readonly_order_snapshot` schema and reconstruct only its allowlisted
fields. Require exact top-level keys and accepted row keys; reject unsupported
metadata, false pagination completeness, inconsistent counts, noncanonical
numbers, malformed clocks/dates, duplicate numeric order identities, wrong
account alias/market/currency, and unsafe verification flags. At most 20 pages,
15 rows per page capacity, and 300 rows. The saved format is already normalized;
do not silently normalize forged noncanonical values. Original order references
retain their padding/blank representation. A saved historical inquiry remains
reviewable; the collector's rolling 30-day request window is not reapplied.
The inquiry date cannot be later than the observation's KST date.

Match only by `(order_date, integer broker_org_no, integer broker_order_id)`.
Never guess an unacknowledged local order from symbol/quantity/side. The inquiry
date scopes comparisons; older local orders stay visible. Unresolved local
orders remain blocking even outside that date. Unmatched local/observed records
are review findings, not proof of absence, rejection, or permission to retry.

Compare symbol, side, limit division `00`, original order quantity, limit price,
cumulative fill quantity, cumulative gross KRW, and working/remaining quantity.
Expose differences as allowlisted field names with local/observed values.
Do not infer status from remaining quantity or cancellation/rejection flags.
Nonzero original-order references and related records require review; do not
sum linked history or turn it into extra fills. Blank/zero original references
do not establish an original-order lifecycle state.

Age exceeding the configured maximum and any future observation timestamp are
explicit findings. Missing observation input is supported for journal-only
monitoring, with a visible `not_supplied` comparison status. Unresolved local
orders always set `needs_attention=true`; exact comparison can still coexist
with an unresolved order. A clean closed synthetic journal with equal fresh
observations may set `needs_attention=false`, while every safety/verification
flag above remains false.

Required report contract:

```text
schema_version=1, kind="stock_order_review", mode="paper",
live_orders_enabled=false, account_ref, generated_at_utc,
account_binding_verified=false, economic_reconciliation_verified=false,
lifecycle_confirmation_verified=false, safe_to_submit=false,
comparison_status: not_supplied | observations_equal | needs_review,
needs_attention: bool, journal_blocked: bool,
journal: audited snapshot,
observation_summary: null or {inquiry_date, observed_at_utc,
  pages_fetched, row_count, age_seconds, freshness: fresh|stale|future},
orders: [{local: journal order, observed: row or null,
  comparison: not_supplied|not_acknowledged|not_observed|observations_equal|
              different|related_history|out_of_scope,
  differences: [{field, local, observed}]}],
unmatched_observations: [allowlisted rows],
findings: [{code, client_order_id: string or null,
            broker_order_id: string or null}],
summary: {order_count, scoped_order_count, matched_order_count,
  different_order_count, unmatched_observation_count,
  unresolved_order_count, findings_count}
```

Return deterministic order/field/finding ordering. The API returns a complete
report or a static ValueError; no partial success. All output values come from
validated aliases, identifiers, canonical money, audited journal, and fixed codes.

## Task B: backup and restore candidate

Create `tools/stock_order_backup.py`,
`apps/server/tests/test_stock_order_backup.py`, and
`docs/STOCK_ORDER_BACKUP.md`.

Public APIs (standard library only):

```python
create_backup(database, destination, *, account_ref)
verify_backup(directory, *, account_ref)
restore_candidate(directory, destination, *, account_ref)
```

Use SQLite native `backup()` for WAL consistency, never copy only the live main
file. Audit source before capture and compare its audited state with the copied
snapshot; refuse changes during capture rather than labeling a different state
as the requested snapshot. Source may use an existing journal instance elsewhere;
backup never appends or repairs it. Document stopped-writer operation.

Destination is an exclusive new 0700 directory in an existing trusted parent,
containing only `journal.sqlite` and `manifest.json`, both 0600. Final symlinks,
dangling links, files/directories, and SQLite reserved-sidecar collisions are
refused/preserved. Never delete replacement paths. A failed creation can leave
incomplete evidence; verification requires the final manifest and both exact
files. No overwriting, recursive deletion of user paths, or active-path restore.

Export the copied database in standalone DELETE journal mode. Since the live
journal audits WAL mode, validate backup replay on an isolated private temporary
copy switched to WAL. Verification never writes backup directory contents.
Hash the actual standalone bytes; verify SHA-256 plus full semantic journal
replay and a canonical snapshot digest/counts/account alias in a strict manifest.
Refuse extra/unmanifested files and sidecars. Bound database input at 100 MB and
manifest at 64 KiB; refuse duplicate JSON keys, nonfinite values, and malformed
manifest fields. Hashes detect mismatch, not authenticated provenance or a valid
coordinated rewrite. Expose no file paths/credential material in JSON diagnostics.

Restore creates an unused mode-0600 journal path in an existing private directory,
including the existing core's sidecar-preservation checks. Copy only a verified
backup into that newly owned candidate, convert to WAL, audit against the verified
snapshot, and report success only after checks. Preserve the source backup and
existing journals. The candidate may be resumed manually in a paper workflow;
restored uncertain/partial orders keep their blocked state. No configuration,
active pointers, order transport, or broker flags are changed.

CLI: required `--mode paper`, subcommands `create`, `verify`, `restore`; each
requires `--account-ref`; create uses `--database --destination`, verify uses
`--backup`, restore uses `--backup --destination`. Exit 0 on verified success,
2 on static refusal. Expose the same semantic snapshot in return values under
`snapshot` for root demo integration; include status, mode, live_orders_enabled,
account alias, replay_verified and candidate_restore_only flags.

## Task C: operator report and integrated synthetic trial

Root creates `tools/stock_order_review.py` and
`tools/stock_order_review_smoke.py` with meaningful actual-process regressions
in `apps/server/tests/test_stock_order_review_cli.py`.

Review CLI reads an existing journal read-only, optionally a saved observation
JSON (max 2 MB, duplicate/nonfinite/oversized-number rejection), and calls Task A.
Require `--mode paper --database --account-ref`; allow `--observations`,
`--max-age-seconds`, and optional exclusive output files `--output-json` and
`--output-html`. Reject input/output aliasing and collisions. Reject destinations
equal to any source main path or its reserved
`-wal`, `-shm`, `-journal` sibling, even if absent; reject cross-output sibling
families too. Restore destinations must not reside inside or alias the source
backup directory. These checks include normalized parent aliases. Output errors are
static; no raw payload/path echo. Exit 0 if review generated without attention,
3 if a valid report needs attention, 2 on invalid/unavailable inputs or output.

HTML is a self-contained Korean report with clear paper/unverified labels,
counts, outstanding states, local-vs-observed quantities, explicit differences,
unmatched observations, freshness, and why further review is needed. Use escaped
text, no JavaScript, no external resources, restrictive CSP, private permissions,
and replacement-preserving exclusive writes. Reports contain local aliases and
order observations; keep them private. Do not build order-action controls.

Synthetic smoke uses the existing 14-event scenario with explicit synthetic
labeling and a consistent chosen clock/date, constructs normalized fake KIS
observations, checks equality and a deliberate discrepancy, creates/verifies a
backup, restores into a new candidate, and compares audited state. Include a
pending-order backup/restoration probe in tests. A default temporary run leaves
no artifacts; optional exclusive new output directory retains DBs/reports/backup.
No HTTP requests. Commit only synthetic sample JSON/HTML, never databases.

## Integration and validation

Add both new suites and core review suite to offline_checks. Update README,
operator guides and readiness checklist with precise local-only milestones.
Independent read-only review must inspect input trust, cross-date identities,
linked orders, backup replay, source/output preservation and HTML escaping.
Run full pytest, extended socket/DNS-forbidden offline suite, compileall, fatal
flake8, and diff validation. Publish expected-SHA-leased GitHub tree/commit/ref,
verify uploaded source hashes and exact-head Python 3.10/3.11/3.12 + Docker CI.
