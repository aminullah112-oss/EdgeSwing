from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import asyncio
import logging
import time
import pandas as pd
from fastapi import FastAPI,HTTPException,Query,WebSocket,WebSocketDisconnect
from fastapi.responses import RedirectResponse,JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from urllib.parse import urlencode
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from backend.config import DATA_DIR
from backend.db import (init_db,universe,save_universe,token,latest_job,close_stale_jobs,report_history,get_report,save_report,load_candles,save_watchlist,remove_watchlist,live_watchlist)
from backend.kite_service import KiteService
from backend.analysis import screener,build_stock,daily_report
from backend.backtesting import backtest_frame
from backend.intelligence import market_regime,sector_strength,build_opportunities,timeframe_alignment,risk_reference,_trend
from backend.signals import record_and_update,performance
from backend.indicators import enrich
from backend.db import signal_history
from backend import jobs,config,security

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log=logging.getLogger('arr')

@asynccontextmanager
async def lifespan(application):
    jobs.start_scheduler()
    try:
        yield
    finally:
        if jobs.scheduler.running:
            jobs.scheduler.shutdown(wait=False)

app=FastAPI(title='ARR Market Intelligence',version='0.5.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
init_db(); close_stale_jobs(); kite=KiteService(); jobs.configure(kite)
ROOT=Path(__file__).resolve().parents[1]
PUBLIC_API={'/api/health'}

@app.middleware('http')
async def require_access_key(request,call_next):
    path=request.url.path
    if path.startswith('/api/') and path not in PUBLIC_API and request.method!='OPTIONS':
        if not security.configured():
            return JSONResponse({'detail':'Server is locked: set APP_ACCESS_KEY (24+ characters).'},status_code=503)
        scheme,_,value=request.headers.get('authorization','').partition(' ')
        if scheme.lower()!='bearer' or not security.key_matches(value):
            return JSONResponse({'detail':'Missing or wrong access key'},status_code=401)
    response=await call_next(request)
    if path.startswith(('/api/','/auth/')):
        response.headers['Cache-Control']='no-store'
    return response

# Added after the key check so CORS is the outermost layer and 401s stay readable by the dashboard.
app.add_middleware(CORSMiddleware,allow_origins=config.ALLOWED_ORIGINS,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

@app.get('/api/status')
def status():
    j=latest_job()
    return {'connected':kite.connected,'universe_count':len(universe()),'live_symbols':len(kite.live_snapshots()),'mode':'kite' if kite.connected else 'offline','last_job':j,'scheduler_running':jobs.scheduler.running,'live':kite.live_status()}
@app.post('/api/auth/login-url')
def login_url():
    try:
        extra=urlencode({'state':security.issue_state()})
        return {'url':kite.login_url()+'&'+urlencode({'redirect_params':extra})}
    except Exception as e:raise HTTPException(400,str(e))
@app.get('/auth/callback')
def callback(request_token:str=Query(default=''),state:str=Query(default=''),status:str=Query(default='')):
    # Public by necessity (Zerodha redirects the browser here); the one-time state ties it to a keyed request.
    if not security.consume_state(state):raise HTTPException(403,'Login session expired or not started from the dashboard')
    if not request_token or status not in ('','success'):raise HTTPException(400,'Zerodha login was not completed')
    try:kite.exchange(request_token)
    except Exception as e:
        log.warning('Kite session exchange failed: %s',type(e).__name__)
        raise HTTPException(400,'Zerodha session exchange failed. Check API key, secret and redirect URL.')
    return RedirectResponse(config.FRONTEND_URL+'?connected=1#setup')
@app.post('/api/instruments/sync')
def sync_instruments():
    try:return {'count':kite.sync_instruments()}
    except Exception as e:raise HTTPException(400,str(e))
@app.post('/api/universe/load')
def load_universe():
    p=DATA_DIR/'nse500.csv'
    if not p.exists():raise HTTPException(400,'Add data/nse500.csv with a symbol column first')
    d=pd.read_csv(p); cols={c.lower().strip():c for c in d.columns}
    if 'symbol' not in cols:raise HTTPException(400,'CSV requires symbol column')
    cc=cols.get('company') or cols.get('company name')
    sc=cols.get('sector') or cols.get('industry')
    rows=[(str(r[cols['symbol']]).strip().upper(),str(r[cc]).strip() if cc and pd.notna(r[cc]) else '',str(r[sc]).strip() if sc and pd.notna(r[sc]) else '') for _,r in d.iterrows() if pd.notna(r[cols['symbol']])]
    save_universe(rows);return {'loaded':len(rows),'mapped':sum(bool(token(s)) for s,_,_ in rows),'sector_mapped':sum(bool(sec) for _,_,sec in rows)}
class Hist(BaseModel):symbols:list[str]=Field(min_length=1,max_length=100);interval:str='day';days:int=Field(default=400,ge=5,le=2000)
@app.post('/api/history/sync')
def history(req:Hist):
    allowed={'minute','3minute','5minute','10minute','15minute','30minute','60minute','day'}
    if req.interval not in allowed:raise HTTPException(400,'Unsupported interval')
    out={}
    for i,s in enumerate(req.symbols):
        if i:time.sleep(0.4)  # Kite historical API allows about 3 requests per second
        try:out[s]={'candles':kite.history(s.upper(),req.interval,req.days)}
        except Exception as e:
            log.warning('History sync failed for %s: %s',s,e)
            out[s]={'error':str(e)}
    return out
@app.get('/api/universe')
def get_universe():return universe()
@app.get('/api/screener')
def get_screener(timeframe:str='day'):
    try:return screener(timeframe)
    except Exception as e:raise HTTPException(500,str(e))
@app.get('/api/daily-report')
def get_daily_report():
    try:
        report=daily_report(); report['signal_tracking']=record_and_update(report.get('intelligence',{}).get('opportunities',[])); save_report(report); return report
    except Exception as e:raise HTTPException(500,str(e))
@app.get('/api/reports/history')
def get_report_history():return report_history()
@app.get('/api/reports/{report_id}')
def get_report_by_id(report_id:int):
    report=get_report(report_id)
    if not report:raise HTTPException(404,'Report not found')
    return report
@app.get('/api/intelligence/opportunities')
def intelligence_opportunities(limit:int=Query(default=100,ge=1,le=200)):
    rows=screener('day')
    return {'market_regime':market_regime(rows),'opportunities':build_opportunities(rows,limit=limit)}
@app.get('/api/intelligence/summary')
def intelligence_summary(limit:int=Query(default=100,ge=1,le=200)):
    rows=screener('day')
    sector_rows=sector_strength(rows)
    return {
        'market_regime':market_regime(rows),
        'sector_strength':sector_rows,
        'sector_coverage':sum(bool(x.get('sector')) for x in universe()),
        'universe_count':len(universe()),
        'opportunities':build_opportunities(rows,limit=limit),
    }
@app.get('/api/intelligence/sectors')
def intelligence_sectors():
    rows=screener('day')
    return {'sectors':sector_strength(rows),'sector_coverage':sum(bool(x.get('sector')) for x in universe()),'universe_count':len(universe())}

def quote_is_fresh(quote:dict|None,max_age_seconds:int=120):
    if not quote or not quote.get('updated_at'):return False
    try:
        updated=pd.Timestamp(quote['updated_at'])
        now=pd.Timestamp.now(tz=updated.tz) if updated.tz is not None else pd.Timestamp.now()
        return 0 <= (now-updated).total_seconds() <= max_age_seconds
    except Exception:return False

@app.get('/api/intelligence/live')
def live_opportunities(interval:str='5minute',limit:int=Query(default=30,ge=1,le=100)):
    allowed={'minute','1minute','3minute','5minute','10minute','15minute','30minute','60minute'}
    if interval not in allowed:raise HTTPException(400,'Unsupported live analysis interval')
    all_symbols=[x['symbol'] for x in live_watchlist()]
    symbols=all_symbols[:limit]
    quotes={x['symbol']:x for x in kite.live_snapshots()}
    results=[]
    for symbol in symbols:
        mtf=live_timeframe_alignment(symbol)
        intraday_df=live_intraday_frame(symbol,interval)
        live_row=None
        if not intraday_df.empty:
            enriched=enrich(intraday_df)
            latest=enriched.iloc[-1].to_dict()
            live_row={k:clean_live_value(v) for k,v in latest.items()}
        quote=quotes.get(symbol,{})
        fresh=quote_is_fresh(quote)
        cached_intraday_trend=_trend(live_row) if live_row else 'unavailable'
        live_trend=cached_intraday_trend if fresh else 'unavailable'
        daily_trend=mtf['trends']['daily']; weekly_trend=mtf['trends']['weekly']
        base_confirmed=daily_trend=='bullish' and weekly_trend=='bullish'
        if base_confirmed and live_trend=='bullish':
            score=100; label='LIVE OPPORTUNITY — ALL TIMEFRAMES ALIGNED'
        elif base_confirmed and not fresh:
            score=80; label='DAILY + WEEKLY CONFIRMED — WAITING FOR FRESH LIVE TICKS'
        elif base_confirmed:
            score=55; label='DAILY + WEEKLY ALIGNED — INTRADAY NOT CONFIRMED'
        elif daily_trend=='bullish' or weekly_trend=='bullish':
            score=35; label='PARTIAL ALIGNMENT'
        else:
            score=0; label='NOT CONFIRMED'
        daily_row=mtf.get('daily') or {}
        levels=risk_reference(daily_row)
        results.append({
            'symbol':symbol,'current_price':quote.get('last_price') if fresh else (live_row or {}).get('close'),
            'live_trend':live_trend,'cached_intraday_trend':cached_intraday_trend,'daily_trend':daily_trend,'weekly_trend':weekly_trend,
            'live_interval':interval,'live_bars':len(intraday_df),'tick_fresh':fresh,'live_bias_score':score,
            'status':label,'rsi':(live_row or {}).get('rsi_14'),'ema_20':(live_row or {}).get('ema_20'),
            'daily_close':daily_row.get('close'),'entry_trigger':levels.get('entry_trigger'),
            'stop_loss':levels.get('stop_loss'),'target_price':levels.get('target_price'),
            'last_tick_at':quote.get('timestamp'),'updated_at':quote.get('updated_at'),
        })
    results.sort(key=lambda r:(r['live_bias_score'],r.get('last_tick_at') or ''),reverse=True)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'interval':interval,'watchlist_count':len(all_symbols),'analysed_watchlist':len(symbols),'omitted':max(0,len(all_symbols)-len(symbols)),'stream':kite.live_status(),'opportunities':results,
            'method':'Live watchlist overlay: cached daily and weekly structure plus tick-derived intraday candle trend. A tick must be fresher than 120 seconds before the live trend can confirm a live opportunity.'}
class BacktestRequest(BaseModel):
    symbol:str=Field(min_length=1,max_length=32)
    initial_capital:float=Field(default=100000,ge=1000,le=1000000000)
    risk_pct:float=Field(default=1,gt=0,le=5)
    slippage_bps:float=Field(default=10,ge=0,le=500)
    cost_bps_per_side:float=Field(default=11.5,ge=0,le=200)
    max_hold:int=Field(default=20,ge=1,le=100)
@app.post('/api/backtest')
def run_backtest(req:BacktestRequest):
    symbol=req.symbol.strip().upper()
    try:
        from backend.analysis import frame
        df=frame(symbol,'day')
        return {'symbol':symbol,**backtest_frame(df,req.initial_capital,req.risk_pct,req.slippage_bps,req.max_hold,req.cost_bps_per_side)}
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(500,str(exc))
@app.get('/api/signals/history')
def get_signal_history(limit:int=Query(default=100,ge=1,le=500)):
    return signal_history(limit)
@app.get('/api/signals/performance')
def get_signal_performance(): return performance()
@app.post('/api/signals/refresh')
def refresh_signals():
    try:
        report=daily_report(); result=record_and_update(report.get('intelligence',{}).get('opportunities',[])); save_report(report)
        return result
    except Exception as exc: raise HTTPException(500,str(exc))

@app.get('/api/market/breadth')
def market_breadth():
    rows=screener('day')
    valid=[r for r in rows if r.get('close') is not None]
    n=len(valid)
    above20=sum(1 for r in valid if r.get('ema_20') is not None and r['close']>r['ema_20'])
    above50=sum(1 for r in valid if r.get('ema_50') is not None and r['close']>r['ema_50'])
    above200=sum(1 for r in valid if r.get('sma_200') is not None and r['close']>r['sma_200'])
    bullish=sum(1 for r in valid if r.get('supertrend_direction')=='bullish')
    adv=sum(1 for r in valid if r.get('return_21d_pct') is not None and r['return_21d_pct']>0)
    dec=sum(1 for r in valid if r.get('return_21d_pct') is not None and r['return_21d_pct']<0)
    return {'as_of':datetime.now(timezone.utc).isoformat(),'analysed':n,'above_ema20':above20,'above_ema20_pct':round(100*above20/n,1) if n else None,'above_ema50':above50,'above_ema50_pct':round(100*above50/n,1) if n else None,'above_sma200':above200,'above_sma200_pct':round(100*above200/n,1) if n else None,'bullish_supertrend':bullish,'bullish_supertrend_pct':round(100*bullish/n,1) if n else None,'advancers_21d':adv,'decliners_21d':dec}
@app.get('/api/stock/{symbol}')
def get_stock(symbol:str,timeframe:str='day'):
    x=build_stock(symbol.upper(),timeframe)
    if not x:raise HTTPException(404,'No cached candles for this symbol/timeframe')
    return x
class Sub(BaseModel):symbols:list[str]=Field(min_length=1,max_length=3000)
@app.post('/api/live/subscribe')
def subscribe(req:Sub):
    symbols=list(dict.fromkeys(str(s).strip().upper() for s in req.symbols if str(s).strip()))
    missing=[s for s in symbols if not token(s)]
    mapped={s:token(s) for s in symbols if token(s)}
    if not mapped:raise HTTPException(400,'No mapped instrument tokens. Sync the NSE instrument master first.')
    try:
        kite.subscribe(list(mapped.values()))
        save_watchlist(list(mapped))
        return {'subscribed':len(mapped),'symbols':list(mapped),'unmapped':missing,'status':kite.live_status()}
    except Exception as e:raise HTTPException(400,str(e))
@app.post('/api/live/unsubscribe')
def unsubscribe(req:Sub):
    symbols=list(dict.fromkeys(str(s).strip().upper() for s in req.symbols if str(s).strip()))
    mapped=[token(s) for s in symbols if token(s)]
    count=kite.unsubscribe(mapped)
    remove_watchlist(symbols)
    return {'unsubscribed':count,'symbols':symbols,'status':kite.live_status()}
@app.post('/api/live/restore')
def restore_live_watchlist():
    kite.require()
    symbols=[x['symbol'] for x in live_watchlist()]
    mapped={s:token(s) for s in symbols if token(s)}
    missing=[s for s in symbols if s not in mapped]
    if not mapped:raise HTTPException(400,'No saved mapped symbols. Sync instruments and add symbols to the live watchlist first.')
    try:
        kite.subscribe(list(mapped.values()))
        return {'restored':len(mapped),'symbols':list(mapped),'unmapped':missing,'status':kite.live_status()}
    except Exception as exc:raise HTTPException(400,str(exc))
@app.post('/api/live/stop')
def stop_live():
    kite.stop_live()
    remove_watchlist([x['symbol'] for x in live_watchlist()])
    return {'stopped':True,'status':kite.live_status()}
@app.get('/api/live/status')
def live_status():return kite.live_status()
@app.get('/api/live/watchlist')
def get_live_watchlist():return live_watchlist()
@app.get('/api/live/snapshot')
def live_snapshot():return kite.live_snapshots()

def live_intraday_frame(symbol:str,interval:str):
    allowed={'minute','1minute','3minute','5minute','10minute','15minute','30minute','60minute'}
    if interval not in allowed:raise HTTPException(400,'Supported live intervals: 1minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute')
    canonical='minute' if interval=='1minute' else interval
    minute_rows=load_candles(symbol,'minute')
    current=kite.live_candles.current_candle(symbol)
    if current:
        minute_rows=[r for r in minute_rows if str(r.get('date'))!=str(current.get('date'))]+[current]
    minute_df=pd.DataFrame(minute_rows)
    if canonical=='minute':
        result=minute_df
    else:
        exact=pd.DataFrame(load_candles(symbol,canonical))
        if not minute_df.empty:
            minute_df['date']=pd.to_datetime(minute_df['date'],errors='coerce')
            minute_df=minute_df.dropna(subset=['date']).set_index('date').sort_index()
            n=int(canonical.replace('minute',''))
            aggregated=minute_df.resample(f'{n}min',origin='start_day',offset='9h15min',closed='left',label='left').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['open','high','low','close']).reset_index()
        else:
            aggregated=pd.DataFrame(columns=['date','open','high','low','close','volume'])
        result=pd.concat([exact,aggregated],ignore_index=True) if not exact.empty or not aggregated.empty else pd.DataFrame()
        if not result.empty:
            result['date']=pd.to_datetime(result['date'],errors='coerce')
            result=result.dropna(subset=['date']).sort_values('date').drop_duplicates('date',keep='last')
    if result.empty:return result
    result['date']=pd.to_datetime(result['date'],errors='coerce')
    return result.dropna(subset=['date']).sort_values('date').reset_index(drop=True)

