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

load_dotenv('.env')

WATCH_SCORE = float(os.getenv('WATCH_SCORE', '7.0'))
CONFIRMED_SCORE = 8.0
MAX_SPREAD_PCT = float(os.getenv('MAX_SPREAD_PCT', '0.40'))
MIN_RVOL = float(os.getenv('MIN_RVOL', '1.25'))
SCAN_SECONDS = int(os.getenv('SCAN_SECONDS', '60'))
DAY_TRADING = os.getenv('DAY_TRADING', 'true').lower() in ('1', 'true', 'yes', 'on')
SWING_TRADING = os.getenv('SWING_TRADING', 'true').lower() in ('1', 'true', 'yes', 'on')

WHOLE_MARKET = os.getenv('WHOLE_MARKET', 'true').lower() in ('1', 'true', 'yes', 'on')
DEEP_CANDIDATES = min(int(os.getenv('DEEP_CANDIDATES', '24')), 16 if DAY_TRADING else 24)
UNIVERSE_REFRESH_SECONDS = int(os.getenv('UNIVERSE_REFRESH_SECONDS', '900'))
MIN_PRICE = float(os.getenv('MIN_PRICE', '5'))
MAX_PRICE = float(os.getenv('MAX_PRICE', '1000'))
MIN_TURNOVER = float(os.getenv('MIN_TURNOVER', '5000000'))
MIN_VOLUME = float(os.getenv('MIN_VOLUME', '300000'))
SNAPSHOT_BATCH = min(int(os.getenv('SNAPSHOT_BATCH', '400')), 400)

CORE_WATCHLIST = [x.strip().upper() for x in os.getenv('WATCHLIST', 'QQQ,SPY,NVDA,TSLA,AMD,AMZN,META,GOOGL,AAPL,MSFT,AVGO,ARM,COIN,HIMS').split(',') if x.strip()]


def signal_window_open():
    """Chief may send trade signals only 8:30 AM through 4:00 PM New York time, weekdays."""
    now = datetime.now(ZoneInfo('America/New_York'))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (8 * 60 + 30) <= minutes < (16 * 60)


def telegram(msg):
    token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
    if token and chat:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':msg},timeout=15); r.raise_for_status()

def discord(msg):
    url=os.getenv('DISCORD_WEBHOOK_URL','')
    if url:
        r=requests.post(url,json={'content':msg},timeout=15); r.raise_for_status()

def notify(msg): telegram(msg); discord(msg)
def ema(s,n): return s.ewm(span=n,adjust=False).mean()


def normalize(df):
    df=df.rename(columns={c:c.lower() for c in df.columns})
    for target in ('open','high','low','close','volume'):
        if target not in df.columns:
            for c in df.columns:
                if c.lower().endswith(target): df[target]=df[c]; break
    keep=['open','high','low','close','volume']
    if 'time_key' in df.columns: keep=['time_key']+keep
    out=df[keep].copy()
    for c in ('open','high','low','close','volume'): out[c]=pd.to_numeric(out[c],errors='coerce')
    return out.dropna(subset=['open','high','low','close','volume'])


def metrics(df):
    if len(df)<55: raise RuntimeError('not enough candles')
    c=df.close; e20=ema(c,20); e50=ema(c,50); prior_vol=df.volume.tail(21).iloc[:-1].mean(); rvol=float(df.volume.iloc[-1]/max(prior_vol,1))
    bull=e20.iloc[-1]>e50.iloc[-1] and e20.iloc[-1]>e20.iloc[-5] and e50.iloc[-1]>e50.iloc[-5]
    bear=e20.iloc[-1]<e50.iloc[-1] and e20.iloc[-1]<e20.iloc[-5] and e50.iloc[-1]<e50.iloc[-5]
    return {'bull':bool(bull),'bear':bool(bear),'rvol':rvol,'e20':float(e20.iloc[-1]),'e50':float(e50.iloc[-1])}


