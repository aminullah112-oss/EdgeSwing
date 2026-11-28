import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from backend.config import DATABASE_PATH

_data_version=0
def data_version():return (DATABASE_PATH,_data_version)
def _bump():
    global _data_version
    _data_version+=1

@contextmanager
def connect():
    c=sqlite3.connect(DATABASE_PATH, timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=30000')
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS candles(symbol TEXT,timeframe TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,timeframe,ts))')
        c.execute('CREATE TABLE IF NOT EXISTS instruments(symbol TEXT PRIMARY KEY,instrument_token INTEGER,name TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS live_watchlist(symbol TEXT PRIMARY KEY, added_at TEXT NOT NULL)')
        c.execute("CREATE TABLE IF NOT EXISTS universe(symbol TEXT PRIMARY KEY,company TEXT,sector TEXT DEFAULT '')")
        cols={r['name'] for r in c.execute('PRAGMA table_info(universe)')}
        if 'sector' not in cols: c.execute("ALTER TABLE universe ADD COLUMN sector TEXT DEFAULT ''")
        c.execute('''CREATE TABLE IF NOT EXISTS job_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            total INTEGER DEFAULT 0, completed INTEGER DEFAULT 0,
            errors TEXT DEFAULT '[]', message TEXT DEFAULT '')''')
        c.execute('CREATE TABLE IF NOT EXISTS report_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, report_json TEXT NOT NULL)')
        c.execute("""CREATE TABLE IF NOT EXISTS signal_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
            signal_date TEXT NOT NULL, generated_at TEXT NOT NULL, reference_price REAL NOT NULL,
            entry_trigger REAL NOT NULL, stop_loss REAL NOT NULL, target_price REAL NOT NULL,
            confidence REAL, status TEXT NOT NULL DEFAULT 'active', exit_date TEXT, exit_price REAL,
            pnl_pct REAL, actual_entry REAL, details_json TEXT DEFAULT '{}', UNIQUE(symbol,strategy,signal_date))""")
        signal_cols={r['name'] for r in c.execute('PRAGMA table_info(signal_snapshots)')}
        if 'actual_entry' not in signal_cols: c.execute('ALTER TABLE signal_snapshots ADD COLUMN actual_entry REAL')
        c.execute('CREATE INDEX IF NOT EXISTS idx_signals_status_date ON signal_snapshots(status,signal_date)')

def save_candles(s,tf,rows):
    if not rows: return 0
    values=[]
    for x in rows:
        dt=x.get('date')
        if hasattr(dt,'isoformat'): dt=dt.isoformat()
        values.append((s,tf,str(dt),float(x['open']),float(x['high']),float(x['low']),float(x['close']),float(x.get('volume',0) or 0)))
    with connect() as c:
        c.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)',values)
    _bump()
    return len(values)

def load_candles(s,tf):
    with connect() as c: return [dict(r) for r in c.execute('SELECT ts date,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts',(s,tf))]

def save_instruments(rows):
    with connect() as c: c.executemany('INSERT OR REPLACE INTO instruments VALUES(?,?,?)',rows)

def instrument_symbol_map():
    with connect() as c: rows=c.execute('SELECT symbol,instrument_token FROM instruments').fetchall()
    return {str(int(r['instrument_token'])):r['symbol'] for r in rows}

def token(s):
    with connect() as c: r=c.execute('SELECT instrument_token FROM instruments WHERE symbol=?',(s,)).fetchone()
    return int(r[0]) if r else None

def save_universe(rows):
    values=[]
    for row in rows:
        values.append((row[0],row[1],row[2] if len(row)>2 and row[2] else ''))
    with connect() as c: c.executemany('INSERT OR REPLACE INTO universe(symbol,company,sector) VALUES(?,?,?)',values)
    _bump()

def universe():
    with connect() as c: return [dict(r) for r in c.execute('SELECT symbol,company,sector FROM universe ORDER BY symbol')]

def create_job(name,total=0,message='Queued'):
    now=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        cur=c.execute('INSERT INTO job_runs(job_name,status,started_at,total,message) VALUES(?,?,?,?,?)',(name,'queued',now,total,message))
        return cur.lastrowid

