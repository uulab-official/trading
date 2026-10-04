"""A persisted same-day limit must prevent a fresh-process BUY.

Each test uses
two fresh Python processes, a new temporary SQLite DB, and actual paper orders.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
EVIDENCE = None

CONFIG = """bot:
  id: restart-audit
  mode: paper
exchange:
  id: binance
  sandbox: true
  testnet: true
strategies:
  - name: offline-fixture
    enabled: true
    type: ma_cross
    symbol: BTC/USDT
    timeframe: 1h
"""


def child(directory, phase, scenario):
    # Deliberately do not inherit credentials or unrelated environment settings.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": directory,
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "TZ": "UTC"}
    result = subprocess.run([sys.executable, str(HERE / "legacy_restart_worker.py"), phase, scenario],
                            cwd=directory, env=env, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise AssertionError(f"Fixture process failed: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout), result.stderr


class ActualLegacyRestartTests(unittest.TestCase):
    def exercise(self, scenario, expected_before):
        with tempfile.TemporaryDirectory(prefix="legacy-risk-audit-") as temp:
            active = Path(temp) / "config/active"
            active.mkdir(parents=True)
            (active / "bot_config.yml").write_text(CONFIG)
            before, before_log = child(temp, "establish", scenario)
            self.assertFalse(before["before_stop"]["buy_allowed"])
            self.assertIn(expected_before, before["before_stop"]["buy_reason"])
            if scenario in ("missing_baseline", "corrupt_state"):
                with sqlite3.connect(Path(temp) / "data/paper_bot_state.db") as conn:
                    if scenario == "missing_baseline":
                        raw, = conn.execute("SELECT data FROM state WHERE key='bot:restart-audit:legacy_checkpoint:v1'").fetchone()
                        state = json.loads(raw)
                        del state["accounting"]["daily_start_value"]
                        replacement = json.dumps(state)
                    else:
                        replacement = '{"version":'
                    conn.execute("UPDATE state SET data=? WHERE key='bot:restart-audit:legacy_checkpoint:v1'", (replacement,))
                    conn.commit()
            after, after_log = child(temp, "resume", scenario)
            self.assertFalse(after["new_buy_filled"],
                f"{scenario}: fresh process filled a new BUY; before={before['before_stop']['buy_reason']}; "
                f"restored={after['after_initialize']['buy_reason']}; "
                f"PnL={after['after_initialize']['daily_pnl_pct']}%; "
                f"daily_trades={after['after_initialize']['risk']['daily_trades']}")

    def test_circuit_survives_graceful_restart(self):
        self.exercise("circuit", "Circuit breaker")

    def test_daily_loss_survives_graceful_restart_after_cooldown(self):
        self.exercise("daily_loss", "Daily loss limit")

    def test_active_cooldown_survives_graceful_restart(self):
        self.exercise("cooldown", "Cooldown active")

    def test_daily_trade_limit_survives_graceful_restart(self):
        self.exercise("trade_count", "Daily trade limit")

    def test_restart_with_utc_clock_rollback_does_not_clear_circuit(self):
        self.exercise("rollback", "Circuit breaker")

    def test_missing_baseline_does_not_enable_new_entry(self):
        self.exercise("missing_baseline", "Circuit breaker")

    def test_corrupt_existing_state_is_not_a_funded_fresh_start(self):
        self.exercise("corrupt_state", "Circuit breaker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
