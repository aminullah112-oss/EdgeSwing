from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import app as app_module, config, security
from backend.analysis import resample
from backend.backtesting import backtest_frame
from backend.indicators import aligned_closes, comparative_rs, enrich

KEY = 'k' * 32
AUTH = {'Authorization': f'Bearer {KEY}'}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, 'APP_ACCESS_KEY', KEY)
    monkeypatch.setattr(config, 'KITE_API_KEY', 'testkey')
    monkeypatch.setattr(config, 'KITE_API_SECRET', 'testsecret')
    return TestClient(app_module.app)


def bars(n, freq='D', start='2026-01-01'):
    c = pd.Series([100 + i * 0.5 for i in range(n)])
    return pd.DataFrame({'date': pd.date_range(start, periods=n, freq=freq), 'open': c, 'high': c + 1,
                         'low': c - 1, 'close': c, 'volume': 1000})


def test_health_is_public_and_data_is_private(client):
    assert client.get('/api/health').status_code == 200
    for path in ['/api/status', '/api/screener', '/api/universe', '/api/live/snapshot', '/api/signals/history']:
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401, path
    assert client.get('/api/status', headers=AUTH).status_code == 200
    assert client.post('/api/live/stop').status_code == 401


def test_server_locks_without_a_strong_key(client, monkeypatch):
    monkeypatch.setattr(config, 'APP_ACCESS_KEY', 'short')
    assert client.get('/api/status', headers={'Authorization': 'Bearer short'}).status_code == 503


def test_cors_allows_only_configured_origin(client):
    ok = client.options('/api/status', headers={'Origin': config.ALLOWED_ORIGINS[0], 'Access-Control-Request-Method': 'GET',
                                                'Access-Control-Request-Headers': 'authorization'})
    assert ok.headers['access-control-allow-origin'] == config.ALLOWED_ORIGINS[0]
    bad = client.get('/api/health', headers={'Origin': 'https://evil.example'})
    assert 'access-control-allow-origin' not in bad.headers


def test_callback_needs_a_one_time_state(client, monkeypatch):
    seen = []
    monkeypatch.setattr(app_module.kite, 'exchange', lambda token: seen.append(token))
    assert client.get('/auth/callback?request_token=abc').status_code == 403
    url = client.post('/api/auth/login-url', headers=AUTH).json()['url']
    assert url.startswith('https://kite.zerodha.com/connect/login?v=3&api_key=testkey')
    assert 'testsecret' not in url
    state = parse_qs(parse_qs(urlparse(url).query)['redirect_params'][0])['state'][0]
    first = client.get(f'/auth/callback?request_token=abc&status=success&state={state}', follow_redirects=False)
    assert first.status_code == 307 and 'request_token' not in first.headers['location']
    assert client.get(f'/auth/callback?request_token=abc&state={state}').status_code == 403
    assert seen == ['abc']


def test_websocket_requires_key(client):
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'key': 'wrong'})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'key': KEY})
        assert 'quotes' in ws.receive_json()


def test_local_frontend_files_are_served(client):
    for path in ['/', '/config.js', '/vendor/lightweight-charts.standalone.production.js']:
        assert client.get(path).status_code == 200, path
    assert client.get('/%2e%2e/backend/config.py').status_code == 404


def test_rs_compares_the_same_dates():
    stock, bench = bars(40), bars(40)
    gappy = stock.drop(index=[30, 31]).reset_index(drop=True)
    assert comparative_rs(*aligned_closes(gappy, bench), periods=(21,))['rs_21d_pct'] == 0
    stale = bench.iloc[:-1]
    assert comparative_rs(*aligned_closes(stock, stale), periods=(21,))['rs_21d_pct'] is None


def test_vwap_resets_each_session_and_rolls_on_daily():
    two_days = pd.concat([bars(5, '5min', '2026-01-01 09:15'), bars(5, '5min', '2026-01-02 09:15')])
    out = enrich(two_days)
    assert out.vwap.iloc[5] == pytest.approx(100.0)
    daily = enrich(bars(25))
    assert pd.isna(daily.vwap.iloc[18])
    assert daily.vwap.iloc[-1] == pytest.approx(daily.close.iloc[-20:].mean())


def test_ema20_pullback_detection():
    d = bars(80)
    assert not enrich(d).pullback_ema20.iloc[-1]
    ema20 = enrich(d).ema_20.iloc[-1]
    d.loc[78, ['low', 'close', 'open']] = [ema20 - 2, d.close.iloc[77] - 3, d.close.iloc[77]]
    assert enrich(d).pullback_ema20.iloc[-1]


def test_backtest_charges_are_applied():
    closes, level = [], 100.0
    for i in range(200):
        level += 3.0 if i % 25 == 20 else (0.9 if i % 2 == 0 else -0.6)
        closes.append(level)
    d = bars(200)
    d['close'] = d['open'] = closes
    d['high'] = d.close + 0.5
    d['low'] = d.close - 0.5
    d.loc[range(20, 200, 25), 'volume'] = 5000
    free = backtest_frame(d.copy(), cost_bps_per_side=0)
    paid = backtest_frame(d.copy())
    assert free['metrics']['total_trades'] > 0, 'fixture must generate a trade'
    assert paid['metrics']['final_equity'] < free['metrics']['final_equity']


def test_weekly_resample():
    assert len(resample(bars(14), 'W-FRI')) == 3
