# trading-strategy

Strategy interfaces and implementations for the
[trading monorepo](../../README.md), supporting Python 3.10 and newer.

The package provides moving-average, RSI, and breakout strategies with
completed-candle evaluation. Direct analysis retains its legacy naïve UTC
signal timestamp; candle-driven evaluation uses the supplied candle's instant.

Install the core and data packages first, then run
`python -m pip install -e packages/strategy` from the repository root. The root
README describes the complete development environment.

Run `python -m pytest packages/strategy/tests/` for strategy regressions, or
`python tools/paper_lab.py` for the synthetic integrated paper experiment.