def ema8_vwap_3m(df):
    if df is None or len(df)<12: return {'bull_cross':False,'bear_cross':False,'bull_aligned':False,'bear_aligned':False,'ema8':0.0,'vwap':0.0,'text':'3m EMA8/VWAP unavailable'}
    d=df.copy()
    if 'time_key' in d.columns:
        dt=pd.to_datetime(d['time_key'],errors='coerce'); valid=dt.notna()
        if valid.any():
            last_date=dt[valid].dt.date.iloc[-1]; same_day=dt.dt.date==last_date
            if same_day.sum()>=8: d=d.loc[same_day].copy()
    e8=ema(d['close'],8); typical=(d['high']+d['low']+d['close'])/3.0; vol=d['volume'].clip(lower=0); cum_vol=vol.cumsum().replace(0,pd.NA); vwap=(typical*vol).cumsum()/cum_vol; vwap=vwap.ffill().bfill()
    if len(d)<2 or vwap.isna().all(): return {'bull_cross':False,'bear_cross':False,'bull_aligned':False,'bear_aligned':False,'ema8':float(e8.iloc[-1]),'vwap':0.0,'text':'3m EMA8/VWAP unavailable'}
    bull_series=(e8>vwap)&(e8.shift(1)<=vwap.shift(1)); bear_series=(e8<vwap)&(e8.shift(1)>=vwap.shift(1)); bull_cross=bool(bull_series.tail(3).fillna(False).any()); bear_cross=bool(bear_series.tail(3).fillna(False).any()); bull_aligned=bool(e8.iloc[-1]>vwap.iloc[-1] and d['close'].iloc[-1]>vwap.iloc[-1]); bear_aligned=bool(e8.iloc[-1]<vwap.iloc[-1] and d['close'].iloc[-1]<vwap.iloc[-1]); state='BULL CROSS' if bull_cross else 'BEAR CROSS' if bear_cross else 'BULLISH' if bull_aligned else 'BEARISH' if bear_aligned else 'NEUTRAL'
    return {'bull_cross':bull_cross,'bear_cross':bear_cross,'bull_aligned':bull_aligned,'bear_aligned':bear_aligned,'ema8':float(e8.iloc[-1]),'vwap':float(vwap.iloc[-1]),'text':f"3m EMA8/VWAP: {state} | EMA8 {e8.iloc[-1]:.2f} | VWAP {vwap.iloc[-1]:.2f}"}


def score_setup(side,entry_m,higher_m,daily,patterns,momentum,micro3,spread_pct=999.0,trade_type='DAY TRADE'):
    score=0.0; reasons=[]; aligned=(side=='CALL' and higher_m['bull'] and daily['bull']) or (side=='PUT' and higher_m['bear'] and daily['bear'])
    if aligned: score+=2.5; reasons.append('HTF trend aligned')
    elif (side=='CALL' and higher_m['bull']) or (side=='PUT' and higher_m['bear']): score+=1.2; reasons.append('Higher-timeframe trend aligned')
    if entry_m['rvol']>=MIN_RVOL: score+=1.5; reasons.append(f"RVOL {entry_m['rvol']:.2f}x")
    matching=[p for p in patterns if p.side==side]
    if matching: p=matching[0]; score+=min(2.2,1.0+p.confidence*1.4); reasons.append(p.name)
    ema_ok=(side=='CALL' and entry_m['e20']>entry_m['e50']) or (side=='PUT' and entry_m['e20']<entry_m['e50'])
    if ema_ok: score+=1.3; reasons.append('20/50 EMA structure')
    mom_points,mom_reason=momentum_score(side,momentum)
    if mom_points: score+=mom_points; reasons.append(mom_reason)
    cross_ok=micro3['bull_cross'] if side=='CALL' else micro3['bear_cross']; micro_aligned=micro3['bull_aligned'] if side=='CALL' else micro3['bear_aligned']
    if cross_ok: score+=1.3 if trade_type=='DAY TRADE' else 0.6; reasons.append('3m EMA8/VWAP fresh cross')
    elif micro_aligned: score+=0.6 if trade_type=='DAY TRADE' else 0.3; reasons.append('3m EMA8/VWAP aligned')
    if spread_pct<=MAX_SPREAD_PCT: score+=1.0; reasons.append(f'spread {spread_pct:.2f}%')
    if trade_type=='SWING' and aligned: reasons.append('Swing trend structure')
    return min(round(score,1),10.0),reasons,(matching[0] if matching else None)


