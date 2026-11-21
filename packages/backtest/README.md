# trading-backtest

Historical replay and simulation components for the
[trading monorepo](../../README.md), supporting Python 3.10 and newer.

The package provides historical data loading, a virtual broker, strategy
execution, and performance reporting. Historical request epochs are calculated
from explicit UTC instants so that the host's local timezone does not shift the
requested window.

Install the workspace dependencies described in the root README, then run
`python -m pip install -e packages/backtest` from the repository root.

Run `python -m pytest packages/backtest/tests/` for package tests or
`python tools/offline_checks.py --include-backtest` for the expanded
network-forbidden verification suite. Simulated results are described in
[the paper lab guide](../../docs/PAPER_LAB.md).
