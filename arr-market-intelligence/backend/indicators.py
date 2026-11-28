import numpy as np
import pandas as pd

def enrich(df):
    d=df.copy().sort_values('date').reset_index(drop=True)
    for c in ['open','high','low','close','volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    cl,hi,lo=d.close,d.high,d.low
    for n in [9,20,21,50,100,200]: d[f'ema_{n}']=cl.ewm(span=n,adjust=False,min_periods=n).mean()
    for n in [20,50,200]: d[f'sma_{n}']=cl.rolling(n,min_periods=n).mean()
    delta=cl.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    ag=gain.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); al=loss.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    d['rsi_14']=100-100/(1+ag/al.replace(0,np.nan)); d.loc[(al==0)&(ag>0),'rsi_14']=100; d.loc[(al==0)&(ag==0),'rsi_14']=50
    pc=cl.shift(); tr=pd.concat([hi-lo,(hi-pc).abs(),(lo-pc).abs()],axis=1).max(axis=1)
    d['atr_14']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    # Supertrend(10,3). Plain numpy arrays: the same recursion via .iloc was far too slow for 500 symbols.
    atr10=tr.ewm(alpha=1/10,adjust=False,min_periods=10).mean().to_numpy()
    mid=((hi+lo)/2).to_numpy(); ub=mid+3*atr10; lb=mid-3*atr10; c_=cl.to_numpy(dtype=float)
    fub=ub.copy(); flb=lb.copy(); st=np.full(len(d),np.nan); direction=np.full(len(d),None,dtype=object)
    for i in range(len(d)):
        if np.isnan(atr10[i]): continue
        if i==0 or np.isnan(atr10[i-1]): st[i]=ub[i]; direction[i]='bearish'; continue
        fub[i]=ub[i] if ub[i]<fub[i-1] or c_[i-1]>fub[i-1] else fub[i-1]
        flb[i]=lb[i] if lb[i]>flb[i-1] or c_[i-1]<flb[i-1] else flb[i-1]
        if st[i-1]==fub[i-1]:
            if c_[i]<=fub[i]: st[i]=fub[i]; direction[i]='bearish'
            else: st[i]=flb[i]; direction[i]='bullish'
        else:
            if c_[i]>=flb[i]: st[i]=flb[i]; direction[i]='bullish'
            else: st[i]=fub[i]; direction[i]='bearish'
    d['supertrend']=st; d['supertrend_direction']=pd.Series(direction,index=d.index,dtype=object)
    e12=cl.ewm(span=12,adjust=False,min_periods=12).mean(); e26=cl.ewm(span=26,adjust=False,min_periods=26).mean()
    d['macd']=e12-e26; d['macd_signal']=d.macd.ewm(span=9,adjust=False,min_periods=9).mean(); d['macd_hist']=d.macd-d.macd_signal
    up=hi.diff(); down=-lo.diff(); pdm=pd.Series(np.where((up>down)&(up>0),up,0),index=d.index); mdm=pd.Series(np.where((down>up)&(down>0),down,0),index=d.index)
    pdi=100*pdm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/d.atr_14.replace(0,np.nan); mdi=100*mdm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/d.atr_14.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); d['plus_di']=pdi; d['minus_di']=mdi; d['adx_14']=dx.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    d['bb_mid']=cl.rolling(20,min_periods=20).mean(); sd=cl.rolling(20,min_periods=20).std(ddof=0); d['bb_upper']=d.bb_mid+2*sd; d['bb_lower']=d.bb_mid-2*sd
    d['volume_sma_20']=d.volume.rolling(20,min_periods=20).mean(); d['volume_ratio']=d.volume/d.volume_sma_20.replace(0,np.nan)
    # Prior-period breakout levels exclude the current candle to avoid look-ahead bias.
    d['prior_high_20']=hi.shift(1).rolling(20,min_periods=20).max()
    d['prior_high_55']=hi.shift(1).rolling(55,min_periods=55).max()
    d['high_52w']=hi.rolling(252,min_periods=100).max()
    d['return_21d_pct']=((cl/cl.shift(21))-1)*100
    d['return_63d_pct']=((cl/cl.shift(63))-1)*100
    d['rsi_change_5d']=d.rsi_14-d.rsi_14.shift(5)
    d['macd_hist_change_3d']=d.macd_hist-d.macd_hist.shift(3)
    d['breakout_20d']=(cl>d.prior_high_20)&(d.volume_ratio>=1.5)
    d['breakout_55d']=(cl>d.prior_high_55)&(d.volume_ratio>=1.5)
    # VWAP: resets each session on intraday bars; rolling 20 sessions on daily and higher bars.
    typical=(hi+lo+cl)/3; vol=d.volume.fillna(0); dates=pd.to_datetime(d.date,errors='coerce')
    if dates.diff().min()<pd.Timedelta(1,unit='D'):
        day=dates.dt.date; num=(typical*vol).groupby(day).cumsum(); den=vol.groupby(day).cumsum()
    else:
        num=(typical*vol).rolling(20,min_periods=20).sum(); den=vol.rolling(20,min_periods=20).sum()
    d['vwap']=num/den.replace(0,np.nan)
    # Pullback: uptrend, EMA20 touched within 3 bars, then a higher close back above EMA20.
    touched=(lo<=d.ema_20).astype(int).rolling(3,min_periods=1).max().astype(bool)
    d['pullback_ema20']=(d.ema_20>d.ema_50)&(cl>d.ema_50)&touched&(cl>d.ema_20)&(cl>cl.shift())
    return d

def comparative_rs(stock,bench,periods=(21,63,126,252)):
    s=pd.Series(stock).reset_index(drop=True); b=pd.Series(bench).reset_index(drop=True); out={}
    for p in periods:
        if len(s)>p and len(b)>p and s.iloc[-p-1] and b.iloc[-p-1]:
            out[f'rs_{p}d_pct']=round(((s.iloc[-1]/s.iloc[-p-1]-1)-(b.iloc[-1]/b.iloc[-p-1]-1))*100,2)
        else: out[f'rs_{p}d_pct']=None
    return out

def strength_score(r):
    tests=[(r.get('rs_rating') is not None and r.get('rs_rating',0)>=80,30),(r.get('close') is not None and r.get('ema_20') is not None and r['close']>r['ema_20'],10),(r.get('ema_20') is not None and r.get('ema_50') is not None and r['ema_20']>r['ema_50'],10),(r.get('supertrend_direction')=='bullish',15),(r.get('rsi_14') is not None and 55<=r['rsi_14']<=75,10),(r.get('volume_ratio') is not None and r['volume_ratio']>=1.5,10),(r.get('macd') is not None and r.get('macd_signal') is not None and r['macd']>r['macd_signal'],10),(r.get('adx_14') is not None and r['adx_14']>=20,5)]
    return sum(points for ok,points in tests if ok)

def aligned_closes(stock, bench):
    """Closes for the dates both series traded, so RS never compares different days."""
    def by_day(frame):
        return pd.Series(frame.close.to_numpy(dtype=float), index=frame.date.astype(str).str[:10])
    a, b = by_day(stock), by_day(bench)
    # A benchmark that lags the stock would silently give stale RS, so report nothing instead.
    if a.empty or b.empty or a.index.max() != b.index.max():
        return pd.Series(dtype=float), pd.Series(dtype=float)
    joined = pd.concat([a.rename('s'), b.rename('b')], axis=1, join='inner').sort_index()
    return joined.s, joined.b
