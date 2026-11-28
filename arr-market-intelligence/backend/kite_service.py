import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from backend import config
from backend.db import save_instruments, token, save_candles, instrument_symbol_map, save_watchlist, live_watchlist
from backend.live_engine import LiveCandleEngine


class KiteService:
    def __init__(self):
        self.kite = None
        self.ticker = None
        self.access_token = None
        self.connected = False
        self.ticker_connected = False
        self.ticker_status = "disconnected"
        self.live = {}
        self._token_lock = threading.RLock()
        self._subscribed_tokens = set()
        self.token_symbols = instrument_symbol_map()
        self.live_candles = LiveCandleEngine()
        self.last_ticker_error = None

    def login_url(self):
        if not config.KITE_API_KEY:
            raise RuntimeError('Set KITE_API_KEY in local .env')
        return f'https://kite.zerodha.com/connect/login?v=3&api_key={config.KITE_API_KEY}'

    def exchange(self, request_token):
        from kiteconnect import KiteConnect
        if not config.KITE_API_KEY or not config.KITE_API_SECRET:
            raise RuntimeError('KITE_API_KEY and KITE_API_SECRET required in .env')
        if self.ticker is not None:
            self.stop_live()  # a ticker built with the previous token cannot be reused
        self.connected = False
        self.kite = KiteConnect(api_key=config.KITE_API_KEY)
        data = self.kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
        self.access_token = data['access_token']
        self.kite.set_access_token(self.access_token)
        self.kite.set_session_expiry_hook(self._on_session_expired)
        self.connected = True
        return {'user_name': data.get('user_name'), 'user_id': data.get('user_id')}

    def _on_session_expired(self):
        self.connected = False

    def require(self):
        if not self.connected or not self.kite:
            raise RuntimeError('Connect Zerodha first')

    def sync_instruments(self):
        self.require()
        df = pd.DataFrame(self.kite.instruments('NSE'))
        eq = df[(df.exchange == 'NSE') & (df.instrument_type.isin(['EQ', 'INDEX']))].copy()
        # Normalize benchmark aliases so analytics can address them consistently.
        eq.loc[eq.tradingsymbol.astype(str).str.upper().isin(['NIFTY 50', 'NIFTY50']), 'tradingsymbol'] = 'NIFTY 50'
        eq.loc[eq.tradingsymbol.astype(str).str.upper().isin(['NIFTY 500', 'NIFTY500']), 'tradingsymbol'] = 'NIFTY 500'
        save_instruments([(r.tradingsymbol, int(r.instrument_token), r.name or r.tradingsymbol) for r in eq.itertuples()])
        with self._token_lock:
            self.token_symbols = {str(int(r.instrument_token)): r.tradingsymbol for r in eq.itertuples()}
        return len(eq)

    def history(self, symbol, interval, days):
        self.require()
        instrument_token = token(symbol)
        if not instrument_token:
            raise ValueError(f'{symbol}: instrument token not found; sync instruments')
        # Kite reads naive datetimes as IST, so never use the host's local clock here.
        now = datetime.now(ZoneInfo('Asia/Kolkata')).replace(tzinfo=None)
        rows = self.kite.historical_data(instrument_token, now - timedelta(days=days), now, interval)
        save_candles(symbol, interval, rows)
        return len(rows)

    def _on_ticks(self, ws, ticks):
        with self._token_lock:
            symbols = dict(self.token_symbols)
        updated = self.live_candles.process_ticks(ticks, symbols)
        with self._token_lock:
            for value in updated:
                self.live[str(value['instrument_token'])] = value

    def _on_connect(self, ws, response):
        with self._token_lock:
            tokens = list(self._subscribed_tokens)
        existing = set(getattr(ws, 'subscribed_tokens', {}).keys())
        missing = [value for value in tokens if value not in existing]
        if missing:
            ws.subscribe(missing)
            ws.set_mode(ws.MODE_FULL, missing)
        self.ticker_connected = True
        self.ticker_status = 'connected'
        self.last_ticker_error = None

    def _on_close(self, ws, code, reason):
        self.ticker_connected = False
        if self.ticker_status != 'stopped':
            self.ticker_status = 'closed'

    def _on_error(self, ws, code, reason):
        self.ticker_connected = False
        self.ticker_status = 'error'
        self.last_ticker_error = f'{code}: {reason}'

    def _on_reconnect(self, ws, attempts_count):
        self.ticker_status = f'reconnecting ({attempts_count})'

    def _on_noreconnect(self, ws):
        self.ticker_connected = False
        self.ticker_status = 'reconnect_failed'

    def subscribe(self, tokens):
        from kiteconnect import KiteTicker
        self.require()
        clean = {int(value) for value in tokens if value is not None}
        if not clean:
            raise ValueError('No valid instrument tokens')
        with self._token_lock:
            new_tokens = clean - self._subscribed_tokens
            self._subscribed_tokens.update(clean)
            if self.ticker is None:
                self.ticker = KiteTicker(config.KITE_API_KEY, self.access_token)
                self.ticker.on_ticks = self._on_ticks
                self.ticker.on_connect = self._on_connect
                self.ticker.on_close = self._on_close
                self.ticker.on_error = self._on_error
                self.ticker.on_reconnect = self._on_reconnect
                self.ticker.on_noreconnect = self._on_noreconnect
                self.ticker_status = 'connecting'
                self.ticker.connect(threaded=True)
            elif self.ticker_connected and new_tokens:
                self.ticker.subscribe(list(new_tokens))
                self.ticker.set_mode(self.ticker.MODE_FULL, list(new_tokens))
        return len(clean)

    def unsubscribe(self, tokens):
        clean = {int(value) for value in tokens if value is not None}
        with self._token_lock:
            remove = clean & self._subscribed_tokens
            self._subscribed_tokens.difference_update(remove)
            if self.ticker and self.ticker_connected and remove:
                self.ticker.unsubscribe(list(remove))
            for token_id in remove:
                self.live.pop(str(token_id), None)
            if remove:
                self.live_candles.remove_tokens(list(remove))
            return len(remove)

    def stop_live(self):
        with self._token_lock:
            ticker = self.ticker
            self.ticker = None
            self._subscribed_tokens.clear()
            self.ticker_connected = False
            self.ticker_status = 'stopped'
            self.live.clear()
            self.live_candles.clear()
        if ticker:
            try:
                ticker.close()
            except Exception:
                pass
        return True

    def live_snapshots(self):
        with self._token_lock:
            return [dict(value) for value in self.live.values()]

    def live_quote(self, symbol):
        target = str(symbol).upper()
        with self._token_lock:
            return next((dict(value) for value in self.live.values() if value.get('symbol') == target), None)

    def live_status(self):
        with self._token_lock:
            return {
                'connected': self.ticker_connected,
                'status': self.ticker_status,
                'subscribed_tokens': len(self._subscribed_tokens),
                'last_error': self.last_ticker_error,
                'candle_engine': self.live_candles.stats(),
            }
