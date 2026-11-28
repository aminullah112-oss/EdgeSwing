from datetime import datetime
from zoneinfo import ZoneInfo

import backend.db as db
from backend.live_engine import LiveCandleEngine

IST = ZoneInfo('Asia/Kolkata')


def test_tick_stream_builds_minute_ohlcv_and_persists_completed_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'live.sqlite'))
    db.init_db()
    engine = LiveCandleEngine()
    symbols = {'12345': 'RELIANCE'}

    engine.process_ticks([
        {
            'instrument_token': 12345,
            'last_price': 100.0,
            'volume_traded': 1000,
            'last_traded_quantity': 5,
            'exchange_timestamp': datetime(2026, 11, 28, 9, 15, 5, tzinfo=IST),
        },
        {
            'instrument_token': 12345,
            'last_price': 102.0,
            'volume_traded': 1010,
            'last_traded_quantity': 10,
            'exchange_timestamp': datetime(2026, 11, 28, 9, 15, 40, tzinfo=IST),
        },
    ], symbols)

    current = engine.current_candle('RELIANCE')
    assert current['open'] == 100.0
    assert current['high'] == 102.0
    assert current['low'] == 100.0
    assert current['close'] == 102.0
    assert current['volume'] == 15.0

    engine.process_ticks([{
        'instrument_token': 12345,
        'last_price': 101.0,
        'volume_traded': 1017,
        'last_traded_quantity': 7,
        'exchange_timestamp': datetime(2026, 11, 28, 9, 16, 2, tzinfo=IST),
    }], symbols)

    saved = db.load_candles('RELIANCE', 'minute')
    assert len(saved) == 1
    assert saved[0]['open'] == 100.0
    assert saved[0]['high'] == 102.0
    assert saved[0]['low'] == 100.0
    assert saved[0]['close'] == 102.0
    assert saved[0]['volume'] == 15.0
    assert engine.current_candle('RELIANCE')['close'] == 101.0
    assert engine.stats()['candles_finalized'] == 1


def test_late_tick_does_not_rewrite_the_active_minute(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'late.sqlite'))
    db.init_db()
    engine = LiveCandleEngine()
    symbols = {'9': 'INFY'}
    engine.process_ticks([{
        'instrument_token': 9, 'last_price': 50, 'volume_traded': 100,
        'exchange_timestamp': datetime(2026, 11, 28, 9, 17, tzinfo=IST),
    }], symbols)
    engine.process_ticks([{
        'instrument_token': 9, 'last_price': 49, 'volume_traded': 102,
        'exchange_timestamp': datetime(2026, 11, 28, 9, 16, 50, tzinfo=IST),
    }], symbols)
    assert engine.current_candle('INFY')['close'] == 50.0
    assert engine.snapshots()[0]['last_price'] == 49.0


def test_live_5minute_interval_resamples_minute_bars(tmp_path, monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'aggregate.sqlite'))
    db.init_db()
    app_module.kite.live_candles.clear()
    db.save_candles('TCS', 'minute', [
        {'date': f'2026-11-28T09:{minute}:00+05:30', 'open': opening, 'high': high, 'low': low, 'close': close, 'volume': 10}
        for minute, opening, high, low, close in [
            ('15', 100, 101, 99, 100.5),
            ('16', 100.5, 102, 100, 101.5),
            ('17', 101.5, 103, 101, 102.5),
            ('18', 102.5, 103, 102, 102.0),
            ('19', 102.0, 104, 101.5, 103.5),
        ]
    ])
    result = app_module.live_intraday_frame('TCS', '5minute')
    assert len(result) == 1
    row = result.iloc[0]
    assert row['open'] == 100
    assert row['high'] == 104
    assert row['low'] == 99
    assert row['close'] == 103.5
    assert row['volume'] == 50


