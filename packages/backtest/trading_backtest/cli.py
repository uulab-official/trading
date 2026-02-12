"""Backtest CLI - 백테스트 명령줄 인터페이스"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

from trading_strategy import RSIStrategy, MACrossStrategy, StrategyConfig
from trading_backtest.engines.backtest_engine import BacktestEngine, BacktestConfig
from trading_backtest.data_loader import HistoricalDataLoader


def run_backtest(args):
    """백테스트 실행"""
    print(f"🚀 Running backtest for {args.symbol} with {args.strategy} strategy")
    
    # 데이터 로드
    loader = HistoricalDataLoader(args.data_dir)
    try:
        df = loader.load_from_csv(args.symbol, args.timeframe)
    except FileNotFoundError:
        print(f"⚠️  Data not found locally. Fetching from {args.exchange}...")
        df = loader.fetch_from_exchange(args.exchange, args.symbol, args.timeframe, args.days)
        loader.save_to_csv(df, args.symbol, args.timeframe)
    
    print(f"📊 Loaded {len(df)} candles")
    
    # 전략 설정
    config = StrategyConfig(
        name=f"{args.strategy}_{args.symbol}",
        symbol=args.symbol,
        timeframe=args.timeframe
    )
    
    if args.strategy == "rsi":
        strategy = RSIStrategy(
            config,
            rsi_period=args.rsi_period,
            oversold=args.oversold,
            overbought=args.overbought
        )
    elif args.strategy == "ma_cross":
        strategy = MACrossStrategy(
            config,
            fast_period=args.fast_period,
            slow_period=args.slow_period
        )
    else:
        print(f"❌ Unknown strategy: {args.strategy}")
        sys.exit(1)
    
    # 백테스트 설정
    backtest_config = BacktestConfig(
        initial_balance=Decimal(str(args.balance)),
        commission_pct=Decimal(str(args.commission)),
        slippage_pct=Decimal(str(args.slippage)),
        position_size_pct=Decimal(str(args.position_size))
    )
    
    # 백테스트 실행
    engine = BacktestEngine(backtest_config)
    
    def progress(current, total):
        pct = (current / total) * 100
        print(f"\r⏳ Progress: {pct:.1f}%", end='', flush=True)
    
    result = engine.run(strategy, df, progress_callback=progress)
    print("\n✅ Backtest completed!")
    
    # 결과 출력
    print("\n" + "="*60)
    print("📈 BACKTEST RESULTS")
    print("="*60)
    print(f"Strategy:        {result.strategy_name}")
    print(f"Symbol:          {result.symbol}")
    print(f"Period:          {len(df)} candles")
    print(f"Initial Balance: ${float(result.initial_balance):,.2f}")
    print(f"Final Balance:   ${float(result.final_balance):,.2f}")
    print(f"Total Return:    {float(result.total_return_pct):+.2f}%")
    print(f"CAGR:            {float(result.cagr):.2f}%")
    print("-"*60)
    print("RISK METRICS")
    print("-"*60)
    print(f"Volatility:      {float(result.volatility):.2f}%")
    print(f"Max Drawdown:    {float(result.max_drawdown):.2f}%")
    print(f"Sharpe Ratio:    {float(result.sharpe_ratio):.2f}")
    print(f"Sortino Ratio:   {float(result.sortino_ratio):.2f}")
    print(f"Calmar Ratio:    {float(result.calmar_ratio):.2f}")
    print("-"*60)
    print("TRADE STATISTICS")
    print("-"*60)
    print(f"Total Trades:    {result.total_trades}")
    print(f"Win Rate:        {float(result.win_rate):.1f}%")
    print(f"Profit Factor:   {float(result.profit_factor):.2f}")
    print(f"Avg Win:         {float(result.avg_win):+.2f}%")
    print(f"Avg Loss:        {float(result.avg_loss):.2f}%")
    print(f"Expectancy:      {float(result.expectancy):+.2f}%")
    print("="*60)
    
    # 결과 저장
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # JSON 결과 저장
        import json
        with open(output_path, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\n💾 Results saved to {output_path}")
        
        # Equity curve 저장
        equity_path = output_path.with_suffix('.equity.csv')
        result.equity_curve.to_csv(equity_path, index=False)
        print(f"💾 Equity curve saved to {equity_path}")
    
    # 차트 생성
    if args.plot:
        fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
        
        # Price & Equity
        ax1 = axes[0]
        ax1_twin = ax1.twinx()
        
        ax1.plot(result.equity_curve['datetime'], result.equity_curve['price'], 
                label='Price', color='gray', alpha=0.5)
        ax1_twin.plot(result.equity_curve['datetime'], result.equity_curve['equity'], 
                     label='Equity', color='blue', linewidth=2)
        ax1.set_ylabel('Price')
        ax1_twin.set_ylabel('Equity ($)', color='blue')
        ax1.set_title(f'Backtest: {result.strategy_name}')
        ax1.legend(loc='upper left')
        ax1_twin.legend(loc='upper right')
        
        # Drawdown
        ax2 = axes[1]
        drawdown = (result.equity_curve['equity'] / result.equity_curve['equity'].cummax() - 1) * 100
        ax2.fill_between(result.equity_curve['datetime'], drawdown, 0, 
                        color='red', alpha=0.3)
        ax2.set_ylabel('Drawdown (%)')
        ax2.axhline(y=float(result.max_drawdown), color='red', linestyle='--', 
                   label=f'MDD: {float(result.max_drawdown):.2f}%')
        ax2.legend()
        
        # Position
        ax3 = axes[2]
        ax3.plot(result.equity_curve['datetime'], result.equity_curve['position'], 
                color='green', drawstyle='steps-post')
        ax3.set_ylabel('Position Size')
        ax3.set_xlabel('Date')
        
        plt.tight_layout()
        
        if args.output:
            chart_path = Path(args.output).with_suffix('.png')
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            print(f"📊 Chart saved to {chart_path}")
        
        plt.show()


def fetch_data(args):
    """히스토리컬 데이터 수집"""
    print(f"📥 Fetching {args.days} days of {args.timeframe} data for {args.symbol}")
    
    loader = HistoricalDataLoader(args.data_dir)
    df = loader.fetch_from_exchange(args.exchange, args.symbol, args.timeframe, args.days)
    loader.save_to_csv(df, args.symbol, args.timeframe)
    
    print(f"✅ Saved {len(df)} candles to data/historical/")


def main():
    parser = argparse.ArgumentParser(
        description='Trading Bot Backtest CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run RSI backtest
  python -m trading_backtest run --symbol BTC/USDT --strategy rsi --days 365
  
  # Run MA Cross backtest with custom parameters
  python -m trading_backtest run --symbol ETH/USDT --strategy ma_cross --fast-period 20 --slow-period 50
  
  # Fetch historical data
  python -m trading_backtest fetch --symbol BTC/USDT --timeframe 4h --days 730
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Run command
    run_parser = subparsers.add_parser('run', help='Run backtest')
    run_parser.add_argument('--symbol', required=True, help='Trading pair (e.g., BTC/USDT)')
    run_parser.add_argument('--strategy', required=True, choices=['rsi', 'ma_cross'], 
                           help='Strategy to test')
    run_parser.add_argument('--timeframe', default='1h', help='Candle timeframe (default: 1h)')
    run_parser.add_argument('--days', type=int, default=365, help='Days of data to use')
    run_parser.add_argument('--balance', type=float, default=10000, help='Initial balance')
    run_parser.add_argument('--commission', type=float, default=0.1, help='Commission percent')
    run_parser.add_argument('--slippage', type=float, default=0.05, help='Slippage percent')
    run_parser.add_argument('--position-size', type=float, default=10, help='Position size percent')
    run_parser.add_argument('--data-dir', default='data/historical', help='Data directory')
    run_parser.add_argument('--exchange', default='binance', help='Exchange to fetch data')
    run_parser.add_argument('--output', '-o', help='Output file path')
    run_parser.add_argument('--plot', action='store_true', help='Show/Generate charts')
    
    # RSI parameters
    run_parser.add_argument('--rsi-period', type=int, default=14)
    run_parser.add_argument('--oversold', type=float, default=30)
    run_parser.add_argument('--overbought', type=float, default=70)
    
    # MA parameters
    run_parser.add_argument('--fast-period', type=int, default=50)
    run_parser.add_argument('--slow-period', type=int, default=200)
    
    # Fetch command
    fetch_parser = subparsers.add_parser('fetch', help='Fetch historical data')
    fetch_parser.add_argument('--symbol', required=True, help='Trading pair')
    fetch_parser.add_argument('--timeframe', default='1h', help='Candle timeframe')
    fetch_parser.add_argument('--days', type=int, default=365, help='Days to fetch')
    fetch_parser.add_argument('--exchange', default='binance', help='Exchange')
    fetch_parser.add_argument('--data-dir', default='data/historical', help='Data directory')
    
    args = parser.parse_args()
    
    if args.command == 'run':
        run_backtest(args)
    elif args.command == 'fetch':
        fetch_data(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
