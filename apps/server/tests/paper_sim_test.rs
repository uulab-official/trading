use trading_server::core::paper_sim::{self, AttemptStatus, OrderStyle, ReplayInput, StrategyKind};
use uuid::Uuid;

fn input() -> ReplayInput {
    serde_json::from_str(include_str!("../config/paper_replay_example.json")).unwrap()
}

#[test]
fn delayed_open_partial_fill_and_fees_are_accounted_without_future_volume() {
    let mut scenario = input();
    scenario.strategies.truncate(1);
    // End before the recovery crossover to keep the inventory fully exited.
    scenario.bars.truncate(7);
    let report = paper_sim::simulate(&scenario).unwrap();
    let r = &report.results[0];
    assert_eq!(r.attempts.len(), 2);
    let buy = &r.attempts[0];
    assert_eq!(buy.signal_ms, scenario.bars[2].open_ms + 59_999);
    assert_eq!(buy.execution_ms, Some(scenario.bars[3].open_ms));
    assert_eq!(buy.requested_lots, 4);
    assert_eq!(buy.filled_lots, 3);
    assert_eq!(buy.fill_price_atoms_per_lot, Some(120));
    assert_eq!(buy.fee_atoms, 4);
    assert_eq!(buy.status, AttemptStatus::PartialThenCanceled);
    let sell = &r.attempts[1];
    assert_eq!(sell.fill_price_atoms_per_lot, Some(80));
    assert_eq!(sell.filled_lots, 3);
    assert_eq!(sell.fee_atoms, 3);
    assert_eq!(r.final_equity_atoms, 9873);
    assert_eq!(r.realized_pnl_atoms, -127);
    assert_eq!(r.unrealized_pnl_atoms, 0);
    assert_eq!(r.fees_atoms, 7);
    assert_eq!(r.return_bps, -127);
    assert_eq!(r.remaining_lots, 0);
    assert_eq!(r.max_sampled_drawdown_bps, 127);
    assert_eq!(paper_sim::simulate(&scenario).unwrap(), report);

    // Current candle volume and close are not known at its open.
    scenario.bars[3].volume = "0".into();
    scenario.bars[3].close = "121".into();
    scenario.bars[3].high = "121".into();
    let changed = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(changed.results[0].attempts[0], *buy);
}

#[test]
fn limit_ioc_does_not_cross_gap_and_market_slippage_is_adverse() {
    let mut scenario = input();
    scenario.strategies.truncate(1);
    scenario.bars.truncate(7);
    scenario.execution.style = OrderStyle::Limit { offset_bps: 0 };
    let report = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(report.results[0].attempts[0].status, AttemptStatus::LimitNotMet);
    assert_eq!(report.results[0].fills, 0);
    assert_eq!(report.results[0].final_equity_atoms, 10000);
    scenario.execution.style = OrderStyle::Market;
    scenario.execution.slippage_bps = 100;
    let report = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(report.results[0].attempts[0].fill_price_atoms_per_lot, Some(122));
    assert_eq!(report.results[0].attempts[1].fill_price_atoms_per_lot, Some(79));
}

#[test]
fn warmup_only_and_last_signal_never_create_same_bar_execution() {
    let mut scenario = input();
    scenario.strategies.truncate(1);
    scenario.bars.truncate(3);
    let report = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(report.results[0].attempts.len(), 1);
    assert_eq!(report.results[0].attempts[0].status, AttemptStatus::Expired);
    assert_eq!(report.results[0].attempts[0].execution_ms, None);
    assert_eq!(report.results[0].fills, 0);
    scenario.bars.truncate(2);
    scenario.warmup_bars = 1;
    let report = paper_sim::simulate(&scenario).unwrap();
    assert!(report.results[0].attempts.is_empty());
}

#[test]
fn risk_halt_survives_exit_and_zero_liquidity_cancels() {
    let mut scenario = input();
    scenario.strategies.truncate(1);
    scenario.bars.truncate(7);
    scenario.risk.max_daily_loss_bps = 50;
    let report = paper_sim::simulate(&scenario).unwrap();
    assert!(report.results[0].buys_halted);
    assert_eq!(report.results[0].remaining_lots, 0);
    assert_eq!(report.results[0].realized_pnl_atoms, -127);
    scenario.bars[2].volume = "0".into();
    let report = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(report.results[0].attempts[0].status, AttemptStatus::NoLiquidity);
    assert_eq!(report.results[0].final_equity_atoms, 10000);
}

