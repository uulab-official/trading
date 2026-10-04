"""
24/7 Crypto Trading Bot
메인 애플리케이션
"""

import asyncio
import argparse
import logging
import signal
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
import os

# Core imports
from trading_core import (
    ExchangeInterface,
    PaperExchange,
    create_execution_exchange,
    validate_execution_mode,
    OrderManager,
    RiskManager,
    RiskConfig as RiskManagerConfig,
    Portfolio,
    SQLiteStateStore,
    BotState,
    OrderSide,
    OrderType,
    load_config,
    TelegramNotifier,
    NotificationConfig,
    NullNotifier,
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
from trading_core.legacy_checkpoint import LegacyCheckpoint, CheckpointError, is_legacy_paper_symbol


class RecoveryRiskGate:
    """Unknown risk is a diagnostic state, never zero loss or permission to buy."""
    def __init__(self, reason):
        self.reason = reason

    def check_trade_allowed(self, *args, **kwargs):
        return False, self.reason

    def is_trading_allowed(self):
        return False

    def reset_daily_stats(self):
        pass

    def get_risk_report(self):
        return {"daily_pnl": None, "daily_pnl_pct": None, "daily_trades": None,
                "daily_losses": None, "circuit_breaker": None,
                "cooldown_active": None, "recovery_required": self.reason}

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

    def __init__(self, config_dir: str = "config", *, enable_live_trading: bool = False):
        self.config = load_config(config_dir)
        self.enable_live_trading = enable_live_trading
        validate_execution_mode(self.config, enable_live_trading=enable_live_trading)
        self.running = False
        self._paper_checkpoint = None
        self._paper_lock = asyncio.Lock()
        self._paper_initialization_error = None
        self._paper_mode_enabled = self.config.mode == 'paper'

        # Core components
        self.exchange: Optional[ExchangeInterface] = None
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

        # Telegram notifier
        self.notifier = self._create_notifier()

    def _create_notifier(self):
        """텔레그램 노티파이어 생성"""
        if self.config.mode == 'paper':
            return NullNotifier()
        telegram_cfg = self.config.telegram

        if not telegram_cfg.enabled:
            logger.info("Telegram notifications disabled")
            return NullNotifier()

        try:
            notification_config = NotificationConfig(
                trade=telegram_cfg.notifications.get('trade', True),
                error=telegram_cfg.notifications.get('error', True),
                daily_report=telegram_cfg.notifications.get('daily_report', True),
                position=telegram_cfg.notifications.get('position', True),
            )

            notifier = TelegramNotifier(
                bot_token=telegram_cfg.bot_token,
                chat_id=telegram_cfg.chat_id,
                config=notification_config
            )

            if notifier.enabled:
                logger.info("Telegram notifier initialized")
            else:
                logger.warning("Telegram notifier failed to initialize")

            return notifier
        except Exception as e:
            logger.error(f"Failed to create Telegram notifier: {e}")
            return NullNotifier()

    async def initialize(self) -> None:
        """봇 초기화"""
        logger.info(f"Initializing Trading Bot: {self.config.name} v{self.config.version}")
        logger.info(f"Mode: {self.config.mode}")
        validate_execution_mode(self.config, enable_live_trading=self.enable_live_trading)

        # Validate configuration
        from trading_core.config_manager import get_config_manager
        config_mgr = get_config_manager()
        errors = config_mgr.validate_config()
        if errors:
            for error in errors:
                logger.error(f"Config error: {error}")
            raise ValueError("Invalid configuration")

        if self.config.mode == 'paper':
            await self._initialize_paper()
            return

        # State store
        state_path = "data/paper_bot_state.db" if self.config.mode == 'paper' else "data/bot_state.db"
        self.state_store = SQLiteStateStore(state_path)
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
        self.exchange = create_execution_exchange(
            self.config,
            enable_live_trading=self.enable_live_trading,
            initial_balance=self.portfolio.cash_balance,
            initial_positions={position.symbol: position.size for position in self.portfolio.get_open_positions()},
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
        if self.config.mode == 'live':
            self.websocket = BinanceWebSocket(testnet=exchange_cfg.testnet)

        # Setup strategies
        await self._setup_strategies()

        logger.info("Trading Bot initialized successfully")

        # Send startup notification
        await self.notifier.notify_startup(
            bot_name=self.config.name,
            version=self.config.version,
            mode=self.config.mode
        )

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
        
        # Offline paper execution has no external market-data startup.
        if self.config.mode == 'live':
            await self.websocket.connect()
            for symbol in self.candle_buffers.keys():
                await self.websocket.subscribe_ticker(symbol)
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

        if self._paper_mode_enabled:
            try:
                current = asyncio.current_task()
                tasks = [task for task in self._tasks if task is not current]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._save_state()
                if self.order_manager:
                    await self.order_manager.stop()
                if self.exchange:
                    await self.exchange.disconnect()
            finally:
                if self._paper_checkpoint:
                    self._paper_checkpoint.release()
            return
        
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

        # Send shutdown notification
        await self.notifier.notify_shutdown(
            bot_name=self.config.name,
            reason="User initiated"
        )

        logger.info("Trading Bot stopped")
    
    async def _main_loop(self) -> None:
        """메인 루프"""
        while self.running:
            try:
                # Check if trading is allowed
                if not self._paper_mode_enabled and not self.risk_manager.is_trading_allowed():
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
        timestamp = data.get('timestamp')
        
        if not symbol or price is None:
            return
        if not isinstance(price, Decimal) or not price.is_finite() or price < 0:
            logger.warning("Ignoring invalid price update for %s", symbol)
            return

        if getattr(self, '_paper_mode_enabled', False):
            await self._paper_price_update(symbol, price, timestamp)
            return
        
        # Update portfolio prices
        self.portfolio.update_position_prices({symbol: price})

        # A conservative valuation mark does not certify an executable quote.
        # Zero marks must not create automatic liquidation orders.
        if price == 0:
            return
        
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

        if getattr(self, '_paper_mode_enabled', False):
            await self._paper_process_signal(signal)
            return
        
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
        if getattr(self, '_paper_mode_enabled', False):
            await self._paper_process_signal(signal)
            return
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

            # Send notification
            await self.notifier.notify_position_opened(
                symbol=signal.symbol,
                side='long',
                size=size,
                entry_price=signal.price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=signal.strategy_id
            )

        except Exception as e:
            logger.error(f"Failed to open position: {e}")
            await self.notifier.notify_error(
                error_message=str(e),
                context=f"Open position {signal.symbol}"
            )
    
    async def _close_position(self, symbol: str, price: Decimal, reason: str) -> None:
        """포지션 청산"""
        if getattr(self, '_paper_mode_enabled', False):
            async with self._paper_lock:
                await self._paper_close_locked(symbol, price)
            return
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
            self.risk_manager.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)

            logger.info(
                f"Position closed: {symbol} @ {price} (PnL: {trade.pnl:.2f}, Reason: {reason})"
            )

            # Send notification
            await self.notifier.notify_position_closed(
                symbol=symbol,
                side='long',
                size=position.size,
                entry_price=position.entry_price,
                exit_price=price,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct if hasattr(trade, 'pnl_pct') else Decimal("0"),
                reason=reason
            )

        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            await self.notifier.notify_error(
                error_message=str(e),
                context=f"Close position {symbol}"
            )
    
    async def _update_candles(self) -> None:
        """캔들 데이터 업데이트 - 거래소에서 가져오기"""
        for symbol, buffer in self.candle_buffers.items():
            try:
                # 거래소에서 캔들 데이터 가져오기
                candles = await self.exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=buffer.timeframe,
                    limit=100  # 최근 100개 캔들
                )
                
                if candles:
                    # 캔들 버퍼 업데이트
                    for candle in candles:
                        buffer.add_candle(candle)
                    
                    logger.debug(f"Updated {len(candles)} candles for {symbol}")
                    
            except Exception as e:
                logger.error(f"Failed to fetch candles for {symbol}: {e}")
    
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
        """상태 저장 - 웹 대시보드를 위해 추가 상태 저장"""
        if getattr(self, '_paper_mode_enabled', False):
            async with self._paper_lock:
                if self._paper_checkpoint:
                    self._paper_checkpoint.observe_period()
                    self._paper_checkpoint.save()
            return
        if not self.bot_state or not self.portfolio:
            return
        
        # 기본 상태 저장
        self.bot_state.save_portfolio(self.portfolio.to_dict())
        self.bot_state.save_orders({
            'orders': {k: v.to_dict() for k, v in self.order_manager.get_all_orders().items()}
        })
        
        # 웹 대시보드를 위한 추가 상태 저장
        try:
            # 봇 실행 상태
            self.bot_state.store.save('bot:main:status', {
                'status': 'running' if self.running else 'stopped',
                'mode': self.config.mode,
                'bot_id': self.config.bot_id,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            # 포지션 정보 (웹에서 바로 읽을 수 있도록)
            positions = []
            for symbol, position in self.portfolio.positions.items():
                pos_dict = position.to_dict()
                pos_dict['symbol'] = symbol
                positions.append(pos_dict)
            
            self.bot_state.store.save('bot:main:positions', positions)
            
            # 리스크 상태
            self.bot_state.store.save('bot:main:risk', {
                'daily_pnl': float(self.risk_manager.daily_pnl),
                'daily_trades': self.risk_manager.daily_stats['trade_count'],
                'is_trading_allowed': self.risk_manager.is_trading_allowed(),
                'circuit_breaker': self.risk_manager.circuit_breaker_triggered
            })
            
            logger.debug("State saved with web dashboard data")
        except Exception as e:
            logger.error(f"Failed to save web dashboard state: {e}")
    
    async def _risk_check_loop(self) -> None:
        """리스크 체크 루프"""
        while self.running:
            try:
                # Reset daily stats at midnight
                if self._paper_mode_enabled:
                    async with self._paper_lock:
                        if self._paper_checkpoint:
                            self._paper_checkpoint.observe_period()
                            self._paper_checkpoint.save()
                            report = self._paper_checkpoint.risk_report()
                        else:
                            report = self.risk_manager.get_risk_report()
                else:
                    self.risk_manager.reset_daily_stats()
                    report = self.risk_manager.get_risk_report()
                if not report['circuit_breaker'] and report['daily_pnl_pct'] is not None and report['daily_pnl_pct'] < -2:
                    logger.warning(f"Approaching daily loss limit: {report['daily_pnl_pct']:.2f}%")
                
                await asyncio.sleep(300)  # 5분마다 체크
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Risk check error: {e}")
                await asyncio.sleep(60)

    @property
    def recovery_reason(self):
        return (self._paper_checkpoint.recovery_reason if self._paper_checkpoint
                else self._paper_initialization_error)

    def get_recovery_status(self):
        """Read-only diagnostics remain available when execution is blocked."""
        portfolio = self.portfolio.to_dict() if self.portfolio else None
        if portfolio is not None and self._paper_checkpoint and self._paper_checkpoint.risk is None:
            portfolio['daily_start_value'] = None
        return {"mode": self.config.mode, "recovery_required": self.recovery_reason,
                "portfolio": portfolio,
                "risk": (self._paper_checkpoint.risk_report() if self._paper_checkpoint
                         else self.risk_manager.get_risk_report() if self.risk_manager else None)}

    async def _initialize_paper(self):
        risk_cfg = self.config.risk
        # Preserve the legacy effective settings; unrelated config-forwarding
        # changes are deliberately outside this recovery patch.
        config = RiskManagerConfig(
            max_position_size_pct=Decimal(str(risk_cfg.max_position_size_pct)),
            max_positions=risk_cfg.max_positions,
            risk_per_trade_pct=Decimal(str(risk_cfg.risk_per_trade_pct)),
            max_daily_loss_pct=Decimal(str(risk_cfg.max_daily_loss_pct)),
            max_leverage=Decimal(str(risk_cfg.max_leverage)))
        try:
            if self.state_store is None:
                self.state_store = SQLiteStateStore("data/paper_bot_state.db", initialize=False)
            session = LegacyCheckpoint(self.state_store, self.config.bot_id, config)
            self._paper_checkpoint = session
            session.restore()
            self.portfolio, self.exchange, self.order_manager = session.portfolio, session.exchange, session.manager
            self.risk_manager = session.risk or RecoveryRiskGate(session.recovery_reason)
            self.bot_state = BotState(self.state_store, self.config.bot_id)
            if self.exchange is not None:
                await self.exchange.connect()  # Purely local PaperExchange; never a live adapter.
            if session.recovery_reason:
                logger.error("Paper recovery required: %s", session.recovery_reason)
        except Exception as error:
            self._paper_initialization_error = (str(error) if isinstance(error, CheckpointError)
                                                else "Paper state could not be opened; existing state preserved")
            self.risk_manager = RecoveryRiskGate(self._paper_initialization_error)
            if self._paper_checkpoint:
                self._paper_checkpoint.recovery_reason = self._paper_initialization_error
                self._paper_checkpoint.release()
        try:
            await self._setup_strategies()
        except BaseException:
            self._paper_initialization_error = "Paper initialization did not complete"
            if self._paper_checkpoint:
                self._paper_checkpoint.recovery_reason = self._paper_initialization_error
                self._paper_checkpoint.release()
            if self.exchange:
                await self.exchange.disconnect()
            raise

    async def _paper_process_signal(self, signal):
        async with self._paper_lock:
            session = self._paper_checkpoint
            if session is None or self.exchange is None or self.portfolio is None:
                return
            if not is_legacy_paper_symbol(signal.symbol):
                return
            if signal.type in (SignalType.SELL, SignalType.CLOSE):
                # Entry-only limits must not reject an explicit reducing exit.
                await self._paper_close_locked(signal.symbol, signal.price)
                return
            if signal.type != SignalType.BUY or not session.entry_allowed()[0]:
                return
            if not isinstance(signal.price, Decimal) or not signal.price.is_finite() or signal.price <= 0:
                return
            # A caller's valuation is not a certified execution price.
            try:
                quote = await self.exchange.fetch_ticker(signal.symbol)
            except ValueError:
                return
            if quote.last != signal.price:
                return
            try:
                session.validate_quote(signal.symbol, signal.price)
            except CheckpointError:
                return
            stop = signal.stop_loss if signal.stop_loss is not None else self.risk_manager.calculate_stop_loss(signal.price, 'buy')
            if not isinstance(stop, Decimal) or not stop.is_finite() or stop < 0 or stop >= signal.price:
                return
            size = self.risk_manager.calculate_position_size(signal.price, stop)
            if size <= 0 or size * signal.price > self.portfolio.cash_balance:
                return
            allowed, reason = self.risk_manager.check_trade_allowed(signal.symbol, 'buy', size, signal.price)
            # Persist a legitimate UTC rollover even when this entry is rejected.
            session.save()
            if not allowed:
                logger.warning("Paper entry rejected: %s", reason)
                return
            target = signal.take_profit if signal.take_profit is not None else self.risk_manager.calculate_take_profit(signal.price, stop, 'buy')
            if not isinstance(target, Decimal) or not target.is_finite() or target <= 0:
                return
            try:
                key = session.begin_intent('buy', signal.symbol, amount=size, price=signal.price)
                order = await self.order_manager.place_order(signal.symbol, OrderSide.BUY, OrderType.MARKET,
                    size, strategy_id=signal.strategy_id, client_order_id=key)
                if order.status.value != 'filled' or order.filled != size or order.remaining != 0:
                    raise CheckpointError("Paper entry did not fill completely")
                self.portfolio.open_position(signal.symbol, 'long', size, signal.price,
                    stop_loss=stop, take_profit=target, strategy_id=signal.strategy_id, order_id=key)
                session.finish_intent()
            except BaseException:
                session.fail_transition()
                raise

    async def _paper_close_locked(self, symbol, price):
        session = self._paper_checkpoint
        if session is None or self.portfolio is None or self.exchange is None or session.intent is not None:
            return
        if session.recovery_reason and not session.risk_only_recovery and not session.recovery_reason.startswith('UTC clock rollback'):
            return
        position = self.portfolio.get_position(symbol)
        if position is None or not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return
        try:
            quote = await self.exchange.fetch_ticker(symbol)
        except ValueError:
            return
        balances = await self.exchange.fetch_balance()
        holding = balances.get(symbol.split('/')[0])
        if quote.last != price or holding is None or holding.total != position.size:
            return
        try:
            session.validate_quote(symbol, price)
        except CheckpointError:
            return
        session.observe_period()
        try:
            key = session.begin_intent('sell', symbol, amount=position.size, price=price)
            order = await self.order_manager.place_order(symbol, OrderSide.SELL, OrderType.MARKET,
                position.size, client_order_id=key)
            if order.status.value != 'filled' or order.filled != position.size or order.remaining != 0:
                raise CheckpointError("Paper close did not fill completely")
            trade = self.portfolio.close_position(symbol, price)
            if session.risk is not None:
                prior_cooldown = session.risk._cooldown_until
                session.risk.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)
                if prior_cooldown is not None and session.risk._cooldown_until is not None:
                    session.risk._cooldown_until = max(prior_cooldown, session.risk._cooldown_until)
            session.finish_intent()
        except BaseException:
            session.fail_transition()
            raise

    async def _paper_price_update(self, symbol, price, timestamp=None):
        async with self._paper_lock:
            session = self._paper_checkpoint
            if session is None or self.portfolio is None or self.exchange is None or session.intent:
                return
            if not is_legacy_paper_symbol(symbol):
                return
            if session.recovery_reason and not session.risk_only_recovery and not session.recovery_reason.startswith('UTC clock rollback'):
                return
            try:
                previous = await self.exchange.fetch_ticker(symbol)
            except ValueError:
                previous = None
            observed_timestamp = (previous.timestamp if previous else 0) if timestamp is None else timestamp
            if type(observed_timestamp) is not int or observed_timestamp < 0:
                return
            if previous is not None and observed_timestamp < previous.timestamp:
                return
            session.observe_period()
            self.portfolio.update_position_prices({symbol: price})
            if price > 0:
                self.exchange.set_price(symbol, price, timestamp=observed_timestamp)
            # Recovery accepts observations for an explicit reducing exit, but
            # never automatically closes or overwrites the preserved source.
            if session.recovery_reason:
                return
            session.save()
            if price == 0:
                return
            position = self.portfolio.get_position(symbol)
            if position and (position.should_stop_loss() or position.should_take_profit()):
                await self._paper_close_locked(symbol, price)


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description="Trading bot; paper is the default execution mode")
    parser.add_argument('--config-dir', default='config')
    parser.add_argument('--enable-live-trading', action='store_true', help='User-only opt-in for real exchange trading; never required for paper mode')
    args = parser.parse_args()
    bot = TradingBot(args.config_dir, enable_live_trading=args.enable_live_trading)
    
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
