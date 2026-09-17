import os
import time
import math
import re
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from chief_patterns import detect_patterns
from chief_intelligence import momentum_snapshot, momentum_score, news_context
from chief_options import recommend_options
from chief_confirmation import evaluate_confirmation
from chief_swing_daily import analyze_daily_swing

load_dotenv('.env')

WATCH_SCORE = float(os.getenv('WATCH_SCORE', '7.0'))
CONFIRMED_SCORE = 8.0
EARLY_SETUP_SCORE = float(os.getenv('EARLY_SETUP_SCORE', '8.5'))
MAX_SPREAD_PCT = float(os.getenv('MAX_SPREAD_PCT', '0.40'))
MIN_RVOL = float(os.getenv('MIN_RVOL', '1.25'))
SCAN_SECONDS = 25
DAY_TRADING = os.getenv('DAY_TRADING', 'true').lower() in ('1','true','yes','on')
SWING_TRADING = os.getenv('SWING_TRADING', 'true').lower() in ('1','true','yes','on')
WHOLE_MARKET = os.getenv('WHOLE_MARKET', 'true').lower() in ('1','true','yes','on')
# Split architecture:
# - DAY pool keeps 5 live feeds per symbol for fast intraday scans.
# - SWING pool rotates through Daily + 1H only, then immediately unsubscribes.
# This lets Chief inspect far more symbols without holding 5 feeds open on every stock.
DAY_CANDIDATES = min(int(os.getenv('DAY_CANDIDATES', '10')), 12)
SWING_CANDIDATES = min(int(os.getenv('SWING_CANDIDATES', '100')), 150)
SWING_BATCH_SIZE = min(int(os.getenv('SWING_BATCH_SIZE', '10')), 20)
UNIVERSE_REFRESH_SECONDS = int(os.getenv('UNIVERSE_REFRESH_SECONDS', '900'))
MIN_PRICE = float(os.getenv('MIN_PRICE', '5'))
MAX_PRICE = float(os.getenv('MAX_PRICE', '1000'))
MIN_TURNOVER = float(os.getenv('MIN_TURNOVER', '5000000'))
MIN_VOLUME = float(os.getenv('MIN_VOLUME', '300000'))
SNAPSHOT_BATCH = min(int(os.getenv('SNAPSHOT_BATCH', '400')), 400)
CORE_WATCHLIST = [x.strip().upper() for x in os.getenv('WATCHLIST','QQQ,SPY,NVDA,TSLA,AMD,AMZN,META,GOOGL,AAPL,MSFT,AVGO,ARM,COIN,HIMS').split(',') if x.strip()]


def signal_window_open():
    now = datetime.now(ZoneInfo('America/New_York'))
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return 510 <= m < 960


def telegram(msg):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN',''), os.getenv('TELEGRAM_CHAT_ID','')
    if token and chat:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id':chat,'text':msg}, timeout=15)
        r.raise_for_status()


def discord(msg):
    url = os.getenv('DISCORD_WEBHOOK_URL','')
    if url:
        r = requests.post(url, json={'content':msg}, timeout=15)
        r.raise_for_status()


def notify(msg):
    telegram(msg); discord(msg)


def ema(s,n): return s.ewm(span=n, adjust=False).mean()


def normalize(df):
    df = df.rename(columns={c:c.lower() for c in df.columns})
    for target in ('open','high','low','close','volume'):
        if target not in df.columns:
            for c in df.columns:
                if c.lower().endswith(target):
                    df[target] = df[c]; break
    keep = ['open','high','low','close','volume']
    if 'time_key' in df.columns: keep = ['time_key'] + keep
    out = df[keep].copy()
    for c in ('open','high','low','close','volume'):
        out[c] = pd.to_numeric(out[c], errors='coerce')
    return out.dropna(subset=['open','high','low','close','volume'])


def metrics(df):
    if len(df) < 55: raise RuntimeError('not enough candles')
    c = df.close; e20 = ema(c,20); e50 = ema(c,50)
    prior_vol = df.volume.tail(21).iloc[:-1].mean()
    rvol = float(df.volume.iloc[-1] / max(prior_vol,1))
    bull = e20.iloc[-1] > e50.iloc[-1] and e20.iloc[-1] > e20.iloc[-5] and e50.iloc[-1] > e50.iloc[-5]
    bear = e20.iloc[-1] < e50.iloc[-1] and e20.iloc[-1] < e20.iloc[-5] and e50.iloc[-1] < e50.iloc[-5]
    return {'bull':bool(bull),'bear':bool(bear),'rvol':rvol,'e20':float(e20.iloc[-1]),'e50':float(e50.iloc[-1])}


