from backend.intelligence import _trend, risk_reference


def test_trend_classification_uses_multiple_indicators():
    bullish = {'close': 120, 'ema_20': 115, 'ema_50': 110, 'supertrend_direction': 'bullish'}
    bearish = {'close': 90, 'ema_20': 95, 'ema_50': 100, 'supertrend_direction': 'bearish'}
    assert _trend(bullish) == 'bullish'
    assert _trend(bearish) == 'bearish'
    assert _trend(None) == 'unavailable'


def test_atr_risk_levels_have_two_to_one_reward_risk():
    levels = risk_reference({'close': 100, 'atr_14': 2, 'prior_high_20': 101})
    assert levels['entry_trigger'] == 101.2
    assert levels['stop_loss'] == 98.2
    assert levels['target_price'] == 107.2
    assert levels['reward_risk'] == 2.0


def test_market_regime_is_unknown_without_benchmark_candles(monkeypatch):
    import backend.intelligence as intelligence
    monkeypatch.setattr(intelligence, '_last_enriched', lambda symbol, timeframe: None)
    out = intelligence.market_regime([])
    assert out['regime'] == 'UNKNOWN'
    assert out['regime_score'] is None


def test_intelligence_uses_daily_weekly_and_sector_metadata(tmp_path, monkeypatch):
    import pandas as pd
    import backend.db as db
    import backend.intelligence as intelligence
    from backend.analysis import screener

    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'intel.sqlite'))
    db.init_db()
    db.save_universe([('ALPHA', 'Alpha Limited', 'Industrials')])
    dates = pd.date_range('2025-01-01', periods=320, freq='B')
    rows = []
    for idx, date in enumerate(dates):
        close = 100 + idx * 0.35
        rows.append({'date':date, 'open':close-0.1, 'high':close+0.7, 'low':close-0.7, 'close':close, 'volume':100000+idx*10})
    db.save_candles('ALPHA', 'day', rows)
    for symbol, base in [('NIFTY 50', 200), ('NIFTY 500', 300)]:
        bench = []
        for idx, date in enumerate(dates):
            close = base + idx * 0.22
            bench.append({'date':date, 'open':close-0.1, 'high':close+0.5, 'low':close-0.5, 'close':close, 'volume':1000000})
        db.save_candles(symbol, 'day', bench)

    analysed = screener('day')
    assert analysed and analysed[0]['sector'] == 'Industrials'
    output = intelligence.assemble_intelligence(analysed)
    assert output['market_regime']['regime'] == 'BULLISH'
    assert output['sector_strength'][0]['sector'] == 'Industrials'
    assert output['opportunities']
    candidate = output['opportunities'][0]
    assert candidate['timeframes']['daily'] == 'bullish'
    assert candidate['timeframes']['weekly'] == 'bullish'
    assert candidate['timeframe_confirmation'] == 'DAILY + WEEKLY CONFIRMED'
    assert candidate['reward_risk'] == 2.0
