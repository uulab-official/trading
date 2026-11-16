# Offline chronological strategy research

Continue the approved paper-first platform without enabling trading or changing the existing execution engine.

A bounded candidate set is scored only on each training window. The selected candidate is then evaluated once on the next disjoint test window. Rolling/anchored windows and a gap are explicit. Warm-up bars precede the evaluated window; they may include the gap because they were already observable, but their signals, cash, positions and risk state are never carried into scoring. No shuffled splits, test-set optimization, live credentials or broker orders.

Reuse PaperRunner/PaperExchange via strategy_factory rather than implementing fills again. Each segment starts with identical cash, cost and risk assumptions. Terminal open positions are marked, not forcibly liquidated; the omitted exit cost is explicitly disclosed. With too few training fills, select no strategy and record a skipped/cash window. Selection ties resolve by candidate ID. Publish every training score, selected ID, test result and skipped window; do not report a compounded portfolio return, Sharpe or MDD from endpoint-only data.

Expose a separate read-only research report and page in the existing loopback server. A missing/corrupt report is unavailable, not a zero-return experiment. Validate deterministic selection, intervals, arithmetic, identity, bounds and exact schema before serving. Retain last-good browser data on refresh failure. No HTML rendering of supplied strings.

Alternatives: live brokerage integration requires credentials and real-provider testing; unrestricted parameter optimization encourages uncontrolled work and overfitting. This increment uses a capped local research job and keeps live work outside scope.

Limits: <=5000 contiguous bars, <=8 candidates, <=24 complete folds, train/test <=1000 bars, <=500 warm-up bars; a replay-work budget protects the original runner's prefix hashing. Output is immutable, bounded and never overwrites unrelated files. Hashes check identity, not exchange authenticity or investment value. Repeated human selection against test results remains an overfitting risk.

References (architecture only; no source copied): scikit-learn TimeSeriesSplit and Freqtrade lookahead/recursive analysis official documentation.