def clean_live_value(value):
    if pd.isna(value):return None
    if hasattr(value,'isoformat'):return value.isoformat()
    if hasattr(value,'item'):return value.item()
    return value

def live_timeframe_alignment(symbol:str):
    """Use the current tick-derived daily candle for daily and weekly trend context."""
    current=kite.live_candles.current_daily_candle(symbol)
    if not current:
        return timeframe_alignment(symbol)
    daily_df=pd.DataFrame(load_candles(symbol,'day'))
    live_row=pd.DataFrame([current])
    live_row['date']=pd.to_datetime(live_row['date'],errors='coerce')
    if not daily_df.empty:
        daily_df['date']=pd.to_datetime(daily_df['date'],errors='coerce')
        live_date=live_row.iloc[0]['date'].date()
        daily_df=daily_df[daily_df['date'].dt.date!=live_date]
        daily_df=pd.concat([daily_df,live_row],ignore_index=True)
    else:
        daily_df=live_row
    daily_df=daily_df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    daily_enriched=enrich(daily_df)
    daily_latest={k:clean_live_value(v) for k,v in daily_enriched.iloc[-1].to_dict().items()}
    weekly_bars=(daily_df.set_index('date').sort_index()
                 .resample('W-FRI')
                 .agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
                 .dropna(subset=['open','high','low','close']).reset_index())
    weekly_enriched=enrich(weekly_bars)
    weekly_latest={k:clean_live_value(v) for k,v in weekly_enriched.iloc[-1].to_dict().items()} if not weekly_enriched.empty else None
    trends={'intraday':'unavailable','daily':_trend(daily_latest),'weekly':_trend(weekly_latest)}
    return {'trends':trends,'daily':daily_latest,'weekly':weekly_latest,'intraday':None,'intraday_interval':None,
            'daily_basis':'live tick OHLC + cached prior sessions','weekly_basis':'current live daily candle aggregated into current exchange week'}

