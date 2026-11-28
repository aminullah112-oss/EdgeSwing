import pandas as pd
from backend.indicators import enrich,comparative_rs,strength_score

def sample(n=260):
 c=pd.Series([100+i*.5 for i in range(n)])
 return pd.DataFrame({'date':pd.date_range('2024-01-01',periods=n),'open':c-.2,'high':c+1,'low':c-1,'close':c,'volume':[1000+i*2 for i in range(n)]})
def test_indicator_columns():
 d=enrich(sample())
 for k in ['ema_20','ema_50','ema_200','rsi_14','supertrend','atr_14','macd','adx_14','bb_upper','volume_ratio']:assert k in d
 assert d.rsi_14.iloc[-1]>90 and d.ema_20.iloc[-1]>d.ema_50.iloc[-1]
def test_rs_equal_performance():
 s=pd.Series([100+i for i in range(300)]); b=pd.Series([200+2*i for i in range(300)])
 assert abs(comparative_rs(s,b)['rs_21d_pct'])<.01
def test_score():
 r={'rs_rating':95,'close':120,'ema_20':110,'ema_50':100,'supertrend_direction':'bullish','rsi_14':65,'volume_ratio':2,'macd':2,'macd_signal':1,'adx_14':25}
 assert strength_score(r)==100

def test_breakout_features_are_prior_period_based():
    out = enrich(sample(300))
    assert 'prior_high_20' in out.columns
    assert 'prior_high_55' in out.columns
    assert 'breakout_20d' in out.columns
    assert out.prior_high_20.iloc[-1] <= out.high.iloc[-2]
