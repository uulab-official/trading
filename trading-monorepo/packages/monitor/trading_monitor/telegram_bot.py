"""Telegram bot for trading notifications - 텔레그램 알림 봇"""

import logging
from typing import Optional
from datetime import datetime
from decimal import Decimal

import telegram
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """텔레그램 알림 시스템"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot: Optional[telegram.Bot] = None
        self.app: Optional[Application] = None
        
    async def initialize(self) -> None:
        """봇 초기화"""
        self.bot = telegram.Bot(token=self.bot_token)
        self.app = Application.builder().token(self.bot_token).build()
        
        # 명령어 핸들러 등록
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("portfolio", self.cmd_portfolio))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("daily", self.cmd_daily_report))
        
        logger.info("Telegram notifier initialized")
    
    async def start(self) -> None:
        """봇 시작"""
        if self.app:
            await self.app.initialize()
            await self.app.start()
            logger.info("Telegram bot started")
    
    async def stop(self) -> None:
        """봇 중지"""
        if self.app:
            await self.app.stop()
            logger.info("Telegram bot stopped")
    
    async def send_message(self, message: str, parse_mode: str = 'HTML') -> None:
        """메시지 전송"""
        if self.bot:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")
    
    async def notify_order(self, order_type: str, symbol: str, side: str, 
                          amount: Decimal, price: Decimal, pnl: Optional[Decimal] = None) -> None:
        """주문 알림"""
        emoji = "🟢" if side == 'buy' else "🔴"
        pnl_text = f"\n💰 PnL: <code>{pnl:+.2f}%</code>" if pnl else ""
        
        message = f"""
{emoji} <b>Order {order_type.upper()}</b>

📊 Symbol: <code>{symbol}</code>
📈 Side: <b>{side.upper()}</b>
💵 Amount: <code>{amount:.6f}</code>
💲 Price: <code>${price:.2f}</code>{pnl_text}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        """
        
        await self.send_message(message)
    
    async def notify_position_closed(self, symbol: str, entry_price: Decimal, 
                                     exit_price: Decimal, pnl: Decimal, 
                                     duration_hours: float) -> None:
        """포지션 청산 알림"""
        emoji = "✅" if pnl > 0 else "❌"
        
        message = f"""
{emoji} <b>Position Closed</b>

📊 Symbol: <code>{symbol}</code>
📥 Entry: <code>${entry_price:.2f}</code>
📤 Exit: <code>${exit_price:.2f}</code>
💰 PnL: <code>{pnl:+.2f}%</code>
⏱ Duration: <code>{duration_hours:.1f}h</code>

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        """
        
        await self.send_message(message)
    
    async def notify_risk_alert(self, alert_type: str, message: str) -> None:
        """리스크 알림"""
        emoji = "🚨" if 'critical' in alert_type else "⚠️"
        
        text = f"""
{emoji} <b>RISK ALERT: {alert_type.upper()}</b>

{message}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
        """
        
        await self.send_message(text)
    
    async def send_daily_report(self, stats: dict) -> None:
        """일일 리포트"""
        message = f"""
📊 <b>Daily Trading Report</b>
📅 {datetime.utcnow().strftime('%Y-%m-%d')}

💰 Initial Balance: <code>${stats['initial_balance']:,.2f}</code>
💵 Final Balance: <code>${stats['final_balance']:,.2f}</code>
📈 Daily Return: <code>{stats['daily_return']:+.2f}%</code>

📊 Trades Today: <code>{stats['trades_count']}</code>
🎯 Win Rate: <code>{stats['win_rate']:.1f}%</code>
💸 Total Fees: <code>${stats['total_fees']:,.2f}</code>

📉 Max Drawdown: <code>{stats['max_drawdown']:.2f}%</code>
📊 Open Positions: <code>{stats['open_positions']}</code>

⏰ Report generated at {datetime.utcnow().strftime('%H:%M:%S')} UTC
        """
        
        await self.send_message(message)
    
    # Command Handlers
    async def cmd_start(self, update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start 명령"""
        await update.message.reply_text(
            "🤖 <b>Trading Bot Monitor</b>\n\n"
            "Available commands:\n"
            "/status - Check bot status\n"
            "/portfolio - View portfolio\n"
            "/positions - View open positions\n"
            "/daily - Get daily report",
            parse_mode='HTML'
        )
    
    async def cmd_status(self, update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status 명령"""
        # TODO: 실제 상태 조회 연동
        await update.message.reply_text(
            "✅ <b>Bot Status: RUNNING</b>\n\n"
            "• Connected to exchange\n"
            "• WebSocket: Active\n"
            "• Last trade: 5 minutes ago",
            parse_mode='HTML'
        )
    
    async def cmd_portfolio(self, update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/portfolio 명령"""
        # TODO: 실제 포트폴리오 조회 연동
        await update.message.reply_text(
            "📊 <b>Portfolio Summary</b>\n\n"
            "Total Value: <code>$10,250.50</code>\n"
            "Daily PnL: <code>+2.5%</code>\n"
            "Open Positions: <code>2</code>",
            parse_mode='HTML'
        )
    
    async def cmd_positions(self, update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/positions 명령"""
        # TODO: 실제 포지션 조회 연동
        await update.message.reply_text(
            "📈 <b>Open Positions</b>\n\n"
            "1. BTC/USDT\n"
            "   Size: 0.15 | PnL: +1.2%\n\n"
            "2. ETH/USDT\n"
            "   Size: 2.5 | PnL: -0.5%",
            parse_mode='HTML'
        )
    
    async def cmd_daily_report(self, update: telegram.Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/daily 명령"""
        # TODO: 실제 리포트 생성 연동
        await update.message.reply_text(
            "📊 <b>Today's Report</b>\n\n"
            "Trades: 5\n"
            "Win Rate: 60%\n"
            "Net Profit: +$125.50",
            parse_mode='HTML'
        )