def ema8_vwap_3m(df):
    if df is None or len(df) < 12:
        return {'bull_cross':False,'bear_cross':False,'bull_aligned':False,'bear_aligned':False,'ema8':0.0,'vwap':0.0,'text':'3m EMA8/VWAP unavailable'}
    d = df.copy()
    if 'time_key' in d.columns:
        dt = pd.to_datetime(d.time_key, errors='coerce')
        if dt.notna().any():
            last_date = dt[dt.notna()].dt.date.iloc[-1]
            same = dt.dt.date == last_date
            if same.sum() >= 8: d = d.loc[same].copy()
    e8 = ema(d.close,8); typical=(d.high+d.low+d.close)/3.0; vol=d.volume.clip(lower=0)
    vwap=(typical*vol).cumsum()/vol.cumsum().replace(0,pd.NA); vwap=vwap.ffill().bfill()
    bull_series=(e8>vwap)&(e8.shift(1)<=vwap.shift(1)); bear_series=(e8<vwap)&(e8.shift(1)>=vwap.shift(1))
    bc=bool(bull_series.tail(3).fillna(False).any()); sc=bool(bear_series.tail(3).fillna(False).any())
    ba=bool(e8.iloc[-1]>vwap.iloc[-1] and d.close.iloc[-1]>vwap.iloc[-1]); sa=bool(e8.iloc[-1]<vwap.iloc[-1] and d.close.iloc[-1]<vwap.iloc[-1])
    state='BULL CROSS' if bc else 'BEAR CROSS' if sc else 'BULLISH' if ba else 'BEARISH' if sa else 'NEUTRAL'
    return {'bull_cross':bc,'bear_cross':sc,'bull_aligned':ba,'bear_aligned':sa,'ema8':float(e8.iloc[-1]),'vwap':float(vwap.iloc[-1]),'text':f'3m EMA8/VWAP: {state} | EMA8 {e8.iloc[-1]:.2f} | VWAP {vwap.iloc[-1]:.2f}'}


def score_day(side, m15, m60, daily, patterns, momentum, micro3, spread_pct):
    score=0.0; reasons=[]
    aligned=(side=='CALL' and m60['bull'] and daily['bull']) or (side=='PUT' and m60['bear'] and daily['bear'])
    if aligned: score+=2.5; reasons.append('HTF trend aligned')
    elif (side=='CALL' and m60['bull']) or (side=='PUT' and m60['bear']): score+=1.2; reasons.append('1H trend aligned')
    if m15['rvol']>=MIN_RVOL: score+=1.5; reasons.append(f"RVOL {m15['rvol']:.2f}x")
    matching=[p for p in patterns if p.side==side]; pat=matching[0] if matching else None
    if pat: score+=min(2.2,1.0+pat.confidence*1.4); reasons.append(pat.name)
    if (side=='CALL' and m15['e20']>m15['e50']) or (side=='PUT' and m15['e20']<m15['e50']): score+=1.3; reasons.append('20/50 EMA structure')
    mp,mr=momentum_score(side,momentum)
    if mp: score+=mp; reasons.append(mr)
    cross=micro3['bull_cross'] if side=='CALL' else micro3['bear_cross']; aligned3=micro3['bull_aligned'] if side=='CALL' else micro3['bear_aligned']
    if cross: score+=1.3; reasons.append('3m EMA8/VWAP fresh cross')
    elif aligned3: score+=0.6; reasons.append('3m EMA8/VWAP aligned')
    if spread_pct<=MAX_SPREAD_PCT: score+=1.0; reasons.append(f'spread {spread_pct:.2f}%')
    return min(round(score,1),10.0),reasons,pat


