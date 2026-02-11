"""
24/7 Crypto Trading Bot
메인 애플리케이션
"""

import asyncio
import logging
import signal
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
import os

# Core imports
from trading_core import (
    CCXTExchange,
    OrderManager,
    RiskManager,
    RiskConfig as RiskManagerConfig,
    Portfolio,
    SQLiteStateStore,
    BotState,
    OrderSide,
    OrderType,
    load_config,
)

# Data imports
from trading_data import BinanceWebSocket, CandleBuffer

# Strategy imports
from trading_strategy import (
    Strategy,
    StrategyConfig,
    Signal,
    SignalType,
    MACrossStrategy,
    RSIStrategy,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/trading-bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class TradingBot:
    """메인 트레이딩 봇"""

    def __init__(self, config_dir: str = "config"):
        self.config = load_config(config_dir)
        self.running = False

        # Core components
        self.exchange: Optional[CCXTExchange] = None
        self.order_manager: Optional[OrderManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.portfolio: Optional[Portfolio] = None
        self.state_store: Optional[SQLiteStateStore] = None
        self.bot_state: Optional[BotState] = None

        # Data
        self.websocket: Optional[BinanceWebSocket] = None
        self.candle_buffers: Dict[str, CandleBuffer] = {}

        # Strategies
        self.strategies: List[Strategy] = []

        # Tasks
        self._tasks: List[asyncio.Task] = []
    
    async def initialize(self) -> None:
        """봇 초기화"""
        logger.info(f"Initializing Trading Bot: {self.config.name} v{self.config.version}")
        logger.info(f"Mode: {self.config.mode}")

        # Validate configuration
        from trading_core.config_manager import get_config_manager
        config_mgr = get_config_manager()
        errors = config_mgr.validate_config()
        if errors:
            for error in errors:
                logger.error(f"Config error: {error}")
            raise ValueError("Invalid configuration")

        # State store
        self.state_store = SQLiteStateStore("data/bot_state.db")
        self.bot_state = BotState(self.state_store, self.config.bot_id)

        # Restore or create portfolio
        portfolio_data = self.bot_state.load_portfolio()
        if portfolio_data:
            self.portfolio = Portfolio.from_dict(portfolio_data)
            logger.info(f"Portfolio restored: {self.portfolio.get_total_value()}")
        else:
            initial_balance = Decimal("10000")  # Default initial balance
            self.portfolio = Portfolio(initial_balance)
            logger.info(f"New portfolio created with {initial_balance}")

        # Exchange
        exchange_cfg = self.config.exchange
        self.exchange = CCXTExchange(
            exchange_id=exchange_cfg.id,
            api_key=exchange_cfg.api_key,
            api_secret=exchange_cfg.api_secret,
            sandbox=exchange_cfg.sandbox,
        )
        await self.exchange.connect()
        logger.info(f"Connected to {exchange_cfg.id} (sandbox: {exchange_cfg.sandbox})")

        # Order manager
        self.order_manager = OrderManager(self.exchange)
        await self.order_manager.start()

        # Restore orders
        orders_data = self.bot_state.load_orders()
        if orders_data:
            # TODO: Restore orders from state
            pass

        # Risk manager
        risk_cfg = self.config.risk
        risk_config = RiskManagerConfig(
            max_position_size_pct=Decimal(str(risk_cfg.max_position_size_pct)),
            max_positions=risk_cfg.max_positions,
            risk_per_trade_pct=Decimal(str(risk_cfg.risk_per_trade_pct)),
            max_daily_loss_pct=Decimal(str(risk_cfg.max_daily_loss_pct)),
            max_leverage=Decimal(str(risk_cfg.max_leverage)),
        )
        self.risk_manager = RiskManager(risk_config, self.portfolio)
        logger.info("Risk manager initialized")

        # WebSocket
        self.websocket = BinanceWebSocket(testnet=exchange_cfg.testnet)

        # Setup strategies
        await self._setup_strategies()

        logger.info("Trading Bot initialized successfully")
    
    async def _setup_strategies(self) -> None:
        """전략 설정"""
        strategies_config = self.config.strategies

        for strat_config in strategies_config:
            if not strat_config.enabled:
                continue

            name = strat_config.name
            symbol = strat_config.symbol
            timeframe = strat_config.timeframe
            strategy_type = strat_config.type
            params = strat_config.params

            strategy_cfg = StrategyConfig(
                name=name,
                symbol=symbol,
                timeframe=timeframe,
            )

            # Create strategy based on type
            if strategy_type == 'ma_cross':
                strategy = MACrossStrategy(
                    strategy_cfg,
                    fast_period=params.get('fast_period', 50),
                    slow_period=params.get('slow_period', 200),
                )
            elif strategy_type == 'rsi':
                strategy = RSIStrategy(
                    strategy_cfg,
                    rsi_period=params.get('period', 14),
                    oversold=params.get('oversold', 30),
                    overbought=params.get('overbought', 70),
                )
            else:
                logger.warning(f"Unknown strategy type: {strategy_type}")
                continue

            self.strategies.append(strategy)

            # Setup candle buffer
            if symbol not in self.candle_buffers:
                self.candle_buffers[symbol] = CandleBuffer(
                    max_size=500,
                    timeframe=timeframe
                )

            logger.info(f"Strategy added: {name} ({strategy_type}) for {symbol}")
    
    async def start(self) -> None:
        """봇 시작"""
        self.running = True
        logger.info("Starting Trading Bot...")
        
        # Start WebSocket
        await self.websocket.connect()
        
        # Subscribe to symbols
        for symbol in self.candle_buffers.keys():
            await self.websocket.subscribe_ticker(symbol)
        
        # Setup WebSocket handlers
        self.websocket.on('ticker', self._on_price_update)
        
        # Start main loops
        self._tasks = [
            asyncio.create_task(self._main_loop()),
            asyncio.create_task(self._save_state_loop()),
            asyncio.create_task(self._risk_check_loop()),
        ]
        
        # Wait for all tasks
        await asyncio.gather(*self._tasks, return_exceptions=True)
    
    async def stop(self) -> None:
        """봇 중지"""
        logger.info("Stopping Trading Bot...")
        self.running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        # Stop components
        if self.order_manager:
            await self.order_manager.stop()
        
        if self.websocket:
            await self.websocket.disconnect()
        
        if self.exchange:
            await self.exchange.disconnect()
        
        # Save final state
        await self._save_state()
        
        logger.info("Trading Bot stopped")
    
    async def _main_loop(self) -> None:
        """메인 루프"""
        while self.running:
            try:
                # Check if trading is allowed
                if not self.risk_manager.is_trading_allowed():
                    await asyncio.sleep(60)
                    continue
                
                # Fetch historical data for candles
                await self._update_candles()
                
                # Run strategies
                for strategy in self.strategies:
                    if not strategy.is_active:
                        continue
                    
                    symbol = strategy.symbol
                    if symbol in self.candle_buffers:
                        candles = self.candle_buffers[symbol]
                        signal = strategy.on_candle(candles.current) if candles.current else None
                        
                        if signal and signal.type != SignalType.HOLD:
                            await self._process_signal(signal)
                
                await asyncio.sleep(60)  # 1분마다 체크
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(10)
    
    async def _on_price_update(self, data: Dict) -> None:
        """가격 업데이트 핸들러"""
        symbol = data.get('symbol')
        price = data.get('price')
        timestamp = data.get('timestamp', 0)
        
        if not symbol or not price:
            return
        
        # Update portfolio prices
        self.portfolio.update_position_prices({symbol: price})
        
        # Check stop loss / take profit
        position = self.portfolio.get_position(symbol)
        if position:
            if position.should_stop_loss():
                logger.warning(f"Stop loss triggered for {symbol}")
                await self._close_position(symbol, price, "stop_loss")
            elif position.should_take_profit():
                logger.info(f"Take profit triggered for {symbol}")
                await self._close_position(symbol, price, "take_profit")
    
    async def _process_signal(self, signal: Signal) -> None:
        """신호 처리"""
        logger.info(f"Processing signal: {signal.type.value} {signal.symbol}")
        
        # Risk check
        allowed, reason = self.risk_manager.check_trade_allowed(
            symbol=signal.symbol,
            side=signal.type.value,
            amount=signal.position_size or Decimal("0"),
            price=signal.price
        )
        
        if not allowed:
            logger.warning(f"Trade rejected by risk manager: {reason}")
            return
        
        # Execute trade
        if signal.type == SignalType.BUY:
            await self._open_long_position(signal)
        elif signal.type == SignalType.SELL:
            # 현물에서는 매도 = 포지션 청산만 가능
            position = self.portfolio.get_position(signal.symbol)
            if position:
                await self._close_position(signal.symbol, signal.price, "signal")
    
    async def _open_long_position(self, signal: Signal) -> None:
        """롱 포지션 진입"""
        try:
            # Calculate position size
            stop_loss = signal.stop_loss or self.risk_manager.calculate_stop_loss(
                entry_price=signal.price,
                side='buy'
            )
            
            size = self.risk_manager.calculate_position_size(
                entry_price=signal.price,
                stop_loss_price=stop_loss
            )
            
            # Execute order
            order = await self.order_manager.place_order(
                symbol=signal.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=size,
                strategy_id=signal.strategy_id
            )
            
            # Update portfolio
            take_profit = signal.take_profit or self.risk_manager.calculate_take_profit(
                entry_price=signal.price,
                stop_loss_price=stop_loss,
                side='buy'
            )
            
            self.portfolio.open_position(
                symbol=signal.symbol,
                side='long',
                size=size,
                entry_price=signal.price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_id=signal.strategy_id,
                order_id=order.client_order_id
            )
            
            logger.info(f"Long position opened: {signal.symbol} {size} @ {signal.price}")
            
        except Exception as e:
            logger.error(f"Failed to open position: {e}")
    
    async def _close_position(self, symbol: str, price: Decimal, reason: str) -> None:
        """포지션 청산"""
        try:
            position = self.portfolio.get_position(symbol)
            if not position:
                return
            
            # Execute sell order
            await self.order_manager.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                amount=position.size
            )
            
            # Update portfolio
            trade = self.portfolio.close_position(symbol, price)
            
            # Update risk manager
            self.risk_manager.on_position_closed(trade.pnl)
            
            logger.info(
                f"Position closed: {symbol} @ {price} (PnL: {trade.pnl:.2f}, Reason: {reason})"
            )
            
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
    
    async def _update_candles(self) -> None:
        """캔들 데이터 업데이트"""
        # TODO: Fetch historical candles from exchange
        pass
    
    async def _save_state_loop(self) -> None:
        """상태 저장 루프"""
        while self.running:
            try:
                await self._save_state()
                await asyncio.sleep(60)  # 1분마다 저장
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Save state error: {e}")
                await asyncio.sleep(10)
    
    async def _save_state(self) -> None:
        """상태 저장"""
        if not self.bot_state or not self.portfolio:
            return
        
        self.bot_state.save_portfolio(self.portfolio.to_dict())
        self.bot_state.save_orders({
            'orders': {k: v.to_dict() for k, v in self.order_manager.get_all_orders().items()}
        })
        
        logger.debug("State saved")
    
    async def _risk_check_loop(self) -> None:
        """리스크 체크 루프"""
        while self.running:
            try:
                # Reset daily stats at midnight
                self.risk_manager.reset_daily_stats()
                
                # Log risk status
                report = self.risk_manager.get_risk_report()
                if not report['circuit_breaker'] and report['daily_pnl_pct'] < -2:
                    logger.warning(f"Approaching daily loss limit: {report['daily_pnl_pct']:.2f}%")
                
                await asyncio.sleep(300)  # 5분마다 체크
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Risk check error: {e}")
                await asyncio.sleep(60)


def main():
    """메인 함수"""
    bot = TradingBot()
    
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        asyncio.create_task(bot.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    async def run():
        await bot.initialize()
        await bot.start()
    
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
