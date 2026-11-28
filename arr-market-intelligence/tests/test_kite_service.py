"""Kite wrapper behaviour against a fake client; no Zerodha connectivity is exercised."""
import backend.db as db
from backend.kite_service import KiteService


class FakeKite:
    def __init__(self):
        self.calls = []

    def instruments(self, exchange):
        def row(symbol, token, kind='EQ'):
            return {'exchange': 'NSE', 'instrument_type': kind, 'tradingsymbol': symbol, 'instrument_token': token, 'name': symbol}
        return [row('RELIANCE', 1), row('NIFTY 50', 2), row('NIFTY 500', 3), row('SKIPME', 4, 'FUT')]

    def historical_data(self, token, start, end, interval):
        self.calls.append((token, start, end, interval))
        return [{'date': '2026-11-27T00:00:00+05:30', 'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 10}]


def test_instrument_mapping_and_history_with_fake_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'kite.sqlite'))
    db.init_db()
    service = KiteService()
    service.kite, service.connected = FakeKite(), True
    assert service.sync_instruments() == 3
    assert (db.token('RELIANCE'), db.token('NIFTY 50'), db.token('NIFTY 500'), db.token('SKIPME')) == (1, 2, 3, None)
    assert service.history('NIFTY 500', 'day', 400) == 1
    token, start, end, interval = service.kite.calls[0]
    assert (token, interval, (end - start).days, end.tzinfo) == (3, 'day', 400, None)
    assert db.load_candles('NIFTY 500', 'day')[0]['close'] == 2
    service._on_session_expired()
    assert service.connected is False
