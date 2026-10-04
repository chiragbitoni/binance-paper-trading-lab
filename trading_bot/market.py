from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests


SPOT_API = "https://api.binance.com"
# Binance's official market-data-only host works from cloud runners where the
# main trading API may return HTTP 451. It exposes klines/exchangeInfo only and
# never accepts account credentials or orders.
MARKET_DATA_API = "https://data-api.binance.vision"
FUTURES_MARKET_DATA_API = "https://fapi.binance.com"


class BinanceMarketData:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()

    def candles(self, symbol: str, interval: str, start_ms: int | None = None,
                end_ms: int | None = None, limit: int = 1000) -> pd.DataFrame:
        rows: list[list] = []
        cursor = start_ms
        while True:
            params: dict[str, int | str] = {"symbol": symbol, "interval": interval, "limit": limit}
            if cursor is not None:
                params["startTime"] = cursor
            if end_ms is not None:
                params["endTime"] = end_ms
            response = self.session.get(f"{MARKET_DATA_API}/api/v3/klines", params=params, timeout=self.timeout)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            if start_ms is None or len(batch) < limit:
                break
            next_cursor = int(batch[-1][0]) + 1
            if next_cursor <= (cursor or 0) or (end_ms is not None and next_cursor > end_ms):
                break
            cursor = next_cursor
            time.sleep(0.08)

        columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                   "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
        frame = pd.DataFrame(rows, columns=columns)
        if frame.empty:
            return frame
        numeric = ["open", "high", "low", "close", "volume", "quote_volume", "taker_base", "taker_quote"]
        frame[numeric] = frame[numeric].astype(float)
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
        frame["trades"] = frame["trades"].astype(int)
        return frame.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

    def exchange_info(self, symbol: str) -> dict:
        response = self.session.get(f"{MARKET_DATA_API}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["symbols"][0]


class BinanceLiveClient:
    """Signed, read-only Spot account client used only for connectivity checks.

    This project intentionally has no order-creation method.  Keeping the
    capability out of the client makes the paper-only boundary enforceable in
    code, rather than relying on an environment-variable acknowledgement.
    """

    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["X-MBX-APIKEY"] = self.api_key

    def _signed(self, method: str, path: str, params: dict | None = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000
        query = urlencode(payload)
        payload["signature"] = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        response = self.session.request(method, f"{SPOT_API}{path}", params=payload, timeout=15)
        response.raise_for_status()
        return response.json()

    def account(self) -> dict:
        return self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})


class BinanceFuturesMarketData:
    """Public USD-M Futures market data only.

    This client intentionally has no authenticated endpoints and no order
    method.  It supplies data for the separate simulated Futures lab only.
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()

    def candles(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        response = self.session.get(
            f"{FUTURES_MARKET_DATA_API}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=self.timeout,
        )
        response.raise_for_status()
        columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                   "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
        frame = pd.DataFrame(response.json(), columns=columns)
        if frame.empty:
            return frame
        numeric = ["open", "high", "low", "close", "volume", "quote_volume", "taker_base", "taker_quote"]
        frame[numeric] = frame[numeric].astype(float)
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
        frame["trades"] = frame["trades"].astype(int)
        return frame.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

    def premium_index(self, symbol: str) -> dict:
        response = self.session.get(
            f"{FUTURES_MARKET_DATA_API}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return {"mark_price": float(payload["markPrice"]), "funding_rate": float(payload["lastFundingRate"]),
                "next_funding_time": int(payload["nextFundingTime"])}


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
