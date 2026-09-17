import math
import pandas as pd


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _sma(s, n):
    return s.rolling(n).mean()


def _weekly_from_daily(df):
    d = df.copy()
    if 'time_key' in d.columns:
        idx = pd.to_datetime(d['time_key'], errors='coerce')
    else:
        idx = pd.date_range(end=pd.Timestamp.today(), periods=len(d), freq='B')
    d = d.assign(_dt=idx).dropna(subset=['_dt']).set_index('_dt')
    return d.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()


def _four_hour_from_60m(df):
    d = df.copy()
    if 'time_key' in d.columns:
        idx = pd.to_datetime(d['time_key'], errors='coerce')
        d = d.assign(_dt=idx).dropna(subset=['_dt']).set_index('_dt')
        # Group each trading day into sequential four-hour blocks. This avoids overnight mixing.
        parts = []
        for _, day in d.groupby(d.index.date):
            if day.empty:
                continue
            grp = pd.Series(range(len(day)), index=day.index) // 4
            x = day.groupby(grp).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
            parts.append(x)
        if parts:
            return pd.concat(parts, ignore_index=True)
    # Fallback if timestamps are unavailable.
    grp = pd.Series(range(len(d)), index=d.index) // 4
    return d.groupby(grp).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()


def _trend(df):
    if df is None or len(df) < 55:
        return False, False
    c = df['close']
    e8, e21, e50 = _ema(c, 8), _ema(c, 21), _ema(c, 50)
    bull = bool(e8.iloc[-1] > e21.iloc[-1] > e50.iloc[-1] and c.iloc[-1] > e8.iloc[-1])
    bear = bool(e8.iloc[-1] < e21.iloc[-1] < e50.iloc[-1] and c.iloc[-1] < e8.iloc[-1])
    return bull, bear


def _rs_state(stock_close, benchmark_close):
    n = min(len(stock_close), len(benchmark_close))
    if n < 55:
        return False, False
    rs = stock_close.iloc[-n:].reset_index(drop=True) / benchmark_close.iloc[-n:].reset_index(drop=True).replace(0, pd.NA)
    rs20, rs50 = _sma(rs, 20), _sma(rs, 50)
    bull = bool(rs.iloc[-1] > rs20.iloc[-1] > rs50.iloc[-1])
    bear = bool(rs.iloc[-1] < rs20.iloc[-1] < rs50.iloc[-1])
    return bull, bear


def _last_two_pivots(series, kind='high', left=10, right=10):
    vals = series.reset_index(drop=True)
    pts = []
    for i in range(left, len(vals) - right):
        window = vals.iloc[i-left:i+right+1]
        v = vals.iloc[i]
        if (kind == 'high' and v >= window.max()) or (kind == 'low' and v <= window.min()):
            pts.append((i, float(v)))
    return pts[-2:] if len(pts) >= 2 else []


