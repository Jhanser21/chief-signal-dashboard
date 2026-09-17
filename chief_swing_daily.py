import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

EMA_FAST=8; EMA_MID=21; MA_SLOW=50
RVOL_LEN=20; RVOL_STRONG=1.50
COMPRESSION_LEN=20; INTERNAL_LOOKBACK=8; MAJOR_LOOKBACK=20
FRONT_MIN_SCORE=70; FRONT_PROJ_RVOL=1.20; MAX_BASE_WIDTH_PCT=12.0


def _ema(s,n): return s.ewm(span=n,adjust=False).mean()
def _sma(s,n): return s.rolling(n).mean()

def _weekly_from_daily(df):
    d=df.copy(); idx=pd.to_datetime(d.get('time_key'),errors='coerce') if 'time_key' in d else pd.date_range(end=pd.Timestamp.today(),periods=len(d),freq='B')
    d=d.assign(_dt=idx).dropna(subset=['_dt']).set_index('_dt')
    return d.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()

def _four_hour_from_60m(df):
    d=df.copy()
    if 'time_key' in d:
        idx=pd.to_datetime(d.time_key,errors='coerce'); d=d.assign(_dt=idx).dropna(subset=['_dt']).set_index('_dt'); parts=[]
        for _,day in d.groupby(d.index.date):
            if not day.empty:
                grp=pd.Series(range(len(day)),index=day.index)//4
                parts.append(day.groupby(grp).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}))
        if parts:return pd.concat(parts,ignore_index=True)
    grp=pd.Series(range(len(d)),index=d.index)//4
    return d.groupby(grp).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()

def _trend(df):
    if df is None or len(df)<55:return False,False
    c=df.close; e8=_ema(c,8); e21=_ema(c,21); e50=_ema(c,50)
    return bool(c.iloc[-1]>e8.iloc[-1]>e21.iloc[-1]>e50.iloc[-1]),bool(c.iloc[-1]<e8.iloc[-1]<e21.iloc[-1]<e50.iloc[-1])

def _rs_state(stock,bench):
    n=min(len(stock),len(bench))
    if n<55:return False,False
    rs=stock.iloc[-n:].reset_index(drop=True)/bench.iloc[-n:].reset_index(drop=True).replace(0,pd.NA); r20=_sma(rs,20); r50=_sma(rs,50)
    return bool(rs.iloc[-1]>r20.iloc[-1]>r50.iloc[-1]),bool(rs.iloc[-1]<r20.iloc[-1]<r50.iloc[-1])

def _projected_rvol(today_vol,avg_daily_vol):
    if avg_daily_vol<=0:return 0.0
    now=datetime.now(ZoneInfo('America/New_York')); cur=now.hour*60+now.minute; start=570; end=960
    if start<=cur<end:
        progress=max(min((cur-start)/(end-start),1.0),0.10)
        return float((today_vol/progress)/avg_daily_vol)
    return float(today_vol/avg_daily_vol)

