# Quality and UTC implementation plan

Date: 2026-11-21 (Asia/Seoul)

1. Establish the published baseline and missing credential status. Inspect existing
   warnings, CI scripts, timestamp contracts, and current tool compatibility.
2. In parallel, remove deprecated UTC calls in core/server and in
   strategy/backtest/monitor. Demonstrate relevant failures with regression tests,
   preserve legacy serialized formats, and fix timezone-dependent epoch conversion.
3. Add failing lint failure-propagation regressions, pin test/lint tools, enable
   required formatting checks across all three source roots, and configure pytest's
   plugin loading and UTC deprecation errors.
4. Once concurrent edits finish, apply isort then Black once across the source roots.
   Review import-order side effects and verify all required checks.
5. Obtain fresh independent review. Resolve material findings and rerun the affected
   checks plus the required full verification gates.
6. Update README and verification evidence. Publish with an expected-head lease,
   verify remote file bytes, and wait for exact-revision full CI completion.