def update_job(job_id,**fields):
    allowed={'status','finished_at','total','completed','errors','message'}
    data={k:(json.dumps(v) if k=='errors' else v) for k,v in fields.items() if k in allowed}
    if not data:return
    sql=', '.join(f'{k}=?' for k in data)
    with connect() as c:c.execute(f'UPDATE job_runs SET {sql} WHERE id=?',(*data.values(),job_id))

def close_stale_jobs():
    # A job cannot survive a restart; leaving it 'running' would block every later sync.
    with connect() as c:c.execute("UPDATE job_runs SET status='interrupted',finished_at=?,message='Server restarted' WHERE status IN ('queued','running')",(datetime.now(timezone.utc).isoformat(),))

def latest_job():
    with connect() as c:r=c.execute('SELECT * FROM job_runs ORDER BY id DESC LIMIT 1').fetchone()
    if not r:return None
    x=dict(r)
    try:x['errors']=json.loads(x.get('errors') or '[]')
    except Exception:x['errors']=[]
    return x

def save_report(report):
    with connect() as c:c.execute('INSERT INTO report_snapshots(generated_at,report_json) VALUES(?,?)',(report['generated_at'],json.dumps(report,default=str)))

def report_history(limit=20):
    with connect() as c:rows=c.execute('SELECT id,generated_at FROM report_snapshots ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
    return [dict(r) for r in rows]

def get_report(report_id):
    with connect() as c:r=c.execute('SELECT report_json FROM report_snapshots WHERE id=?',(report_id,)).fetchone()
    return json.loads(r['report_json']) if r else None


def save_signals(rows, strategy='ARR_BREAKOUT_V1'):
    if not rows: return 0
    values=[]
    for r in rows:
        signal_date=str(r.get('signal_date') or r.get('date') or '')[:10]
        if not signal_date or any(r.get(k) is None for k in ('close','entry_trigger','stop_loss','target_price')): continue
        values.append((r['symbol'],strategy,signal_date,datetime.now(timezone.utc).isoformat(),float(r.get('close')),float(r['entry_trigger']),float(r['stop_loss']),float(r['target_price']),float(r.get('confidence') or 0),json.dumps(r,default=str)))
    if not values: return 0
    with connect() as c:
        before=c.total_changes
        c.executemany("INSERT OR IGNORE INTO signal_snapshots(symbol,strategy,signal_date,generated_at,reference_price,entry_trigger,stop_loss,target_price,confidence,details_json) VALUES(?,?,?,?,?,?,?,?,?,?)",values)
        return c.total_changes-before

def active_signals():
    with connect() as c: rows=c.execute("SELECT * FROM signal_snapshots WHERE status='active' ORDER BY signal_date").fetchall()
    return [dict(r) for r in rows]

def update_signal_outcome(signal_id,status,exit_date=None,exit_price=None,pnl_pct=None,actual_entry=None):
    with connect() as c:
        c.execute('UPDATE signal_snapshots SET status=?,exit_date=?,exit_price=?,pnl_pct=?,actual_entry=? WHERE id=?',(status,exit_date,exit_price,pnl_pct,actual_entry,signal_id))

def signal_history(limit=100):
    with connect() as c: rows=c.execute('SELECT * FROM signal_snapshots ORDER BY signal_date DESC,id DESC LIMIT ?', (limit,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try: item['details']=json.loads(item.pop('details_json') or '{}')
        except Exception: item['details']={}
        out.append(item)
    return out


def save_watchlist(symbols):
    now = datetime.now(timezone.utc).isoformat()
    values = [(str(symbol).strip().upper(), now) for symbol in symbols if str(symbol).strip()]
    if not values:
        return 0
    with connect() as c:
        c.executemany('INSERT OR IGNORE INTO live_watchlist(symbol,added_at) VALUES(?,?)', values)
        return c.total_changes

def remove_watchlist(symbols):
    values = [(str(symbol).strip().upper(),) for symbol in symbols if str(symbol).strip()]
    if not values:
        return 0
    with connect() as c:
        before = c.total_changes
        c.executemany('DELETE FROM live_watchlist WHERE symbol=?', values)
        return c.total_changes - before

def live_watchlist():
    with connect() as c:
        return [dict(row) for row in c.execute('SELECT symbol,added_at FROM live_watchlist ORDER BY symbol')]
