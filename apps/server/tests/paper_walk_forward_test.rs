
use trading_server::core::paper_sim::{self, ReplayBar, ReplayInput, WalkForwardConfig};

fn input() -> ReplayInput {
    serde_json::from_str(include_str!("../config/paper_replay_example.json")).unwrap()
}

fn options() -> WalkForwardConfig {
    WalkForwardConfig {
        train_bars: 4, test_bars: 2, min_train_fills: 1, max_train_drawdown_bps: 10_000,
    }
}

#[test]
fn train_only_selection_and_fresh_test_account() {
    let source = input();
    let report = paper_sim::walk_forward(&source, options()).unwrap();
    assert_eq!(report.folds.len(), 1);
    assert_eq!(report.unused_trailing_bars, 1);
    assert_eq!(report.evaluated_folds, 1);
    let first = &report.folds[0];
    assert_eq!(first.train_start_ms, source.bars[2].open_ms);
    assert_eq!(first.train_end_ms, source.bars[5].open_ms + 59_999);
    assert_eq!(first.test_start_ms, source.bars[6].open_ms);
    let result = first.test_result.as_ref().unwrap();
    assert_eq!(first.selected_strategy.as_ref(), Some(&result.id));
    assert_eq!(result.initial_cash_atoms, source.account.initial_cash);
    assert!(result.attempts.iter().all(|a| a.signal_ms >= first.test_start_ms));
    assert_eq!(result.equity_curve.len(), 2);
    assert_eq!(report.mean_test_return_bps, Some(result.return_bps));
    assert_eq!(report, paper_sim::walk_forward(&source, options()).unwrap());
    let mut changed = source.clone();
    for bar in &mut changed.bars[6..8] {
        bar.open = "200".into(); bar.high = "210".into();
        bar.low = "190".into(); bar.close = "205".into(); bar.volume = "99".into();
    }
    let later = paper_sim::walk_forward(&changed, options()).unwrap();
    assert_ne!(report.experiment_sha256, later.experiment_sha256);
    assert_eq!(first.training_candidates, later.folds[0].training_candidates);
    assert_eq!(first.selected_strategy, later.folds[0].selected_strategy);
}

#[test]
fn no_eligible_candidate_means_no_test_return() {
    let report = paper_sim::walk_forward(&input(), WalkForwardConfig {
        min_train_fills: 4, ..options()
    }).unwrap();
    assert_eq!(report.evaluated_folds, 0);
    assert_eq!(report.skipped_folds, 1);
    assert_eq!(report.mean_test_return_bps, None);
    assert_eq!(report.worst_test_drawdown_bps, None);
    assert!(report.folds[0].selected_strategy.is_none());
    assert!(report.folds[0].test_result.is_none());
}

#[test]
fn rolling_test_windows_never_overlap() {
    let mut source = input();
    for price in [110, 90, 80, 120, 130, 70] {
        let open_ms = source.bars.last().unwrap().open_ms + 60_000;
        let p = price.to_string();
        source.bars.push(ReplayBar { open_ms, open: p.clone(), high: p.clone(),
            low: p.clone(), close: p, volume: "20".into() });
    }
    let report = paper_sim::walk_forward(&source, options()).unwrap();
    assert_eq!(report.folds.len(), 4);
    assert_eq!(report.unused_trailing_bars, 1);
    for pair in report.folds.windows(2) {
        assert_eq!(pair[0].test_end_ms + 1, pair[1].test_start_ms);
        assert_eq!(pair[1].train_end_ms + 1, pair[1].test_start_ms);
    }
}

#[test]
fn ties_have_stable_lexical_order() {
    let mut source = input();
    let mut a = source.strategies[0].clone();
    a.id = "zeta".into();
    let mut b = a.clone();
    b.id = "alpha".into();
    source.strategies = vec![a, b];
    let report = paper_sim::walk_forward(&source, options()).unwrap();
    assert_eq!(report.folds[0].selected_strategy.as_deref(), Some("alpha"));
    source.strategies.reverse();
    let report = paper_sim::walk_forward(&source, options()).unwrap();
    assert_eq!(report.folds[0].selected_strategy.as_deref(), Some("alpha"));
}

#[test]
fn invalid_windows_and_trailing_input_are_refused() {
    let source = input();
    for config in [
        WalkForwardConfig { train_bars: 0, ..options() },
        WalkForwardConfig { test_bars: 1, ..options() },
        WalkForwardConfig { train_bars: usize::MAX, ..options() },
        WalkForwardConfig { test_bars: 100, ..options() },
        WalkForwardConfig { min_train_fills: 0, ..options() },
        WalkForwardConfig { max_train_drawdown_bps: 10001, ..options() },
    ] {
        assert!(paper_sim::walk_forward(&source, config).is_err());
    }
    let mut invalid = source;
    invalid.bars.last_mut().unwrap().open = "0".into();
    assert!(paper_sim::walk_forward(&invalid, options()).is_err());
}