def format_alert(ticker,side,score,status,price,pat,pattern_tf,reasons,spread_pct,momentum,micro3,news,options,confirmation,trade_type):
    atr_note='Pattern-based invalidation' if pat and pat.invalidation else 'Use confirmed structure invalidation'; trigger=f"{pat.trigger:.2f}" if pat and pat.trigger else f"{price:.2f} confirmation"; invalid=f"{pat.invalidation:.2f}" if pat and pat.invalidation else atr_note; icon='✅' if status=='CONFIRMED' else '⏳'; status_text='CONFIRMED — PRICE ACTION VALIDATED' if status=='CONFIRMED' else 'WAITING FOR PRICE-ACTION CONFIRMATION...'; options_text=options['text'] if status=='CONFIRMED' else '⏳ Options: Chief will select contracts after price action confirms.'; timeframe_text='1m + 5m patterns / 3m EMA8-VWAP timing / 15m execution / 1H + Daily bias' if trade_type=='DAY TRADE' else '1H execution / Daily swing bias / 3m timing context'; pattern_text=f"{pat.name} ({pattern_tf})" if pat else 'No A+ pattern yet'
    return f"{icon} CHIEF {status} {side} | {ticker} | {trade_type}\nScore: {score}/10\nPrice: {price:.2f}\nStyle: {trade_type} | {timeframe_text}\nMomentum: {momentum['text']}\n3m Timing: {micro3['text']}\nSpread: {spread_pct:.2f}%\nPattern: {pattern_text}\nTrigger: {trigger}\nInvalidation: {invalid}\nConfirmation: {confirmation['text']}\nNews: {news['text']}\nWhy: {', '.join(reasons)}\n\n{options_text}\n\nStatus: {icon} {status_text}"


def moomoo_context():
    from moomoo import OpenQuoteContext
    return OpenQuoteContext(host=os.getenv('MOOMOO_HOST','127.0.0.1'),port=int(os.getenv('MOOMOO_PORT','11111')))

def _truthy_series(series): return series.fillna(False).map(lambda v:str(v).strip().lower() in ('true','1','yes','y'))


def get_us_stock_universe(ctx):
    from moomoo import RET_OK,Market,SecurityType
    ret,data=ctx.get_stock_basicinfo(Market.US,SecurityType.STOCK)
    if ret!=RET_OK: raise RuntimeError(f'get_stock_basicinfo failed: {data}')
    if data is None or data.empty or 'code' not in data.columns: raise RuntimeError('Moomoo returned an empty US stock universe')
    raw_count=len(data)
    if 'delisting' in data.columns: data=data[~_truthy_series(data['delisting'])].copy()
    if 'suspension' in data.columns: data=data[~_truthy_series(data['suspension'])].copy()
    codes=data['code'].dropna().astype(str).drop_duplicates().tolist(); print(f'CHIEF universe: {len(codes)} active US symbols from {raw_count} Moomoo records',flush=True); return codes


def _unsupported_code_from_error(err,batch):
    text=str(err); m=re.search(r'not available for\s+([A-Z0-9.\-]+)',text,re.I)
    if not m: return None
    raw=m.group(1).rstrip('.,;:').upper(); candidates={raw,f'US.{raw}' if not raw.startswith('US.') else raw}
    for code in batch:
        if code.upper() in candidates or code.upper().replace('US.','')==raw.replace('US.',''): return code
    return None


def _snapshot_resilient(ctx,batch,depth=0):
    from moomoo import RET_OK
    if not batch: return [],[]
    working=list(batch); skipped=[]
    for _ in range(min(30,len(working))):
        ret,data=ctx.get_market_snapshot(working)
        if ret==RET_OK and data is not None and not data.empty: return [data],skipped
        bad=_unsupported_code_from_error(data,working)
        if bad:
            working.remove(bad); skipped.append(bad)
            if not working: return [],skipped
            time.sleep(0.08); continue
        break
    if len(working)==1: return [],skipped+working
    mid=len(working)//2; lf,ls=_snapshot_resilient(ctx,working[:mid],depth+1); rf,rs=_snapshot_resilient(ctx,working[mid:],depth+1); return lf+rf,skipped+ls+rs


def get_snapshots(ctx,codes):
    frames=[]; skipped=[]; batches=(len(codes)+SNAPSHOT_BATCH-1)//SNAPSHOT_BATCH
    for i in range(0,len(codes),SNAPSHOT_BATCH):
        batch_no=i//SNAPSHOT_BATCH+1; got,bad=_snapshot_resilient(ctx,codes[i:i+SNAPSHOT_BATCH]); frames.extend(got); skipped.extend(bad)
        if batch_no==1 or batch_no%5==0 or batch_no==batches: print(f'CHIEF stage 1 progress: batch {batch_no}/{batches}, {sum(len(x) for x in frames)} quoted, {len(skipped)} unsupported skipped',flush=True)
        time.sleep(0.35)
    if skipped: print(f'CHIEF stage 1: skipped {len(set(skipped))} unsupported/unquotable US symbols',flush=True)
    return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()


