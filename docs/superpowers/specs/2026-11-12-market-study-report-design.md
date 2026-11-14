# Read-only market study report (v39)

## Intent
The v38 study runner already compares a fixed set of independently audited paper
accounts. Its JSON output and per-child status overview make it difficult to see
the shared assumptions and candidate results together. Add one local HTML report
to the existing read-only `paper_study.py check` command.

This is the next development increment selected under the user's continuing
implementation instruction. The existing Python/stdlib stack and draft branch
remain the delivery path. No new service or package is needed.

## Approach
A self-contained static report fits the existing local private-file workflow.
Extending the web server would introduce account-directory configuration and
network exposure; adding JavaScript state would duplicate the existing verifier.
The selected approach renders the current checker result once, without another
engine, browser-side calculation or automatic refresh.

## Contract
- `python tools/paper_study.py check DIRECTORY --html NEW_FILE` calls the existing
  checker and keeps its JSON stdout and exit statuses: 0 verified, 4 incomplete or
  missing receipt, 2 blocked/unavailable. `check` without HTML is unchanged.
- HTML output is optional and available only on `check`. The command never
  starts, resumes or repairs a study. It never creates a missing receipt.
- The output must be a new file in an existing private owner-only directory,
  outside the inspected study (including aliases). Reject an existing target,
  symlink, unsafe parent or source descendant before inspection; recheck before
  publication. Reuse the existing no-clobber 0600 publisher. Publication uncertainty
  returns 3 with a fixed error; never delete or overwrite the resulting file.
- A pure `render_study_report(report: dict) -> str` in
  `apps/server/trading_bot/market_study_html.py` accepts only the v1 read-only
  `market_paper_study` inspection envelope (paper, live disabled, resume disabled).
  It performs no I/O or account/return arithmetic and never mutates its input.
- Show a Korean overview with study verification status, observation time, market,
  timeframe, source bar count, baseline and common capital/cost/risk assumptions.
  Use the quote currency from the inspected symbol, never a currency conversion.
- Preserve every candidate and its plan order, including not-started, incomplete,
  reconciliation-required, blocked and unavailable rows. Show strategy parameters,
  exact existing equity/return/fees/fills/bars and risk-stop flags only for an
  individually verified row. Missing values remain unavailable, never zero.
- Display baseline return differences in percentage points only when the overall
  study and receipt are verified and every candidate is verified. A missing or
  corrupt receipt is visibly incomplete/blocked even if child audits succeeded.
- No profit rank, winner, summed accounts, invented MDD/win rate, annualization,
  out-of-sample claim, profitability promise or live-readiness implication.
- Escape every supplied string. Use no scripts, remote assets, network requests,
  forms or execution links. Restrictive CSP and no-referrer; responsive 390px and
  1440px layouts, keyboard-usable details, clear status text alongside colors.
- Explain that this is a historical snapshot with sequential observations;
  later account changes require a fresh check. Costs are simulation assumptions.
  Keep technical provenance and diagnostic codes in expandable details.

## Validation
Use real synthetic-source studies for CLI integration: verified, missing receipt,
blocked receipt, missing source, no-clobber, source aliases, unsafe parents and
publication failure. Compare private source inventories before/after inspection.
Renderer tests inspect actual HTML semantics and malicious text, incomplete rows,
precise values/units and receipt gating. Check desktop/mobile browser rendering.
Run the existing order, Python package and Node regressions, then review the scoped
diff independently and verify the delivered commit's GitHub Actions results.

## Delivery boundaries
Do not modify fingerprinted engines, account schemas or v38 receipts. Keep PR #1
Draft; no main merge, deployment, credentials, broker orders or live activation.
All demonstration results must be explicitly synthetic and must not be committed
as real exchange data or personal account artifacts.