def day_alert(ticker,side,score,price,pat,tf,reasons,spread,momentum,micro3,news,options,confirmation):
    pattern=f'{pat.name} ({tf})' if pat else 'No A+ pattern yet'
    trigger=f'{pat.trigger:.2f}' if pat and pat.trigger else f'{price:.2f} confirmation'
    invalid=f'{pat.invalidation:.2f}' if pat and pat.invalidation else 'Use confirmed structure invalidation'
    return (f'✅ CHIEF CONFIRMED {side} | {ticker} | DAY TRADE\nScore: {score}/10\nPrice: {price:.2f}\nStyle: DAY TRADE | 5m patterns / 3m EMA8-VWAP timing / 15m execution / 1H + Daily bias\nMomentum: {momentum["text"]}\n3m Timing: {micro3["text"]}\nSpread: {spread:.2f}%\nPattern: {pattern}\nTrigger: {trigger}\nInvalidation: {invalid}\nConfirmation: {confirmation["text"]}\nNews: {news["text"]}\nWhy: {", ".join(reasons)}\n\n{options["text"]}\n\nStatus: ✅ CONFIRMED — PRICE ACTION VALIDATED')


def swing_alert(ticker,side,price,a,spread,news,options):
    stop=f'{a["stop"]:.2f}' if a.get('stop') is not None else 'N/A'; target=f'{a["target"]:.2f}' if a.get('target') is not None else 'N/A'
    return (f'✅ CHIEF CONFIRMED {side} | {ticker} | SWING\nScore: {a["score"]}/10 | JR Score {a["raw_score"]}/100 {a["grade"]}\nPrice: {price:.2f}\nStyle: SWING | DAILY execution / Weekly + 4H alignment / RS + volume + structure\nMomentum: Daily structure engine\nSpread: {spread:.2f}%\nPattern: {a["structure"]} (Daily)\nTrigger: {a["confirmation_text"]}\nInvalidation: {stop}\nTarget: {target} (2R structure target)\nConfirmation: {a["confirmation_text"]}\nTrend: Weekly {a["weekly"]} | Daily {a["daily"]} | 4H {a["h4"]}\nRelative Strength: {a["rs_text"]}\nVolume: {a["volume_state"]} | RVOL {a["rvol"]:.2f}x\nBull Score: {a["bull_score"]}/100 | Bear Score: {a["bear_score"]}/100 | Edge: {a["edge"]} pts\nModel Position Tier: {a["position_size_pct"]}% of normal size\nNews: {news["text"]}\nWhy: {", ".join(a["reasons"])}\n\n{options["text"]}\n\nStatus: ✅ CONFIRMED — JR DAILY SWING METRICS VALIDATED')


def moomoo_context():
    from moomoo import OpenQuoteContext
    return OpenQuoteContext(host=os.getenv('MOOMOO_HOST','127.0.0.1'),port=int(os.getenv('MOOMOO_PORT','11111')))


def _truthy_series(s): return s.fillna(False).map(lambda v:str(v).strip().lower() in ('true','1','yes','y'))


def get_us_stock_universe(ctx):
    from moomoo import RET_OK,Market,SecurityType
    ret,data=ctx.get_stock_basicinfo(Market.US,SecurityType.STOCK)
    if ret!=RET_OK or data is None or data.empty: raise RuntimeError(f'get_stock_basicinfo failed: {data}')
    raw=len(data)
    if 'delisting' in data.columns: data=data[~_truthy_series(data.delisting)].copy()
    if 'suspension' in data.columns: data=data[~_truthy_series(data.suspension)].copy()
    codes=data.code.dropna().astype(str).drop_duplicates().tolist(); print(f'CHIEF universe: {len(codes)} active US symbols from {raw} Moomoo records',flush=True); return codes


def _unsupported(err,batch):
    m=re.search(r'not available for\s+([A-Z0-9.\-]+)',str(err),re.I)
    if not m:return None
    raw=m.group(1).rstrip('.,;:').upper()
    for code in batch:
        if code.upper().replace('US.','')==raw.replace('US.',''): return code
    return None


def _snapshot_resilient(ctx,batch):
    from moomoo import RET_OK
    if not batch:return [],[]
    work=list(batch); skipped=[]
    for _ in range(min(30,len(work))):
        ret,data=ctx.get_market_snapshot(work)
        if ret==RET_OK and data is not None and not data.empty:return [data],skipped
        bad=_unsupported(data,work)
        if bad: work.remove(bad); skipped.append(bad); continue
        break
    if len(work)<=1:return [],skipped+work
    mid=len(work)//2; lf,ls=_snapshot_resilient(ctx,work[:mid]); rf,rs=_snapshot_resilient(ctx,work[mid:]); return lf+rf,skipped+ls+rs