def analyze_daily_swing(dd, d60, benchmarks, side):
    """Python port of JR Swing Leader Clean PRO analytical logic.

    Executes setup/confirmation logic on Daily bars, derives Weekly from Daily and 4H from 60m,
    and evaluates relative strength against SPY/QQQ/IWM daily histories.
    """
    if dd is None or len(dd) < 120 or d60 is None or len(d60) < 80:
        return {'ok': False, 'confirmed': False, 'score': 0, 'text': 'Insufficient Daily/1H history'}

    d = dd.copy().reset_index(drop=True)
    w = _weekly_from_daily(d)
    h4 = _four_hour_from_60m(d60)
    c, h, l, o, v = d.close, d.high, d.low, d.open, d.volume
    e8, e21, e50 = _ema(c, 8), _ema(c, 21), _ema(c, 50)
    vol_avg = _sma(v, 20)
    rvol = float(v.iloc[-1] / max(float(vol_avg.iloc[-1]), 1.0))

    weekly_bull, weekly_bear = _trend(w)
    daily_bull, daily_bear = _trend(d)
    h4_bull, h4_bear = _trend(h4)

    bar_range = max(float(h.iloc[-1] - l.iloc[-1]), 1e-9)
    strong_close = bool(c.iloc[-1] >= h.iloc[-1] - bar_range * 0.30)
    weak_close = bool(c.iloc[-1] <= l.iloc[-1] + bar_range * 0.30)
    accumulation = bool(c.iloc[-1] > o.iloc[-1] and rvol >= 1.5 and strong_close)
    distribution = bool(c.iloc[-1] < o.iloc[-1] and rvol >= 1.5 and weak_close)
    bull_dry = bool(v.iloc[-1] < vol_avg.iloc[-1] * 0.50 and c.iloc[-1] > e21.iloc[-1])
    bear_dry = bool(v.iloc[-1] < vol_avg.iloc[-1] * 0.50 and c.iloc[-1] < e21.iloc[-1])

    rs = {}
    for sym in ('SPY', 'QQQ', 'IWM'):
        b = benchmarks.get(sym)
        rs[sym] = _rs_state(c, b.close) if b is not None and len(b) >= 55 else (False, False)
    rs_bull = all(rs[s][0] for s in rs)
    rs_bear = all(rs[s][1] for s in rs)

    range_now = float(h.tail(20).max() - l.tail(20).min())
    range_past = float(h.iloc[-40:-20].max() - l.iloc[-40:-20].min())
    compression = bool(range_past > 0 and range_now < range_past * 0.75 and v.iloc[-1] < vol_avg.iloc[-1])
    higher_lows = bool(l.tail(10).min() > l.iloc[-20:-10].min())
    lower_highs = bool(h.tail(10).max() < h.iloc[-20:-10].max())
    bull_vcp = bool(compression and higher_lows and bull_dry)
    bear_vcp = bool(compression and lower_highs and bear_dry)

    ph = _last_two_pivots(h, 'high')
    pl = _last_two_pivots(l, 'low')
    upper_price = None
    lower_price = None
    x = len(d) - 1
    if len(ph) == 2 and ph[1][0] != ph[0][0]:
        upper_price = ph[0][1] + (ph[1][1]-ph[0][1]) * (x-ph[0][0]) / (ph[1][0]-ph[0][0])
    if len(pl) == 2 and pl[1][0] != pl[0][0]:
        lower_price = pl[0][1] + (pl[1][1]-pl[0][1]) * (x-pl[0][0]) / (pl[1][0]-pl[0][0])
    wedge_pop = bool(upper_price is not None and c.iloc[-1] > upper_price and rvol >= 1.5)
    wedge_break = bool(lower_price is not None and c.iloc[-1] < lower_price and rvol >= 1.5)

    breakout_level = float(h.iloc[-21:-1].max())
    breakdown_level = float(l.iloc[-21:-1].min())
    breakout = bool(c.iloc[-1] > breakout_level and rvol >= 1.5)
    breakdown = bool(c.iloc[-1] < breakdown_level and rvol >= 1.5)

    prior_low = float(l.iloc[-21:-1].min())
    prior_high = float(h.iloc[-21:-1].max())
    prev_prior_low = float(l.iloc[-22:-2].min())
    prev_prior_high = float(h.iloc[-22:-2].max())
    undercut_prev = bool(l.iloc[-2] < prev_prior_low)
    upthrust_prev = bool(h.iloc[-2] > prev_prior_high)
    undercut_rally = bool(undercut_prev and c.iloc[-1] > prior_low)
    upthrust_fail = bool(upthrust_prev and c.iloc[-1] < prior_high)

    cup_high, cup_low = float(h.tail(80).max()), float(l.tail(80).min())
    cup_range = cup_high - cup_low
    cup_depth = (cup_range / cup_high * 100.0) if cup_high > 0 else 0.0
    near_cup_high = bool(c.iloc[-1] >= cup_low + cup_range * 0.85)
    near_cup_low = bool(c.iloc[-1] <= cup_low + cup_range * 0.15)
    handle_tight = bool(float(h.tail(15).max() - l.tail(15).min()) < cup_range * 0.35)
    handle_dry = bool(v.iloc[-1] < vol_avg.iloc[-1])
    cup_handle = bool(15 <= cup_depth <= 55 and near_cup_high and handle_tight and handle_dry and c.iloc[-1] > e50.iloc[-1])
    inverse_cup = bool(15 <= cup_depth <= 55 and near_cup_low and handle_tight and handle_dry and c.iloc[-1] < e50.iloc[-1])

    move_low, move_high = float(l.tail(40).min()), float(h.tail(40).max())
    prior_move = ((move_high-move_low)/move_low*100) if move_low > 0 else 0
    prior_drop = ((move_high-move_low)/move_high*100) if move_high > 0 else 0
    flag_range = float(h.tail(10).max() - l.tail(10).min())
    high_tight_flag = bool(prior_move >= 50 and flag_range < c.iloc[-1]*0.15 and v.iloc[-1] < vol_avg.iloc[-1] and c.iloc[-1] > e21.iloc[-1])
    high_tight_bear = bool(prior_drop >= 50 and flag_range < c.iloc[-1]*0.15 and v.iloc[-1] < vol_avg.iloc[-1] and c.iloc[-1] < e21.iloc[-1])

    flat_high, flat_low = float(h.tail(30).max()), float(l.tail(30).min())
    flat_range = ((flat_high-flat_low)/flat_low*100) if flat_low > 0 else 999
    bull_flat = bool(flat_range <= 15 and c.iloc[-1] > e50.iloc[-1] and v.iloc[-1] < vol_avg.iloc[-1])
    bear_flat = bool(flat_range <= 15 and c.iloc[-1] < e50.iloc[-1] and v.iloc[-1] < vol_avg.iloc[-1])

    resistance_now, resistance_past = float(h.tail(20).max()), float(h.iloc[-30:-10].max())
    support_now, support_past = float(l.tail(20).min()), float(l.iloc[-30:-10].min())
    flat_res = abs(resistance_now-resistance_past)/max(float(c.iloc[-1]),1e-9) < 0.03
    flat_sup = abs(support_now-support_past)/max(float(c.iloc[-1]),1e-9) < 0.03
    asc_triangle = bool(flat_res and higher_lows and c.iloc[-1] > e21.iloc[-1])
    desc_triangle = bool(flat_sup and lower_highs and c.iloc[-1] < e21.iloc[-1])

    strong_move = bool(c.iloc[-11] > 0 and (c.iloc[-1]-c.iloc[-11])/c.iloc[-11]*100 > 12)
    strong_drop = bool(c.iloc[-11] > 0 and (c.iloc[-11]-c.iloc[-1])/c.iloc[-11]*100 > 12)
    orderly_pullback = bool(c.iloc[-1] < h.tail(10).max() and c.iloc[-1] > e21.iloc[-1] and v.iloc[-1] < vol_avg.iloc[-1])
    orderly_bounce = bool(c.iloc[-1] > l.tail(10).min() and c.iloc[-1] < e21.iloc[-1] and v.iloc[-1] < vol_avg.iloc[-1])
    bull_flag = bool(strong_move and orderly_pullback and daily_bull)
    bear_flag = bool(strong_drop and orderly_bounce and daily_bear)

    if len(w) >= 12:
        weekly_range = (float(w.high.tail(12).max()-w.low.tail(12).min()) / max(float(w.low.tail(12).min()),1e-9) * 100)
    else:
        weekly_range = 999
    weekly_bull_base = bool(weekly_range <= 35 and weekly_bull)
    weekly_bear_base = bool(weekly_range <= 35 and weekly_bear)
    ema_pullback = bool(daily_bull and l.iloc[-1] <= e21.iloc[-1] and c.iloc[-1] > e21.iloc[-1] and v.iloc[-1] <= vol_avg.iloc[-1])
    ema_rejection = bool(daily_bear and h.iloc[-1] >= e21.iloc[-1] and c.iloc[-1] < e21.iloc[-1] and v.iloc[-1] <= vol_avg.iloc[-1])

    down_vol = d.assign(_dv=v.where(c < o, 0))._dv.iloc[-11:-1].max()
    up_vol = d.assign(_uv=v.where(c > o, 0))._uv.iloc[-11:-1].max()
    pocket_pivot = bool(c.iloc[-1] > o.iloc[-1] and v.iloc[-1] > down_vol and c.iloc[-1] > e8.iloc[-1] and c.iloc[-1] > e21.iloc[-1])
    pocket_sell = bool(c.iloc[-1] < o.iloc[-1] and v.iloc[-1] > up_vol and c.iloc[-1] < e8.iloc[-1] and c.iloc[-1] < e21.iloc[-1])

    bull_setup = any((bull_vcp,cup_handle,high_tight_flag,bull_flat,asc_triangle,bull_flag,weekly_bull_base,ema_pullback,undercut_rally))
    bear_setup = any((bear_vcp,inverse_cup,high_tight_bear,bear_flat,desc_triangle,bear_flag,weekly_bear_base,ema_rejection,upthrust_fail))
    bull_confirmation = bool(wedge_pop or breakout or pocket_pivot or (accumulation and bull_setup))
    bear_confirmation = bool(wedge_break or breakdown or pocket_sell or (distribution and bear_setup))
    clean_long = bool(weekly_bull and daily_bull and h4_bull and rs_bull and bull_confirmation and rvol >= 1.5)
    clean_short = bool(weekly_bear and daily_bear and h4_bear and rs_bear and bear_confirmation and rvol >= 1.5)

    bull_items = [(weekly_bull,20,'Weekly bull'),(daily_bull,20,'Daily bull'),(h4_bull,10,'4H bull'),(rs_bull,15,'RS leader vs SPY/QQQ/IWM'),(accumulation,10,'Accumulation'),(compression,5,'Compression'),(bull_vcp,5,'VCP'),(wedge_pop,5,'Wedge Pop'),(breakout,5,'Breakout'),(cup_handle,5,'Cup & Handle'),(high_tight_flag,5,'High Tight Flag'),(pocket_pivot,5,'Pocket Pivot'),(bull_flat,3,'Flat Base'),(asc_triangle,3,'Ascending Triangle'),(bull_flag,3,'Bull Flag'),(weekly_bull_base,3,'Weekly Base'),(ema_pullback,3,'EMA Pullback'),(undercut_rally,3,'Undercut & Rally')]
    bear_items = [(weekly_bear,20,'Weekly bear'),(daily_bear,20,'Daily bear'),(h4_bear,10,'4H bear'),(rs_bear,15,'RS laggard vs SPY/QQQ/IWM'),(distribution,10,'Distribution'),(compression,5,'Compression'),(bear_vcp,5,'Bear VCP'),(wedge_break,5,'Wedge Breakdown'),(breakdown,5,'Breakdown'),(inverse_cup,5,'Inverse Cup & Handle'),(high_tight_bear,5,'High Tight Bear Flag'),(pocket_sell,5,'Pocket Pivot Sell'),(bear_flat,3,'Bear Flat Base'),(desc_triangle,3,'Descending Triangle'),(bear_flag,3,'Bear Flag'),(weekly_bear_base,3,'Weekly Bear Base'),(ema_rejection,3,'EMA Rejection'),(upthrust_fail,3,'Upthrust & Fail')]
    bull_score = min(sum(weight for cond,weight,_ in bull_items if cond),100)
    bear_score = min(sum(weight for cond,weight,_ in bear_items if cond),100)
    edge = abs(bull_score-bear_score)
    bull_has_edge = bull_score > bear_score and bull_score >= 60 and edge >= 15
    bear_has_edge = bear_score > bull_score and bear_score >= 60 and edge >= 15

    wanted_bull = side == 'CALL'
    raw_score = bull_score if wanted_bull else bear_score
    confirmed = clean_long if wanted_bull else clean_short
    has_edge = bull_has_edge if wanted_bull else bear_has_edge
    items = bull_items if wanted_bull else bear_items
    reasons = [name for cond,_,name in items if cond]
    grade = 'A+' if raw_score >= 90 else 'A' if raw_score >= 80 else 'B' if raw_score >= 70 else 'C' if raw_score >= 60 else 'WAIT'
    structure_order = [
        ('Wedge Pop' if wanted_bull else 'Wedge Breakdown', wedge_pop if wanted_bull else wedge_break),
        ('Breakout' if wanted_bull else 'Breakdown', breakout if wanted_bull else breakdown),
        ('Pocket Pivot' if wanted_bull else 'Pocket Pivot Sell', pocket_pivot if wanted_bull else pocket_sell),
        ('VCP' if wanted_bull else 'Bear VCP', bull_vcp if wanted_bull else bear_vcp),
        ('Cup & Handle' if wanted_bull else 'Inverse Cup & Handle', cup_handle if wanted_bull else inverse_cup),
        ('High Tight Flag' if wanted_bull else 'High Tight Bear Flag', high_tight_flag if wanted_bull else high_tight_bear),
        ('Ascending Triangle' if wanted_bull else 'Descending Triangle', asc_triangle if wanted_bull else desc_triangle),
        ('Bull Flag' if wanted_bull else 'Bear Flag', bull_flag if wanted_bull else bear_flag),
        ('Flat Base' if wanted_bull else 'Bear Flat Base', bull_flat if wanted_bull else bear_flat),
        ('Weekly Base' if wanted_bull else 'Weekly Bear Base', weekly_bull_base if wanted_bull else weekly_bear_base),
        ('EMA Pullback' if wanted_bull else 'EMA Rejection', ema_pullback if wanted_bull else ema_rejection),
        ('Undercut & Rally' if wanted_bull else 'Upthrust & Fail', undercut_rally if wanted_bull else upthrust_fail),
        ('Compression', compression),
    ]
    structure = next((name for name,cond in structure_order if cond), 'None')
    volume_state = 'Accumulation' if accumulation else 'Distribution' if distribution else 'Bull Dry-Up' if bull_dry else 'Bear Dry-Up' if bear_dry else 'Normal'
    rs_text = ' / '.join(f"{s}:{'Leader' if rs[s][0] else 'Laggard' if rs[s][1] else 'Mixed'}" for s in ('SPY','QQQ','IWM'))
    pos_size = 100 if raw_score >= 90 else 75 if raw_score >= 80 else 50 if raw_score >= 70 else 25 if raw_score >= 60 else 0

    # Bot-native risk plan: daily structure stop and automatic 2R target.
    price = float(c.iloc[-1])
    if wanted_bull:
        stop = float(l.tail(10).min())
        risk = price-stop
        target = price + 2*risk if risk > 0 else None
    else:
        stop = float(h.tail(10).max())
        risk = stop-price
        target = price - 2*risk if risk > 0 else None

    return {
        'ok': True, 'confirmed': bool(confirmed and has_edge and raw_score >= 70),
        'raw_score': int(raw_score), 'score': round(raw_score/10.0,1), 'bull_score': int(bull_score), 'bear_score': int(bear_score),
        'edge': int(edge), 'grade': grade, 'position_size_pct': pos_size, 'structure': structure,
        'rvol': rvol, 'volume_state': volume_state, 'rs_text': rs_text,
        'weekly': 'Bullish' if weekly_bull else 'Bearish' if weekly_bear else 'Neutral',
        'daily': 'Bullish' if daily_bull else 'Bearish' if daily_bear else 'Neutral',
        'h4': 'Bullish' if h4_bull else 'Bearish' if h4_bear else 'Neutral',
        'reasons': reasons, 'stop': stop, 'target': target,
        'confirmation_text': ('Daily clean LONG trigger' if wanted_bull else 'Daily clean SHORT trigger') if confirmed else 'Waiting for full Daily clean trigger',
    }