def test_remove_tokens_clears_live_quote_and_partial_candle():
    engine = LiveCandleEngine()
    engine.process_ticks([{
        'instrument_token': 123, 'last_price': 10, 'volume_traded': 500,
        'exchange_timestamp': datetime(2026, 11, 28, 9, 15, tzinfo=IST),
    }], {'123': 'SBIN'})
    assert engine.snapshots()
    assert engine.current_candle('SBIN') is not None
    engine.remove_tokens([123])
    assert engine.snapshots() == []
    assert engine.current_candle('SBIN') is None


def test_quote_freshness_gate_rejects_stale_data():
    from datetime import timedelta
    from backend.app import quote_is_fresh

    fresh = datetime.now(IST).isoformat()
    stale = (datetime.now(IST) - timedelta(seconds=180)).isoformat()
    assert quote_is_fresh({'updated_at': fresh}) is True
    assert quote_is_fresh({'updated_at': stale}) is False
    assert quote_is_fresh(None) is False


def test_current_daily_tick_is_included_in_daily_and_weekly_analysis(tmp_path, monkeypatch):
    import pandas as pd
    import backend.app as app_module

    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'daily-live.sqlite'))
    db.init_db()
    app_module.kite.live_candles.clear()
    start = pd.Timestamp('2026-10-01 00:00:00', tz='Asia/Kolkata')
    history = []
    for i in range(40):
        close = 100 + i
        history.append({
            'date': (start + pd.Timedelta(days=i)).isoformat(),
            'open': close - 0.5, 'high': close + 1, 'low': close - 1,
            'close': close, 'volume': 1000 + i,
        })
    db.save_candles('LIVE_TEST', 'day', history)
    now = datetime.now(IST)
    app_module.kite.live_candles.process_ticks([{
        'instrument_token': 321, 'last_price': 150, 'volume_traded': 99999,
        'ohlc': {'open': 140, 'high': 151, 'low': 139, 'close': 138},
        'exchange_timestamp': now,
    }], {'321': 'LIVE_TEST'})
    alignment = app_module.live_timeframe_alignment('LIVE_TEST')
    assert alignment['daily']['close'] == 150
    assert alignment['weekly']['close'] == 150
    assert alignment['daily_basis'].startswith('live tick OHLC')


def test_live_intelligence_joins_watchlist_with_tick_freshness(tmp_path, monkeypatch):
    import pandas as pd
    from fastapi.testclient import TestClient
    import backend.app as app_module

    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'live-intel.sqlite'))
    db.init_db()
    db.save_watchlist(['LIVE_TEST'])
    app_module.kite.live.clear()
    app_module.kite.live_candles.clear()
    app_module.kite.token_symbols = {'777': 'LIVE_TEST'}
    start = pd.Timestamp('2026-09-01 00:00:00', tz='Asia/Kolkata')
    history = []
    for i in range(70):
        close = 100 + i * 0.8
        history.append({'date': (start + pd.Timedelta(days=i)).isoformat(), 'open': close - .4,
                        'high': close + 1, 'low': close - 1, 'close': close, 'volume': 10000 + i * 100})
    db.save_candles('LIVE_TEST', 'day', history)
    app_module.kite._on_ticks(None, [{
        'instrument_token': 777, 'last_price': 160, 'volume_traded': 50000,
        'last_traded_quantity': 25, 'ohlc': {'open': 155, 'high': 161, 'low': 154, 'close': 154.5},
        'exchange_timestamp': datetime.now(IST),
    }])
    monkeypatch.setattr(app_module.config, 'APP_ACCESS_KEY', 't' * 32)
    with TestClient(app_module.app) as client:
        response = client.get('/api/intelligence/live?interval=5minute',
                              headers={'Authorization': 'Bearer ' + 't' * 32})
    assert response.status_code == 200
    result = response.json()
    assert result['watchlist_count'] == 1
    assert result['opportunities'][0]['symbol'] == 'LIVE_TEST'
    assert result['opportunities'][0]['tick_fresh'] is True
    app_module.kite.live.clear()
    app_module.kite.live_candles.clear()
    app_module.kite.token_symbols = {}