def get_snapshots(ctx,codes):
    frames=[]; skipped=[]; batches=(len(codes)+SNAPSHOT_BATCH-1)//SNAPSHOT_BATCH
    for i in range(0,len(codes),SNAPSHOT_BATCH):
        got,bad=_snapshot_resilient(ctx,codes[i:i+SNAPSHOT_BATCH]); frames+=got; skipped+=bad
        n=i//SNAPSHOT_BATCH+1
        if n==1 or n%5==0 or n==batches: print(f'CHIEF stage 1 progress: batch {n}/{batches}, {sum(len(x) for x in frames)} quoted, {len(skipped)} unsupported skipped',flush=True)
        time.sleep(.35)
    return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()


def _filtered_market(snapshot):
    if snapshot.empty:return pd.DataFrame()
    d=snapshot.copy()
    for c in ['last_price','prev_close_price','volume','turnover','volume_ratio','ask_price','bid_price']:
        if c not in d.columns:d[c]=0.0
        d[c]=pd.to_numeric(d[c],errors='coerce').fillna(0.0)
    d=d[(d.last_price>=MIN_PRICE)&(d.last_price<=MAX_PRICE)&(d.volume>=MIN_VOLUME)&(d.turnover>=MIN_TURNOVER)&(d.prev_close_price>0)].copy()
    if d.empty:return d
    d['move_pct']=((d.last_price/d.prev_close_price)-1).abs()*100
    mid=(d.ask_price+d.bid_price)/2
    d['spread_pct']=((d.ask_price-d.bid_price)/mid.replace(0,pd.NA)*100).fillna(999)
    d['liq']=d.turnover.clip(lower=1).map(math.log10)
    d['vr']=d.volume_ratio.clip(0,5)
    return d[d.spread_pct<=MAX_SPREAD_PCT].copy()


def rank_day_candidates(snapshot):
    d=_filtered_market(snapshot)
    if d.empty:return []
    # Intraday pool favors current movement, RVOL and liquidity.
    d['day_rank']=d.move_pct.clip(upper=15)*1.9+d.vr*1.8+d.liq*.9-d.spread_pct.clip(upper=5)*2
    return d.sort_values(['day_rank','turnover'],ascending=[False,False]).head(DAY_CANDIDATES).code.astype(str).tolist()


def rank_swing_candidates(snapshot):
    d=_filtered_market(snapshot)
    if d.empty:return []
    # Swing discovery intentionally does NOT rank only today's biggest movers.
    # It favors liquidity, participation and reasonable spread while penalizing
    # already-extended daily moves. JR Swing PRO then does the real structure ranking.
    extension_penalty=(d.move_pct-6.0).clip(lower=0)*0.8
    d['swing_rank']=d.liq*1.8+d.vr.clip(upper=3)*1.0+d.move_pct.clip(upper=4)*0.25-extension_penalty-d.spread_pct.clip(upper=5)*2
    core_codes=[f'US.{x}' for x in CORE_WATCHLIST]
    d['core_bonus']=d.code.astype(str).isin(core_codes).astype(int)*2.0
    d['swing_rank']+=d.core_bonus
    return d.sort_values(['swing_rank','turnover'],ascending=[False,False]).head(SWING_CANDIDATES).code.astype(str).tolist()


def build_scan_pools(ctx):
    if not WHOLE_MARKET:
        base=[f'US.{x}' for x in CORE_WATCHLIST]
        return base[:DAY_CANDIDATES],base[:SWING_CANDIDATES]
    u=get_us_stock_universe(ctx)
    print(f'CHIEF stage 1: scanning {len(u)} US symbols...',flush=True)
    snap=get_snapshots(ctx,u)
    day=rank_day_candidates(snap)
    swing=rank_swing_candidates(snap)
    for code in [f'US.{x}' for x in CORE_WATCHLIST]:
        if code not in day and len(day)<DAY_CANDIDATES:day.append(code)
        if code not in swing and len(swing)<SWING_CANDIDATES:swing.append(code)
    print(f"CHIEF DAY pool: {len(day)} -> {', '.join(c.replace('US.','') for c in day)}",flush=True)
    print(f"CHIEF SWING pool: {len(swing)} rotating symbols",flush=True)
    return day,swing

