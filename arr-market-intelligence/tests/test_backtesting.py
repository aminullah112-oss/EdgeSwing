import pandas as pd
import pytest
from backend.backtesting import backtest_frame


def frame(n=120):
    close = [100 + i * 0.15 + (i % 5) * 0.08 for i in range(n)]
    return pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=n, freq='B'),
        'open': [x - 0.05 for x in close],
        'high': [x + 0.4 for x in close],
        'low': [x - 0.4 for x in close],
        'close': close,
        'volume': [100000 + (i % 7) * 1000 for i in range(n)],
    })


def test_backtest_returns_explicit_assumptions_and_metrics():
    result = backtest_frame(frame(), initial_capital=100000, risk_pct=1, slippage_bps=10, max_hold=20)
    assert result['status'] == 'completed'
    assert 'next session open' in result['assumptions']['entry'].lower()
    assert 'stop is assumed first' in result['assumptions']['exit'].lower()
    assert 'max_drawdown_pct' in result['metrics']
    assert isinstance(result['trades'], list)


def test_backtest_insufficient_history_is_not_fabricated():
    result = backtest_frame(frame(40))
    assert result['status'] == 'insufficient_data'
    assert result['trades'] == []


def test_backtest_rejects_invalid_risk():
    with pytest.raises(ValueError):
        backtest_frame(frame(), risk_pct=10)


def test_backtest_enters_on_next_session_and_marks_to_market():
    import numpy as np
    from backend.backtesting import backtest_frame
    rng = np.random.default_rng(5)
    returns = rng.normal(.0015, .005, 130)
    close = 100 * np.cumprod(1 + returns)
    close[110:] = close[109] * np.cumprod(1 + np.array([.02,.006,.008,.008,.006,.007,.005,.006,.005,.006,.005,.004,.004,.004,.003,.004,.003,.003,.003,.003]))
    volume = np.full(len(close), 100000.0); volume[110] = 300000
    op = close * .998; high = np.maximum(op, close) * 1.004; low = np.minimum(op, close) * .996
    data = pd.DataFrame({'date':pd.date_range('2025-01-01', periods=len(close), freq='B'), 'open':op, 'high':high, 'low':low, 'close':close, 'volume':volume})
    result = backtest_frame(data)
    assert result['metrics']['total_trades'] == 1
    trade = result['trades'][0]
    assert trade['entry_date'] > trade['signal_date']
    assert len(result['equity_curve']) > 2
    assert 'mark-to-market' in result['metrics']['drawdown_basis']