def analyze_daily_swing(dd,d60,benchmarks,side):
    """Faithful bot port of JR Swing Leader Clean PRO: Daily swing execution with Weekly/4H trend, index RS, volume, internal front-run and major confirmed breakout logic."""
    if dd is None or len(dd)<120 or d60 is None or len(d60)<80:return {'ok':False,'confirmed':False,'score':0,'text':'Insufficient Daily/1H history'}
    d=dd.copy().reset_index(drop=True); w=_weekly_from_daily(d); h4=_four_hour_from_60m(d60)
    c,h,l,o,v=d.close,d.high,d.low,d.open,d.volume; e8=_ema(c,8); e21=_ema(c,21); e50=_ema(c,50); vol_avg=_sma(v,RVOL_LEN)
    rvol=float(v.iloc[-1]/max(float(vol_avg.iloc[-1]),1.0)); wb,ws=_trend(w); db,ds=_trend(d); hb,hs=_trend(h4)
    br=max(float(h.iloc[-1]-l.iloc[-1]),1e-9); strong_close=c.iloc[-1]>=h.iloc[-1]-br*.30; weak_close=c.iloc[-1]<=l.iloc[-1]+br*.30
    accumulation=bool(c.iloc[-1]>o.iloc[-1] and rvol>=RVOL_STRONG and strong_close); distribution=bool(c.iloc[-1]<o.iloc[-1] and rvol>=RVOL_STRONG and weak_close); dry=bool(v.iloc[-1]<vol_avg.iloc[-1]*.60)

    rs={}
    for sym in ('SPY','QQQ','IWM'):
        b=benchmarks.get(sym); rs[sym]=_rs_state(c,b.close) if b is not None and len(b)>=55 else (False,False)
    bull_rs_count=sum(int(rs[s][0]) for s in rs); bear_rs_count=sum(int(rs[s][1]) for s in rs); rs_bull=bull_rs_count>=2; rs_bear=bear_rs_count>=2

    comp_hi=float(h.iloc[-COMPRESSION_LEN-1:-1].max()); comp_lo=float(l.iloc[-COMPRESSION_LEN-1:-1].min()); comp_width=((comp_hi-comp_lo)/comp_lo*100) if comp_lo>0 else 999
    compression=bool(comp_width<=15 and v.iloc[-1]<vol_avg.iloc[-1]); higher_lows=bool(l.tail(5).min()>l.iloc[-10:-5].min()); lower_highs=bool(h.tail(5).max()<h.iloc[-10:-5].max())
    bull_vcp=bool(compression and higher_lows and dry); bear_vcp=bool(compression and lower_highs and dry)
    bull_flag=bool(c.iloc[-1]>e21.iloc[-1] and e8.iloc[-1]>e21.iloc[-1] and higher_lows and v.iloc[-1]<vol_avg.iloc[-1]); bear_flag=bool(c.iloc[-1]<e21.iloc[-1] and e8.iloc[-1]<e21.iloc[-1] and lower_highs and v.iloc[-1]<vol_avg.iloc[-1])
    bull_cup=bool(c.iloc[-1]>e50.iloc[-1] and c.iloc[-1]>e21.iloc[-1] and compression and higher_lows); bear_cup=bool(c.iloc[-1]<e50.iloc[-1] and c.iloc[-1]<e21.iloc[-1] and compression and lower_highs)

    int_res=float(h.iloc[-INTERNAL_LOOKBACK-1:-1].max()); int_sup=float(l.iloc[-INTERNAL_LOOKBACK-1:-1].min()); maj_res=float(h.iloc[-MAJOR_LOOKBACK-1:-1].max()); maj_sup=float(l.iloc[-MAJOR_LOOKBACK-1:-1].min())
    int_hi=int_res; int_lo=int_sup; int_width=((int_hi-int_lo)/int_lo*100) if int_lo>0 else 999; tight=int_width<=MAX_BASE_WIDTH_PCT
    recent_range=float(h.tail(INTERNAL_LOOKBACK).max()-l.tail(INTERNAL_LOOKBACK).min()); prev_range=float(h.iloc[-2*INTERNAL_LOOKBACK:-INTERNAL_LOOKBACK].max()-l.iloc[-2*INTERNAL_LOOKBACK:-INTERNAL_LOOKBACK].min()); contracting=prev_range>0 and recent_range<=prev_range
    vol_contract=float(v.tail(5).mean())<float(vol_avg.iloc[-1]); bull_struct=bool(tight and (contracting or higher_lows)); bear_struct=bool(tight and (contracting or lower_highs))
    bull_struct_ok=bool(bull_struct or bull_vcp or bull_flag or bull_cup or compression); bear_struct_ok=bool(bear_struct or bear_vcp or bear_flag or bear_cup or compression)
    int_bull_break=bool(h.iloc[-1]>int_res and c.iloc[-1]>int_res); int_bear_break=bool(l.iloc[-1]<int_sup and c.iloc[-1]<int_sup); before_major_bull=bool(c.iloc[-1]<maj_res); before_major_bear=bool(c.iloc[-1]>maj_sup)
    major_bull=bool(h.iloc[-1]>maj_res and c.iloc[-1]>maj_res); major_bear=bool(l.iloc[-1]<maj_sup and c.iloc[-1]<maj_sup)

    bull_score=min((20 if wb else 0)+(20 if db else 0)+(10 if hb else 0)+(15 if rs_bull else 0)+(10 if accumulation else 0)+(5 if compression else 0)+(5 if bull_vcp else 0)+(5 if bull_flag else 0)+(5 if bull_cup else 0)+(5 if higher_lows else 0)+(3 if c.iloc[-1]>e8.iloc[-1] else 0)+(2 if e8.iloc[-1]>e21.iloc[-1] else 0),100)
    bear_score=min((20 if ws else 0)+(20 if ds else 0)+(10 if hs else 0)+(15 if rs_bear else 0)+(10 if distribution else 0)+(5 if compression else 0)+(5 if bear_vcp else 0)+(5 if bear_flag else 0)+(5 if bear_cup else 0)+(5 if lower_highs else 0)+(3 if c.iloc[-1]<e8.iloc[-1] else 0)+(2 if e8.iloc[-1]<e21.iloc[-1] else 0),100)

    avg_daily=float(v.iloc[-21:-1].mean()) if len(v)>=21 else float(vol_avg.iloc[-1]); proj_rvol=_projected_rvol(float(v.iloc[-1]),avg_daily)
    daily_bull_transition=bool(c.iloc[-1]>e21.iloc[-1] and e8.iloc[-1]>=e21.iloc[-1] and c.iloc[-1]>e50.iloc[-1]); daily_bear_transition=bool(c.iloc[-1]<e21.iloc[-1] and e8.iloc[-1]<=e21.iloc[-1] and c.iloc[-1]<e50.iloc[-1])
    bull_momentum=bool(c.iloc[-1]>e8.iloc[-1] and e8.iloc[-1]>=e21.iloc[-1] and c.iloc[-1]>e21.iloc[-1]); bear_momentum=bool(c.iloc[-1]<e8.iloc[-1] and e8.iloc[-1]<=e21.iloc[-1] and c.iloc[-1]<e21.iloc[-1])
    bull_part=bool(c.iloc[-1]>o.iloc[-1] and c.iloc[-1]>=h.iloc[-1]-br*.40); bear_part=bool(c.iloc[-1]<o.iloc[-1] and c.iloc[-1]<=l.iloc[-1]+br*.40)
    front_long=bool(wb and (db or daily_bull_transition) and hb and rs_bull and bull_momentum and bull_score>=FRONT_MIN_SCORE and bull_score>bear_score and bull_struct_ok and int_bull_break and before_major_bull and proj_rvol>=FRONT_PROJ_RVOL and bull_part and not distribution)
    front_short=bool(ws and (ds or daily_bear_transition) and hs and rs_bear and bear_momentum and bear_score>=FRONT_MIN_SCORE and bear_score>bull_score and bear_struct_ok and int_bear_break and before_major_bear and proj_rvol>=FRONT_PROJ_RVOL and bear_part and not accumulation)
    clean_long=bool(major_bull and wb and db and hb and rs_bull and bull_score>=FRONT_MIN_SCORE and rvol>=RVOL_STRONG); clean_short=bool(major_bear and ws and ds and hs and rs_bear and bear_score>=FRONT_MIN_SCORE and rvol>=RVOL_STRONG)

    want_bull=side=='CALL'; raw=bull_score if want_bull else bear_score; confirmed=clean_long if want_bull else clean_short; early=(front_long and not clean_long) if want_bull else (front_short and not clean_short)
    pattern=('VCP' if (bull_vcp if want_bull else bear_vcp) else 'Cup & Handle' if (bull_cup if want_bull else bear_cup) else 'Bull Flag' if want_bull and bull_flag else 'Bear Flag' if (not want_bull and bear_flag) else 'Compression' if compression else 'Higher Lows' if want_bull and higher_lows else 'Lower Highs' if (not want_bull and lower_highs) else 'Major Breakout' if want_bull and major_bull else 'Major Breakdown' if (not want_bull and major_bear) else 'Internal Structure')
    reasons=[]
    for cond,name in [(wb if want_bull else ws,'Weekly trend'),(db if want_bull else ds,'Daily trend'),(hb if want_bull else hs,'4H trend'),(rs_bull if want_bull else rs_bear,'Index RS 2/3+'),(accumulation if want_bull else distribution,'Volume confirmation'),(compression,'Compression'),(bull_vcp if want_bull else bear_vcp,'VCP'),(bull_flag if want_bull else bear_flag,'Flag'),(bull_cup if want_bull else bear_cup,'Cup/Handle'),(higher_lows if want_bull else lower_highs,'Structure'),(major_bull if want_bull else major_bear,'Major level break')]:
        if cond:reasons.append(name)
    grade='A+' if raw>=90 else 'A' if raw>=80 else 'B' if raw>=70 else 'C' if raw>=60 else 'WAIT'; edge=abs(bull_score-bear_score); price=float(c.iloc[-1]); stop=int_sup if want_bull else int_res; risk=(price-stop) if want_bull else (stop-price); target=(price+2*risk) if want_bull and risk>0 else (price-2*risk) if (not want_bull and risk>0) else None
    rs_text=' / '.join(f"{s}:{'Leader' if rs[s][0] else 'Laggard' if rs[s][1] else 'Mixed'}" for s in ('SPY','QQQ','IWM'))
    vol_state='Accumulation' if accumulation else 'Distribution' if distribution else 'Dry-Up' if dry else 'Normal'
    return {'ok':True,'confirmed':confirmed,'front_run':early,'raw_score':int(raw),'score':round(raw/10,1),'bull_score':int(bull_score),'bear_score':int(bear_score),'edge':int(edge),'grade':grade,'position_size_pct':100 if raw>=90 else 75 if raw>=80 else 50 if raw>=70 else 25 if raw>=60 else 0,'structure':pattern,'rvol':rvol,'projected_rvol':proj_rvol,'volume_state':vol_state,'rs_text':rs_text,'weekly':'Bullish' if wb else 'Bearish' if ws else 'Neutral','daily':'Bullish' if db else 'Bearish' if ds else 'Neutral','h4':'Bullish' if hb else 'Bearish' if hs else 'Neutral','reasons':reasons,'stop':stop,'target':target,'internal_trigger':int_res if want_bull else int_sup,'major_trigger':maj_res if want_bull else maj_sup,'confirmation_text':('Daily major LONG breakout confirmed' if want_bull else 'Daily major SHORT breakdown confirmed') if confirmed else ('Daily internal LONG front-run trigger' if want_bull else 'Daily internal SHORT front-run trigger') if early else 'Waiting for JR Swing PRO trigger'}