def rank_market_candidates(snapshot):
    if snapshot.empty:return []
    d=snapshot.copy(); numeric=['last_price','prev_close_price','volume','turnover','volume_ratio','ask_price','bid_price']
    for c in numeric:
        if c not in d.columns:d[c]=0.0
        d[c]=pd.to_numeric(d[c],errors='coerce').fillna(0.0)
    d=d[(d.last_price>=MIN_PRICE)&(d.last_price<=MAX_PRICE)&(d.volume>=MIN_VOLUME)&(d.turnover>=MIN_TURNOVER)&(d.prev_close_price>0)].copy()
    if d.empty:return []
    d['move_pct']=((d.last_price/d.prev_close_price)-1.0).abs()*100.0; mid=(d.ask_price+d.bid_price)/2.0; d['spread_pct']=((d.ask_price-d.bid_price)/mid.replace(0,pd.NA)*100.0).fillna(999.0); d['liquidity_score']=d.turnover.clip(lower=1).map(lambda x:math.log10(x)); d['volume_ratio_score']=d.volume_ratio.clip(lower=0,upper=5); d['market_rank']=(d.move_pct.clip(upper=15)*1.8+d.volume_ratio_score*1.6+d.liquidity_score*0.9-d.spread_pct.clip(upper=5)*2.0); d=d.sort_values(['market_rank','turnover'],ascending=[False,False]); return d.head(DEEP_CANDIDATES)['code'].astype(str).tolist()


def build_deep_scan_list(ctx):
    if not WHOLE_MARKET:return [f'US.{x}' for x in CORE_WATCHLIST[:DEEP_CANDIDATES]]
    universe=get_us_stock_universe(ctx); print(f'CHIEF stage 1: scanning {len(universe)} US symbols...',flush=True); snap=get_snapshots(ctx,universe); print(f'CHIEF stage 1: received usable snapshots for {len(snap)} symbols',flush=True); ranked=rank_market_candidates(snap); combined=[]
    for code in ranked+[f'US.{x}' for x in CORE_WATCHLIST]:
        if code not in combined:combined.append(code)
        if len(combined)>=DEEP_CANDIDATES:break
    print(f"CHIEF stage 2: {len(combined)} deep candidates -> {', '.join(c.replace('US.','') for c in combined)}",flush=True); return combined


def snapshot_for_code(ctx,code):
    from moomoo import RET_OK
    ret,data=ctx.get_market_snapshot([code])
    if ret!=RET_OK or data is None or data.empty:raise RuntimeError(f'snapshot failed: {data}')
    row=data.iloc[0]; price=float(row.get('last_price',0) or 0); ask=float(row.get('ask_price',0) or 0); bid=float(row.get('bid_price',0) or 0); mid=(ask+bid)/2.0; spread_pct=((ask-bid)/mid*100.0) if ask>0 and bid>0 and mid>0 else 999.0; return price,spread_pct


def get_bars(ctx,code,ktype,count=300):
    from moomoo import RET_OK,SubType,KLType
    subtype={KLType.K_1M:SubType.K_1M,KLType.K_3M:SubType.K_3M,KLType.K_5M:SubType.K_5M,KLType.K_15M:SubType.K_15M,KLType.K_60M:SubType.K_60M,KLType.K_DAY:SubType.K_DAY}[ktype]; ret,err=ctx.subscribe([code],[subtype],subscribe_push=False)
    if ret!=RET_OK:raise RuntimeError(f'subscribe failed {code} {ktype}: {err}')
    ret,data=ctx.get_cur_kline(code,count,ktype)
    if ret!=RET_OK:raise RuntimeError(str(data))
    return normalize(data)


def release_removed_candidates(ctx,removed):
    if not removed:return
    from moomoo import RET_OK,SubType
    subtypes=[SubType.K_1M,SubType.K_3M,SubType.K_5M,SubType.K_15M,SubType.K_60M,SubType.K_DAY]
    for code in removed:
        ret,err=ctx.unsubscribe([code],subtypes)
        if ret!=RET_OK:print(f'unsubscribe warning {code}: {err}',flush=True)


def _pattern_source_tf(pat,p1,p5,p15):
    if pat is None:return ''
    if any(pat is p for p in p5):return '5m'
    if any(pat is p for p in p1):return '1m'
    if any(pat is p for p in p15):return '15m'
    return ''


