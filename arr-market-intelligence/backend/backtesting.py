"""Conservative, close-of-day breakout backtest for one NSE cash symbol."""
from __future__ import annotations
import math
import pandas as pd
from backend.indicators import enrich


def backtest_frame(df: pd.DataFrame, initial_capital: float = 100000.0,
                   risk_pct: float = 1.0, slippage_bps: float = 10.0,
                   max_hold: int = 20, cost_bps_per_side: float = 11.5) -> dict:
    if initial_capital <= 0 or not 0 < risk_pct <= 5 or slippage_bps < 0 or not 1 <= max_hold <= 100 or cost_bps_per_side < 0:
        raise ValueError('Invalid backtest parameters')
    if df is None or len(df) < 80:
        return {'status': 'insufficient_data', 'message': 'At least 80 daily candles are required.', 'trades': [], 'metrics': {}}
    data = enrich(df.copy()).reset_index(drop=True)
    data['date'] = pd.to_datetime(data['date'], errors='coerce')
    cash = float(initial_capital)
    equity = [
        {'date': data.iloc[0]['date'].date().isoformat(), 'equity': round(cash, 2)}
    ]
    trades = []
    i = 1
    slip = slippage_bps / 10000.0
    cost_rate = cost_bps_per_side / 10000.0
    while i < len(data) - 1:
        signal = data.iloc[i - 1]
        valid = (
            pd.notna(signal.get('breakout_20d')) and bool(signal['breakout_20d'])
            and pd.notna(signal.get('ema_20')) and pd.notna(signal.get('ema_50'))
            and signal['close'] > signal['ema_50']
            and signal['ema_20'] > signal['ema_50']
            and pd.notna(signal.get('rsi_14')) and 50 <= signal['rsi_14'] <= 78
            and signal.get('supertrend_direction') == 'bullish'
            and pd.notna(signal.get('atr_14')) and signal['atr_14'] > 0
        )
        if not valid:
            i += 1
            continue
        entry_bar = data.iloc[i]
        raw_entry = float(entry_bar['open'])
        entry = raw_entry * (1 + slip)
        stop = entry - 1.5 * float(signal['atr_14'])
        target = entry + 3.0 * float(signal['atr_14'])
        risk_per_share = entry - stop
        risk_budget = cash * risk_pct / 100.0
        qty_by_risk = math.floor(risk_budget / risk_per_share) if risk_per_share > 0 else 0
        qty_by_cash = math.floor(cash * 0.98 / entry) if entry > 0 else 0
        qty = min(qty_by_risk, qty_by_cash)
        if qty <= 0:
            i += 1
            continue
        exit_idx = min(len(data) - 1, i + max_hold - 1)
        exit_price = float(data.iloc[exit_idx]['close'])
        exit_reason = 'time_exit'
        actual_exit_idx = exit_idx
        for j in range(i, exit_idx + 1):
            bar = data.iloc[j]
            op, hi, lo = float(bar['open']), float(bar['high']), float(bar['low'])
            # Conservative ordering: when both levels trade in one candle, assume stop first.
            if op <= stop:
                exit_price, exit_reason, actual_exit_idx = op, 'gap_stop', j
                break
            if lo <= stop:
                exit_price, exit_reason, actual_exit_idx = stop, 'stop_loss', j
                break
            if op >= target:
                exit_price, exit_reason, actual_exit_idx = op, 'gap_target', j
                break
            if hi >= target:
                exit_price, exit_reason, actual_exit_idx = target, 'target_hit', j
                break
        exit_bar = data.iloc[actual_exit_idx]
        fill_exit = exit_price * (1 - slip)
        gross_pnl = (fill_exit - entry) * qty
        # NSE delivery: STT 0.1% each side plus stamp, exchange, SEBI and GST charges.
        transaction_cost = (entry * qty + fill_exit * qty) * cost_rate
        net_pnl = gross_pnl - transaction_cost
        start_cash = cash
        cash += net_pnl
        # Track daily mark-to-market equity while a position is open, not just realised exits.
        entry_cost_estimate = entry * qty * cost_rate
        for mark_idx in range(i, actual_exit_idx + 1):
            mark_bar = data.iloc[mark_idx]
            mark_equity = cash if mark_idx == actual_exit_idx else start_cash + (float(mark_bar['close']) - entry) * qty - entry_cost_estimate
            equity.append({'date': mark_bar['date'].date().isoformat(), 'equity': round(mark_equity, 2)})
        ret_pct = (net_pnl / (entry * qty)) * 100 if entry * qty else 0
        trades.append({
            'signal_date': signal['date'].date().isoformat(),
            'entry_date': entry_bar['date'].date().isoformat(),
            'exit_date': exit_bar['date'].date().isoformat(),
            'entry_price': round(entry, 2), 'exit_price': round(fill_exit, 2),
            'stop_loss': round(stop, 2), 'target_price': round(target, 2),
            'quantity': qty, 'exit_reason': exit_reason,
            'net_pnl': round(net_pnl, 2), 'return_pct': round(ret_pct, 2),
            'bars_held': int(actual_exit_idx - i + 1), 'equity_before': round(start_cash, 2),
            'equity_after': round(cash, 2),
        })
        i = actual_exit_idx + 1
    pnl = [float(t['net_pnl']) for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    peak = float(initial_capital)
    max_dd = 0.0
    for point in equity:
        peak = max(peak, float(point['equity']))
        if peak:
            max_dd = max(max_dd, (peak - float(point['equity'])) / peak * 100)
    metrics = {
        'initial_capital': round(initial_capital, 2),
        'final_equity': round(cash, 2),
        'net_pnl': round(cash - initial_capital, 2),
        'total_return_pct': round((cash / initial_capital - 1) * 100, 2),
        'total_trades': len(trades),
        'win_rate_pct': round(len(wins) / len(trades) * 100, 2) if trades else 0,
        'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss else (None if gross_profit == 0 else 'infinite'),
        'average_trade_pct': round(sum(float(t['return_pct']) for t in trades) / len(trades), 2) if trades else 0,
        'max_drawdown_pct': round(max_dd, 2),
        'drawdown_basis': 'Daily mark-to-market equity on active positions; flat periods between trades retain the last realised equity.',
        'winning_trades': len(wins), 'losing_trades': len(losses),
    }
    return {
        'status': 'completed', 'strategy': 'ARR 20-session breakout + EMA trend + Supertrend',
        'assumptions': {
            'signal': 'Signal on completed daily candle: 20-session volume breakout, close above EMA50, EMA20 above EMA50, RSI 50–78 and bullish Supertrend.',
            'entry': 'Next session open, with configured slippage.',
            'risk': '1.5 ATR stop, 3 ATR target; position size is risk-budget constrained and cash funded.',
            'exit': 'Stop/target checked using OHLC; if both are reached in one candle, stop is assumed first. Otherwise time exit at max holding period.',
            'costs': f'{slippage_bps:g} bps slippage and {cost_bps_per_side:g} bps charges per side (delivery estimate; flat DP charge not modelled).',
            'limitations': 'Single-symbol, one-position-at-a-time simulation. Does not model market impact, exact broker charges, corporate actions, circuit limits, liquidity constraints or order queue behaviour. Historical results do not predict future returns.',
        },
        'parameters': {'initial_capital': initial_capital, 'risk_pct': risk_pct, 'slippage_bps': slippage_bps, 'max_hold': max_hold, 'cost_bps_per_side': cost_bps_per_side},
        'metrics': metrics, 'trades': trades, 'equity_curve': equity,
    }
