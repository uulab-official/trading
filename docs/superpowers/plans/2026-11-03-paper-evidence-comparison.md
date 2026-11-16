# Paper evidence comparison implementation plan

> Execute inline with superpowers:executing-plans and failure-first tests.

**Goal:** Explain and validate changes between two saved local-paper observations.
**Architecture:** Single private read/validation helper, independent pure comparison,
then existing CLI dispatch. No database or network in comparison.
**Tech Stack:** Python 3.10+, unittest, original Decimal context, stdlib only.
**Spec:** docs/PAPER_EVIDENCE_COMPARISON.md

## Global constraints
Preserve v28 verify/export API and error semantics. No live enablement or main merge.
Only six focused files; old reconstructed support is excluded from delivery.

## Review focus
Different accounts/settings; rewritten old trades; acknowledgement-only recovery;
rounding after large balances; replacement between validation and use.

## Tasks
- [x] Add failing tests with real PaperTradingSession snapshots, mutation fixtures,
  CLI channels, exact arithmetic and prior-file preservation.
- [x] Share one validated byte read; implement identity/history compatibility,
  original-context suffix replay and bounded full-inventory delta report.
- [x] Wire CLI compare and exit 5 without changing existing commands.
- [ ] Review failure cases, rerun related regressions and independent patch apply.
- [ ] Commit focused changes to existing draft branch; inspect exact-commit CI.

## Decisions
Ruling: continuous execution already requested; no new strategy/broker decisions.
Ruling: use isolated scoped reconstruction because full git clone failed DNS.
Ruling: source account is never opened by comparison; result describes captured
bytes, not current source paths. Saved capture clock reversal is a warning only;
revision determines order. No claim of a continuous authenticated audit trail.

Review: a failure-first CLI regression reproduced a second import from the error
handler leaking an exception. The handler now uses only the already-loaded error
class and preserves fixed JSON when imports fail. Inline review only; no independent
reviewer tool is available. Initial tests corrected a mistaken control API name;
corrected baseline had 29 expected feature failures before implementation.