def evaluate_trade_mode(ctx,ticker,side,trade_type,price,spread_pct,d1,d3,d5,d15,d60,dd,m15,m60,daily,last_alert):
    micro3=ema8_vwap_3m(d3); p1,p5,p15=[],[],[]
    if trade_type=='DAY TRADE': entry_m,higher_m=m15,m60; p1=detect_patterns(d1); p5=detect_patterns(d5); p15=detect_patterns(d15); patterns=p5+p1+p15; momentum=momentum_snapshot(d15)
    else: entry_m,higher_m=m60,daily; patterns=detect_patterns(d60); momentum=momentum_snapshot(d60)
    score,reasons,pat=score_setup(side,entry_m,higher_m,daily,patterns,momentum,micro3,spread_pct,trade_type)
    if score<WATCH_SCORE:return
    pattern_tf=''
    if trade_type=='DAY TRADE':
        pattern_tf=_pattern_source_tf(pat,p1,p5,p15)
        if pattern_tf=='5m':entry_df=d5; reasons.append('5m pattern confirmation')
        elif pattern_tf=='1m':entry_df=d1; reasons.append('1m pattern confirmation')
        else:
            entry_df=d15
            if pat:reasons.append('15m pattern confirmation')
    else: entry_df=d60; pattern_tf='1H' if pat else ''
    confirmation=evaluate_confirmation(side=side,df=entry_df,score=score,pattern=pat,min_score=CONFIRMED_SCORE)
    if not confirmation['confirmed']:return
    if not signal_window_open():
        print(f'{ticker} {trade_type} {side}: confirmed setup suppressed outside 8:30 AM-4:00 PM ET signal window.',flush=True)
        return
    status='CONFIRMED'; key=(ticker,side,trade_type,status,pattern_tf,pat.name if pat else '')
    if time.time()-last_alert.get(key,0)<=1800:return
    news=news_context(ctx,ticker); options=recommend_options(ctx,ticker,side,trade_type=trade_type)
    if not options.get('ok'):
        print(f"{ticker} {trade_type} {side}: confirmed setup has no valid options contract: {options.get('text','option scan failed')}. Sending underlying signal anyway.",flush=True)
    notify(format_alert(ticker,side,score,status,price,pat,pattern_tf,reasons,spread_pct,momentum,micro3,news,options,confirmation,trade_type)); last_alert[key]=time.time()


def run():
    from moomoo import KLType
    ctx=moomoo_context(); last_alert={}; candidates=[]; last_universe_refresh=0.0
    print('Chief Bot started. Signal delivery window: 8:30 AM-4:00 PM ET weekdays.',flush=True)
    try:
        while True:
            now=time.time()
            if not candidates or now-last_universe_refresh>=UNIVERSE_REFRESH_SECONDS:
                old=set(candidates)
                try:
                    new_candidates=build_deep_scan_list(ctx)
                    if new_candidates: candidates=new_candidates; last_universe_refresh=now; release_removed_candidates(ctx,old-set(candidates))
                    else:print('Stage-1 returned no candidates; keeping previous list.',flush=True)
                except Exception as e:
                    print(f'whole-market refresh error: {e}',flush=True)
                    if not candidates:candidates=[f'US.{x}' for x in CORE_WATCHLIST[:DEEP_CANDIDATES]]
            for code in candidates:
                ticker=code.replace('US.','')
                try:
                    d1=get_bars(ctx,code,KLType.K_1M); d3=get_bars(ctx,code,KLType.K_3M); d5=get_bars(ctx,code,KLType.K_5M); d15=get_bars(ctx,code,KLType.K_15M); d60=get_bars(ctx,code,KLType.K_60M); dd=get_bars(ctx,code,KLType.K_DAY); price,spread_pct=snapshot_for_code(ctx,code)
                    if spread_pct>MAX_SPREAD_PCT:continue
                    m15,m60,daily=metrics(d15),metrics(d60),metrics(dd)
                    for side in ('CALL','PUT'):
                        if DAY_TRADING:evaluate_trade_mode(ctx,ticker,side,'DAY TRADE',price,spread_pct,d1,d3,d5,d15,d60,dd,m15,m60,daily,last_alert)
                        if SWING_TRADING:evaluate_trade_mode(ctx,ticker,side,'SWING',price,spread_pct,d1,d3,d5,d15,d60,dd,m15,m60,daily,last_alert)
                except Exception as e:print(f'{ticker}: {e}',flush=True)
            time.sleep(SCAN_SECONDS)
    finally:ctx.close()


if __name__=='__main__':run()
