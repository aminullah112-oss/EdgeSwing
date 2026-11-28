"""Tick-to-candle aggregation for Zerodha KiteTicker streams.

Kite `volume_traded` is cumulative for the exchange session. The engine converts
that cumulative counter into per-minute volume deltas and persists completed
1-minute OHLCV candles. The currently forming minute stays in memory and is
merged into API responses so dashboards can display it without writing every tick
through to SQLite.
"""
from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from backend.db import save_candles

IST = ZoneInfo("Asia/Kolkata")


class LiveCandleEngine:
    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._daily: dict[str, dict[str, Any]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._stats = {"ticks_received": 0, "candles_finalized": 0, "last_tick_at": None}

    @staticmethod
    def _as_ist(value: Any) -> datetime:
        if isinstance(value, datetime):
            dt = value
        elif value:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        else:
            dt = datetime.now(IST)
        # KiteTicker converts epoch timestamps with datetime.fromtimestamp(), so
        # naive values represent the host's local timezone. astimezone() applies
        # that host timezone before converting to exchange time (IST).
        return dt.astimezone(IST)

    @staticmethod
    def _minute_start(dt: datetime) -> datetime:
        return dt.replace(second=0, microsecond=0)

    @staticmethod
    def _serialise_candle(candle: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": candle["date"].isoformat(),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle["volume"]),
        }

    def process_ticks(self, ticks: list[dict[str, Any]], token_symbols: dict[str, str]) -> list[dict[str, Any]]:
        finalized: list[tuple[str, dict[str, Any]]] = []
        updated: list[dict[str, Any]] = []
        with self._lock:
            for tick in ticks:
                raw_token = tick.get("instrument_token")
                if raw_token is None:
                    continue
                token = str(int(raw_token))
                symbol = token_symbols.get(token)
                if not symbol:
                    continue
                price = tick.get("last_price")
                if price is None:
                    continue
                try:
                    price = float(price)
                    if price <= 0:
                        continue
                except (TypeError, ValueError):
                    continue

                dt = self._as_ist(tick.get("exchange_timestamp") or tick.get("timestamp"))
                minute = self._minute_start(dt)
                session = dt.date().isoformat()
                state = self._states.setdefault(token, {"symbol": symbol, "session": session, "last_cumulative_volume": None, "candle": None})
                if session < state.get("session", session):
                    # Ignore late packets from a previous exchange session.
                    continue
                if state.get("session") != session:
                    state["session"] = session
                    state["last_cumulative_volume"] = None

                # Convert session-cumulative volume into an incremental trade volume.
                raw_cumulative = tick.get("volume_traded")
                cumulative = None
                if raw_cumulative is not None:
                    try:
                        cumulative = max(0.0, float(raw_cumulative))
                    except (TypeError, ValueError):
                        cumulative = None
                delta_volume = 0.0
                if cumulative is not None:
                    previous = state.get("last_cumulative_volume")
                    if previous is None:
                        delta_volume = max(0.0, float(tick.get("last_traded_quantity") or 0))
                    elif cumulative >= previous:
                        delta_volume = cumulative - previous
                    else:
                        # Defensive reset if upstream cumulative volume rolls over.
                        delta_volume = max(0.0, float(tick.get("last_traded_quantity") or 0))
                    state["last_cumulative_volume"] = cumulative
                elif tick.get("last_traded_quantity") is not None:
                    try:
                        delta_volume = max(0.0, float(tick["last_traded_quantity"]))
                    except (TypeError, ValueError):
                        delta_volume = 0.0

                candle = state.get("candle")
                if candle is None:
                    candle = {"date": minute, "open": price, "high": price, "low": price, "close": price, "volume": delta_volume}
                    state["candle"] = candle
                elif minute > candle["date"]:
                    finalized.append((symbol, self._serialise_candle(candle)))
                    candle = {"date": minute, "open": price, "high": price, "low": price, "close": price, "volume": delta_volume}
                    state["candle"] = candle
                elif minute == candle["date"]:
                    candle["high"] = max(float(candle["high"]), price)
                    candle["low"] = min(float(candle["low"]), price)
                    candle["close"] = price
                    candle["volume"] = float(candle["volume"]) + delta_volume
                # Late/out-of-order ticks update the quote snapshot but do not rewrite an old bar.

                # Build the in-progress exchange-day candle from the daily OHLC
                # embedded in Kite's full tick, replacing the previous close with LTP.
                raw_ohlc = tick.get("ohlc") or {}
                try:
                    day_open = float(raw_ohlc.get("open") or price)
                    day_high = max(float(raw_ohlc.get("high") or price), price)
                    day_low = min(float(raw_ohlc.get("low") or price), price)
                except (TypeError, ValueError):
                    day_open = day_high = day_low = price
                current_daily = self._daily.get(token)
                if current_daily is None or current_daily.get("session") != session:
                    current_daily = {
                        "session": session,
                        "date": datetime(dt.year, dt.month, dt.day, tzinfo=IST),
                        "open": day_open,
                        "high": day_high,
                        "low": day_low,
                        "close": price,
                        "volume": float(cumulative) if cumulative is not None else delta_volume,
                    }
                    self._daily[token] = current_daily
                else:
                    current_daily["high"] = max(float(current_daily["high"]), day_high, price)
                    current_daily["low"] = min(float(current_daily["low"]), day_low, price)
                    current_daily["close"] = price
                    if cumulative is not None:
                        try:
                            current_daily["volume"] = float(cumulative)
                        except (TypeError, ValueError):
                            pass
                    else:
                        current_daily["volume"] = float(current_daily["volume"]) + delta_volume

                snapshot = {
                    "symbol": symbol,
                    "instrument_token": int(raw_token),
                    "last_price": price,
                    "volume_traded": tick.get("volume_traded"),
                    "last_traded_quantity": tick.get("last_traded_quantity"),
                    "ohlc": tick.get("ohlc"),
                    "timestamp": dt.isoformat(),
                    "updated_at": datetime.now(IST).isoformat(),
                }
                self._latest[token] = snapshot
                updated.append(dict(snapshot))
                self._stats["ticks_received"] += 1
                self._stats["last_tick_at"] = dt.isoformat()

        # Batch completed one-minute bars outside the lock to keep tick handling short.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for symbol, candle in finalized:
            grouped.setdefault(symbol, []).append(candle)
        for symbol, candles in grouped.items():
            save_candles(symbol, "minute", candles)
            with self._lock:
                self._stats["candles_finalized"] += len(candles)
        return updated

    def current_candle(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            for state in self._states.values():
                if state.get("symbol") == symbol.upper() and state.get("candle"):
                    return self._serialise_candle(state["candle"])
        return None

    def current_daily_candle(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            for token, state in self._states.items():
                if state.get("symbol") == symbol.upper() and token in self._daily:
                    daily = dict(self._daily[token])
                    daily.pop("session", None)
                    daily["date"] = daily["date"].isoformat()
                    return daily
        return None

    def current_candles(self, symbol: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            results = []
            for state in self._states.values():
                if state.get("candle") and (symbol is None or state.get("symbol") == symbol.upper()):
                    results.append({"symbol": state["symbol"], **self._serialise_candle(state["candle"])})
            return results

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._latest.values()]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {**self._stats, "symbols_with_ticks": len(self._latest), "active_minute_candles": len(self._states)}

    def remove_tokens(self, tokens: list[int]) -> None:
        """Forget live quotes and partial bars for instruments being unsubscribed."""
        keys = {str(int(value)) for value in tokens}
        with self._lock:
            for key in keys:
                self._states.pop(key, None)
                self._daily.pop(key, None)
                self._latest.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._daily.clear()
            self._latest.clear()
            self._stats = {"ticks_received": 0, "candles_finalized": 0, "last_tick_at": None}
