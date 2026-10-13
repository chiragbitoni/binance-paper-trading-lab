"""Guarded DhanHQ v2 integration for MCX commodity derivatives.

This is deliberately independent from the Binance paper engine.  It supports
real Dhan requests only after three separate opt-ins: configuration, a local
environment acknowledgement, and an explicit CLI confirmation.  Credentials
are read only from environment variables and are never written to disk.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests


DHAN_V2 = "https://api.dhan.co/v2"
MCX_SEGMENT = "MCX_COMM"
LIVE_ACKNOWLEDGEMENT = "I_UNDERSTAND_DHAN_LIVE_ORDERS"
AUTOMATION_ACKNOWLEDGEMENT = "I_UNDERSTAND_DHAN_AUTOMATION"
IST = ZoneInfo("Asia/Kolkata")


class DhanAPIError(RuntimeError):
    """A Dhan endpoint rejected, failed, or returned an invalid response."""


class LiveOrderLocked(RuntimeError):
    """A live-order gate was not deliberately enabled."""


@dataclass(frozen=True)
class MCXTradePlan:
    """A fully protected MCX trade proposal; quantity is exchange units."""

    security_id: str
    side: str
    quantity: int
    entry_price: float
    stop_loss_price: float
    target_price: float
    trailing_jump: float = 0.0
    product_type: str = "INTRADAY"
    entry_order_type: str = "LIMIT"
    correlation_id: str | None = None

    def validate(self) -> None:
        if not self.security_id.strip():
            raise ValueError("security_id is required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.product_type not in {"INTRADAY", "MARGIN"}:
            raise ValueError("product_type must be INTRADAY or MARGIN")
        if self.entry_order_type not in {"LIMIT", "MARKET"}:
            raise ValueError("entry_order_type must be LIMIT or MARKET")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if min(self.entry_price, self.stop_loss_price, self.target_price) <= 0:
            raise ValueError("entry, stop, and target prices must be positive")
        if self.trailing_jump < 0:
            raise ValueError("trailing_jump cannot be negative")
        if self.side == "BUY" and not self.stop_loss_price < self.entry_price < self.target_price:
            raise ValueError("BUY plan requires stop < entry < target")
        if self.side == "SELL" and not self.target_price < self.entry_price < self.stop_loss_price:
            raise ValueError("SELL plan requires target < entry < stop")

    @property
    def maximum_loss(self) -> float:
        return abs(self.entry_price - self.stop_loss_price) * self.quantity

    def as_super_order(self, client_id: str) -> dict[str, Any]:
        self.validate()
        return {
            "dhanClientId": client_id,
            "correlationId": self.correlation_id or f"mcx-{uuid.uuid4().hex[:20]}",
            "transactionType": self.side,
            "exchangeSegment": MCX_SEGMENT,
            "productType": self.product_type,
            "orderType": self.entry_order_type,
            "securityId": self.security_id,
            "quantity": self.quantity,
            "price": self.entry_price,
            "targetPrice": self.target_price,
            "stopLossPrice": self.stop_loss_price,
            "trailingJump": self.trailing_jump,
        }


class DhanMCXClient:
    """Small authenticated client with no implicit live-order capability."""

    def __init__(self, client_id: str, access_token: str, *, allow_live_orders: bool = False,
                 session: requests.Session | None = None) -> None:
        if not client_id or not access_token:
            raise ValueError("Dhan client ID and access token are required")
        self.client_id = client_id
        self.access_token = access_token
        self.allow_live_orders = allow_live_orders
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": access_token,
            "client-id": client_id,
        })

    @classmethod
    def from_environment(cls, options: dict) -> "DhanMCXClient":
        client_id = os.getenv("DHAN_CLIENT_ID", "")
        token = os.getenv("DHAN_ACCESS_TOKEN", "")
        # A configuration edit alone must never accidentally create a live bot.
        allow = bool(options.get("live_order_submission")) and os.getenv("DHAN_LIVE_ORDER_ACK") == LIVE_ACKNOWLEDGEMENT
        return cls(client_id, token, allow_live_orders=allow)

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        response = self.session.request(method, f"{DHAN_V2}{path}", json=payload, timeout=20)
        try:
            body = response.json()
        except ValueError as exc:
            raise DhanAPIError(f"Dhan returned non-JSON HTTP {response.status_code}") from exc
        if not response.ok:
            detail = body.get("remarks") or body.get("errorMessage") or body
            raise DhanAPIError(f"Dhan HTTP {response.status_code}: {detail}")
        if isinstance(body, dict) and body.get("status") == "failure":
            raise DhanAPIError(str(body))
        return body

    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def fund_limits(self) -> dict:
        return self._request("GET", "/fundlimit")

    def positions(self) -> list[dict]:
        data = self._request("GET", "/positions")
        return data if isinstance(data, list) else data.get("data", [])

    def margin(self, plan: MCXTradePlan) -> dict:
        plan.validate()
        return self._request("POST", "/margincalculator", {
            "dhanClientId": self.client_id, "exchangeSegment": MCX_SEGMENT,
            "transactionType": plan.side, "quantity": plan.quantity,
            "productType": plan.product_type, "securityId": plan.security_id,
            "price": plan.entry_price, "triggerPrice": plan.stop_loss_price,
        })

    def ltp(self, security_id: str) -> float:
        data = self._request("POST", "/marketfeed/ltp", {MCX_SEGMENT: [int(security_id)]})
        try:
            return float(data["data"][MCX_SEGMENT][str(security_id)]["last_price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DhanAPIError(f"Dhan did not return an MCX LTP for {security_id}") from exc

    def intraday_candles(self, security_id: str, instrument: str, interval: int,
                          from_date: str, to_date: str) -> pd.DataFrame:
        """Fetch MCX OHLCV from Dhan; callers must use completed candles only."""
        if interval not in {1, 5, 15, 25, 60}:
            raise ValueError("Dhan interval must be one of 1, 5, 15, 25, or 60 minutes")
        data = self._request("POST", "/charts/intraday", {
            "securityId": security_id, "exchangeSegment": MCX_SEGMENT, "instrument": instrument,
            "interval": str(interval), "oi": True, "fromDate": from_date, "toDate": to_date,
        })
        required = ("timestamp", "open", "high", "low", "close", "volume")
        if any(key not in data for key in required):
            raise DhanAPIError("Dhan intraday response omitted OHLCV fields")
        frame = pd.DataFrame({key: data[key] for key in required})
        if len(frame) < 40:
            raise DhanAPIError("at least 40 Dhan MCX candles are required for analysis")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
        return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    def place_super_order(self, plan: MCXTradePlan) -> dict:
        if not self.allow_live_orders:
            raise LiveOrderLocked("Dhan live-order gate is locked")
        return self._request("POST", "/super/orders", plan.as_super_order(self.client_id))

    def order_by_correlation(self, correlation_id: str) -> dict:
        return self._request("GET", f"/orders/external/{correlation_id}")


@dataclass(frozen=True)
class MCXAnalysis:
    """Explainable completed-candle analysis, not a profit prediction."""

    signal: str
    confidence: float
    close: float
    atr: float
    rsi: float
    ema_fast: float
    ema_slow: float
    volume_ratio: float
    proposed_stop: float | None
    proposed_target: float | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal, "confidence": self.confidence, "close": self.close, "atr": self.atr,
            "rsi": self.rsi, "ema_fast": self.ema_fast, "ema_slow": self.ema_slow,
            "volume_ratio": self.volume_ratio, "proposed_stop": self.proposed_stop,
            "proposed_target": self.proposed_target, "reasons": list(self.reasons),
            "notice": "Signal is explainable market analysis, not a prediction or an order instruction.",
        }


class MCXSignalAnalyzer:
    """A conservative, explainable trend/volatility scorer for MCX research.

    It intentionally ignores the latest possibly-forming intraday bar. Any
    learned/AI model must be separately trained, validated out of sample, and
    approved before it can ever drive live entries.
    """

    def analyze(self, candles: pd.DataFrame) -> MCXAnalysis:
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(candles.columns) or len(candles) < 40:
            raise ValueError("analysis needs at least 40 OHLCV candles")
        completed = candles.iloc[:-1].copy()
        if len(completed) < 30:
            raise ValueError("analysis needs at least 30 completed candles")
        close = completed["close"]
        high, low = completed["high"], completed["low"]
        previous_close = close.shift(1)
        true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
        atr = float(true_range.rolling(14).mean().iloc[-1])
        gain, loss = close.diff().clip(lower=0), -close.diff().clip(upper=0)
        average_gain, average_loss = gain.rolling(14).mean().iloc[-1], loss.rolling(14).mean().iloc[-1]
        rsi = 100.0 if average_loss == 0 else float(100 - 100 / (1 + average_gain / average_loss))
        fast, slow = float(close.ewm(span=9, adjust=False).mean().iloc[-1]), float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        volume_baseline = float(completed["volume"].tail(20).median())
        volume_ratio = float(completed["volume"].iloc[-1] / volume_baseline) if volume_baseline > 0 else 0.0
        latest = float(close.iloc[-1])
        long_score = int(fast > slow) + int(latest > fast) + int(52 <= rsi <= 72) + int(volume_ratio >= 1.0)
        short_score = int(fast < slow) + int(latest < fast) + int(28 <= rsi <= 48) + int(volume_ratio >= 1.0)
        if not pd.notna(atr) or atr <= 0:
            return MCXAnalysis("HOLD", 0.0, latest, 0.0, rsi, fast, slow, volume_ratio, None, None,
                               ("ATR is unavailable; no trade plan.",))
        if long_score >= 3:
            return MCXAnalysis("BUY", round(long_score / 4, 2), latest, atr, rsi, fast, slow, volume_ratio,
                               latest - 1.5 * atr, latest + 2.5 * atr,
                               ("EMA trend is positive", "price is above fast EMA", "RSI/volume confirmation"))
        if short_score >= 3:
            return MCXAnalysis("SELL", round(short_score / 4, 2), latest, atr, rsi, fast, slow, volume_ratio,
                               latest + 1.5 * atr, latest - 2.5 * atr,
                               ("EMA trend is negative", "price is below fast EMA", "RSI/volume confirmation"))
        return MCXAnalysis("HOLD", round(max(long_score, short_score) / 4, 2), latest, atr, rsi, fast, slow,
                           volume_ratio, None, None,
                           ("Trend, momentum, and volume do not agree strongly enough.",))


class MCXExecutionEngine:
    """Risk checks for a protected Dhan MCX order before broker submission."""

    def __init__(self, options: dict, client: DhanMCXClient) -> None:
        self.options = options
        self.client = client

    def _mcx_open_positions(self) -> list[dict]:
        return [position for position in self.client.positions() if position.get("exchangeSegment") == MCX_SEGMENT
                and abs(float(position.get("netQty", position.get("netQuantity", 0)) or 0)) > 0]

    @staticmethod
    def _amount(data: dict, *keys: str) -> float:
        for key in keys:
            if data.get(key) not in {None, ""}:
                return float(data[key])
        raise DhanAPIError(f"Dhan response omitted one of: {', '.join(keys)}")

    def status(self) -> dict:
        profile = self.client.profile()
        funds = self.client.fund_limits()
        positions = self._mcx_open_positions()
        return {
            "profile": profile,
            "available_balance": self._amount(funds, "availabelBalance", "availableBalance"),
            "mcx_open_positions": len(positions),
            "live_order_submission": self.client.allow_live_orders,
            "configured_allocation": float(self.options["allocated_capital_inr"]),
        }

    def preview(self, plan: MCXTradePlan) -> dict:
        plan.validate()
        if not self.options.get("enabled"):
            raise LiveOrderLocked("dhan_mcx.enabled is false")
        allowed = {str(item) for item in self.options.get("allowed_security_ids", [])}
        if allowed and plan.security_id not in allowed:
            raise ValueError("security_id is not in dhan_mcx.allowed_security_ids")
        allocated = float(self.options["allocated_capital_inr"])
        daily_cap = float(self.options["max_daily_loss_inr"])
        per_trade_cap = float(self.options["max_loss_per_trade_inr"])
        if min(allocated, daily_cap, per_trade_cap) <= 0:
            raise ValueError("enter positive allocated capital, daily loss, and per-trade loss before trading")
        all_positions = self.client.positions()
        positions = [position for position in all_positions if position.get("exchangeSegment") == MCX_SEGMENT
                     and abs(float(position.get("netQty", position.get("netQuantity", 0)) or 0)) > 0]
        if len(positions) >= int(self.options["max_open_positions"]):
            raise LiveOrderLocked("maximum MCX open-position count reached")
        # Dhan Positions exposes realised and unrealised profit fields for the
        # current day. The cap is deliberately conservative: current-day net
        # losses from every MCX position (including a manually closed one still
        # returned by Positions) count against the bot's daily stop.
        daily_net_pnl = sum(
            float(position.get("realizedProfit", 0) or 0) + float(position.get("unrealizedProfit", 0) or 0)
            for position in all_positions if position.get("exchangeSegment") == MCX_SEGMENT
        )
        daily_loss = max(0.0, -daily_net_pnl)
        if daily_loss >= daily_cap:
            raise LiveOrderLocked(f"MCX daily loss ₹{daily_loss:.2f} has reached cap ₹{daily_cap:.2f}")
        if plan.maximum_loss > per_trade_cap:
            raise LiveOrderLocked(f"plan risk ₹{plan.maximum_loss:.2f} exceeds per-trade cap ₹{per_trade_cap:.2f}")
        margin = self.client.margin(plan)
        required_margin = self._amount(margin, "total_margin", "totalMargin")
        funds = self.client.fund_limits()
        available = self._amount(funds, "availabelBalance", "availableBalance")
        if required_margin > allocated:
            raise LiveOrderLocked(f"required margin ₹{required_margin:.2f} exceeds allocated capital ₹{allocated:.2f}")
        if required_margin > available:
            raise LiveOrderLocked(f"required margin ₹{required_margin:.2f} exceeds Dhan available balance ₹{available:.2f}")
        reward = abs(plan.target_price - plan.entry_price) * plan.quantity
        return {
            "approved": True,
            "security_id": plan.security_id,
            "segment": MCX_SEGMENT,
            "product_type": plan.product_type,
            "entry_order_type": plan.entry_order_type,
            "required_margin": required_margin,
            "available_balance": available,
            "allocation_cap": allocated,
            "maximum_loss": plan.maximum_loss,
            "per_trade_loss_cap": per_trade_cap,
            "daily_loss_cap": daily_cap,
            "current_mcx_daily_loss": daily_loss,
            "maximum_reward": reward,
            "risk_reward_ratio": round(reward / plan.maximum_loss, 3),
            "protection": "Dhan Super Order: entry + target + stop-loss + optional trailing jump",
        }

    def execute(self, plan: MCXTradePlan, *, confirmed: bool, automated: bool = False) -> dict:
        preview = self.preview(plan)
        if not confirmed:
            raise LiveOrderLocked("pass --confirm-live after reviewing the preview")
        if not self.options.get("live_order_submission"):
            raise LiveOrderLocked("dhan_mcx.live_order_submission is false")
        if automated:
            if not self.options.get("auto_trade_enabled"):
                raise LiveOrderLocked("dhan_mcx.auto_trade_enabled is false")
            if os.getenv("DHAN_AUTOMATION_ACK") != AUTOMATION_ACKNOWLEDGEMENT:
                raise LiveOrderLocked("DHAN_AUTOMATION_ACK is missing")
        response = self.client.place_super_order(plan)
        return {"preview": preview, "broker_response": response}


class DhanMCXAutopilot:
    """Process a configured watchlist once per completed candle.

    The local state table makes a unique (security, candle) signal a one-shot
    broker request. If a request has an uncertain outcome, it is marked for
    reconciliation rather than retried automatically, avoiding duplicate live
    orders after a network timeout.
    """

    def __init__(self, options: dict, engine: MCXExecutionEngine, client: DhanMCXClient,
                 state_path: str = "data/dhan_mcx_autopilot.db") -> None:
        self.options, self.engine, self.client = options, engine, client
        path = Path(state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS submitted_signals (
            security_id TEXT NOT NULL, candle_time TEXT NOT NULL, correlation_id TEXT NOT NULL,
            status TEXT NOT NULL, response TEXT, PRIMARY KEY (security_id, candle_time))""")
        self.connection.commit()

    @staticmethod
    def _within_session(item: dict, now: datetime) -> bool:
        current = now.strftime("%H:%M")
        start, end = item["session_start_ist"], item["session_end_ist"]
        return start <= current <= end if start <= end else current >= start or current <= end

    def _already_processed(self, security_id: str, candle_time: str) -> bool:
        return self.connection.execute("SELECT 1 FROM submitted_signals WHERE security_id=? AND candle_time=?",
                                       (security_id, candle_time)).fetchone() is not None

    def _reserve(self, security_id: str, candle_time: str, correlation_id: str) -> bool:
        try:
            self.connection.execute("INSERT INTO submitted_signals VALUES (?,?,?,?,NULL)",
                                    (security_id, candle_time, correlation_id, "reserved"))
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def _finish(self, security_id: str, candle_time: str, status: str, response: Any) -> None:
        self.connection.execute("UPDATE submitted_signals SET status=?,response=? WHERE security_id=? AND candle_time=?",
                                (status, str(response)[:4000], security_id, candle_time))
        self.connection.commit()

    def run_once(self, now: datetime | None = None) -> list[dict]:
        if not self.options.get("enabled"):
            raise LiveOrderLocked("dhan_mcx.enabled is false")
        now = now or datetime.now(IST)
        outcomes: list[dict] = []
        start = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        end = now.strftime("%Y-%m-%d %H:%M:%S")
        analyzer = MCXSignalAnalyzer()
        for item in self.options["watchlist"]:
            security_id = str(item["security_id"])
            if not self._within_session(item, now):
                outcomes.append({"security_id": security_id, "status": "outside_configured_session"})
                continue
            candles = self.client.intraday_candles(security_id, item["instrument"], int(item.get("interval", 5)), start, end)
            analysis = analyzer.analyze(candles)
            candle_time = str(candles.iloc[-2]["timestamp"])
            if self._already_processed(security_id, candle_time):
                outcomes.append({"security_id": security_id, "status": "already_processed", "candle_time": candle_time})
                continue
            if analysis.signal == "HOLD" or analysis.confidence < float(self.options["minimum_signal_confidence"]):
                outcomes.append({"security_id": security_id, "status": "no_qualified_signal", "analysis": analysis.as_dict()})
                continue
            correlation_id = f"auto-{security_id}-{int(candles.iloc[-2]["timestamp"].timestamp())}"[:30]
            plan = MCXTradePlan(security_id, analysis.signal, int(item["quantity"]), analysis.close,
                                float(analysis.proposed_stop), float(analysis.proposed_target),
                                trailing_jump=float(item.get("trailing_jump", 0)),
                                product_type=item.get("product_type", self.options["default_product_type"]),
                                entry_order_type=item.get("entry_order_type", "LIMIT"), correlation_id=correlation_id)
            preview = self.engine.preview(plan)
            if not self.options.get("auto_trade_enabled"):
                outcomes.append({"security_id": security_id, "status": "qualified_preview_only", "analysis": analysis.as_dict(), "preview": preview})
                continue
            if not self._reserve(security_id, candle_time, correlation_id):
                outcomes.append({"security_id": security_id, "status": "already_reserved", "candle_time": candle_time})
                continue
            try:
                result = self.engine.execute(plan, confirmed=True, automated=True)
                self._finish(security_id, candle_time, "submitted", result["broker_response"])
                outcomes.append({"security_id": security_id, "status": "submitted", "result": result})
            except Exception as exc:
                # Never retry an uncertain order automatically. A deployment
                # operator must reconcile the correlation ID with Dhan first.
                self._finish(security_id, candle_time, "needs_reconciliation", str(exc))
                outcomes.append({"security_id": security_id, "status": "needs_reconciliation", "error": str(exc),
                                 "correlation_id": correlation_id})
        return outcomes

    def run_forever(self, poll_seconds: int) -> None:
        if poll_seconds < 15:
            raise ValueError("poll_seconds must be at least 15")
        try:
            while True:
                for outcome in self.run_once():
                    print(outcome, flush=True)
                time.sleep(poll_seconds)
        finally:
            self.close()

    def close(self) -> None:
        self.connection.close()
