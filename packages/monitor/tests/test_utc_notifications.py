"""Monitor timestamps retain their wire/text format without starting services."""

import importlib.util
import sys
import unittest
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from trading_monitor.telegram_bot import TelegramNotifier

INSTANT = datetime(2026, 11, 21, 12, 34, 56, 123456, tzinfo=timezone.utc)


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            raise AssertionError("monitor clocks must explicitly select UTC")
        return INSTANT.astimezone(tz)


class MonitorUtcTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_and_status_timestamps_keep_naive_utc_iso_format(self):
        spec = importlib.util.spec_from_file_location(
            "dashboard_utc_contract", Path(__file__).resolve().parents[1] / "web_dashboard.py"
        )
        dashboard = importlib.util.module_from_spec(spec)

        class OfflineDisconnect(Exception):
            pass

        # Test the actual handlers directly. Route registration and static files
        # belong to the separate web image and need no FastAPI dependency here.
        router = SimpleNamespace(
            get=lambda *args, **kwargs: lambda handler: handler,
            websocket=lambda *args, **kwargs: lambda handler: handler,
            mount=lambda *args, **kwargs: None,
        )
        registration_stubs = {
            "fastapi": SimpleNamespace(
                FastAPI=lambda **kwargs: router,
                WebSocket=object,
                WebSocketDisconnect=OfflineDisconnect,
                HTTPException=RuntimeError,
            ),
            "fastapi.staticfiles": SimpleNamespace(StaticFiles=lambda **kwargs: object()),
            "fastapi.responses": SimpleNamespace(HTMLResponse=object),
        }
        with patch.dict(sys.modules, registration_stubs):
            spec.loader.exec_module(dashboard)

        class OfflineSocket:
            def __init__(self):
                self.messages = []

            async def accept(self):
                pass

            async def send_json(self, message):
                self.messages.append(message)

            async def receive_text(self):
                raise OfflineDisconnect()

        client = OfflineSocket()
        with patch.object(dashboard, "datetime", FrozenClock), warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            status = await dashboard.get_status()
            await dashboard.websocket_endpoint(client)
            self.assertEqual(dashboard.connected_clients, [])
            dashboard.connected_clients.append(client)
            await dashboard.broadcast_to_clients()

        expected = "2026-11-21T12:34:56.123456"
        self.assertEqual(status["timestamp"], expected)
        self.assertEqual(
            [message["timestamp"] for message in client.messages], [expected, expected]
        )
        self.assertIsNone(datetime.fromisoformat(status["timestamp"]).tzinfo)

    async def test_notification_text_keeps_explicit_utc_without_sending_messages(self):
        notifier = TelegramNotifier("unused-offline-token", "unused-offline-recipient")
        with (
            patch.object(notifier, "send_message", new_callable=AsyncMock) as send,
            patch("trading_monitor.telegram_bot.datetime", FrozenClock),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", DeprecationWarning)
            await notifier.notify_order("market", "BTC/USDT", "buy", Decimal("1"), Decimal("100"))
            await notifier.notify_position_closed(
                "BTC/USDT", Decimal("100"), Decimal("101"), Decimal("1"), 2
            )
            await notifier.notify_risk_alert("info", "offline clock check")
            await notifier.send_daily_report(
                {
                    "initial_balance": 100,
                    "final_balance": 101,
                    "daily_return": 1,
                    "trades_count": 1,
                    "win_rate": 100,
                    "total_fees": 0,
                    "max_drawdown": 0,
                    "open_positions": 0,
                }
            )

        self.assertEqual(send.await_count, 4)
        messages = [call.args[0] for call in send.await_args_list]
        for message in messages[:3]:
            self.assertIn("2026-11-21 12:34:56 UTC", message)
        self.assertIn("2026-11-21", messages[3])
        self.assertIn("Report generated at 12:34:56 UTC", messages[3])
        self.assertIsNone(notifier.bot)
        self.assertIsNone(notifier.app)


if __name__ == "__main__":
    unittest.main()