@app.get('/api/live/candles/{symbol}')
def live_candles(symbol:str,interval:str='5minute',limit:int=Query(default=300,ge=20,le=1000)):
    d=live_intraday_frame(symbol.upper(),interval)
    if d.empty:return {'symbol':symbol.upper(),'interval':interval,'candles':[],'latest':None,'live_status':kite.live_status()}
    enriched=enrich(d).tail(limit).copy()
    records=[]
    for _,row in enriched.iterrows():records.append({k:clean_live_value(v) for k,v in row.to_dict().items()})
    return {'symbol':symbol.upper(),'interval':interval,'candles':records,'latest':records[-1] if records else None,'live_status':kite.live_status()}

@app.get('/api/live/analysis/{symbol}')
def live_analysis(symbol:str,interval:str='5minute'):
    d=live_intraday_frame(symbol.upper(),interval)
    if d.empty:return {'symbol':symbol.upper(),'interval':interval,'status':'waiting_for_ticks','message':'Subscribe this symbol and wait for live ticks to create minute candles.','live_status':kite.live_status()}
    e=enrich(d)
    last=e.iloc[-1].to_dict()
    latest={k:clean_live_value(v) for k,v in last.items()}
    enough=len(e)>=50
    quote=kite.live_quote(symbol)
    fresh=quote_is_fresh(quote)
    bullish=bool(enough and fresh and pd.notna(last.get('ema_9')) and pd.notna(last.get('ema_20')) and pd.notna(last.get('ema_50')) and last['close']>last['ema_9']>last['ema_20']>last['ema_50'] and (pd.isna(last.get('rsi_14')) or last['rsi_14']>=50))
    bearish=bool(enough and fresh and pd.notna(last.get('ema_9')) and pd.notna(last.get('ema_20')) and pd.notna(last.get('ema_50')) and last['close']<last['ema_9']<last['ema_20']<last['ema_50'] and (pd.isna(last.get('rsi_14')) or last['rsi_14']<=50))
    bias='BULLISH MOMENTUM' if bullish else 'BEARISH MOMENTUM' if bearish else 'CACHED INTRADAY / WAITING FOR FRESH TICKS' if enough else 'MIXED / BUILDING HISTORY'
    status='live' if fresh and enough else 'historical_only' if enough else 'building_history'
    age=round((pd.Timestamp.now(tz=pd.Timestamp(quote['updated_at']).tz)-pd.Timestamp(quote['updated_at'])).total_seconds(),1) if quote and quote.get('updated_at') else None
    return {'symbol':symbol.upper(),'interval':interval,'status':status,'tick_fresh':fresh,'tick_age_seconds':age,'bars':len(e),'bias':bias,'latest':latest,'live_status':kite.live_status(),'method':'Tick-derived OHLCV candles. A quote must be newer than 120 seconds before the live bias can be marked as live. Intraday EMA/RSI are descriptive filters, not execution signals.'}

