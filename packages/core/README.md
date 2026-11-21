# trading-core

Core infrastructure for the [trading monorepo](../../README.md), supporting
Python 3.10 and newer.

The package contains configuration, exchange interfaces, paper accounting,
order and portfolio management, risk controls, durable journals, and checkpoint
validation. Legacy order and position timestamps retain their naïve UTC format;
strict checkpoint serialization records an explicit UTC offset.

From the repository root, install with `python -m pip install -e packages/core`.
Follow the root README for the remaining workspace packages and test tools.

Run `python tools/offline_checks.py` from the root for the network-forbidden
safety and replay checks. See [the paper lab guide](../../docs/PAPER_LAB.md) for
the synthetic end-to-end experiment and its verification boundaries.
