"""Persist opportunity signals and track entry-triggered reference outcomes."""
from __future__ import annotations
from datetime import datetime, timezone
from backend.db import save_signals, active_signals, signal_history, update_signal_outcome, load_candles


def _date(value: str) -> str:
    return str(value or '')[:10]


def record_and_update(opportunities: list[dict]) -> dict:
    inserted = save_signals(opportunities)
    resolved = 0
    for signal in active_signals():
        candles = load_candles(signal['symbol'], 'day')
        later = [c for c in candles if _date(c['date']) > _date(signal['signal_date'])]
        entered = False
        actual_entry = None
        entered_bars = 0
        status = None
        exit_date = exit_price = pnl_pct = None
        trigger = float(signal['entry_trigger'])
        stop, target = float(signal['stop_loss']), float(signal['target_price'])
        for idx, bar in enumerate(later, start=1):
            op, hi, lo = float(bar['open']), float(bar['high']), float(bar['low'])
            if not entered:
                # The simulated position starts only after the stored trigger is traded.
                if op >= target:
                    status = 'gap_missed'
                    exit_date = _date(bar['date'])
                    update_signal_outcome(signal['id'], status, exit_date, None, None, None)
                    resolved += 1
                    break
                if op >= trigger:
                    entered, actual_entry = True, op
                elif hi >= trigger:
                    entered, actual_entry = True, trigger
                elif idx >= 20:
                    status = 'not_triggered'
                    exit_date = _date(bar['date'])
                    update_signal_outcome(signal['id'], status, exit_date, None, None, None)
                    resolved += 1
                    break
                if not entered:
                    continue
            entered_bars += 1
            # If stop and target both trade on the entry bar, the stop is assumed first.
            if op <= stop:
                status, exit_price = 'stop_hit', op
            elif lo <= stop:
                status, exit_price = 'stop_hit', stop
            elif op >= target:
                status, exit_price = 'target_hit', op
            elif hi >= target:
                status, exit_price = 'target_hit', target
            elif entered and entered_bars >= 20:
                status, exit_price = 'expired', float(bar['close'])
            if status:
                exit_date = _date(bar['date'])
                pnl_pct = round((exit_price / actual_entry - 1) * 100, 2) if actual_entry else None
                update_signal_outcome(signal['id'], status, exit_date, round(exit_price, 2), pnl_pct, round(actual_entry, 2) if actual_entry else None)
                resolved += 1
                break
    return {'new_signals': inserted, 'resolved_signals': resolved, 'active_signals': len(active_signals())}


def performance(limit: int = 500) -> dict:
    history = signal_history(limit)
    closed = [x for x in history if x.get('status') in ('target_hit', 'stop_hit', 'expired') and x.get('pnl_pct') is not None]
    wins = [x for x in closed if float(x['pnl_pct']) > 0]
    losers = [x for x in closed if float(x['pnl_pct']) < 0]
    return {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'total_signals': len(history),
        'active': sum(x.get('status') == 'active' for x in history),
        'closed': len(closed),
        'target_hits': sum(x.get('status') == 'target_hit' for x in closed),
        'stop_hits': sum(x.get('status') == 'stop_hit' for x in closed),
        'expired': sum(x.get('status') == 'expired' for x in closed),
        'not_triggered': sum(x.get('status') == 'not_triggered' for x in history),
        'gap_missed': sum(x.get('status') == 'gap_missed' for x in history),
        'win_rate_pct': round(len(wins) / len(closed) * 100, 2) if closed else None,
        'average_outcome_pct': round(sum(float(x['pnl_pct']) for x in closed) / len(closed), 2) if closed else None,
        'average_win_pct': round(sum(float(x['pnl_pct']) for x in wins) / len(wins), 2) if wins else None,
        'average_loss_pct': round(sum(float(x['pnl_pct']) for x in losers) / len(losers), 2) if losers else None,
        'method': 'A signal becomes a simulated trade only after its entry trigger is crossed. The tracker uses the actual simulated entry (opening gap or trigger), stored stop and target; stop is assumed first when both stop and target are touched in the same daily candle. Gross reference returns exclude transaction costs.',
    }