def snapshot_for_code(ctx,code):
    from moomoo import RET_OK
    ret,data=ctx.get_market_snapshot([code])
    if ret!=RET_OK or data is None or data.empty:raise RuntimeError(f'snapshot failed: {data}')
    r=data.iloc[0]; price=float(r.get('last_price',0) or 0); ask=float(r.get('ask_price',0) or 0); bid=float(r.get('bid_price',0) or 0); mid=(ask+bid)/2
    return price,((ask-bid)/mid*100 if ask>0 and bid>0 and mid>0 else 999)


def get_bars(ctx,code,ktype,count=300):
    from moomoo import RET_OK,SubType,KLType
    subtype={KLType.K_3M:SubType.K_3M,KLType.K_5M:SubType.K_5M,KLType.K_15M:SubType.K_15M,KLType.K_60M:SubType.K_60M,KLType.K_DAY:SubType.K_DAY}[ktype]
    ret,err=ctx.subscribe([code],[subtype],subscribe_push=False)
    if ret!=RET_OK:raise RuntimeError(f'subscribe failed {code} {ktype}: {err}')
    ret,data=ctx.get_cur_kline(code,count,ktype)
    if ret!=RET_OK:raise RuntimeError(str(data))
    return normalize(data)


def release_removed_day_candidates(ctx,removed):
    if not removed:return
    from moomoo import RET_OK,SubType
    subs=[SubType.K_3M,SubType.K_5M,SubType.K_15M,SubType.K_60M,SubType.K_DAY]
    for code in removed:
        ret,err=ctx.unsubscribe([code],subs)
        if ret!=RET_OK:print(f'day-pool unsubscribe warning {code}: {err}',flush=True)


def release_temporary_swing(ctx,code,day_set):
    if code in day_set:return
    from moomoo import RET_OK,SubType
    ret,err=ctx.unsubscribe([code],[SubType.K_60M,SubType.K_DAY])
    if ret!=RET_OK:print(f'swing-temp unsubscribe warning {code}: {err}',flush=True)


def evaluate_day(ctx,ticker,side,price,spread,d3,d5,d15,d60,dd,m15,m60,daily,last_alert):
    micro=ema8_vwap_3m(d3); p5=detect_patterns(d5); p15=detect_patterns(d15); patterns=p5+p15; momentum=momentum_snapshot(d15)
    score,reasons,pat=score_day(side,m15,m60,daily,patterns,momentum,micro,spread)
    if score<WATCH_SCORE:return
    tf='5m' if pat and any(pat is p for p in p5) else '15m' if pat else ''; entry=d5 if tf=='5m' else d15
    conf=evaluate_confirmation(side=side,df=entry,score=score,pattern=pat,min_score=CONFIRMED_SCORE)
    if not conf['confirmed'] or not signal_window_open():return
    key=(ticker,side,'DAY TRADE',tf,pat.name if pat else '')
    if time.time()-last_alert.get(key,0)<=1800:return
    news=news_context(ctx,ticker); options=recommend_options(ctx,ticker,side,trade_type='DAY TRADE')
    notify(day_alert(ticker,side,score,price,pat,tf,reasons,spread,momentum,micro,news,options,conf)); last_alert[key]=time.time()


def evaluate_swing(ctx,ticker,side,price,spread,d60,dd,benchmarks,last_alert,swing_send_count):
    a=analyze_daily_swing(dd,d60,benchmarks,side)
    if not a.get('ok') or not a.get('confirmed') or not signal_window_open():return
    key=(ticker,side,'SWING','DAILY',a.get('structure','None'))
    if time.time()-last_alert.get(key,0)<=1800:return
    swing_key=(ticker,side,a.get('structure','None'))
    if swing_send_count.get(swing_key,0)>=2:return
    news=news_context(ctx,ticker); options=recommend_options(ctx,ticker,side,trade_type='SWING')
    notify(swing_alert(ticker,side,price,a,spread,news,options)); last_alert[key]=time.time(); swing_send_count[swing_key]=swing_send_count.get(swing_key,0)+1