#[test]
fn strategies_are_independent_and_share_only_input() {
    let scenario = input();
    let report = paper_sim::simulate(&scenario).unwrap();
    assert_eq!(report.results.len(), 2);
    for (index, result) in report.results.iter().enumerate() {
        let mut single = scenario.clone();
        single.strategies = vec![scenario.strategies[index].clone()];
        assert_eq!(&paper_sim::simulate(&single).unwrap().results[0], result);
    }
    let html = paper_sim::html(&report).unwrap();
    assert!(html.contains("Independent strategy comparison"));
    assert!(html.contains("Historical simulation only"));
    assert!(html.contains("polyline"));
    assert!(!html.contains("<script"));
}

#[test]
fn invalid_scenarios_fail_before_a_report_or_destination() {
    let mut gap = input();
    gap.bars[3].open_ms += 60_000;
    assert!(paper_sim::simulate(&gap).is_err());
    let mut bad = input();
    bad.bars[3].high = "119".into();
    assert!(paper_sim::simulate(&bad).is_err());
    let mut bad = input();
    bad.bars[3].close = "120.1".into();
    assert!(paper_sim::simulate(&bad).is_err());
    let mut bad = input();
    bad.strategies[1].id = "MA".into();
    assert!(paper_sim::simulate(&bad).is_err());
    let mut bad = input();
    bad.strategies[0].strategy = StrategyKind::MaCross { fast: 2, slow: 2 };
    assert!(paper_sim::simulate(&bad).is_err());
    let mut bad = input();
    bad.execution.delay_bars = 0;
    assert!(paper_sim::simulate(&bad).is_err());
    let mut bad = input();
    bad.warmup_bars = 0;
    assert!(paper_sim::simulate(&bad).is_err());
}

#[tokio::test]
async fn persistence_is_idempotent_and_rejects_different_or_damaged_history() {
    let path = std::env::temp_dir().join(format!("paper-replay-{}", Uuid::new_v4()));
    std::fs::create_dir(&path).unwrap();
    let scenario = input();
    let report = paper_sim::persist(&scenario, &path).await.unwrap();
    assert_eq!(paper_sim::persist(&scenario, &path).await.unwrap(), report);
    assert!(path.join("paper-ma.sqlite").is_file());
    assert!(path.join("paper-rsi.sqlite").is_file());
    let mut changed = scenario.clone();
    changed.bars[0].volume = "19".into();
    assert!(paper_sim::persist(&changed, &path).await.is_err());
    assert_eq!(paper_sim::persist(&scenario, &path).await.unwrap(), report);
    std::fs::remove_file(path.join("paper-rsi.sqlite")).unwrap();
    assert_eq!(paper_sim::persist(&scenario, &path).await.unwrap(), report);
    std::fs::remove_file(path.join("paper-ma.sqlite")).unwrap();
    std::fs::write(path.join("paper-ma.sqlite"), b"damaged").unwrap();
    assert!(paper_sim::persist(&scenario, &path).await.is_err());
    assert_eq!(std::fs::read(path.join("paper-ma.sqlite")).unwrap(), b"damaged");
    std::fs::remove_dir_all(path).unwrap();
}

#[test]
fn cli_replay_outputs_json_and_static_html_on_local_fixture() {
    let path = std::env::temp_dir().join(format!("paper-replay-cli-{}", Uuid::new_v4()));
    std::fs::create_dir(&path).unwrap();
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("config/paper_replay_example.json");
    for html in [false, true] {
        let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_trading-server"));
        command.env_clear().arg("paper-replay").arg("--input").arg(&fixture).arg("--output-dir").arg(&path);
        if html { command.arg("--html"); }
        let result = command.output().unwrap();
        assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
        if html {
            assert!(String::from_utf8(result.stdout).unwrap().contains("<!doctype html>"));
        } else {
            let parsed: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
            assert_eq!(parsed["mode"], "offline_paper_replay");
            assert_eq!(parsed["results"].as_array().unwrap().len(), 2);
        }
    }
    std::fs::remove_dir_all(path).unwrap();
}
