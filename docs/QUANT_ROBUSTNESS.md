# Fixed-selection benchmark and cost diagnostics

This is optional offline research, not investment advice or a live-order gate. The existing `--robustness` flag on `tools/quant_research.py` creates schema-2 reports; without it the schema-1 path and experiment IDs remain unchanged. Existing readers now accept both versions. Old reports never show invented zero diagnostics.

## Run

Install the existing engine packages and supply a previously collected local history file:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
python tools/quant_research.py /tmp/uulab-btc-history.json \
  --output /tmp/uulab-btc-robustness.json \
  --train-size 120 --test-size 60 --gap 1 --warmup 100 \
  --initial-balance 1000000 --fee-pct 0.1 --slippage-pct 0.05 --robustness
python apps/dashboard/server.py --research-report /tmp/uulab-btc-robustness.json --port 8765
```

Open `http://127.0.0.1:8765/research.html` locally. Fees are modeling assumptions, not a broker fee quote. Output is immutable; choose a new filename for an additional experiment. The importer and any original POSIX-checkpoint constraints remain unchanged. This research path uses independent in-memory windows, no checkpoint.

## Comparison contract

Training selection is performed once using the base fee/slippage configuration. The selected strategy stays fixed while both rates are multiplied by exactly 1, 2 and 3. Stressed test results cannot reselect the strategy. Each stressed scenario is a fresh run of the original `PaperRunner`, so changed affordability or risk exits may change fills; stress is not simply a subtraction from profit. Higher costs need not produce monotonically lower returns. A zero rate remains zero; this is not an additive spread/liquidity stress model.

The cash reference earns zero interest. A skipped selection stays cash in every scenario and remains in all-window averages. The buy-and-hold reference uses the same initial `position_size_pct`, buys once at the second scored bar's open, and values at the final scored close. This matches the earliest possible next-bar execution boundary, not a claim of identical entry timing to the strategy. Original `PaperExchange` computes fees/slippage and balances, using the same eight-decimal quantity floor. The remainder stays cash. This is NOT a 100%-invested index benchmark, has no active risk exits, and is not risk/exposure matched. There is no terminal forced sale; future exit costs are not included.

Independent Decimal arithmetic checks the reference against its entry/mark prices and settings. Position value is projected as marked equity minus cash at the original 28-digit calculation precision; microscopic residual marks can round away without implying the purchase never occurred. Every strategy/reference difference is in **percentage points**, not relative percentage growth or risk-adjusted alpha. Summary values are arithmetic means over all independent test windows, not compounded portfolio returns. UI diagnostic rates are rounded to four decimals for display; hover/JSON retain original values.

## Validation and boundaries

The v2 run identity includes the fixed policy as well as the original experiment. The reader checks versions, exact fields, all folds, scenario ordering/rates, base-test agreement, skipped-cash accounting, reference math, timestamps and aggregate counts/totals. Missing/corrupt diagnostic reports fail as unavailable, not zero performance. A bounded work estimate includes all additional test runs and warm-up before evaluation starts. No-clobber publication remains in place. Neither hashes nor consistent arithmetic authenticate source prices or a trusted evaluator: an attacker with write access can fabricate a mutually consistent report.

No credentials, broker calls, public hosting, order submission, partial fills or deployment were added. No live provider access was needed for this increment. Separate holdout and prospective paper testing remain necessary; repeatedly inspecting these diagnostics while retuning can still overfit. No Sharpe, MDD or CAGR is inferred from endpoints.

## Verification

```bash
python -m unittest discover -s tests -p 'test_research_robustness.py' -v
python -m unittest discover -s tests -p 'test_robustness_cli.py' -v
python -m unittest discover -s tests -p 'test_robustness_engine.py' -v
node --test tests/research_robustness_client.test.mjs
# Optional Playwright + Chromium, explicitly synthetic and without browser HTTP:
python tools/check_research_robustness_ui.py --output /tmp/research-robustness-previews
```

Local sparse reconstruction: 13 new Python contract checks, 2 CLI checks and 7 Node tests passed. Chromium offline rendering passed desktop/mobile/dark, expansion, retained stale data, recovery, legacy unavailability and no page-level overflow. The missing-feature/flag/client tests were observed failing before implementation; a tiny-mark rounding mismatch was separately reproduced, tested and corrected. Original-engine and real read-only HTTP cases are additionally wired to GitHub's existing Python 3.10/3.12 integration job; consult the actual commit checks for their outcome. Container DNS prevented a full clone. Local tests are not a claim of locally running the whole original engine. No independent reviewer subagent was available; review was inline.

## Upstream design references (no copied implementation)

- QuantConnect slippage modeling: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts
- Freqtrade backtesting assumptions and dry-run limitations: https://www.freqtrade.io/en/stable/backtesting/

These informed explicit cost/assumption reporting. Their engines, source code and results were not copied or embedded.
