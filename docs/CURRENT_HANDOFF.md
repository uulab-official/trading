# Trading continuation handoff — 2026-11-12 (v39)

## Resume point

Repository: `uulab-official/trading`. Continue on
`feat/smc-market-structure`; [PR #1](https://github.com/uulab-official/trading/pull/1)
remains Draft. The recovered v38 baseline was `00712a42e26fd674dc9921b50d6f99303e01c98d`.
Its Quant Regression, Order Outcome Safety and CI/CD workflows had all succeeded.
The old `4f7d677` conversation checkpoint is obsolete.

The user asked to resume the interrupted automation-system development. Ongoing
implementation and development-branch delivery are authorized. Keep the existing
main-merge, deployment, credentials and live-execution boundaries.

## Delivered implementation

- Pure Korean HTML study renderer, original candidate order, exact
  authoritative metrics, explicit missing values, per-row states and receipt-gated
  percentage-point comparisons. No network assets, scripts or execution actions.
- `python tools/paper_study.py check DIRECTORY --html NEW_FILE`.
  The optional output is private, no-clobber and outside the inspected study;
  JSON and inspection exit statuses are retained. Existing check/run semantics,
  source bindings, original audit and fingerprinted engines are unchanged.
- Two pre-existing legacy-paper compatibility repairs, found by testing
  the actual restored checkout. Exact empty signal IDs become `None` only at legacy
  BUY handoffs; malformed explicit IDs remain rejected. Checkpoint restore accepts
  original records without `cancel_requested` and newer explicit boolean `False`;
  active/malformed values remain blocked with source bytes preserved.
- CI now includes `apps/server/tests/` alongside package tests, so the legacy
  lifecycle/restart path is a required check instead of an unexercised gap.
- Independent review found a late output-directory replacement race.
  Publication now binds both the canonical parent path and its device/inode to
  the original validated directory. Regression cases cover a symlink replacement
  and relocation of that same directory into the study; neither may write there.

See [MARKET_PAPER_STUDY.md](MARKET_PAPER_STUDY.md) for the complete report command,
permissions and meaning of nonzero statuses.

## Verification record

- Baseline v38 study suite: 35 passed.
- New renderer: 11 passed after failure-first implementation.
- New real-study CLI/report integration: 17 passed after failure-first implementation.
- Legacy checkpoint/lifecycle/restart/zero-mark/UTC-boundary suites: 53 passed,
  plus 31 parameterized subtests; both original failures reproduced at `00712a4`.
- Final full development regression: 887 tests passed (`unittest discover -s tests`).
- Node clients: 79 tests passed.
- Package and server application suite: 309 tests and 382 subtests passed on the
  final implementation (Python 3.12). No failing test was skipped or weakened.
- Shared private-evidence publisher regressions: 35 passed after the review fix.
- Actual three-candidate synthetic study creation, check and HTML export: verified
  receipt; no real market acquisition and no profitability claim.
- Exact delivered-commit CI is checked after the branch push, independently of
  local evidence. See the PR Checks for the latest branch head.

The initial broad package/application run was interrupted after baseline failures
and a lifecycle wait that could not be reached after invalid ID rejection. It is
not counted as a successful test run. Existing `datetime.utcnow` and pytest-asyncio
deprecation warnings are not new failures.

Actual browser layout verification remains incomplete: there was no connected
local Browser, and Cloud Browser refused the local file protocol. No alternate
browser path was used to bypass that policy. HTML structure/escaping/receipt
semantics are tested; 390px/1440px visual checks remain a manual validation item.

## Keep these boundaries

- HTML is a static historical observation with sequential child checks, not live
  market data or a simultaneous portfolio view. No automatic refresh.
- Missing receipt remains `complete_without_receipt`; HTML generation does not
  repair, resume or execute anything. Every planned row remains visible.
- No candidate ranking, winning strategy selection, balance aggregation or
  out-of-sample/profitability claim. Stop flags are not measured maximum drawdown.
- No automatic study continuation or uncertain-order retry. Individual child
  recovery uses the existing explicit preflight/resume path.
- No main merge, deployment, broker credentials/orders or live enablement.
- Earlier local-only KIS/UI/research cumulative packages are not assumed integrated.
- Private source, accounts and rendered results remain outside Git. UI testing used
  synthetic fixtures, not personal account data or certified provider evidence.

## Next useful work

First complete the desktop/mobile visual check using an authorized local browser.
Then connect existing out-of-sample/walk-forward research evidence to a similarly
read-only result view, with a distinct validation status from same-period studies.
This is a proposed continuation, not evidence that such validation already exists
for any particular strategy or dataset.

## Final delivery verification

Independent review identified one important publication race and no other material
findings. Both the reproduced race and the same-inode relocation variant failed
before the fix and passed afterwards. The renderer remains a projection of the
existing checker, not a second receipt authenticator. It does not fabricate
candidate data when the underlying study cannot be loaded.

All 16 source modules in the original execution fingerprint were compared with
`00712a4` and remain unchanged. Keep the source and result files private.

Final post-review local verification completed: 887 development tests, 309
package/application tests (382 subtests), and 79 Node client tests all passed.
The shell has no GitHub push credential, so delivery uses the connected GitHub
API as a consolidated commit with an exact local/remote Git tree comparison and
an expected-head, fast-forward update. Local incremental commit IDs in the plan
are development checkpoints, not remote delivery identifiers.

Verify remote CI against the actual current PR head rather than assuming a past
workflow run covers later commits. The branch and PR links above are the durable
resume locations; this handoff is written before the delivery CI run completes.
