import copy
import threading
import pandas as pd
from datetime import datetime, timezone
from backend.db import load_candles,universe,data_version
from backend.indicators import enrich,comparative_rs,strength_score,aligned_closes
from backend.intelligence import assemble_intelligence

def frame(symbol,tf):
    x=pd.DataFrame(load_candles(symbol,tf))
    if x.empty:return x
    x['date']=pd.to_datetime(x.date,errors='coerce'); return x.dropna(subset=['date'])
def resample(x,rule):
    if x.empty:return x
    return x.set_index('date').sort_index().resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna().reset_index()
def build_stock(symbol,tf='day'):
    if tf in ('week','weekly'): x=resample(frame(symbol,'day'),'W-FRI')
    elif tf in ('month','monthly'): x=resample(frame(symbol,'day'),'ME')
    else:x=frame(symbol,tf)
    if x.empty:return None
    d=enrich(x); last=d.iloc[-1].to_dict(); clean={}
    for k,v in last.items():
        if pd.isna(v):clean[k]=None
        elif hasattr(v,'isoformat'):clean[k]=v.isoformat()
        elif hasattr(v,'item'):clean[k]=v.item()
        else:clean[k]=v
    candles=d.tail(500).copy(); candles['date']=candles.date.astype(str); candles=candles.astype(object).where(pd.notna(candles),None)
    return {'symbol':symbol,'timeframe':tf,'latest':clean,'candles':candles.to_dict('records')}
_cache={}
_cache_lock=threading.Lock()
def screener(tf='day'):
    """Cached until candles or the universe change; one computation at a time."""
    with _cache_lock:
        version=data_version(); hit=_cache.get(tf)
        if not hit or hit[0]!=version:
            hit=_cache[tf]=(version,_screen(tf))
        return copy.deepcopy(hit[1])
def _screen(tf):
    rows=[]; b50=frame('NIFTY 50','day'); b500=frame('NIFTY 500','day')
    for item in universe():
        s=item['symbol']; stock=build_stock(s,tf); daily=frame(s,'day')
        if not stock:continue
        r=stock['latest']; r['symbol']=s; r['sector']=item.get('sector') or 'Unclassified'
        if not daily.empty:
            if not b50.empty:r.update({'n50_'+k:v for k,v in comparative_rs(*aligned_closes(daily,b50)).items()})
            if not b500.empty:r.update({'n500_'+k:v for k,v in comparative_rs(*aligned_closes(daily,b500)).items()})
        rows.append(r)
    # Blend cross-sectional percentile ranks versus both benchmarks when both are available.
    a=pd.Series([r.get('n50_rs_126d_pct') for r in rows],dtype='float64')
    b=pd.Series([r.get('n500_rs_126d_pct') for r in rows],dtype='float64')
    ra=a.rank(pct=True)*100; rb=b.rank(pct=True)*100
    for i,(r,rank50,rank500) in enumerate(zip(rows,ra,rb)):
        available=[v for v in (rank50,rank500) if pd.notna(v)]
        r['rs_rating']=round(float(sum(available)/len(available)),1) if available else None
        r['rs_rating_nifty50']=round(float(rank50),1) if pd.notna(rank50) else None
        r['rs_rating_nifty500']=round(float(rank500),1) if pd.notna(rank500) else None
        r['strength_score']=strength_score(r)
        r['signal']='STRONG' if r['strength_score']>=80 else 'WATCH' if r['strength_score']>=60 else 'NEUTRAL' if r['strength_score']>=40 else 'WEAK'
    return sorted(rows,key=lambda r:(r.get('strength_score',0),r.get('rs_rating') or 0),reverse=True)
def daily_report():
    """On-demand end-of-day ranking from the latest cached daily candles."""
    rows=screener('day')
    def top(key, predicate=lambda r: True, reverse=True, limit=20):
        selected=[r for r in rows if predicate(r) and r.get(key) is not None]
        return sorted(selected,key=lambda r:r.get(key) if r.get(key) is not None else float('-inf'),reverse=reverse)[:limit]
    rs=top('rs_rating')
    momentum=top('strength_score',lambda r: r.get('rsi_14') is not None and 55<=r['rsi_14']<=75 and r.get('close') is not None and r.get('ema_20') is not None and r['close']>r['ema_20'] and r.get('macd') is not None and r.get('macd_signal') is not None and r['macd']>r['macd_signal'])
    supertrend=top('rs_rating',lambda r:r.get('supertrend_direction')=='bullish')
    volume=top('volume_ratio',lambda r:r.get('volume_ratio') is not None and r['volume_ratio']>=1.5 and (r.get('breakout_20d') is True or r.get('breakout_55d') is True))
    combined=top('strength_score',lambda r:r.get('strength_score',0)>=60)
    pullbacks=top('rs_rating',lambda r:r.get('pullback_ema20') is True)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'universe_loaded':len(universe()),'analysed':len(rows),'benchmark_data':{'nifty50':not frame('NIFTY 50','day').empty,'nifty500':not frame('NIFTY 500','day').empty},'method':'Latest cached daily candle; RS rating is the average cross-sectional percentile of 6-month relative performance vs available Nifty 50 and Nifty 500 benchmark series. Lists are screens, not trade recommendations.','categories':{'relative_strength_leaders':rs,'momentum_leaders':momentum,'supertrend_bullish':supertrend,'volume_breakouts':volume,'ema20_pullbacks':pullbacks,'combined_bullish_confirmations':combined},'intelligence':assemble_intelligence(rows)}
