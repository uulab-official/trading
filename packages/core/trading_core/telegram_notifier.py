"""Telegram Notifier - 텔레그램 알림 시스템"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# telegram 라이브러리는 선택적 의존성
try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Telegram notifications disabled.")


@dataclass
class NotificationConfig:
    """알림 설정"""
    trade: bool = True
    error: bool = True
    daily_report: bool = True
    position: bool = True
    price_alert: bool = False


class TelegramNotifier:
    """텔레그램 알림 관리자"""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        config: Optional[NotificationConfig] = None
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.config = config or NotificationConfig()
        self.bot: Optional[Any] = None
        self.enabled = False

        if not TELEGRAM_AVAILABLE:
            logger.warning("Telegram library not available")
            return

        if not bot_token or not chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")
            return

        try:
            self.bot = Bot(token=bot_token)
            self.enabled = True
            logger.info("Telegram notifier initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")

    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송"""
        if not self.enabled or not self.bot:
            return False

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            return True
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            return False

    async def notify_trade(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        pnl: Optional[Decimal] = None,
        strategy: str = ""
    ) -> bool:
        """거래 알림"""
        if not self.config.trade:
            return False

        emoji = "🟢" if side.lower() == "buy" else "🔴"
        pnl_text = f"\n💰 PnL: <b>{pnl:+.2f} USDT</b>" if pnl is not None else ""
        strategy_text = f" ({strategy})" if strategy else ""

        message = f"""
{emoji} <b>Trade Executed{strategy_text}</b>

📊 Symbol: <code>{symbol}</code>
🔄 Side: <b>{side.upper()}</b>
📈 Size: <b>{size}</b>
💵 Price: <b>{price:.2f} USDT</b>{pnl_text}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_position_opened(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        entry_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        strategy: str = ""
    ) -> bool:
        """포지션 진입 알림"""
        if not self.config.position:
            return False

        emoji = "🟢" if side.lower() == "long" else "🔴"
        strategy_text = f" ({strategy})" if strategy else ""

        sl_text = f"\n🛑 Stop Loss: <b>{stop_loss:.2f}</b>" if stop_loss else ""
        tp_text = f"\n🎯 Take Profit: <b>{take_profit:.2f}</b>" if take_profit else ""

        message = f"""
{emoji} <b>Position Opened{strategy_text}</b>

📊 Symbol: <code>{symbol}</code>
🔄 Side: <b>{side.upper()}</b>
📈 Size: <b>{size}</b>
💵 Entry Price: <b>{entry_price:.2f} USDT</b>{sl_text}{tp_text}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_position_closed(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        entry_price: Decimal,
        exit_price: Decimal,
        pnl: Decimal,
        pnl_pct: Decimal,
        reason: str = "signal"
    ) -> bool:
        """포지션 청산 알림"""
        if not self.config.position:
            return False

        pnl_emoji = "🟢" if pnl >= 0 else "🔴"

        message = f"""
{pnl_emoji} <b>Position Closed</b>

📊 Symbol: <code>{symbol}</code>
🔄 Side: <b>{side.upper()}</b>
📈 Size: <b>{size}</b>
💵 Entry: <b>{entry_price:.2f}</b> → Exit: <b>{exit_price:.2f}</b>

💰 PnL: <b>{pnl:+.2f} USDT</b> ({pnl_pct:+.2f}%)
📝 Reason: <code>{reason}</code>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_error(self, error_message: str, context: str = "") -> bool:
        """에러 알림"""
        if not self.config.error:
            return False

        context_text = f"\n📍 Context: <code>{context}</code>" if context else ""

        message = f"""
🚨 <b>Error Alert</b>{context_text}

❌ {error_message}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_daily_report(
        self,
        date: str,
        total_pnl: Decimal,
        total_trades: int,
        win_count: int,
        loss_count: int,
        win_rate: float,
        portfolio_value: Decimal
    ) -> bool:
        """일일 리포트"""
        if not self.config.daily_report:
            return False

        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        message = f"""
📊 <b>Daily Trading Report - {date}</b>

{pnl_emoji} Total PnL: <b>{total_pnl:+.2f} USDT</b>
📈 Total Trades: <b>{total_trades}</b>
✅ Wins: <b>{win_count}</b> | ❌ Losses: <b>{loss_count}</b>
🎯 Win Rate: <b>{win_rate:.1f}%</b>
💼 Portfolio Value: <b>{portfolio_value:.2f} USDT</b>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_risk_alert(
        self,
        alert_type: str,
        message: str,
        current_value: Optional[Decimal] = None
    ) -> bool:
        """리스크 알림"""
        emoji_map = {
            "daily_loss_limit": "⚠️",
            "circuit_breaker": "🛑",
            "cooldown": "⏸️",
            "max_positions": "📊",
        }
        emoji = emoji_map.get(alert_type, "⚠️")

        value_text = f"\n📊 Current: <b>{current_value}</b>" if current_value else ""

        notification = f"""
{emoji} <b>Risk Alert: {alert_type.replace('_', ' ').title()}</b>

{message}{value_text}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(notification)

    async def notify_startup(self, bot_name: str, version: str, mode: str) -> bool:
        """봇 시작 알림"""
        mode_emoji = "🧪" if mode == "paper" else "🔴"

        message = f"""
🚀 <b>Trading Bot Started</b>

🤖 Bot: <b>{bot_name}</b>
📌 Version: <code>{version}</code>
{mode_emoji} Mode: <b>{mode.upper()}</b>

✅ Bot is running and ready to trade!

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)

    async def notify_shutdown(self, bot_name: str, reason: str = "") -> bool:
        """봇 중지 알림"""
        reason_text = f"\n📝 Reason: <code>{reason}</code>" if reason else ""

        message = f"""
🛑 <b>Trading Bot Stopped</b>

🤖 Bot: <b>{bot_name}</b>{reason_text}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(message)


class NullNotifier:
    """텔레그램 비활성화 시 사용하는 더미 노티파이어"""

    def __init__(self, *args, **kwargs):
        pass

    async def send_message(self, *args, **kwargs) -> bool:
        return False

    async def notify_trade(self, *args, **kwargs) -> bool:
        return False

    async def notify_position_opened(self, *args, **kwargs) -> bool:
        return False

    async def notify_position_closed(self, *args, **kwargs) -> bool:
        return False

    async def notify_error(self, *args, **kwargs) -> bool:
        return False

    async def notify_daily_report(self, *args, **kwargs) -> bool:
        return False

    async def notify_risk_alert(self, *args, **kwargs) -> bool:
        return False

    async def notify_startup(self, *args, **kwargs) -> bool:
        return False

    async def notify_shutdown(self, *args, **kwargs) -> bool:
        return False