def run():
    from moomoo import KLType
    ctx=moomoo_context()
    last_alert={}; swing_send_count={}
    day_candidates=[]; swing_candidates=[]; swing_cursor=0; last_refresh=0.0
    print(f'Chief Bot started. Split scanner active: up to {DAY_CANDIDATES} fast DAY names every {SCAN_SECONDS}s + up to {SWING_CANDIDATES} rotating JR Swing PRO names in batches of {SWING_BATCH_SIZE}. 1m disabled.',flush=True)
    try:
        while True:
            loop_started=time.time()
            now=loop_started
            if not day_candidates or not swing_candidates or now-last_refresh>=UNIVERSE_REFRESH_SECONDS:
                old_day=set(day_candidates)
                try:
                    new_day,new_swing=build_scan_pools(ctx)
                    if new_day:day_candidates=new_day
                    if new_swing:swing_candidates=new_swing
                    last_refresh=now
                    release_removed_day_candidates(ctx,old_day-set(day_candidates))
                    if swing_cursor>=len(swing_candidates):swing_cursor=0
                except Exception as e:
                    print(f'whole-market refresh error: {e}',flush=True)
                    if not day_candidates:day_candidates=[f'US.{x}' for x in CORE_WATCHLIST[:DAY_CANDIDATES]]
                    if not swing_candidates:swing_candidates=[f'US.{x}' for x in CORE_WATCHLIST[:SWING_CANDIDATES]]

            benchmarks={}
            if SWING_TRADING:
                for sym in ('SPY','QQQ','IWM'):
                    try:benchmarks[sym]=get_bars(ctx,f'US.{sym}',KLType.K_DAY)
                    except Exception as e:print(f'{sym} RS benchmark error: {e}',flush=True)

            # Fast intraday pool: keep the full 3m/5m/15m/1H/Daily stack live.
            if DAY_TRADING:
                for code in day_candidates:
                    ticker=code.replace('US.','')
                    try:
                        d3=get_bars(ctx,code,KLType.K_3M); d5=get_bars(ctx,code,KLType.K_5M); d15=get_bars(ctx,code,KLType.K_15M)
                        d60=get_bars(ctx,code,KLType.K_60M); dd=get_bars(ctx,code,KLType.K_DAY)
                        price,spread=snapshot_for_code(ctx,code)
                        if spread>MAX_SPREAD_PCT:continue
                        m15,m60,daily=metrics(d15),metrics(d60),metrics(dd)
                        for side in ('CALL','PUT'):
                            evaluate_day(ctx,ticker,side,price,spread,d3,d5,d15,d60,dd,m15,m60,daily,last_alert)
                    except Exception as e:print(f'{ticker} DAY: {e}',flush=True)

            # Rotating swing pool: only Daily + 1H are needed by JR Swing PRO.
            if SWING_TRADING and swing_candidates:
                batch=[]
                for _ in range(min(SWING_BATCH_SIZE,len(swing_candidates))):
                    batch.append(swing_candidates[swing_cursor%len(swing_candidates)])
                    swing_cursor=(swing_cursor+1)%len(swing_candidates)
                day_set=set(day_candidates)
                print(f"CHIEF SWING batch: {', '.join(x.replace('US.','') for x in batch)}",flush=True)
                for code in batch:
                    ticker=code.replace('US.','')
                    try:
                        d60=get_bars(ctx,code,KLType.K_60M); dd=get_bars(ctx,code,KLType.K_DAY)
                        price,spread=snapshot_for_code(ctx,code)
                        if spread<=MAX_SPREAD_PCT:
                            for side in ('CALL','PUT'):
                                a=analyze_daily_swing(dd,d60,benchmarks,side)
                                if a.get('front_run') and not a.get('confirmed'):
                                    print(f"{ticker} SWING {side}: EARLY {a.get('raw_score',0)}/100 | internal {a.get('internal_trigger')} -> major {a.get('major_trigger')} | proj RVOL {a.get('projected_rvol',0):.2f}x",flush=True)
                                evaluate_swing(ctx,ticker,side,price,spread,d60,dd,benchmarks,last_alert,swing_send_count)
                    except Exception as e:print(f'{ticker} SWING: {e}',flush=True)
                    finally:
                        release_temporary_swing(ctx,code,day_set)

            elapsed=time.time()-loop_started
            sleep_for=max(1.0,SCAN_SECONDS-elapsed)
            time.sleep(sleep_for)
    finally:
        ctx.close()


if __name__=='__main__':run()
