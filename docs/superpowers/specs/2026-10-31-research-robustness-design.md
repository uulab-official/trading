# Research robustness increment

Continue the approved paper-first research path, not live trading. Keep existing v1 reports and experiment identities unchanged unless `--robustness` is explicitly supplied. v2 reports add diagnostic comparisons at the same independently funded test windows.

Compare frozen training-selected strategies against zero-interest cash and one initial-allocation buy-and-hold. The baseline buys at the second scored bar's open (the earliest actionable point under the existing next-bar contract), uses the same position-size percentage, fee/slippage and eight-decimal quantity floor, marks at the last scored close, and never force-liquidates. It has no active risk exits; this is a reference, not a claim of equal risk or exposure.

Reuse PaperRunner for base/stressed strategies and PaperExchange for baseline execution. Do not reselect, retune, or drop skipped/losing folds. Run fixed fee/slippage multipliers 1, 2, 3; zero baseline costs remain zero. Costs may change risk behavior and therefore need not monotonically lower reported returns. Compute signed excess percentage points, all-window arithmetic means, worst return and strict benchmark-beating counts. No alpha, Sharpe, annualization, live-profit or compounded-portfolio claims.

Validate versions, experiment identity including diagnostic policy, fold alignment/timestamps, base metrics, finite bounded decimals, reference arithmetic and summary on read. Bound total work before any evaluator call. Publish using the existing no-clobber contract. UI must distinguish legacy diagnostics-unavailable from zeros; preserve last-good data on errors and render text only.

Options considered: duplicate standalone stress engine (rejected: divergent fills), blanket rerun/reselection at higher costs (rejected: changes the question), optional fixed-winner diagnostics in the existing report (chosen: backward compatible and auditable). No new broker, credential, network, public deployment or main merge.
