"""Always-on, paper-only market-data worker.

Signals remain based on completed four-hour candles.  This worker only uses
public Spot WebSocket prices to mark positions and immediately simulate
protective exits.  It has no credentials and cannot place exchange orders.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import websocket

from .config import Config
from .futures import _close, futures_paper_cycle
from .indicators import add_indicators
from .market import BinanceFuturesMarketData, BinanceMarketData
from .paper import paper_cycle
from .storage import Storage
from .strategies import STRATEGIES


SPOT_DATA_STREAM = "wss://data-stream.binance.vision/stream?streams="
FUTURES_DATA_STREAM = "wss://fstream.binance.com/stream?streams="


class RealtimePaperService:
    """Run the two required loops without changing the paper-only boundary."""

    def __init__(self, cfg: Config, maintenance_seconds: int = 60):
        self.cfg = cfg
        self.maintenance_seconds = maintenance_seconds
        self.storage = Storage(cfg.database)
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._last_maintenance = 0.0

    def _atr(self, symbol: str, futures: bool = False) -> float | None:
        candles = self.storage.futures_candles(symbol) if futures else self.storage.market_candles(symbol)
        if len(candles) < 20:
            return None
        frame = pd.DataFrame(candles)
        frame["taker_base"] = 0.0
        frame["taker_quote"] = 0.0
        frame["quote_volume"] = frame["close"] * frame["volume"]
        value = add_indicators(frame).iloc[-1].atr
        return None if pd.isna(value) or value <= 0 else float(value)

    def _close_spot(self, account: dict, live_price: float, now: str) -> None:
        # A live protective exit gets the observed quote less conservative paper
        # slippage. It is still a simulation, not a Binance order or guarantee.
        sell_price = live_price * (1 - self.cfg.slippage_rate)
        proceeds = account["quantity"] * sell_price
        fee = proceeds * self.cfg.fee_rate
        cost = account["quantity"] * account["entry_price"] * (1 + self.cfg.fee_rate)
        pnl = proceeds - fee - cost
        account["cash"] += proceeds - fee
        self.storage.save_paper_trade({
            "symbol": account["symbol"], "strategy_key": account["strategy_key"], "side": "SELL",
            "price": sell_price, "quantity": account["quantity"], "fee": fee, "realized_pnl": pnl,
            "reason": "live_stop", "candle_time": now,
        })
        account.update(quantity=0.0, entry_price=None, stop_price=None, high_water=None, pending_order=None)
        self.storage.update_paper_account(account)
        print(f"{now} {account['symbol']} {account['strategy_key']}: live protective exit at {sell_price:.6f}", flush=True)

    def _manage_spot(self, symbol: str, price: float, now: str) -> None:
        atr = self._atr(symbol)
        for account in self.storage.paper_accounts():
            if account["symbol"] != symbol or account["quantity"] <= 0:
                continue
            if price <= account["stop_price"]:
                self._close_spot(account, price, now)
                continue
            if atr is None:
                continue
            strategy = STRATEGIES.get(account["strategy_key"])
            trail = strategy.trailing_atr if strategy and strategy.trailing_atr is not None else self.cfg.trailing_stop_atr
            high_water = max(float(account["high_water"] or account["entry_price"]), price)
            stop = max(float(account["stop_price"]), high_water - trail * atr)
            if high_water != account["high_water"] or stop != account["stop_price"]:
                account["high_water"] = high_water
                account["stop_price"] = stop
                self.storage.update_paper_account(account)

    def _manage_futures(self, symbol: str, price: float, now: str) -> None:
        position = self.storage.futures_position()
        if position is None or position["symbol"] != symbol:
            return
        options = self.cfg.futures_paper
        side = position["side"]
        stop_hit = (side == "LONG" and price <= position["stop_price"]) or (side == "SHORT" and price >= position["stop_price"])
        target_hit = (side == "LONG" and price >= position["take_profit"]) or (side == "SHORT" and price <= position["take_profit"])
        if stop_hit or target_hit:
            exit_price = price * (1 - float(options["slippage_rate"]) if side == "LONG" else 1 + float(options["slippage_rate"]))
            reason = "live_stop" if stop_hit else "live_target"
            outcome = _close(self.storage, position, exit_price, reason, now, float(options["fee_rate"]))
            print(f"{now} {symbol} {side}: {reason}, paper P&L {outcome:.6f}", flush=True)
            return
        atr = self._atr(symbol, futures=True)
        if atr is None:
            return
        if side == "LONG":
            position["high_water"] = max(float(position["high_water"]), price)
            position["stop_price"] = max(float(position["stop_price"]), float(position["high_water"]) - float(options["trailing_stop_atr"]) * atr)
        else:
            position["low_water"] = min(float(position["low_water"]), price)
            position["stop_price"] = min(float(position["stop_price"]), float(position["low_water"]) + float(options["trailing_stop_atr"]) * atr)
        self.storage.save_futures_position(position)

    def _futures_uses_spot_proxy(self, symbol: str) -> bool:
        return any(item["symbol"] == symbol and item.get("data_source") == "Spot proxy (USD-M unavailable)"
                   for item in self.storage.futures_snapshots())

    def on_price(self, symbol: str, price: float, source: str = "Binance Spot WebSocket",
                 manage_spot: bool = True, manage_futures: bool = False, save_price: bool = True) -> None:
        if price <= 0:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if save_price:
                self.storage.save_live_price(symbol, price, source)
            if manage_spot:
                self._manage_spot(symbol, price, now)
            if manage_futures:
                self._manage_futures(symbol, price, now)

    def _maintenance(self) -> None:
        with self._lock:
            for message in paper_cycle(self.cfg, self.storage, BinanceMarketData()):
                print(message, flush=True)
            for message in futures_paper_cycle(self.cfg, self.storage, BinanceFuturesMarketData()):
                print(message, flush=True)
        self._last_maintenance = time.monotonic()

    def _on_message(self, _ws, raw: str) -> None:
        try:
            payload = json.loads(raw)
            data = payload.get("data", payload)
            if data.get("e") not in {"24hrMiniTicker", "24hrTicker"}:
                return
            symbol = str(data["s"])
            self.on_price(symbol, float(data["c"]), manage_futures=self._futures_uses_spot_proxy(symbol))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"ignored malformed WebSocket price: {exc}", flush=True)

    def _on_futures_message(self, _ws, raw: str) -> None:
        try:
            payload = json.loads(raw)
            data = payload.get("data", payload)
            if data.get("e") not in {"24hrMiniTicker", "24hrTicker"}:
                return
            self.on_price(str(data["s"]), float(data["c"]), "Binance USD-M Futures WebSocket",
                          manage_spot=False, manage_futures=True, save_price=False)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"ignored malformed Futures WebSocket price: {exc}", flush=True)

    def _on_error(self, _ws, error) -> None:
        print(f"market WebSocket error: {error}", flush=True)

    def _on_close(self, _ws, status, message) -> None:
        print(f"market WebSocket closed: {status} {message or ''}".strip(), flush=True)

    def run(self) -> None:
        print("Starting paper-only real-time worker (no Binance credentials or order methods).", flush=True)
        try:
            self._maintenance()
            all_symbols = sorted(set(self.cfg.symbols + self.cfg.futures_paper["symbols"]))
            streams = "/".join(f"{symbol.lower()}@miniTicker" for symbol in all_symbols)
            while not self._stopping.is_set():
                app = websocket.WebSocketApp(SPOT_DATA_STREAM + streams, on_message=self._on_message,
                                             on_error=self._on_error, on_close=self._on_close)
                worker = threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
                worker.start()
                futures_app = None
                futures_worker = None
                next_futures_retry = 0.0
                while worker.is_alive() and not self._stopping.wait(1):
                    if (futures_worker is None or not futures_worker.is_alive()) and time.monotonic() >= next_futures_retry:
                        futures_app = websocket.WebSocketApp(FUTURES_DATA_STREAM + streams, on_message=self._on_futures_message,
                                                             on_error=self._on_error, on_close=self._on_close)
                        futures_worker = threading.Thread(
                            target=lambda client=futures_app: client.run_forever(ping_interval=20, ping_timeout=10), daemon=True
                        )
                        futures_worker.start()
                        next_futures_retry = time.monotonic() + 30
                    if time.monotonic() - self._last_maintenance >= self.maintenance_seconds:
                        try:
                            self._maintenance()
                        except Exception as exc:
                            print(f"4h strategy maintenance error: {exc}", flush=True)
                app.close()
                if futures_app:
                    futures_app.close()
                if not self._stopping.is_set():
                    print("Reconnecting market WebSocket in 5 seconds...", flush=True)
                    self._stopping.wait(5)
        except KeyboardInterrupt:
            print("Stopping real-time paper worker.", flush=True)
        finally:
            self.storage.close()
