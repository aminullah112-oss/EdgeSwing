"""Opportunity intelligence: timeframe alignment, sector strength and market regime.

All outputs are descriptive research analytics. Risk levels are mechanical ATR
references, not personalised trading instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from backend.db import load_candles, universe
from backend.indicators import enrich

INTRADAY_PREFERENCE = ('15minute', '5minute', '60minute', '30minute', '10minute', '3minute', 'minute')


def candle_frame(symbol: str, timeframe: str = 'day') -> pd.DataFrame:
    rows = load_candles(symbol, timeframe)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    return df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)


def resample_daily(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    return (df.set_index('date').sort_index()
            .resample(rule)
            .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            .dropna(subset=['open', 'high', 'low', 'close']).reset_index())


def _last_enriched(symbol: str, timeframe: str) -> dict | None:
    df = candle_frame(symbol, timeframe)
    if df.empty:
        return None
    row = enrich(df).iloc[-1].to_dict()
    row['date'] = pd.Timestamp(row['date']).isoformat()
    return {k: (None if pd.isna(v) else (v.item() if hasattr(v, 'item') else v)) for k, v in row.items()}


def _trend(row: dict | None) -> str:
    if not row or row.get('close') is None or row.get('ema_20') is None:
        return 'unavailable'
    close, ema20 = float(row['close']), float(row['ema_20'])
    ema50 = row.get('ema_50')
    st = row.get('supertrend_direction')
    if close > ema20 and st == 'bullish' and (ema50 is None or ema20 >= float(ema50)):
        return 'bullish'
    if close < ema20 and st == 'bearish' and (ema50 is None or ema20 <= float(ema50)):
        return 'bearish'
    return 'neutral'


def timeframe_alignment(symbol: str) -> dict:
    daily = _last_enriched(symbol, 'day')
    weekly_bars = resample_daily(candle_frame(symbol, 'day'), 'W-FRI')
    weekly = enrich(weekly_bars).iloc[-1].to_dict() if not weekly_bars.empty else None
    if weekly:
        weekly['date'] = pd.Timestamp(weekly['date']).isoformat()
        weekly = {k: (None if pd.isna(v) else (v.item() if hasattr(v, 'item') else v)) for k, v in weekly.items()}
    intraday = None
    intraday_interval = None
    for interval in INTRADAY_PREFERENCE:
        intraday = _last_enriched(symbol, interval)
        if intraday:
            intraday_interval = interval
            break
    trends = {
        'intraday': _trend(intraday),
        'daily': _trend(daily),
        'weekly': _trend(weekly),
    }
    weights = {'intraday': 25, 'daily': 40, 'weekly': 35}
    available = [(name, trend) for name, trend in trends.items() if trend != 'unavailable']
    available_weight = sum(weights[name] for name, _ in available)
    score = (sum(weights[name] for name, trend in available if trend == 'bullish') / available_weight * 100) if available_weight else None
    daily_weekly_bull = trends['daily'] == 'bullish' and trends['weekly'] == 'bullish'
    if daily_weekly_bull and trends['intraday'] == 'bullish':
        label = 'FULL CONFIRMATION'
    elif daily_weekly_bull and trends['intraday'] == 'unavailable':
        label = 'DAILY + WEEKLY CONFIRMED'
    elif trends['daily'] == 'bullish' and trends['weekly'] == 'bearish':
        label = 'TIMEFRAME CONFLICT'
    elif any(t == 'bullish' for _, t in available):
        label = 'PARTIAL / MIXED'
    else:
        label = 'NOT CONFIRMED'
    return {
        'trends': trends,
        'intraday_interval': intraday_interval,
        'confirmation': label,
        'alignment_score': round(score, 1) if score is not None else None,
        'coverage_pct': round(available_weight, 1),
        'daily': daily,
        'weekly': weekly,
        'intraday': intraday,
    }


def market_regime(rows: list[dict] | None = None) -> dict:
    rows = rows or []
    benchmarks = {}
    for symbol, label in [('NIFTY 50', 'nifty50'), ('NIFTY 500', 'nifty500')]:
        latest = _last_enriched(symbol, 'day')
        if latest:
            benchmarks[label] = {
                'close': latest.get('close'),
                'ema_50': latest.get('ema_50'),
                'ema_200': latest.get('ema_200'),
                'rsi_14': latest.get('rsi_14'),
                'adx_14': latest.get('adx_14'),
                'trend': _trend(latest),
                'date': latest.get('date'),
            }
    above50 = [r for r in rows if r.get('close') is not None and r.get('ema_50') is not None]
    breadth50 = (sum(float(r['close']) > float(r['ema_50']) for r in above50) / len(above50) * 100) if above50 else None
    n50 = benchmarks.get('nifty50') or {}
    n500 = benchmarks.get('nifty500') or {}
    n50_valid = all(n50.get(k) is not None for k in ('close', 'ema_50', 'ema_200'))
    n500_valid = all(n500.get(k) is not None for k in ('close', 'ema_50', 'ema_200'))
    n50_bull = n50_valid and n50['close'] > n50['ema_50'] > n50['ema_200']
    n50_bear = n50_valid and n50['close'] < n50['ema_50'] < n50['ema_200']
    n500_bull = n500_valid and n500['close'] > n500['ema_50'] > n500['ema_200']
    n500_bear = n500_valid and n500['close'] < n500['ema_50'] < n500['ema_200']
    if n50_bull and (not n500_valid or n500_bull) and breadth50 is not None and breadth50 >= 60:
        regime, score = 'BULLISH', 80 + min(20, (breadth50 - 60) * 0.5)
    elif n50_bear and (not n500_valid or n500_bear) and breadth50 is not None and breadth50 <= 40:
        regime, score = 'BEARISH', 20 - min(20, (40 - breadth50) * 0.5)
    elif not n50_valid:
        regime, score = 'UNKNOWN', None
    else:
        regime, score = 'MIXED / TRANSITIONAL', 50
    if n50_bull and n500_bull:
        benchmark_alignment = 'BOTH BULLISH'
    elif n50_bear and n500_bear:
        benchmark_alignment = 'BOTH BEARISH'
    elif n50_valid and n500_valid:
        benchmark_alignment = 'BENCHMARK CONFLICT'
    elif n50_valid:
        benchmark_alignment = 'NIFTY 50 AVAILABLE; NIFTY 500 UNAVAILABLE'
    else:
        benchmark_alignment = 'INSUFFICIENT BENCHMARK DATA'
    return {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'regime': regime,
        'regime_score': round(score, 1) if score is not None else None,
        'benchmark_alignment': benchmark_alignment,
        'breadth_above_ema50_pct': round(breadth50, 1) if breadth50 is not None else None,
        'breadth_sample': len(above50),
        'benchmarks': benchmarks,
        'interpretation': 'Regime is a rule-based context filter using Nifty 50 trend structure and NSE 500 breadth; it is not a forecast.',
    }


def sector_strength(rows: list[dict]) -> list[dict]:
    metadata = {x['symbol']: (x.get('sector') or '').strip() for x in universe()}
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        sector = metadata.get(row.get('symbol'), '')
        if not sector:
            continue
        buckets.setdefault(sector, []).append(row)
    result = []
    for sector, members in buckets.items():
        def avg(key):
            vals = [float(r[key]) for r in members if r.get(key) is not None and math.isfinite(float(r[key]))]
            return round(sum(vals) / len(vals), 2) if vals else None
        eligible = [r for r in members if r.get('close') is not None and r.get('ema_20') is not None]
        result.append({
            'sector': sector,
            'constituents_analysed': len(members),
            'avg_return_21d_pct': avg('return_21d_pct'),
            'avg_return_63d_pct': avg('return_63d_pct'),
            'avg_rs_rating': avg('rs_rating'),
            'above_ema20_pct': round(sum(float(r['close']) > float(r['ema_20']) for r in eligible) / len(eligible) * 100, 1) if eligible else None,
            'bullish_pct': round(sum(r.get('supertrend_direction') == 'bullish' for r in members) / len(members) * 100, 1) if members else None,
        })
    return sorted(result, key=lambda r: (r.get('avg_return_63d_pct') if r.get('avg_return_63d_pct') is not None else -999), reverse=True)


def risk_reference(row: dict) -> dict:
    close = row.get('close')
    atr = row.get('atr_14')
    if close is None or atr is None or float(close) <= 0 or float(atr) <= 0:
        return {'entry_trigger': None, 'stop_loss': None, 'target_price': None, 'risk_pct': None, 'reward_risk': None}
    close, atr = float(close), float(atr)
    prior_high = row.get('prior_high_20')
    trigger = max(close, float(prior_high) + 0.1 * atr) if prior_high is not None else close
    stop = trigger - 1.5 * atr
    target = trigger + 3.0 * atr
    risk_pct = (trigger - stop) / trigger * 100 if trigger else None
    return {
        'entry_trigger': round(trigger, 2),
        'stop_loss': round(stop, 2),
        'target_price': round(target, 2),
        'risk_pct': round(risk_pct, 2) if risk_pct is not None else None,
        'reward_risk': 2.0,
        'level_method': 'Entry reference is max(last close, 20-session prior high + 0.1 ATR); stop = 1.5 ATR below trigger; target = 3 ATR above trigger.',
    }


def build_opportunities(rows: list[dict], limit: int = 100) -> list[dict]:
    candidates = [r for r in rows if (r.get('strength_score') or 0) >= 55 and r.get('close') is not None]
    candidates.sort(key=lambda r: (r.get('strength_score') or 0, r.get('rs_rating') or 0), reverse=True)
    output = []
    sectors = {x['symbol']: (x.get('sector') or 'Unclassified') for x in universe()}
    for row in candidates[:max(0, limit)]:
        mtf = timeframe_alignment(row['symbol'])
        trends = mtf['trends']
        if not (trends['daily'] == 'bullish' and trends['weekly'] == 'bullish'):
            continue
        levels = risk_reference(row)
        if levels['entry_trigger'] is None:
            continue
        item = dict(row)
        item.update(levels)
        item['sector'] = sectors.get(row['symbol'], 'Unclassified')
        item['timeframes'] = {k: mtf['trends'][k] for k in ('intraday', 'daily', 'weekly')}
        item['intraday_interval'] = mtf['intraday_interval']
        item['timeframe_confirmation'] = mtf['confirmation']
        item['alignment_score'] = mtf['alignment_score']
        item['timeframe_coverage_pct'] = mtf['coverage_pct']
        # Confidence combines the platform's technical score with multi-timeframe agreement.
        item['confidence'] = round((0.55 * float(row.get('strength_score') or 0)) + (0.45 * float(mtf.get('alignment_score') or 0)), 1)
        item['signal_date'] = str(row.get('date') or '')[:10]
        item['risk_disclaimer'] = 'Mechanical ATR reference levels for research only; not a personalised recommendation.'
        output.append(item)
    return sorted(output, key=lambda r: (r.get('confidence') or 0, r.get('rs_rating') or 0), reverse=True)


def assemble_intelligence(rows: list[dict]) -> dict:
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'market_regime': market_regime(rows),
        'sector_strength': sector_strength(rows),
        'opportunities': build_opportunities(rows),
        'methodology': {
            'multi_timeframe': 'Daily and weekly confirmation are required. Cached intraday data (15m preferred, then 5m/60m/30m/10m/3m/1m) adds confirmation but is not mandatory.',
            'confidence': '55% of ARR technical strength score + 45% multi-timeframe alignment score, normalised over available timeframe data.',
            'sector_data': 'Sector analytics require a sector column in data/nse500.csv. Constituents without sector classification are excluded from sector ranks.',
            'risk_levels': 'ATR-based reference levels, not a guarantee of execution or a recommendation.',
        },
    }
