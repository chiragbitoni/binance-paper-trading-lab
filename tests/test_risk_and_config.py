from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from trading_bot.config import load_config
from trading_bot.market import BinanceLiveClient
from trading_bot.paper import paper_cycle
from trading_bot.risk import advance_trailing_stop, stop_fill_price
from trading_bot.storage import Storage
from trading_bot.strategies import STRATEGIES, Strategy


class RiskModelTests(unittest.TestCase):
    def test_trailing_stop_only_applies_to_the_next_candle(self) -> None:
        existing_stop = 90.0
        self.assertIsNone(stop_fill_price(100.0, 100.0, existing_stop, 0.0))

        next_stop, high_water = advance_trailing_stop(existing_stop, 100.0, 120.0, 5.0, 2.0)
        self.assertEqual(high_water, 120.0)
        self.assertEqual(next_stop, 110.0)

        # The higher stop becomes active only on a later candle.
        self.assertEqual(stop_fill_price(108.0, 105.0, next_stop, 0.0), 108.0)

    def test_stop_fill_models_an_opening_gap_and_slippage(self) -> None:
        self.assertEqual(stop_fill_price(95.0, 94.0, 100.0, 0.001), 94.905)
        self.assertIsNone(stop_fill_price(101.0, 100.01, 100.0, 0.001))


class ConfigSafetyTests(unittest.TestCase):
    def test_account_client_has_no_order_method(self) -> None:
        self.assertFalse(hasattr(BinanceLiveClient, "market_order"))

    def test_live_mode_is_rejected(self) -> None:
        source = Path("config.json")
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["mode"] = "live"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only paper mode"):
                load_config(path)

    def test_unknown_strategy_is_rejected(self) -> None:
        source = Path("config.json")
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["enabled_strategies"] = ["not_a_strategy"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown strategy"):
                load_config(path)

    def test_invalid_execution_cost_is_rejected(self) -> None:
        source = Path("config.json")
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["slippage_rate"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "slippage_rate"):
                load_config(path)


class PaperExecutionTests(unittest.TestCase):
    def test_close_based_entry_is_filled_on_the_following_candle_open(self) -> None:
        times = pd.date_range("2025-01-01", periods=202, freq="4h", tz="UTC")
        opens = [100.0 + index for index in range(len(times))]
        candles = pd.DataFrame({
            "open_time": times,
            "open": opens,
            "high": [price + 2.0 for price in opens],
            "low": [price - 2.0 for price in opens],
            "close": [price + 0.5 for price in opens],
            "volume": 100.0,
            "close_time": times + pd.Timedelta(hours=4) - pd.Timedelta(milliseconds=1),
            "quote_volume": [price * 100 for price in opens],
            "trades": 10,
            "taker_base": 50.0,
            "taker_quote": [price * 50 for price in opens],
            "ignore": 0,
        })

        class TwoStepMarket:
            calls = 0

            def candles(self, *args, **kwargs):
                self.calls += 1
                return candles.iloc[:201 if self.calls == 1 else 202].copy()

        cfg = load_config()
        cfg = cfg.__class__(**{**cfg.__dict__, "symbols": ["TESTUSDT"], "enabled_strategies": ["test_pending"],
                               "research_only_strategies": [], "database": "unused.db"})
        strategy = Strategy("test_pending", "Test pending entry", "test", lambda _df, i: i == 200,
                            lambda _df, _i: False, 100.0, None)
        STRATEGIES[strategy.key] = strategy
        try:
            with tempfile.TemporaryDirectory() as directory:
                cfg = cfg.__class__(**{**cfg.__dict__, "database": str(Path(directory) / "paper.db")})
                storage = Storage(cfg.database)
                try:
                    market = TwoStepMarket()
                    paper_cycle(cfg, storage, market)
                    account = storage.paper_accounts()[0]
                    self.assertEqual(account["quantity"], 0.0)
                    self.assertEqual(account["pending_order"], "entry")

                    paper_cycle(cfg, storage, market)
                    account = storage.paper_accounts()[0]
                    self.assertGreater(account["quantity"], 0.0)
                    self.assertIsNone(account["pending_order"])
                    trade = storage.recent_paper_trades()[0]
                    self.assertEqual(trade["price"], opens[201] * (1 + cfg.slippage_rate))
                finally:
                    storage.close()
        finally:
            STRATEGIES.pop(strategy.key, None)


if __name__ == "__main__":
    unittest.main()