@app.websocket('/ws/live')
async def live_stream(websocket:WebSocket):
    origin=websocket.headers.get('origin')
    if origin and origin.rstrip('/') not in config.ALLOWED_ORIGINS:
        await websocket.close(code=1008);return
    await websocket.accept()
    try:
        # Browsers cannot set headers on WebSockets, so the first message carries the key.
        try:hello=await asyncio.wait_for(websocket.receive_json(),timeout=5)
        except (asyncio.TimeoutError,ValueError,KeyError):hello=None
        if not isinstance(hello,dict) or not security.key_matches(hello.get('key')):
            await websocket.close(code=1008);return
        while True:
            await websocket.send_json({'status':kite.live_status(),'quotes':kite.live_snapshots(),'current_candles':kite.live_candles.current_candles()})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
@app.post('/api/jobs/full-sync')
def start_full_sync():
    try:
        kite.require()
        return {'job_id':jobs.start_full_sync(),'message':'Full-universe daily refresh queued'}
    except Exception as e:raise HTTPException(409,str(e))
@app.get('/api/jobs/status')
def job_status():return latest_job() or {'status':'not_started','message':'No market-data job has run yet'}
@app.get('/api/health')
def health():return {'ok':True,'scheduler_running':jobs.scheduler.running}

# Local development only: serves the same static files that GitHub Pages publishes. Must stay last.
app.mount('/',StaticFiles(directory=ROOT/'frontend',html=True),name='frontend')
